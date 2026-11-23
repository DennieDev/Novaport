# SPDX-License-Identifier: AGPL-3.0-or-later
import json
from collections.abc import AsyncIterator, Mapping
from typing import Any, Protocol

import aiodocker
import aiohttp

Obj = Mapping[str, Any]


class DockerUnavailable(Exception):
    """The Docker daemon could not be reached."""


class NotFound(Exception):
    pass


class NotSwarmManager(Exception):
    """The connected daemon is not a Swarm manager, so Swarm objects can't be read."""


class ClusterReader(Protocol):
    async def info(self) -> Obj: ...
    async def nodes(self) -> list[Obj]: ...
    async def services(self, label: str | None = None) -> list[Obj]: ...
    async def containers(self, label: str) -> list[Obj]: ...
    async def service(self, service_id: str) -> Obj: ...
    async def tasks(self, service_id: str | None = None) -> list[Obj]: ...
    async def task(self, task_id: str) -> Obj: ...
    async def networks(self) -> list[Obj]: ...
    async def secrets(self) -> list[Obj]: ...
    async def configs(self) -> list[Obj]: ...
    async def service_logs(
        self, service_id: str, *, tail: int, timestamps: bool, tty: bool
    ) -> list[str]: ...
    def follow_service_logs(
        self, service_id: str, *, tail: int, timestamps: bool, tty: bool
    ) -> AsyncIterator[str]: ...


def _translate(exc: Exception) -> Exception:
    if isinstance(exc, aiodocker.DockerError):
        if exc.status == 404:
            return NotFound(exc.message)
        if exc.status == 503 and "swarm" in exc.message.lower():
            return NotSwarmManager(exc.message)
        # aiodocker's status for "couldn't connect at all".
        if exc.status == 900:
            return DockerUnavailable(exc.message)
        return exc
    if isinstance(exc, aiohttp.ClientError | OSError):
        return DockerUnavailable(str(exc))
    return exc


class AiodockerReader:
    def __init__(self, docker: aiodocker.Docker) -> None:
        self._docker = docker

    async def _call[T](self, coro: Any) -> T:
        try:
            result: T = await coro
        except Exception as exc:
            translated = _translate(exc)
            if translated is exc:
                raise
            raise translated from exc
        return result

    async def info(self) -> Obj:
        return await self._call(self._docker.system.info())

    async def nodes(self) -> list[Obj]:
        return await self._call(self._docker.nodes.list())

    async def services(self, label: str | None = None) -> list[Obj]:
        # aiodocker's services.list() has no `status` flag; it gives running and desired counts.
        params = {"status": "true"}
        if label:
            params["filters"] = json.dumps({"label": [label]})
        return await self._call(self._docker._query_json("services", method="GET", params=params))

    async def containers(self, label: str) -> list[Obj]:
        params = {"all": "true", "filters": json.dumps({"label": [label]})}
        return await self._call(
            self._docker._query_json("containers/json", method="GET", params=params)
        )

    async def service(self, service_id: str) -> Obj:
        return await self._call(
            self._docker._query_json(
                f"services/{service_id}", method="GET", params={"insertDefaults": "true"}
            )
        )

    async def tasks(self, service_id: str | None = None) -> list[Obj]:
        filters = {"service": service_id} if service_id else None
        return await self._call(self._docker.tasks.list(filters=filters))

    async def task(self, task_id: str) -> Obj:
        return await self._call(self._docker.tasks.inspect(task_id))

    async def networks(self) -> list[Obj]:
        return await self._call(self._docker.networks.list())

    async def secrets(self) -> list[Obj]:
        return await self._call(self._docker.secrets.list())

    async def configs(self) -> list[Obj]:
        return await self._call(self._docker.configs.list())

    async def service_logs(
        self, service_id: str, *, tail: int, timestamps: bool, tty: bool
    ) -> list[str]:
        lines: list[str] = await self._call(
            self._docker.services.logs(
                service_id,
                stdout=True,
                stderr=True,
                tail=str(tail),
                timestamps=timestamps,
                is_tty=tty,
            )
        )
        return lines

    async def follow_service_logs(
        self, service_id: str, *, tail: int, timestamps: bool, tty: bool
    ) -> AsyncIterator[str]:
        stream: AsyncIterator[str] = self._docker.services.logs(  # type: ignore[assignment]
            service_id,
            stdout=True,
            stderr=True,
            follow=True,
            tail=str(tail),
            timestamps=timestamps,
            is_tty=tty,
        )
        try:
            async for chunk in stream:
                yield chunk
        except Exception as exc:
            translated = _translate(exc)
            if translated is exc:
                raise
            raise translated from exc
