# SPDX-License-Identifier: AGPL-3.0-or-later
import asyncio
import contextlib
import logging
from collections.abc import Mapping
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from swarmer.auth.access import Forbidden
from swarmer.docker import views
from swarmer.docker.client import ClusterReader, DockerUnavailable, NotFound
from swarmer.models import Role
from swarmer.server.deps import (
    DB,
    CurrentAccess,
    Reader,
    ViewerAccess,
    current_user,
    get_reader,
    require_service_role,
    service_stack,
    visible_stack_names,
    websocket_access,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["cluster"], dependencies=[Depends(current_user)])
ws_router = APIRouter(prefix="/api", tags=["cluster"])

Tail = Annotated[int, Query(ge=1, le=10_000)]


class LogLines(BaseModel):
    lines: list[str]


@router.get("/system")
async def system(reader: Reader, _: ViewerAccess) -> views.SystemInfo:
    return views.system_info(await reader.info())


@router.get("/nodes")
async def nodes(reader: Reader, _: ViewerAccess) -> list[views.Node]:
    result = [views.node(n) for n in await reader.nodes()]
    result.sort(key=lambda n: (n.role != "manager", n.hostname))
    return result


@router.get("/services")
async def services(reader: Reader, access: CurrentAccess, db: DB) -> list[views.ServiceSummary]:
    raw = await reader.services()
    allowed = await visible_stack_names(db, access, Role.VIEWER)
    if allowed is not None:
        raw = [s for s in raw if (service_stack(s) or "") in allowed]
    result = [views.service_summary(s) for s in raw]
    result.sort(key=lambda s: (s.stack or "", s.name))
    return result


@router.get("/services/{service_id}")
async def service(service_id: str, reader: Reader, access: CurrentAccess, db: DB) -> views.Service:
    raw, networks = await asyncio.gather(reader.service(service_id), reader.networks())
    await require_service_role(db, access, Role.VIEWER, raw)
    names = {n["Id"]: n.get("Name", "") for n in networks}
    return views.service(raw, names)


async def _node_names(reader: ClusterReader) -> dict[str, str]:
    return {n["ID"]: n.get("Description", {}).get("Hostname", "") for n in await reader.nodes()}


@router.get("/services/{service_id}/tasks")
async def service_tasks(
    service_id: str, reader: Reader, access: CurrentAccess, db: DB
) -> list[views.Task]:
    raw_service, raw_tasks, names = await asyncio.gather(
        reader.service(service_id), reader.tasks(service_id), _node_names(reader)
    )
    await require_service_role(db, access, Role.VIEWER, raw_service)
    # The `service` filter also matches by name prefix, so filter on the exact ID.
    own = [t for t in raw_tasks if t.get("ServiceID") == raw_service["ID"]]
    return views.tasks(own, names)


@router.get("/tasks")
async def all_tasks(reader: Reader, _: ViewerAccess) -> list[views.Task]:
    raw_tasks, names = await asyncio.gather(reader.tasks(), _node_names(reader))
    return views.tasks(raw_tasks, names)


@router.get("/networks")
async def networks(reader: Reader, _: ViewerAccess) -> list[views.Network]:
    result = [views.network(n) for n in await reader.networks()]
    result.sort(key=lambda n: n.name)
    return result


@router.get("/secrets")
async def secrets(reader: Reader, _: ViewerAccess) -> list[views.SwarmObject]:
    result = [views.swarm_object(s) for s in await reader.secrets()]
    result.sort(key=lambda s: s.name)
    return result


@router.get("/configs")
async def configs(reader: Reader, _: ViewerAccess) -> list[views.SwarmObject]:
    result = [views.swarm_object(c) for c in await reader.configs()]
    result.sort(key=lambda c: c.name)
    return result


def _is_tty(raw_service: Mapping[str, Any]) -> bool:
    spec = raw_service.get("Spec", {}).get("TaskTemplate", {}).get("ContainerSpec", {})
    return bool(spec.get("TTY"))


@router.get("/services/{service_id}/logs")
async def service_logs(
    service_id: str,
    reader: Reader,
    access: CurrentAccess,
    db: DB,
    tail: Tail = 200,
    timestamps: bool = True,
) -> LogLines:
    raw = await reader.service(service_id)
    await require_service_role(db, access, Role.VIEWER, raw)
    tty = _is_tty(raw)
    chunks = await reader.service_logs(service_id, tail=tail, timestamps=timestamps, tty=tty)
    return LogLines(lines="".join(chunks).splitlines())


@ws_router.websocket("/services/{service_id}/logs/follow")
async def follow_service_logs(
    websocket: WebSocket, service_id: str, tail: Tail = 100, timestamps: bool = True
) -> None:
    access = await websocket_access(websocket)
    if access is None:
        await websocket.close(code=1008, reason="Not logged in.")
        return
    reader = get_reader(websocket)
    try:
        raw = await reader.service(service_id)
        async with websocket.app.state.sessionmaker() as db:
            await require_service_role(db, access, Role.VIEWER, raw)
    except Forbidden:
        await websocket.close(code=1008, reason="Not allowed.")
        return
    except NotFound:
        await websocket.accept()
        await websocket.close(code=4404, reason="Service not found.")
        return
    except DockerUnavailable:
        await websocket.accept()
        await websocket.close(code=1011, reason="Docker is unavailable.")
        return

    await websocket.accept()
    try:
        tty = _is_tty(raw)
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
