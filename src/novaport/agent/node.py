# SPDX-License-Identifier: AGPL-3.0-or-later
"""The node-local Docker operations the agent exposes."""

from collections.abc import AsyncIterator, Mapping
from typing import Any, Protocol

import aiodocker
from aiodocker.execs import Exec
from aiodocker.stream import Stream

Obj = Mapping[str, Any]


class NodeDocker(Protocol):
    async def info(self) -> Obj: ...
    async def disk_usage(self) -> Obj: ...
    async def volume(self, name: str) -> Obj: ...
    async def container(self, container_id: str) -> Obj: ...
    async def logs(self, container_id: str, *, tail: int, timestamps: bool) -> list[str]: ...
    def follow_logs(
        self, container_id: str, *, tail: int, timestamps: bool
    ) -> AsyncIterator[str]: ...
    async def exec(self, container_id: str, cmd: list[str]) -> tuple[Exec, Stream]: ...


class AiodockerNode:
    def __init__(self, docker: aiodocker.Docker) -> None:
        self.docker = docker

    async def info(self) -> Obj:
        result: Obj = await self.docker.system.info()
        return result

    async def disk_usage(self) -> Obj:
        result: Obj = await self.docker._query_json("system/df", method="GET")
        return result

    async def volume(self, name: str) -> Obj:
        result: Obj = await self.docker._query_json(f"volumes/{name}", method="GET")
        return result

    async def container(self, container_id: str) -> Obj:
        result: Obj = await self.docker.containers.container(container_id).show()
        return result

    async def logs(self, container_id: str, *, tail: int, timestamps: bool) -> list[str]:
        lines: list[str] = await self.docker.containers.container(container_id).log(
            stdout=True, stderr=True, tail=str(tail), timestamps=timestamps
        )
        return lines

    async def follow_logs(
        self, container_id: str, *, tail: int, timestamps: bool
    ) -> AsyncIterator[str]:
        stream = self.docker.containers.container(container_id).log(
            stdout=True, stderr=True, follow=True, tail=str(tail), timestamps=timestamps
        )
        async for chunk in stream:
            yield chunk

    async def exec(self, container_id: str, cmd: list[str]) -> tuple[Exec, Stream]:
        container = self.docker.containers.container(container_id)
        execution = await container.exec(
            cmd,
            stdin=True,
            stdout=True,
            stderr=True,
            tty=True,
            environment={"TERM": "xterm-256color"},
        )
        return execution, execution.start(detach=False)
