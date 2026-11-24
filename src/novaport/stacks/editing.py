# SPDX-License-Identifier: AGPL-3.0-or-later
"""Changes made in the UI, written to git as commits or pull requests."""

import io
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from dotenv import dotenv_values
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from novaport.config import Settings
from novaport.crypto import Cipher
from novaport.deploy.cli import DockerCli
from novaport.deploy.compose import ComposeError, materialize_secrets, secret_variables
from novaport.forges import Forge, ForgeError, open_pull_request
from novaport.git import GitError, GitMirror, Identity, PushRejected
from novaport.locks import lock
from novaport.models import EditMode, Repository, Stack, StackType, User
from novaport.process import CommandTimeout
from novaport.sops import SECRETS_FILE, Sops, SopsError, recipients_for
from novaport.stacks.settings import COMPOSE_FILES
from novaport.stacks.sync import Syncer, SyncResult, credentials_for, mirror_for

MAX_FILE_SIZE = 512 * 1024
SOPS_CONFIG = ".sops.yaml"


class EditError(Exception):
    """The change can't be made as asked (bad path, validation failure, git refused)."""


@dataclass
class FileContent:
    path: str
    content: str | None
    binary: bool
    size: int


@dataclass
class StackFiles:
    commit: str
    files: list[FileContent]


@dataclass
class FileConflict:
    path: str
    base: str | None
    theirs: str | None
    yours: str | None


class EditConflict(Exception):
    def __init__(self, tip: str, conflicts: list[FileConflict]) -> None:
        super().__init__("The stack changed in git since you started editing.")
        self.tip = tip
        self.conflicts = conflicts


@dataclass
class EditResult:
    commit: str
    branch: str
    pull_request_url: str | None = None
    sync: SyncResult | None = None
    warnings: list[str] = field(default_factory=list)


def check_path(path: str) -> str:
    """A safe path relative to the stack folder."""
    pure = PurePosixPath(path)
    if (
        not path
        or pure.is_absolute()
        or any(part in ("", ".", "..", ".git") for part in path.split("/"))
        or "\\" in path
        or "\0" in path
    ):
        raise EditError(f"{path!r} is not a valid file path.")
    if path == SECRETS_FILE:
        raise EditError(
            f"{SECRETS_FILE} is edited through the secrets editor, so it stays encrypted."
        )
    return str(pure)


def _decode(data: bytes) -> str | None:
    try:
        return data.decode()
    except UnicodeDecodeError:
        return None


def author_for(user: User) -> Identity:
    return Identity(user.username, user.email or f"{user.username}@novaport.local")


class StackEditor:
    def __init__(
        self,
        settings: Settings,
        engine: AsyncEngine,
        sessionmaker: async_sessionmaker[AsyncSession],
        syncer: Syncer,
        cipher: Cipher,
        sops: Sops,
        cli: DockerCli,
    ) -> None:
        self.settings = settings
        self.engine = engine
        self.sessionmaker = sessionmaker
        self.syncer = syncer
        self.cipher = cipher
        self.sops = sops
        self.cli = cli
        self.committer = Identity(settings.commit_name, settings.commit_email)

    def _mirror(self, repo: Repository) -> GitMirror:
        return mirror_for(self.settings, repo)

    async def _fetch(self, repo: Repository) -> str:
        try:
            return await self._mirror(repo).fetch(repo.branch, credentials_for(repo, self.cipher))
        except (GitError, ValueError) as exc:
            raise EditError(f"Could not fetch {repo.name}: {exc}") from exc

    async def _read_folder(self, mirror: GitMirror, commit: str, path: str) -> dict[str, bytes]:
        result = {}
        for entry in await mirror.tree_files(commit, path):
            if entry.size <= MAX_FILE_SIZE:
                result[entry.path] = await mirror.blob(entry.blob)
        return result

    async def files(self, stack: Stack) -> StackFiles:
        repo = stack.repository
        async with lock(self.engine, f"sync:{repo.id}"):
            tip = await self._fetch(repo)
            mirror = self._mirror(repo)
            entries = await mirror.tree_files(tip, stack.path)
            files = []
            for entry in entries:
                if entry.path == SECRETS_FILE:
                    continue
                content = (
                    _decode(await mirror.blob(entry.blob)) if entry.size <= MAX_FILE_SIZE else None
                )
                files.append(FileContent(entry.path, content, content is None, entry.size))
        files.sort(key=lambda f: (f.path not in COMPOSE_FILES, f.path))
        return StackFiles(tip, files)

    async def _conflicts(
        self, mirror: GitMirror, stack: Stack, base: str, tip: str, changes: dict[str, bytes | None]
    ) -> list[FileConflict]:
        base_files = await self._read_folder(mirror, base, stack.path)
        tip_files = await self._read_folder(mirror, tip, stack.path)
        yours = {**base_files, **{p: c for p, c in changes.items() if c is not None}}
        for path, content in changes.items():
            if content is None:
                yours.pop(path, None)
        conflicts = []
        for path in sorted(set(base_files) | set(tip_files) | set(changes)):
            if path == SECRETS_FILE:
                continue
            changed_in_git = base_files.get(path) != tip_files.get(path)
            if not changed_in_git and path not in changes:
                continue

            def text(source: dict[str, bytes], key: str = path) -> str | None:
                return _decode(source[key]) if key in source else None

            conflicts.append(FileConflict(path, text(base_files), text(tip_files), text(yours)))
        return conflicts

    async def _validate(
        self, mirror: GitMirror, stack: Stack, tip: str, changes: dict[str, bytes | None]
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="novaport-validate-") as tmp:
            try:
                folder = await mirror.export(tip, stack.path, Path(tmp))
            except GitError:
                folder = Path(tmp) / stack.path
            folder.mkdir(parents=True, exist_ok=True)
            for path, content in changes.items():
                target = folder / path
                if content is None:
                    target.unlink(missing_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(content)
            compose = next((f for f in COMPOSE_FILES if (folder / f).is_file()), None)
            if compose is None:
                raise EditError("The stack would have no compose file.")
            env: dict[str, str] = {}
            if stack.type == StackType.SWARM and (folder / ".env").is_file():
                values = dotenv_values(stream=io.StringIO((folder / ".env").read_text()))
                env = {k: v for k, v in values.items() if v is not None}
            (folder / SECRETS_FILE).unlink(missing_ok=True)
            text = (folder / compose).read_text()
            if stack.type == StackType.SWARM and (variables := secret_variables(text)):
                # Secret values aren't needed to validate, only their shape, so stand-ins do.
                placeholders = dict.fromkeys(variables, "placeholder") | env
                try:
                    source, names = materialize_secrets(text, folder, stack.name, placeholders)
                except ComposeError as exc:
                    raise EditError(str(exc)) from exc
                if names:
                    compose = ".novaport-source.yaml"
                    (folder / compose).write_text(source)
            try:
                result = await self.cli.config(stack.type, stack.name, folder, compose, env)
            except CommandTimeout as exc:
                raise EditError(str(exc)) from exc
            if not result.ok:
                raise EditError(f"The compose file doesn't validate:\n{result.summary()}")

    async def _forge_token(self, repo: Repository) -> str:
        token = repo.forge_token or repo.token
        if not token:
            raise EditError("Pull requests need a forge token on the repository.")
        return self.cipher.decrypt(token)

    async def _publish(
        self,
        db: AsyncSession,
        repo: Repository,
        stack: Stack,
        tip: str,
        changes: dict[str, bytes | None],
        message: str,
        author: Identity,
        base: str,
        mode: EditMode | None = None,
        branch: str | None = None,
        pr_body: str | None = None,
    ) -> EditResult:
        mirror = self._mirror(repo)
        credentials = credentials_for(repo, self.cipher)
        full = {f"{stack.path}/{p}": c for p, c in changes.items()}

        if (mode or stack.edit_mode) == EditMode.PR:
            commit = await mirror.commit(tip, full, message, author, self.committer)
            branch = branch or f"novaport/{stack.name}-{commit[:8]}"
            try:
                await mirror.push(commit, branch, credentials)
                url = await open_pull_request(
                    Forge(repo.forge),
                    repo.forge_api_url,
                    await self._forge_token(repo),
                    repo.url,
                    head=branch,
                    base=repo.branch,
                    title=message.splitlines()[0],
                    body=pr_body or f"Changed {stack.name} in Novaport.",
                )
            except (GitError, ForgeError) as exc:
                raise EditError(str(exc)) from exc
            return EditResult(commit, branch, pull_request_url=url)

        for attempt in range(2):
            commit = await mirror.commit(tip, full, message, author, self.committer)
            try:
                await mirror.push(commit, repo.branch, credentials)
                break
            except PushRejected:
                # Someone pushed between our fetch and push. Retry on top of their commit
                # unless they touched this stack too.
                tip = await self._fetch(repo)
                if attempt or await mirror.changed_between(base, tip, stack.path):
                    raise EditConflict(
                        tip, await self._conflicts(mirror, stack, base, tip, changes)
                    ) from None
            except GitError as exc:
                raise EditError(f"Push failed: {exc}") from exc
        sync = await self.syncer.sync_locked(db, repo)
        return EditResult(commit, repo.branch, sync=sync)

    async def save(
        self,
        stack: Stack,
        user: User,
        base: str,
        changes: dict[str, str | None],
        message: str,
        validate: bool = True,
    ) -> EditResult:
        return await self.save_as(
            stack, author_for(user), base, changes, message, validate=validate
        )

    async def save_as(
        self,
        stack: Stack,
        author: Identity,
        base: str,
        changes: dict[str, str | None],
        message: str,
        *,
        validate: bool = True,
        mode: EditMode | None = None,
        branch: str | None = None,
        pr_body: str | None = None,
    ) -> EditResult:
        """Save changes with an explicit author and, optionally, a different edit mode."""
        encoded = {
            check_path(p): (c.encode() if c is not None else None) for p, c in changes.items()
        }
        if not encoded:
            raise EditError("Nothing to save.")
        async with self.sessionmaker() as db:
            repo = await db.get(Repository, stack.repository_id)
            stack = await db.get(Stack, stack.id) or stack
            if repo is None:
                raise EditError("The repository no longer exists.")
            async with lock(self.engine, f"sync:{repo.id}"):
                tip = await self._fetch(repo)
                mirror = self._mirror(repo)
                if base != tip and await mirror.changed_between(base, tip, stack.path):
                    raise EditConflict(
                        tip, await self._conflicts(mirror, stack, base, tip, encoded)
                    )
                if validate:
                    await self._validate(mirror, stack, tip, encoded)
                result = await self._publish(
                    db,
                    repo,
                    stack,
                    tip,
                    encoded,
                    message,
                    author,
                    base,
                    mode=mode,
                    branch=branch,
                    pr_body=pr_body,
                )
                await db.commit()
                return result

    async def add_stack(
        self,
        repository_id: uuid.UUID,
        name: str,
        stack_type: StackType,
        files: dict[str, str],
        user: User,
        message: str,
    ) -> EditResult:
        """Commit a new stack folder, the way edits to that stack would reach git."""
        encoded: dict[str, bytes | None] = {check_path(p): c.encode() for p, c in files.items()}
        async with self.sessionmaker() as db:
            repo = await db.get(Repository, repository_id)
            if repo is None:
                raise EditError("The repository no longer exists.")
            root = repo.stacks_root.strip("/")
            # Never added to the session: the real row comes from syncing the new commit.
            stack = Stack(
                id=uuid.uuid4(),
                repository_id=repo.id,
                path=f"{root}/{name}" if root else name,
                name=name,
                type=stack_type,
                edit_mode=repo.default_edit_mode,
            )
            async with lock(self.engine, f"sync:{repo.id}"):
                tip = await self._fetch(repo)
                mirror = self._mirror(repo)
                if await mirror.list_files(tip, stack.path):
                    raise EditError(f"{stack.path} already exists in {repo.name}.")
                await self._validate(mirror, stack, tip, encoded)
                result = await self._publish(
                    db,
                    repo,
                    stack,
                    tip,
                    encoded,
                    message,
                    author_for(user),
                    tip,
                    pr_body=f"Adds {name}, which was deployed without Novaport until now.",
                )
                await db.commit()
                return result

    async def read_compose(self, stack: Stack) -> tuple[str, str, str]:
        """Fetch and return (tip commit, compose file name, its content)."""
        repo = stack.repository
        async with lock(self.engine, f"sync:{repo.id}"):
            tip = await self._fetch(repo)
            mirror = self._mirror(repo)
            files = await mirror.list_files(tip, stack.path)
            name = next((f for f in COMPOSE_FILES if f in files), None)
            if name is None:
                raise EditError("The stack has no compose file.")
            content = await mirror.read_file(tip, f"{stack.path}/{name}")
        if content is None:
            raise EditError(f"Could not read {name}.")
        return tip, name, content

    async def has_secrets(self, stack: Stack, commit: str) -> bool:
        files = await self._mirror(stack.repository).list_files(commit, stack.path)
        return SECRETS_FILE in files

    async def read_secrets(self, stack: Stack) -> tuple[str, dict[str, str]]:
        repo = stack.repository
        async with lock(self.engine, f"sync:{repo.id}"):
            tip = await self._fetch(repo)
            raw = await self._mirror(repo).read_file(tip, f"{stack.path}/{SECRETS_FILE}")
        if raw is None:
            return tip, {}
        try:
            return tip, await self.sops.decrypt(raw.encode())
        except SopsError as exc:
            raise EditError(str(exc)) from exc

    async def save_secrets(
        self,
        stack: Stack,
        user: User,
        base: str,
        updates: dict[str, str],
        removals: list[str],
        message: str,
    ) -> EditResult:
        recipient = self.sops.recipient
        if recipient is None:
            raise EditError("NOVAPORT_AGE_KEY is not set, so secrets can't be written.")
        async with self.sessionmaker() as db:
            repo = await db.get(Repository, stack.repository_id)
            stack = await db.get(Stack, stack.id) or stack
            if repo is None:
                raise EditError("The repository no longer exists.")
            async with lock(self.engine, f"sync:{repo.id}"):
                tip = await self._fetch(repo)
                mirror = self._mirror(repo)
                path = f"{stack.path}/{SECRETS_FILE}"
                if base != tip and (
                    await mirror.read_file(base, path) != await mirror.read_file(tip, path)
                ):
                    raise EditConflict(tip, [])
                raw = await mirror.read_file(tip, path)
                try:
                    values = await self.sops.decrypt(raw.encode()) if raw else {}
                    values.update(updates)
                    for key in removals:
                        values.pop(key, None)
                    sops_config = await mirror.read_file(tip, SOPS_CONFIG)
                    content = (
                        await self.sops.encrypt(
                            values, recipients_for(sops_config, path, recipient)
                        )
                        if values
                        else None
                    )
                except SopsError as exc:
                    raise EditError(str(exc)) from exc
                if raw is None and content is None:
                    raise EditError("Nothing to save.")
                result = await self._publish(
                    db, repo, stack, tip, {SECRETS_FILE: content}, message, author_for(user), base
                )
                await db.commit()
                return result


def default_message(stack: Stack, paths: list[str]) -> str:
    what = paths[0] if len(paths) == 1 else f"{len(paths)} files"
    return f"Update {what} in {stack.name}"
