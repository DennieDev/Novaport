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

from novaport.process import CommandFailed, CommandTimeout, run, run_binary

GIT_ENV = {"GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null"}


@dataclass(frozen=True)
class Credentials:
    ssh_private_key: str | None = None
    token: str | None = None


@dataclass(frozen=True)
class CommitInfo:
    sha: str
    author: str
    committed_at: datetime
    message: str


class GitError(Exception):
    pass


class GitMirror:
    """A bare clone used as a local cache; files are read from it by commit, never checked out."""

    def __init__(self, path: Path, url: str, known_hosts: Path, timeout: float = 120) -> None:
        self.path = path
        self.url = url
        self.known_hosts = known_hosts
        self.timeout = timeout

    async def _git(self, *args: str, env: dict[str, str] | None = None) -> str:
        try:
            result = await run(
                ["git", *args], cwd=self.path, env=GIT_ENV | (env or {}), timeout=self.timeout
            )
        except (CommandFailed, CommandTimeout) as exc:
            raise GitError(str(exc)) from exc
        return result.stdout

    @asynccontextmanager
    async def _auth_env(self, credentials: Credentials) -> AsyncIterator[dict[str, str]]:
        env: dict[str, str] = {}
        with tempfile.TemporaryDirectory(prefix="novaport-git-") as tmp:
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
            await self._git(
                "fetch",
                "--quiet",
                "--prune",
                "--no-tags",
                "origin",
                f"+refs/heads/{branch}:{ref}",
                env=env,
            )
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
        out = await self._git("log", "-1", "--format=%H%x00%an%x00%cI%x00%s", commit, "--", path)
        sha, author, when, message = out.rstrip("\n").split("\0", 3)
        return CommitInfo(sha, author, datetime.fromisoformat(when), message)

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
