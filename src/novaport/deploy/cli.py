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
        self,
        stack_type: StackType,
        name: str,
        folder: Path,
        compose_file: str,
        wait_seconds: int,
        env: Mapping[str, str],
        *,
        prune: bool = True,
        pull_images: bool = True,
    ) -> Result: ...

    async def remove_secrets(self, names: list[str]) -> Result: ...

    async def restart_service(self, name: str) -> Result:
        """Replace a Swarm service's tasks without changing its spec."""
        ...

    async def restart_containers(self, ids: list[str]) -> Result: ...


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
        self,
        stack_type: StackType,
        name: str,
        folder: Path,
        compose_file: str,
        wait_seconds: int,
        env: Mapping[str, str],
        *,
        prune: bool = True,
        pull_images: bool = True,
    ) -> Result:
        if stack_type == StackType.SWARM:
            args = [
                "docker",
                "stack",
                "deploy",
                "--compose-file",
                compose_file,
                "--with-registry-auth",
                # `never` leaves the tag unpinned, so each node uses the image it has.
                f"--resolve-image={'always' if pull_images else 'never'}",
                *(["--prune"] if prune else []),
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
                f"--pull={'always' if pull_images else 'missing'}",
                *(["--remove-orphans"] if prune else []),
                "--wait",
                "--wait-timeout",
                str(wait_seconds),
            ]
            timeout = self.timeout + wait_seconds
        return await run(args, cwd=folder, env={**env, **self.env}, timeout=timeout, check=False)

    async def remove_secrets(self, names: list[str]) -> Result:
        return await run(
            ["docker", "secret", "rm", *names], env=self.env, timeout=self.timeout, check=False
        )

    async def restart_service(self, name: str) -> Result:
        return await run(
            ["docker", "service", "update", "--force", "--detach", name],
            env=self.env,
            timeout=self.timeout,
            check=False,
        )

    async def restart_containers(self, ids: list[str]) -> Result:
        return await run(
            ["docker", "restart", *ids], env=self.env, timeout=self.timeout, check=False
        )
