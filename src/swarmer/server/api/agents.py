# SPDX-License-Identifier: AGPL-3.0-or-later
import asyncio
import contextlib
import logging
from collections.abc import Mapping
from typing import Annotated, Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request, WebSocket, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.requests import HTTPConnection
from websockets.asyncio.client import ClientConnection
from websockets.exceptions import ConnectionClosed, InvalidHandshake, InvalidStatus

from swarmer.agents.pool import AgentError, Agents, AgentUnavailable
from swarmer.auth.access import Access, Forbidden
from swarmer.docker import views
from swarmer.docker.client import ClusterReader, DockerUnavailable, NotFound
from swarmer.models import Outcome, Role
from swarmer.server.deps import (
    DB,
    TERMINAL_ACTION,
    CurrentAccess,
    OperatorAccess,
    Reader,
    ViewerAccess,
    current_user,
    get_reader,
    refuse_token_terminal,
    require_service_role,
    websocket_access,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["agents"], dependencies=[Depends(current_user)])
ws_router = APIRouter(prefix="/api", tags=["agents"])

Tail = Annotated[int, Query(ge=1, le=10_000)]


class AgentOut(BaseModel):
    url: str
    node_id: str | None
    hostname: str | None
    version: str | None
    error: str | None


class AgentsOut(BaseModel):
    enabled: bool
    agents: list[AgentOut]


def get_agents(conn: HTTPConnection) -> Agents:
    agents: Agents = conn.app.state.agents
    return agents


AgentsDep = Annotated[Agents, Depends(get_agents)]


def _require_enabled(agents: Agents) -> None:
    if not agents.enabled:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "No agents configured. Set SWARMER_AGENT_TOKEN and SWARMER_AGENT_DNS.",
        )


@router.get("/agents")
async def list_agents(agents: AgentsDep, _: ViewerAccess, refresh: bool = False) -> AgentsOut:
    found = await agents.list(refresh=refresh)
    return AgentsOut(
        enabled=agents.enabled,
        agents=[AgentOut(**a.__dict__) for a in found],
    )


@router.get("/nodes/{node_id}/disk")
async def node_disk(node_id: str, agents: AgentsDep, _: ViewerAccess) -> Any:
    _require_enabled(agents)
    return await agents.get(node_id, "/disk")


@router.get("/nodes/{node_id}/images")
async def node_images(node_id: str, agents: AgentsDep, _: ViewerAccess) -> Any:
    _require_enabled(agents)
    return await agents.get(node_id, "/images")


@router.get("/nodes/{node_id}/volumes")
async def node_volumes(node_id: str, agents: AgentsDep, _: ViewerAccess) -> Any:
    _require_enabled(agents)
    return await agents.get(node_id, "/volumes")


@router.get("/nodes/{node_id}/volumes/{name}/files")
async def node_volume_files(
    node_id: str, name: str, agents: AgentsDep, _: OperatorAccess, path: str = "/"
) -> Any:
    _require_enabled(agents)
    return await agents.get(node_id, f"/volumes/{name}/files", {"path": path})


async def _require_task_role(
    db: Any, reader: ClusterReader, access: Access, role: Role, task: Mapping[str, Any]
) -> None:
    if access.can(role):
        return
    service = await reader.service(task.get("ServiceID", ""))
    await require_service_role(db, access, role, service)


def _container_of(task: Mapping[str, Any]) -> tuple[str, str]:
    node_id = task.get("NodeID")
    container_id = (task.get("Status", {}).get("ContainerStatus") or {}).get("ContainerID")
    if not node_id or not container_id:
        raise NotFound("This task has no container (yet).")
    return node_id, container_id


@router.get("/tasks/{task_id}")
async def get_task(task_id: str, reader: Reader, access: CurrentAccess, db: DB) -> views.Task:
    task = await reader.task(task_id)
    await _require_task_role(db, reader, access, Role.VIEWER, task)
    names = {n["ID"]: n.get("Description", {}).get("Hostname", "") for n in await reader.nodes()}
    return views.task(task, names)


@router.get("/tasks/{task_id}/logs")
async def task_logs(
    task_id: str,
    reader: Reader,
    agents: AgentsDep,
    access: CurrentAccess,
    db: DB,
    tail: Tail = 200,
    timestamps: bool = True,
) -> Any:
    _require_enabled(agents)
    task = await reader.task(task_id)
    await _require_task_role(db, reader, access, Role.VIEWER, task)
    node_id, container_id = _container_of(task)
    return await agents.get(
        node_id, f"/containers/{container_id}/logs", {"tail": tail, "timestamps": timestamps}
    )


async def bridge(browser: WebSocket, agent: ClientConnection) -> None:
    """Relays messages between a browser socket and an agent socket until either closes."""

    async def downstream() -> None:
        async for message in agent:
            if isinstance(message, bytes):
                await browser.send_bytes(message)
            else:
                await browser.send_text(message)

    async def upstream() -> None:
        while True:
            message = await browser.receive()
            if message["type"] == "websocket.disconnect":
                return
            if message.get("text") is not None:
                await agent.send(message["text"])
            elif message.get("bytes") is not None:
                await agent.send(message["bytes"])

    tasks = [asyncio.create_task(downstream()), asyncio.create_task(upstream())]
    _, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    for task in tasks:
        with contextlib.suppress(asyncio.CancelledError, ConnectionClosed, RuntimeError):
            await task

    code = agent.close_code or 1000
    reason = agent.close_reason or ""
    with contextlib.suppress(RuntimeError):
        await browser.close(
            code=code if code in (1000, 1011) or code >= 4000 else 1011, reason=reason
        )


async def _proxy_task_socket(
    websocket: WebSocket,
    task_id: str,
    agent_path: str,
    params: dict[str, Any],
    role: Role,
    audit_action: str | None = None,
) -> None:
    access = await websocket_access(websocket)
    if access is None:
        await websocket.close(code=1008, reason="Not logged in.")
        return
    agents = get_agents(websocket)
    if not agents.enabled:
        await websocket.close(code=1011, reason="No agents configured.")
        return

    reader = get_reader(websocket)
    maker = websocket.app.state.sessionmaker
    try:
        refuse_token_terminal(access, audit_action)
        task = await reader.task(task_id)
        async with maker() as db:
            await _require_task_role(db, reader, access, role, task)
        node_id, container_id = _container_of(task)
    except Forbidden:
        if audit_action:
            async with maker() as db:
                access.audit(
                    db, audit_action, outcome=Outcome.DENIED, target=task_id, details=params
                )
                await db.commit()
        await websocket.close(code=1008, reason="Not allowed.")
        return
    except NotFound as exc:
        await websocket.accept()
        await websocket.close(code=4404, reason=str(exc)[:120])
        return
    except DockerUnavailable:
        await websocket.accept()
        await websocket.close(code=1011, reason="Docker is unavailable.")
        return

    async def audit(outcome: Outcome, **extra: Any) -> None:
        if audit_action:
            async with maker() as db:
                access.audit(
                    db,
                    audit_action,
                    outcome=outcome,
                    target=task_id,
                    details={**params, "node": node_id, **extra},
                )
                await db.commit()

    await websocket.accept()
    try:
        path = agent_path.format(container=container_id) + "?" + urlencode(params)
        async with agents.websocket(node_id, path) as agent:
            # Recorded on connect: the handler can be cancelled when the browser goes away.
            await audit(Outcome.SUCCESS)
            await bridge(websocket, agent)
    except (AgentUnavailable, OSError, InvalidHandshake, InvalidStatus) as exc:
        log.warning("Agent connection for task %s failed: %s", task_id, exc)
        await audit(Outcome.FAILED, error=str(exc)[:500])
        with contextlib.suppress(RuntimeError):
            await websocket.close(code=1011, reason=str(exc)[:120])


@ws_router.websocket("/tasks/{task_id}/logs/follow")
async def follow_task_logs(
    websocket: WebSocket, task_id: str, tail: Tail = 100, timestamps: bool = True
) -> None:
    await _proxy_task_socket(
        websocket,
        task_id,
        "/containers/{container}/logs/follow",
        {"tail": tail, "timestamps": str(timestamps).lower()},
        Role.VIEWER,
    )


@ws_router.websocket("/tasks/{task_id}/exec")
async def exec_task(websocket: WebSocket, task_id: str, cmd: str = "/bin/sh") -> None:
    await _proxy_task_socket(
        websocket,
        task_id,
        "/containers/{container}/exec",
        {"cmd": cmd},
        Role.OPERATOR,
        audit_action=TERMINAL_ACTION,
    )


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AgentUnavailable)
    async def unavailable(_: Request, exc: AgentUnavailable) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=status.HTTP_503_SERVICE_UNAVAILABLE)

    @app.exception_handler(AgentError)
    async def agent_error(_: Request, exc: AgentError) -> JSONResponse:
        code = exc.status if 400 <= exc.status < 500 else status.HTTP_502_BAD_GATEWAY
        return JSONResponse({"detail": exc.detail}, status_code=code)
