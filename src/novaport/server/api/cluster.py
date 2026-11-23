# SPDX-License-Identifier: AGPL-3.0-or-later
import asyncio
import contextlib
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from novaport.docker import views
from novaport.docker.client import ClusterReader, DockerUnavailable, NotFound
from novaport.server.deps import Reader, authenticate, current_user, get_reader

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["cluster"], dependencies=[Depends(current_user)])
ws_router = APIRouter(prefix="/api", tags=["cluster"])

Tail = Annotated[int, Query(ge=1, le=10_000)]


class LogLines(BaseModel):
    lines: list[str]


@router.get("/system")
async def system(reader: Reader) -> views.SystemInfo:
    return views.system_info(await reader.info())


@router.get("/nodes")
async def nodes(reader: Reader) -> list[views.Node]:
    result = [views.node(n) for n in await reader.nodes()]
    result.sort(key=lambda n: (n.role != "manager", n.hostname))
    return result


@router.get("/services")
async def services(reader: Reader) -> list[views.ServiceSummary]:
    result = [views.service_summary(s) for s in await reader.services()]
    result.sort(key=lambda s: (s.stack or "", s.name))
    return result


@router.get("/services/{service_id}")
async def service(service_id: str, reader: Reader) -> views.Service:
    raw, networks = await asyncio.gather(reader.service(service_id), reader.networks())
    names = {n["Id"]: n.get("Name", "") for n in networks}
    return views.service(raw, names)


async def _node_names(reader: ClusterReader) -> dict[str, str]:
    return {n["ID"]: n.get("Description", {}).get("Hostname", "") for n in await reader.nodes()}


@router.get("/services/{service_id}/tasks")
async def service_tasks(service_id: str, reader: Reader) -> list[views.Task]:
    raw_service, raw_tasks, names = await asyncio.gather(
        reader.service(service_id), reader.tasks(service_id), _node_names(reader)
    )
    # The `service` filter also matches by name prefix, so filter on the exact ID.
    own = [t for t in raw_tasks if t.get("ServiceID") == raw_service["ID"]]
    return views.tasks(own, names)


@router.get("/tasks")
async def all_tasks(reader: Reader) -> list[views.Task]:
    raw_tasks, names = await asyncio.gather(reader.tasks(), _node_names(reader))
    return views.tasks(raw_tasks, names)


@router.get("/networks")
async def networks(reader: Reader) -> list[views.Network]:
    result = [views.network(n) for n in await reader.networks()]
    result.sort(key=lambda n: n.name)
    return result


@router.get("/secrets")
async def secrets(reader: Reader) -> list[views.SwarmObject]:
    result = [views.swarm_object(s) for s in await reader.secrets()]
    result.sort(key=lambda s: s.name)
    return result


@router.get("/configs")
async def configs(reader: Reader) -> list[views.SwarmObject]:
    result = [views.swarm_object(c) for c in await reader.configs()]
    result.sort(key=lambda c: c.name)
    return result


async def _is_tty(reader: ClusterReader, service_id: str) -> bool:
    raw = await reader.service(service_id)
    return bool(raw.get("Spec", {}).get("TaskTemplate", {}).get("ContainerSpec", {}).get("TTY"))


@router.get("/services/{service_id}/logs")
async def service_logs(
    service_id: str, reader: Reader, tail: Tail = 200, timestamps: bool = True
) -> LogLines:
    tty = await _is_tty(reader, service_id)
    chunks = await reader.service_logs(service_id, tail=tail, timestamps=timestamps, tty=tty)
    return LogLines(lines="".join(chunks).splitlines())


@ws_router.websocket("/services/{service_id}/logs/follow")
async def follow_service_logs(
    websocket: WebSocket, service_id: str, tail: Tail = 100, timestamps: bool = True
) -> None:
    async with websocket.app.state.sessionmaker() as db:
        session = await authenticate(websocket, db)
    if session is None:
        await websocket.close(code=1008, reason="Not logged in.")
        return
    reader = get_reader(websocket)

    await websocket.accept()
    try:
        tty = await _is_tty(reader, service_id)
        stream = reader.follow_service_logs(service_id, tail=tail, timestamps=timestamps, tty=tty)
        async for chunk in stream:
            await websocket.send_text(chunk)
    except WebSocketDisconnect:
        return
    except NotFound:
        await websocket.close(code=4404, reason="Service not found.")
        return
    except DockerUnavailable:
        await websocket.close(code=1011, reason="Docker is unavailable.")
        return
    with contextlib.suppress(RuntimeError):
        await websocket.close()
