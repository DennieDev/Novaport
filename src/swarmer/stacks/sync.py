# SPDX-License-Identifier: AGPL-3.0-or-later
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from swarmer.config import Settings
from swarmer.crypto import Cipher
from swarmer.git import Credentials, EmptyRepository, GitError, GitMirror, Identity
from swarmer.locks import lock, try_lock
from swarmer.models import Endpoint, GitAuth, Repository, Revision, Stack, StackType
from swarmer.sops import age_recipient
from swarmer.stacks.deployments import deployment_for_new_revision
from swarmer.stacks.settings import (
    COMPOSE_FILES,
    LOCAL_ENDPOINT,
    SETTINGS_FILE,
    InvalidSettings,
    parse_settings,
    stack_name,
)

log = logging.getLogger(__name__)

SOPS_CONFIG = """\
creation_rules:
  - path_regex: secrets\\.env$
    age: >-
      {recipient}
"""


@dataclass
class SyncResult:
    commit: str | None = None
    new_revisions: list[uuid.UUID] = field(default_factory=list)
    queued_deployments: int = 0
    skipped: bool = False
    error: str | None = None


def mirror_path(settings: Settings, repository_id: uuid.UUID) -> Path:
    return settings.data_dir / "repos" / str(repository_id)


def mirror_for(settings: Settings, repo: Repository) -> GitMirror:
    return GitMirror(
        mirror_path(settings, repo.id),
        repo.url,
        known_hosts=settings.data_dir / "known_hosts",
        timeout=settings.command_timeout.total_seconds(),
    )


def credentials_for(repo: Repository, cipher: Cipher) -> Credentials:
    if repo.auth == GitAuth.SSH and repo.ssh_private_key:
        return Credentials(ssh_private_key=cipher.decrypt(repo.ssh_private_key))
    if repo.auth == GitAuth.TOKEN and repo.token:
        return Credentials(token=cipher.decrypt(repo.token))
    return Credentials()


class Syncer:
    def __init__(
        self,
        settings: Settings,
        engine: AsyncEngine,
        sessionmaker: async_sessionmaker[AsyncSession],
        cipher: Cipher,
    ) -> None:
        self.settings = settings
        self.engine = engine
        self.sessionmaker = sessionmaker
        self.cipher = cipher

    async def sync(self, repository_id: uuid.UUID) -> SyncResult:
        async with try_lock(self.engine, f"sync:{repository_id}") as acquired:
            if not acquired:
                return SyncResult(skipped=True)
            async with self.sessionmaker() as db:
                repo = await db.get(Repository, repository_id)
                if repo is None:
                    return SyncResult(skipped=True)
                result = await self.sync_locked(db, repo)
                await db.commit()
                return result

    async def ensure_commit(self, repo: Repository, commit: str) -> None:
        """Make sure the local mirror has `commit`, fetching if it doesn't (a fresh data
        directory after a restart, or a commit that arrived through another replica)."""
        mirror = mirror_for(self.settings, repo)
        if await mirror.has_commit(commit):
            return
        async with lock(self.engine, f"sync:{repo.id}"):
            await mirror.fetch(repo.branch, credentials_for(repo, self.cipher))

    async def sync_locked(self, db: AsyncSession, repo: Repository) -> SyncResult:
        """Sync while the caller already holds this repository's sync lock."""
        return await self._sync(db, repo)

    async def _initialize(
        self, mirror: GitMirror, repo: Repository, credentials: Credentials
    ) -> str:
        """Push a first commit to an empty remote: the stacks folder, and a .sops.yaml with
        Swarmer's age key as recipient when one is configured."""
        root = repo.stacks_root.strip("/")
        files: dict[str, bytes | None] = {f"{root}/.gitkeep" if root else ".gitkeep": b""}
        if self.settings.age_key:
            recipient = age_recipient(self.settings.age_key.get_secret_value())
            files[".sops.yaml"] = SOPS_CONFIG.format(recipient=recipient).encode()
        identity = Identity(self.settings.commit_name, self.settings.commit_email)
        commit = await mirror.commit(None, files, "Initialize repository", identity, identity)
        await mirror.push(commit, repo.branch, credentials)
        log.info("Pushed a first commit to empty repository %s", repo.name)
        return commit

    async def _sync(self, db: AsyncSession, repo: Repository) -> SyncResult:
        mirror = mirror_for(self.settings, repo)
        credentials = credentials_for(repo, self.cipher)
        repo.last_synced_at = datetime.now(UTC)
        try:
            commit = await mirror.fetch(repo.branch, credentials)
        except EmptyRepository:
            try:
                commit = await self._initialize(mirror, repo, credentials)
            except (GitError, ValueError) as exc:
                repo.last_error = (
                    "The repository is empty, and pushing a first commit to it failed. "
                    f"Swarmer needs write access for that: {exc}"
                )
                log.warning("Initializing repository %s failed: %s", repo.name, exc)
                return SyncResult(error=repo.last_error)
        except (GitError, ValueError) as exc:
            repo.last_error = f"Fetch failed: {exc}"
            log.warning("Sync of repository %s failed: %s", repo.name, exc)
            return SyncResult(error=repo.last_error)

        result = SyncResult(commit=commit)
        warnings: list[str] = []
        stacks = {
            s.path: s for s in await db.scalars(select(Stack).where(Stack.repository_id == repo.id))
        }
        seen: set[str] = set()
        endpoints = {e.name: e.id for e in await db.scalars(select(Endpoint))}

        for path in await mirror.list_dirs(commit, repo.stacks_root):
            files = await mirror.list_files(commit, path)
            if not any(f in files for f in COMPOSE_FILES):
                continue
            name = stack_name(path)
            if name is None:
                warnings.append(f"Skipped {path}: folder name isn't a valid stack name.")
                continue

            stack = stacks.get(path)
            if stack is None:
                taken = await db.scalar(select(Stack).where(Stack.name == name))
                if taken is not None:
                    warnings.append(f"Skipped {path}: stack name {name!r} is already in use.")
                    continue
                stack = Stack(repository_id=repo.id, path=path, name=name)
                db.add(stack)
                stacks[path] = stack
            seen.add(path)
            stack.removed_at = None

            try:
                stack_settings = parse_settings(
                    await mirror.read_file(commit, f"{path}/{SETTINGS_FILE}")
                    if SETTINGS_FILE in files
                    else None
                )
            except InvalidSettings as exc:
                stack.config_error = str(exc)
                continue
            endpoint_name = stack_settings.endpoint or LOCAL_ENDPOINT
            if endpoint_name != LOCAL_ENDPOINT and endpoint_name not in endpoints:
                stack.config_error = (
                    f"{SETTINGS_FILE}: unknown endpoint {endpoint_name!r}. "
                    "Add it under Endpoints first."
                )
                continue
            stack.config_error = None
            stack.endpoint_id = endpoints.get(endpoint_name)
            stack.type = stack_settings.type or StackType.SWARM
            stack.deploy_mode = stack_settings.deploy or repo.default_deploy_mode
            stack.edit_mode = stack_settings.edits or repo.default_edit_mode
            await db.flush()

            tree = await mirror.tree_hash(commit, path)
            latest = await db.scalar(
                select(Revision)
                .where(Revision.stack_id == stack.id)
                .order_by(Revision.created_at.desc())
                .limit(1)
            )
            if latest is not None and latest.tree == tree:
                continue

            info = await mirror.last_commit(commit, path)
            revision = Revision(
                stack_id=stack.id,
                commit=info.sha,
                tree=tree,
                message=info.message,
                author=info.author,
                author_email=info.author_email,
                committed_at=info.committed_at,
                created_at=datetime.now(UTC),
            )
            db.add(revision)
            await db.flush()
            result.new_revisions.append(revision.id)
            if await deployment_for_new_revision(db, stack, revision):
                result.queued_deployments += 1

        now = datetime.now(UTC)
        for path, stack in stacks.items():
            if path not in seen and stack.removed_at is None:
                stack.removed_at = now

        repo.last_commit = commit
        repo.last_error = "\n".join(warnings) or None
        return result
