# SPDX-License-Identifier: AGPL-3.0-or-later
"""Builds small git repositories on disk for sync and deploy tests."""

import subprocess
from pathlib import Path

ENV = {
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.com",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_NOSYSTEM": "1",
    "PATH": "/usr/local/bin:/usr/bin:/bin",
}

COMPOSE = "services:\n  web:\n    image: nginx:alpine\n"


class Repo:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.mkdir(parents=True, exist_ok=True)
        self.git("init", "--quiet", "--initial-branch=main")

    @property
    def url(self) -> str:
        return f"file://{self.path}"

    def git(self, *args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=self.path, env=ENV, check=True, capture_output=True, text=True
        ).stdout

    def write(self, relative: str, content: str) -> None:
        target = self.path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)

    def remove(self, relative: str) -> None:
        self.git("rm", "-r", "--quiet", relative)

    def commit(self, message: str) -> str:
        self.git("add", "--all")
        self.git("commit", "--quiet", "--allow-empty", "-m", message)
        return self.git("rev-parse", "HEAD").strip()
