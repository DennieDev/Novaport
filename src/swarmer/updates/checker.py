# SPDX-License-Identifier: AGPL-3.0-or-later
"""Checks services with an `x-updates` policy for newer images and acts on what it finds."""

import logging
import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from swarmer.auth.access import record
from swarmer.docker.client import ClusterReader, DockerUnavailable, NotSwarmManager
from swarmer.docker.views import STACK_LABEL
from swarmer.edge.hub import EndpointOffline, Target
from swarmer.git import Identity
from swarmer.locks import LockTimeout, try_lock
from swarmer.models import EditMode, ImageCheck, Stack, StackType
from swarmer.registries.client import RegistryClient, RegistryError
from swarmer.registries.reference import InvalidReference, Reference, parse_reference
from swarmer.stacks.editing import EditConflict, EditError, StackEditor
from swarmer.updates.policy import (
    Action,
    InvalidCompose,
    Mode,
    Policy,
    ServicePolicy,
    newer_tag,
    read_policies,
)
from swarmer.updates.rewrite import RewriteError, replace_image

log = logging.getLogger(__name__)

CURRENT = "current"
AVAILABLE = "available"
ERROR = "error"
DISABLED = "disabled"


@dataclass
class Finding:
    service: str
    policy: ServicePolicy
    current: str | None
    candidate: str | None = None
    status: str = CURRENT
    error: str | None = None
    action_taken: str | None = None
    action_ref: str | None = None


@dataclass
class UpdateEvent:
    """A newly found update and what Swarmer did about it."""

    stack_id: uuid.UUID
    stack_name: str
    service: str
    current: str | None
    candidate: str
    action_taken: str
    action_ref: str | None


EventHook = Callable[[UpdateEvent], Awaitable[None]]


def _label(ref: str) -> str:
    """Short form for commit messages: the tag, plus a digest prefix when pinned."""
    try:
        parsed = parse_reference(ref)
    except InvalidReference:
        return ref
    text = parsed.tag or ""
    if parsed.digest:
        text += f"@{parsed.digest.removeprefix('sha256:')[:12]}"
    return text


def _branch_part(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-.")[:60] or "update"


class UpdateChecker:
    def __init__(
        self,
        engine: AsyncEngine,
        sessionmaker: async_sessionmaker[AsyncSession],
        editor: StackEditor,
        registry: RegistryClient,
        reader: Callable[[], ClusterReader | None],
        author: Identity,
        targets: Callable[[uuid.UUID | None], Target] | None = None,
    ) -> None:
        self.targets = targets
        self.engine = engine
        self.sessionmaker = sessionmaker
        self.editor = editor
        self.registry = registry
        self.reader = reader
        self.author = author
        self.hooks: list[EventHook] = []

    async def _running_digests(self, stack: Stack) -> dict[str, str]:
        if self.targets is None:
            reader = self.reader()
        else:
            try:
                reader = self.targets(stack.endpoint_id).reader
            except EndpointOffline:
                return {}
        if reader is None or stack.type != StackType.SWARM:
            return {}
        try:
            services = await reader.services(label=f"{STACK_LABEL}={stack.name}")
        except (DockerUnavailable, NotSwarmManager):
            return {}
        prefix = f"{stack.name}_"
        result = {}
        for service in services:
            spec = service.get("Spec", {})
            name = str(spec.get("Name", ""))
            image = str(spec.get("TaskTemplate", {}).get("ContainerSpec", {}).get("Image", ""))
            if name.startswith(prefix) and "@" in image:
                result[name.removeprefix(prefix)] = image.rsplit("@", 1)[1]
        return result

    async def _evaluate(self, entry: ServicePolicy, running: str | None) -> Finding:
        finding = Finding(entry.service, entry, entry.image)
        if entry.error or entry.policy is None:
            finding.status, finding.error = ERROR, entry.error
            return finding
        policy = entry.policy
        if policy.mode == Mode.NONE:
            finding.status = DISABLED
            return finding
        if entry.image is None:
            finding.status, finding.error = ERROR, "The service has no image."
            return finding
        try:
            ref = parse_reference(entry.image)
        except InvalidReference as exc:
            finding.status, finding.error = ERROR, str(exc)
            return finding
        try:
            candidate = await (
                self._newer_version(ref, policy)
                if policy.mode == Mode.VERSION
                else self._newer_digest(ref, running)
            )
        except (RegistryError, ValueError) as exc:
            finding.status, finding.error = ERROR, str(exc)
            return finding
        if candidate is not None:
            finding.status, finding.candidate = AVAILABLE, str(candidate)
        return finding

    async def _newer_version(self, ref: Reference, policy: Policy) -> Reference | None:
        if not ref.tag:
            raise ValueError("Version updates need a tag on the image.")
        tag = newer_tag(ref.tag, await self.registry.tags(ref), policy)
        if tag is None:
            return None
        # Keep a digest pin pinned, to the new tag's digest.
        digest = await self.registry.digest(ref, tag) if ref.digest else None
        return ref.with_tag(tag, digest)

    async def _newer_digest(self, ref: Reference, running: str | None) -> Reference | None:
        latest = await self.registry.digest(ref)
        if latest is None:
            raise ValueError(f"{ref.repository}:{ref.tag} doesn't exist on {ref.registry}.")
        known = ref.digest or running
        return None if known == latest else ref.with_digest(latest)

    async def check(self, stack_id: uuid.UUID) -> list[ImageCheck]:
        """Check one stack now. Returns the stored results, or nothing if another check runs."""
        async with try_lock(self.engine, f"updates:{stack_id}") as acquired:
            if not acquired:
                return []
            async with self.sessionmaker() as db:
                stack = await db.scalar(
                    select(Stack)
                    .options(selectinload(Stack.repository))
                    .where(Stack.id == stack_id)
                )
                if stack is None or stack.removed_at is not None:
                    return []
                return await self._check(db, stack)

    async def _check(self, db: AsyncSession, stack: Stack) -> list[ImageCheck]:
        previous = {
            c.service: c
            for c in await db.scalars(select(ImageCheck).where(ImageCheck.stack_id == stack.id))
        }
        try:
            tip, compose_file, text = await self.editor.read_compose(stack)
            policies = read_policies(text)
        except (EditError, InvalidCompose, LockTimeout) as exc:
            log.warning("Update check of %s failed: %s", stack.name, exc)
            return list(previous.values())

        running = await self._running_digests(stack)
        findings = [await self._evaluate(p, running.get(p.service)) for p in policies]
        new = [
            f
            for f in findings
            if f.status == AVAILABLE
            and (f.service not in previous or previous[f.service].acted_on_ref != f.candidate)
        ]
        await self._act(db, stack, tip, compose_file, text, new)
        acted = {id(f) for f in new}

        now = datetime.now(UTC)
        await db.execute(
            delete(ImageCheck).where(
                ImageCheck.stack_id == stack.id,
                ImageCheck.service.not_in([f.service for f in findings]),
            )
        )
        rows = []
        for f in findings:
            row = previous.get(f.service) or ImageCheck(stack_id=stack.id, service=f.service)
            row.current_ref = f.current
            row.candidate_ref = f.candidate
            row.status = f.status
            row.error = f.error
            row.policy = f.policy.policy.model_dump(mode="json") if f.policy.policy else {}
            row.checked_at = now
            if id(f) in acted:
                row.action_taken = f.action_taken
                row.action_ref = f.action_ref
                if f.action_taken != "failed":
                    row.acted_on_ref = f.candidate
            db.add(row)
            rows.append(row)
        await db.commit()

        for f in new:
            if f.action_taken and f.action_taken != "failed" and f.candidate:
                event = UpdateEvent(
                    stack.id,
                    stack.name,
                    f.service,
                    f.current,
                    f.candidate,
                    f.action_taken,
                    f.action_ref,
                )
                for hook in self.hooks:
                    try:
                        await hook(event)
                    except Exception:
                        log.exception("Update hook failed for %s/%s", stack.name, f.service)
        return rows

    async def _act(
        self,
        db: AsyncSession,
        stack: Stack,
        tip: str,
        compose_file: str,
        text: str,
        findings: list[Finding],
    ) -> None:
        to_commit = []
        for f in findings:
            policy = f.policy.policy
            if policy is None or f.candidate is None:
                continue
            if policy.action == Action.COMMIT:
                to_commit.append(f)
            elif policy.action == Action.PR:
                await self._propose(db, stack, tip, compose_file, text, f)
            elif policy.action == Action.NOTIFY:
                f.action_taken = "notified"
            else:
                f.action_taken = "recorded"
        if to_commit:
            await self._commit(db, stack, tip, compose_file, text, to_commit)

    def _message(self, stack: Stack, findings: list[Finding]) -> str:
        if len(findings) == 1:
            f = findings[0]
            return f"Update {f.service} in {stack.name} to {_label(f.candidate or '')}"
        lines = [f"{f.service}: {f.current} -> {f.candidate}" for f in findings]
        return f"Update images in {stack.name}\n\n" + "\n".join(lines)

    async def _commit(
        self,
        db: AsyncSession,
        stack: Stack,
        tip: str,
        compose_file: str,
        text: str,
        findings: list[Finding],
    ) -> None:
        try:
            for f in findings:
                text = replace_image(text, f.service, f.candidate or "")
            result = await self.editor.save_as(
                stack,
                self.author,
                tip,
                {compose_file: text},
                self._message(stack, findings),
                mode=EditMode.COMMIT,
            )
        except (EditConflict, EditError, RewriteError, LockTimeout) as exc:
            self._failed(findings, exc)
            return
        for f in findings:
            f.action_taken, f.action_ref = "committed", result.commit
        record(
            db,
            "update.commit",
            target=stack.name,
            stack_id=stack.id,
            details={"commit": result.commit, "services": [f.service for f in findings]},
        )

    async def _propose(
        self,
        db: AsyncSession,
        stack: Stack,
        tip: str,
        compose_file: str,
        text: str,
        f: Finding,
    ) -> None:
        label = _label(f.candidate or "")
        try:
            result = await self.editor.save_as(
                stack,
                self.author,
                tip,
                {compose_file: replace_image(text, f.service, f.candidate or "")},
                self._message(stack, [f]),
                mode=EditMode.PR,
                branch=f"swarmer/update-{stack.name}-{_branch_part(f.service)}-{_branch_part(label)}",
                pr_body=(
                    f"Swarmer found a newer image for {f.service} in {stack.name}.\n\n"
                    f"- current: `{f.current}`\n- new: `{f.candidate}`"
                ),
            )
        except (EditConflict, EditError, RewriteError, LockTimeout) as exc:
            self._failed([f], exc)
            return
        f.action_taken, f.action_ref = "pr_opened", result.pull_request_url
        record(
            db,
            "update.pr",
            target=stack.name,
            stack_id=stack.id,
            details={"service": f.service, "pull_request": result.pull_request_url},
        )

    def _failed(self, findings: list[Finding], exc: Exception) -> None:
        message = str(exc)
        if isinstance(exc, EditConflict):
            message = "The stack changed in git during the check; trying again next time."
        for f in findings:
            f.action_taken, f.error = "failed", message[:2000]
