# SPDX-License-Identifier: AGPL-3.0-or-later
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol

from novaport.models import StackType
from novaport.process import Result, run


class DockerCli(Protocol):
    async def config(
        self,
        stack_type: StackType,
        name: str,
        folder: Path,
        compose_file: str,
        env: Mapping[str, str],
    ) -> Result:
        """Validate and normalise the compose file. Output is the merged YAML."""
        ...

    async def apply(
        self, stack_type: StackType, name: str, folder: Path, compose_file: str, wait_seconds: int
    ) -> Result: ...


class SubprocessDockerCli:
    def __init__(self, docker_host: str | None, timeout: float) -> None:
        self.env = {"DOCKER_HOST": docker_host} if docker_host else {}
        self.timeout = timeout

    async def config(
        self,
        stack_type: StackType,
        name: str,
        folder: Path,
        compose_file: str,
        env: Mapping[str, str],
    ) -> Result:
        if stack_type == StackType.SWARM:
            args = ["docker", "stack", "config", "--compose-file", compose_file]
        else:
            args = ["docker", "compose", "--project-name", name, "--file", compose_file, "config"]
        return await run(
            args, cwd=folder, env={**env, **self.env}, timeout=self.timeout, check=False
        )

    async def apply(
        self, stack_type: StackType, name: str, folder: Path, compose_file: str, wait_seconds: int
    ) -> Result:
        if stack_type == StackType.SWARM:
            args = [
                "docker",
                "stack",
                "deploy",
                "--compose-file",
                compose_file,
                "--with-registry-auth",
                "--prune",
                "--detach=true",
                name,
            ]
            timeout = self.timeout
        else:
            args = [
                "docker",
                "compose",
                "--project-name",
                name,
                "--file",
                compose_file,
                "up",
                "--detach",
                "--remove-orphans",
                "--wait",
                "--wait-timeout",
                str(wait_seconds),
            ]
            timeout = self.timeout + wait_seconds
        return await run(args, cwd=folder, env=self.env, timeout=timeout, check=False)
