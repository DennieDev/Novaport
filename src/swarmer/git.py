# SPDX-License-Identifier: AGPL-3.0-or-later
"""Thin async wrapper around the git CLI for one local mirror of a remote repository."""

import base64
import io
import shlex
import tarfile
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from swarmer.process import CommandFailed, CommandTimeout, run, run_binary

GIT_ENV = {"GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null"}


@dataclass(frozen=True)
class Credentials:
    ssh_private_key: str | None = None
    token: str | None = None


@dataclass(frozen=True)
class Identity:
    name: str
    email: str


@dataclass(frozen=True)
class FileEntry:
    path: str  # relative to the folder that was listed
    mode: str
    blob: str
    size: int


@dataclass(frozen=True)
class CommitInfo:
    sha: str
    author: str
    committed_at: datetime
    message: str
    author_email: str = ""


class GitError(Exception):
    pass


class PushRejected(GitError):
    """The remote branch moved since we fetched it."""


class EmptyRepository(GitError):
    """The remote has no branches at all."""


class GitMirror:
    """A bare clone used as a local cache; files are read from it by commit, never checked out."""

    def __init__(self, path: Path, url: str, known_hosts: Path, timeout: float = 120) -> None:
        self.path = path
        self.url = url
        self.known_hosts = known_hosts
        self.timeout = timeout

    async def _git(
        self, *args: str, env: dict[str, str] | None = None, stdin: bytes | None = None
    ) -> str:
        try:
            result = await run(
                ["git", *args],
                cwd=self.path,
                env=GIT_ENV | (env or {}),
                stdin=stdin,
                timeout=self.timeout,
            )
        except (CommandFailed, CommandTimeout) as exc:
            raise GitError(str(exc)) from exc
        return result.stdout

    @asynccontextmanager
    async def _auth_env(self, credentials: Credentials) -> AsyncIterator[dict[str, str]]:
        env: dict[str, str] = {}
        with tempfile.TemporaryDirectory(prefix="swarmer-git-") as tmp:
            if credentials.ssh_private_key:
                key = Path(tmp) / "id"
                key.write_text(credentials.ssh_private_key.rstrip() + "\n")
                key.chmod(0o600)
                self.known_hosts.parent.mkdir(parents=True, exist_ok=True)
                env["GIT_SSH_COMMAND"] = shlex.join(
                    [
                        "ssh",
                        "-i",
                        str(key),
                        "-o",
                        "IdentitiesOnly=yes",
                        "-o",
                        "BatchMode=yes",
                        "-o",
                        "StrictHostKeyChecking=accept-new",
                        "-o",
                        f"UserKnownHostsFile={self.known_hosts}",
                    ]
                )
            if credentials.token:
                # Passed through config environment variables so the token never shows up
                # in the process list or in the stored remote URL.
                basic = base64.b64encode(f"x-access-token:{credentials.token}".encode()).decode()
                env |= {
                    "GIT_CONFIG_COUNT": "1",
                    "GIT_CONFIG_KEY_0": "http.extraHeader",
                    "GIT_CONFIG_VALUE_0": f"Authorization: Basic {basic}",
                }
            yield env

    async def fetch(self, branch: str, credentials: Credentials) -> str:
        """Bring the mirror up to date and return the commit SHA at the tip of `branch`."""
        if not (self.path / "HEAD").exists():
            self.path.mkdir(parents=True, exist_ok=True)
            await self._git("init", "--bare", "--quiet")
            await self._git("remote", "add", "origin", self.url)
        else:
            await self._git("remote", "set-url", "origin", self.url)

        ref = f"refs/remotes/origin/{branch}"
        async with self._auth_env(credentials) as env:
            try:
                await self._git(
                    "fetch",
                    "--quiet",
                    "--prune",
                    "--no-tags",
                    "origin",
                    f"+refs/heads/{branch}:{ref}",
                    env=env,
                )
            except GitError as exc:
                if (
                    "couldn't find remote ref" in str(exc)
                    and not (await self._git("ls-remote", "--heads", "origin", env=env)).strip()
                ):
                    raise EmptyRepository(str(exc)) from exc
                raise
        return (await self._git("rev-parse", f"{ref}^{{commit}}")).strip()

    async def list_dirs(self, commit: str, root: str) -> list[str]:
        """Folders directly under `root` at `commit`, as repository paths."""
        root = root.strip("/")
        try:
            out = await self._git("ls-tree", "-z", f"{commit}:{root}" if root else commit)
        except GitError:
            return []
        names = []
        for entry in filter(None, out.split("\0")):
            meta, name = entry.split("\t", 1)
            if meta.split()[1] == "tree":
                names.append(f"{root}/{name}" if root else name)
        return sorted(names)

    async def has_commit(self, commit: str) -> bool:
        if not (self.path / "HEAD").exists():
            return False
        try:
            await self._git("cat-file", "-e", f"{commit}^{{commit}}")
        except GitError:
            return False
        return True

    async def list_files(self, commit: str, path: str) -> list[str]:
        """File names directly inside `path` at `commit`."""
        try:
            out = await self._git("ls-tree", "-z", "--name-only", f"{commit}:{path}")
        except GitError:
            return []
        return [name for name in out.split("\0") if name]

    async def tree_hash(self, commit: str, path: str) -> str:
        return (await self._git("rev-parse", f"{commit}:{path}")).strip()

    async def read_file(self, commit: str, path: str) -> str | None:
        try:
            return await self._git("show", f"{commit}:{path}")
        except GitError:
            return None

    async def last_commit(self, commit: str, path: str) -> CommitInfo:
        """The most recent commit at or before `commit` that touched `path`."""
        out = await self._git(
            "log", "-1", "--format=%H%x00%an%x00%ae%x00%cI%x00%s", commit, "--", path
        )
        sha, author, email, when, message = out.rstrip("\n").split("\0", 4)
        return CommitInfo(sha, author, datetime.fromisoformat(when), message, email)

    async def export(self, commit: str, path: str, dest: Path) -> Path:
        """Write the contents of `path` at `commit` under `dest` and return that folder."""
        try:
            data = await run_binary(
                ["git", "archive", "--format=tar", commit, "--", path],
                cwd=self.path,
                env=GIT_ENV,
                timeout=self.timeout,
            )
        except (CommandFailed, CommandTimeout) as exc:
            raise GitError(str(exc)) from exc
        with tarfile.open(fileobj=io.BytesIO(data)) as tar:
            tar.extractall(dest, filter="data")
        return dest / path

    async def tree_files(self, commit: str, path: str) -> list[FileEntry]:
        """All files under `path` at `commit`, recursively."""
        try:
            out = await self._git("ls-tree", "-r", "-z", "--long", f"{commit}:{path}")
        except GitError:
            return []
        entries = []
        for line in filter(None, out.split("\0")):
            meta, name = line.split("\t", 1)
            mode, kind, blob, size = meta.split()
            if kind == "blob":
                entries.append(FileEntry(name, mode, blob, int(size) if size != "-" else 0))
        return entries

    async def blob(self, blob: str) -> bytes:
        try:
            return await run_binary(
                ["git", "cat-file", "blob", blob], cwd=self.path, env=GIT_ENV, timeout=self.timeout
            )
        except (CommandFailed, CommandTimeout) as exc:
            raise GitError(str(exc)) from exc

    async def commit(
        self,
        parent: str | None,
        changes: dict[str, bytes | None],
        message: str,
        author: Identity,
        committer: Identity,
    ) -> str:
        """Create a commit on top of `parent` (a root commit if None) with `changes`
        (repository path to content, or None to delete) without a working tree. Returns the new
        commit SHA."""
        with tempfile.TemporaryDirectory(prefix="swarmer-index-") as tmp:
            env = {"GIT_INDEX_FILE": str(Path(tmp) / "index")}
            if parent:
                await self._git("read-tree", parent, env=env)
            lines = []
            for path, content in sorted(changes.items()):
                if content is None:
                    # Mode 0 removes the path from the index.
                    lines.append(f"0 {'0' * 40}\t{path}")
                else:
                    blob = (await self._git("hash-object", "-w", "--stdin", stdin=content)).strip()
                    lines.append(f"100644 {blob}\t{path}")
            await self._git(
                "update-index", "--index-info", env=env, stdin=("\n".join(lines) + "\n").encode()
            )
            tree = (await self._git("write-tree", env=env)).strip()
        identity = {
            "GIT_AUTHOR_NAME": author.name,
            "GIT_AUTHOR_EMAIL": author.email,
            "GIT_COMMITTER_NAME": committer.name,
            "GIT_COMMITTER_EMAIL": committer.email,
        }
        parents = ["-p", parent] if parent else []
        out = await self._git("commit-tree", tree, *parents, env=identity, stdin=message.encode())
        return out.strip()

    async def push(self, commit: str, branch: str, credentials: Credentials) -> None:
        async with self._auth_env(credentials) as env:
            try:
                await self._git(
                    "push", "--quiet", "origin", f"{commit}:refs/heads/{branch}", env=env
                )
            except GitError as exc:
                text = str(exc)
                if any(s in text for s in ("non-fast-forward", "fetch first", "[rejected]")):
                    raise PushRejected(text) from exc
                raise
        # Keep the local tracking ref in step so the next read sees our commit.
        await self._git("update-ref", f"refs/remotes/origin/{branch}", commit)

    async def changed_between(self, old: str, new: str, path: str) -> bool:
        try:
            return await self.tree_hash(old, path) != await self.tree_hash(new, path)
        except GitError:
            return True
