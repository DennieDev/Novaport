# SPDX-License-Identifier: AGPL-3.0-or-later
import io
import logging
import re
import tempfile
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path

import yaml
from dotenv import dotenv_values
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from novaport.agents.pool import Agents
from novaport.config import Settings
from novaport.deploy.cli import DockerCli
from novaport.deploy.compose import (
    LABEL_COMMIT,
    LABEL_REVISION,
    LABEL_STACK,
    ComposeError,
    add_labels,
    apply_secret_names,
    materialize_secrets,
)
from novaport.deploy.converge import converge_swarm
from novaport.deploy.drift import snapshot
from novaport.deploy.preflight import preflight
from novaport.docker.client import ClusterReader
from novaport.docker.views import STACK_LABEL
from novaport.edge.hub import EndpointOffline, Target
from novaport.git import GitError, GitMirror
from novaport.models import (
    Deployment,
    DeploymentEvent,
    DeploymentStatus,
    EventType,
    Stack,
    StackType,
)
from novaport.notifications import Event
from novaport.process import CommandTimeout, Result
from novaport.registries.client import Login, RegistryClient
from novaport.registries.store import docker_config
from novaport.sops import SECRETS_FILE, Sops, SopsError
from novaport.stacks.settings import COMPOSE_FILES

log = logging.getLogger(__name__)

RENDERED_FILE = ".novaport-compose.yaml"
SOURCE_FILE = ".novaport-source.yaml"


class DeployFailed(Exception):
    def __init__(self, stage: str, message: str) -> None:
        super().__init__(message)
        self.stage = stage


class Pipeline:
    def __init__(
        self,
        settings: Settings,
        sessionmaker: async_sessionmaker[AsyncSession],
        cli: DockerCli,
        reader: Callable[[], ClusterReader | None],
        mirror: Callable[[Stack], GitMirror],
        agents: Callable[[], Agents | None] = lambda: None,
        sops: Callable[[], Sops | None] = lambda: None,
        ensure_commit: Callable[[Stack, str], Awaitable[None]] | None = None,
        registry: Callable[[], RegistryClient | None] = lambda: None,
        logins: Callable[[], Awaitable[dict[str, Login]]] | None = None,
        notify: Callable[[Event], None] | None = None,
        targets: Callable[[uuid.UUID | None], Target] | None = None,
    ) -> None:
        self.targets = targets
        self.notify = notify
        self.registry = registry
        self.logins = logins
        self.settings = settings
        self.sessionmaker = sessionmaker
        self.cli = cli
        self.reader = reader
        self.mirror = mirror
        self.agents = agents
        self.sops = sops
        self.ensure_commit = ensure_commit

    async def run(self, deployment_id: uuid.UUID) -> DeploymentStatus:
        async with self.sessionmaker() as db:
            deployment = await db.get(
                Deployment,
                deployment_id,
                options=[
                    selectinload(Deployment.stack).selectinload(Stack.repository),
                    selectinload(Deployment.stack).selectinload(Stack.endpoint),
                ],
            )
            if deployment is None or deployment.status != DeploymentStatus.PENDING:
                return deployment.status if deployment else DeploymentStatus.CANCELLED
            await db.refresh(deployment, ["revision"])
            deployment.status = DeploymentStatus.RUNNING
            deployment.started_at = datetime.now(UTC)
            await db.commit()

            try:
                await self._deploy(db, deployment)
            except DeployFailed as exc:
                await self._event(db, deployment, exc.stage, "error", str(exc))
                deployment.status = DeploymentStatus.FAILED
                deployment.error = str(exc)
            except Exception as exc:
                log.exception("Deployment %s crashed", deployment_id)
                await self._event(db, deployment, deployment.stage or "?", "error", repr(exc))
                deployment.status = DeploymentStatus.FAILED
                deployment.error = f"Internal error: {exc}"
            else:
                deployment.status = DeploymentStatus.SUCCEEDED
                deployment.stack.last_good_revision_id = deployment.revision_id
            deployment.finished_at = datetime.now(UTC)
            await db.commit()
            self._announce(deployment)
            return deployment.status

    def _announce(self, deployment: Deployment) -> None:
        if self.notify is None:
            return
        stack = deployment.stack
        commit = deployment.revision.commit[:10]
        if deployment.status == DeploymentStatus.SUCCEEDED:
            kind, title, text = (
                EventType.DEPLOY_SUCCEEDED,
                f"Deployed {stack.name} at {commit}",
                deployment.revision.message,
            )
        else:
            kind, title, text = (
                EventType.DEPLOY_FAILED,
                f"Deploying {stack.name} at {commit} failed",
                deployment.error or "",
            )
        self.notify(
            Event(
                kind,
                title,
                text,
                stack_id=stack.id,
                stack_name=stack.name,
                link=f"/deployments/{deployment.id}",
                data={
                    "deployment": str(deployment.id),
                    "commit": deployment.revision.commit,
                    "trigger": deployment.trigger,
                },
            )
        )

    async def _event(
        self, db: AsyncSession, deployment: Deployment, stage: str, level: str, message: str
    ) -> None:
        deployment.stage = stage
        db.add(
            DeploymentEvent(
                deployment_id=deployment.id,
                stage=stage,
                level=level,
                message=message[-20_000:],
                at=datetime.now(UTC),
            )
        )
        await db.commit()

    async def _command(
        self, db: AsyncSession, deployment: Deployment, stage: str, result: Result
    ) -> None:
        output = "\n".join(p for p in (result.stdout.strip(), result.stderr.strip()) if p)
        if not result.ok:
            raise DeployFailed(stage, output or f"exit code {result.returncode}")
        if output:
            await self._event(db, deployment, stage, "info", output)

    def _target(self, stack: Stack) -> Target:
        if self.targets is None:
            return Target("local", self.cli, self.reader(), self.agents())
        try:
            return self.targets(stack.endpoint_id)
        except EndpointOffline as exc:
            raise DeployFailed("resolve", str(exc)) from exc

    async def _deploy(self, db: AsyncSession, deployment: Deployment) -> None:
        stack = deployment.stack
        revision = deployment.revision
        timeout = int(self.settings.deploy_timeout.total_seconds())
        target = self._target(stack)
        if stack.endpoint is not None:
            await self._event(
                db, deployment, "resolve", "info", f"Deploying to endpoint {stack.endpoint.name}"
            )

        with tempfile.TemporaryDirectory(prefix="novaport-deploy-") as tmp:
            await self._event(
                db,
                deployment,
                "resolve",
                "info",
                f"Checking out {stack.path} at {revision.commit[:12]}",
            )
            try:
                if self.ensure_commit is not None:
                    await self.ensure_commit(stack, revision.commit)
                folder = await self.mirror(stack).export(revision.commit, stack.path, Path(tmp))
            except (GitError, OSError, ValueError) as exc:
                raise DeployFailed(
                    "resolve", f"Could not read the revision from git: {exc}"
                ) from exc

            compose_file = next((f for f in COMPOSE_FILES if (folder / f).is_file()), None)
            if compose_file is None:
                raise DeployFailed("resolve", "No compose file in the stack folder.")
            secret_values = await self._decrypt_secrets(folder)

            env: dict[str, str] = {}
            env_file = folder / ".env"
            if stack.type == StackType.SWARM and env_file.is_file():
                # `docker stack` doesn't read .env itself, unlike `docker compose`.
                values = dotenv_values(stream=io.StringIO(env_file.read_text()))
                env = {k: v for k, v in values.items() if v is not None}
            env |= secret_values

            secret_names: dict[str, str] = {}
            if stack.type == StackType.SWARM:
                try:
                    source, secret_names = materialize_secrets(
                        (folder / compose_file).read_text(), folder, stack.name, env
                    )
                except ComposeError as exc:
                    raise DeployFailed("resolve", str(exc)) from exc
                if secret_names:
                    compose_file = SOURCE_FILE
                    (folder / compose_file).write_text(source)

            await self._event(db, deployment, "validate", "info", f"Validating {compose_file}")
            try:
                config = await target.cli.config(stack.type, stack.name, folder, compose_file, env)
            except CommandTimeout as exc:
                raise DeployFailed("validate", str(exc)) from exc
            if not config.ok:
                raise DeployFailed("validate", config.summary())

            try:
                rendered = add_labels(
                    apply_secret_names(config.stdout, secret_names),
                    stack.type,
                    {
                        LABEL_STACK: stack.name,
                        LABEL_REVISION: str(revision.id),
                        LABEL_COMMIT: revision.commit,
                    },
                )
            except ComposeError as exc:
                raise DeployFailed("validate", str(exc)) from exc
            (folder / RENDERED_FILE).write_text(rendered)

            await self._preflight(db, deployment, config.stdout, target)

            await self._event(db, deployment, "apply", "info", f"Deploying stack {stack.name}")
            started = datetime.now(UTC)
            logins = await self.logins() if self.logins else {}
            try:
                with docker_config(logins) as auth_env:
                    result = await target.cli.apply(
                        stack.type,
                        stack.name,
                        folder,
                        RENDERED_FILE,
                        timeout,
                        (env if stack.type == StackType.COMPOSE else {}) | auth_env,
                    )
            except CommandTimeout as exc:
                raise DeployFailed("apply", str(exc)) from exc
            await self._command(db, deployment, "apply", result)

        if stack.type == StackType.SWARM:
            await self._converge(db, deployment, started, timeout, target.reader)
            await self._remove_old_secrets(db, deployment, set(secret_names.values()), target)
            await self._snapshot(stack, target.reader)
        else:
            await self._event(db, deployment, "converge", "info", "All containers are up.")

    async def _snapshot(self, stack: Stack, reader: ClusterReader | None) -> None:
        """Remember the services as deployed, as the baseline for drift detection."""
        if reader is None:
            return
        services = await reader.services(label=f"{STACK_LABEL}={stack.name}")
        stack.deployed_spec = snapshot(services, stack.name)
        stack.drift = []
        stack.drift_checked_at = datetime.now(UTC)

    async def _decrypt_secrets(self, folder: Path) -> dict[str, str]:
        encrypted = folder / SECRETS_FILE
        if not encrypted.is_file():
            return {}
        sops = self.sops()
        if sops is None or not sops.available:
            raise DeployFailed(
                "resolve", f"{SECRETS_FILE} is present, but NOVAPORT_AGE_KEY is not set."
            )
        try:
            values = await sops.decrypt(encrypted.read_bytes())
        except SopsError as exc:
            raise DeployFailed("resolve", f"Could not decrypt {SECRETS_FILE}: {exc}") from exc
        encrypted.unlink()
        return values

    async def _remove_old_secrets(
        self, db: AsyncSession, deployment: Deployment, current: set[str], target: Target
    ) -> None:
        """Remove secrets earlier deploys of this stack created and no service uses any more."""
        reader = target.reader
        if reader is None:
            return
        stack = deployment.stack.name
        pattern = re.compile(rf"^{re.escape(stack)}_.+_[0-9a-f]{{8}}$")
        stale = [
            name
            for s in await reader.secrets()
            if (name := s.get("Spec", {}).get("Name", ""))
            and pattern.match(name)
            and name not in current
            and (s.get("Spec", {}).get("Labels") or {}).get(STACK_LABEL) == stack
        ]
        if not stale:
            return
        result = await target.cli.remove_secrets(stale)
        removed = [n for n in stale if n not in result.stderr]
        if removed:
            await self._event(
                db, deployment, "converge", "info", f"Removed old secrets: {', '.join(removed)}"
            )

    async def _preflight(
        self, db: AsyncSession, deployment: Deployment, normalized: str, target: Target
    ) -> None:
        reader = target.reader
        if reader is None:
            await self._event(
                db, deployment, "preflight", "warning", "Skipped: no Docker connection."
            )
            return
        await self._event(
            db, deployment, "preflight", "info", "Checking images, mounts, and references"
        )
        stack = deployment.stack
        report = await preflight(
            yaml.safe_load(normalized),
            stack.type,
            stack.name,
            reader,
            target.agents,
            self.registry(),
        )
        for note in report.notes:
            await self._event(db, deployment, "preflight", "info", note)
        for warning in report.warnings:
            await self._event(db, deployment, "preflight", "warning", warning)
        if report.errors:
            raise DeployFailed("preflight", "\n".join(report.errors))

    async def _converge(
        self,
        db: AsyncSession,
        deployment: Deployment,
        started: datetime,
        timeout: int,
        reader: ClusterReader | None,
    ) -> None:
        if reader is None:
            raise DeployFailed("converge", "No Docker connection to watch the rollout.")

        async def report(summary: str) -> None:
            await self._event(db, deployment, "converge", "info", summary or "No services yet")

        outcome = await converge_swarm(
            reader,
            deployment.stack.name,
            started=started,
            timeout=timeout,
            stable_for=self.settings.deploy_stable_for.total_seconds(),
            report=report,
        )
        if not outcome.ok:
            raise DeployFailed("converge", "\n".join(outcome.errors) or "Rollout failed.")
        await self._event(db, deployment, "converge", "info", "All services are running.")
