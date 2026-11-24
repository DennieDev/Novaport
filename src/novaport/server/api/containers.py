# SPDX-License-Identifier: AGPL-3.0-or-later
"""Services and containers of one stack, wherever the stack runs, with logs and terminals."""

import contextlib
import logging
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request, WebSocket, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from websockets.exceptions import InvalidHandshake, InvalidStatus

from novaport.agents.pool import Agents, AgentUnavailable
from novaport.auth.access import Access, Forbidden
from novaport.docker import views
from novaport.docker.client import ClusterReader, DockerUnavailable, NotFound, NotSwarmManager
from novaport.docker.views import STACK_LABEL, Obj
from novaport.edge.hub import EndpointOffline, Target
from novaport.locks import try_lock
from novaport.models import Outcome, Role, Stack, StackType
from novaport.process import CommandTimeout
from novaport.server.api.agents import bridge
from novaport.server.deps import DB, CurrentAccess, current_user, websocket_access
from novaport.stacks.adopt import COMPOSE_PROJECT, COMPOSE_SERVICE

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["containers"], dependencies=[Depends(current_user)])
ws_router = APIRouter(prefix="/api", tags=["containers"])

Tail = Annotated[int, Query(ge=1, le=10_000)]
# Swarm keeps old tasks around; this many per service is enough to see why something restarts.
HISTORY = 5
HEALTH = re.compile(r"\((healthy|unhealthy|health: starting)\)")


class ContainerRow(BaseModel):
    # A task ID for Swarm stacks, a container ID for Compose projects. Used in URLs.
    ref: str
    name: str
    container_id: str | None
    node: str | None
    node_id: str | None
    state: str
    desired_state: str | None
    status: str | None
    health: str | None
    error: str | None
    image: str
    updated_at: datetime | None
    current: bool


class ServiceRow(BaseModel):
    name: str
    full_name: str
    service_id: str | None
    image: str
    mode: str
    replicas_desired: int | None
    replicas_running: int | None
    ports: list[views.Port]
    update_status: views.UpdateStatus | None
    containers: list[ContainerRow]


class StackHealth(BaseModel):
    # healthy, degraded, down, or stopped (nothing of the stack runs)
    state: str
    problems: list[str]


class StackContainers(BaseModel):
    type: StackType
    endpoint: str | None
    agents: bool
    health: StackHealth
    services: list[ServiceRow]


# Failed tasks this recent mark a service as restarting.
RECENT_FAILURES = timedelta(minutes=10)


def stack_health(rows: list[ServiceRow], now: datetime | None = None) -> StackHealth:
    if not rows:
        return StackHealth(state="stopped", problems=[])
    since = (now or datetime.now(UTC)) - RECENT_FAILURES
    problems: list[str] = []
    running_any = False
    for row in rows:
        running = row.replicas_running or 0
        running_any = running_any or running > 0
        if row.replicas_desired is not None and running < row.replicas_desired:
            problems.append(f"{row.name}: {running}/{row.replicas_desired} running")
        restarts = sum(
            1
            for c in row.containers
            if not c.current
            and c.state in ("failed", "rejected")
            and c.updated_at is not None
            and c.updated_at > since
        )
        if restarts:
            problems.append(f"{row.name}: {restarts} failed in the last 10 minutes")
        exited = [c for c in row.containers if c.current and c.state in ("exited", "dead")]
        if exited and row.mode == "compose":
            problems.append(f"{row.name}: {len(exited)} exited")
    if not running_any:
        return StackHealth(state="down", problems=problems)
    return StackHealth(state="degraded" if problems else "healthy", problems=problems)


class NetworkAddress(BaseModel):
    name: str
    addresses: list[str]


class ContainerInfo(BaseModel):
    ref: str
    name: str
    service: str
    container_id: str | None
    node: str | None
    state: str
    status: str | None
    health: str | None
    health_log: str | None
    error: str | None
    exit_code: int | None
    image: str
    command: list[str]
    env: list[str]
    labels: dict[str, str]
    mounts: list[views.Mount]
    ports: list[views.Port]
    networks: list[NetworkAddress]
    restart_count: int | None
    created_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None


@dataclass
class Located:
    stack: Stack
    target: Target
    node_id: str
    container_id: str | None
    raw: Obj


def _target(app: FastAPI, stack: Stack) -> tuple[Target, ClusterReader]:
    try:
        target: Target = app.state.targets(stack.endpoint_id)
    except EndpointOffline as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    if target.reader is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "No Docker connection.")
    return target, target.reader


async def _stack(
    db: AsyncSession, access: Access, stack_id: uuid.UUID, role: Role, action: str
) -> Stack:
    access.require(role, stack_id, action=action)
    stack = await db.get(Stack, stack_id, options=[selectinload(Stack.endpoint)])
    if stack is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Stack not found.")
    return stack


def _time(value: str | int | None) -> datetime | None:
    if value is None or value == "" or str(value).startswith("0001-"):
        return None
    if isinstance(value, int):
        return datetime.fromtimestamp(value, UTC)
    return datetime.fromisoformat(value[:26].rstrip("Z") + "+00:00")


def _short(full_name: str, stack: str) -> str:
    return full_name.removeprefix(f"{stack}_")


async def _node_names(reader: ClusterReader) -> dict[str, str]:
    try:
        return {n["ID"]: n.get("Description", {}).get("Hostname", "") for n in await reader.nodes()}
    except NotSwarmManager:
        return {}


@dataclass
class Snapshot:
    """What one Docker host runs, fetched once and shared by every stack on it."""

    services: list[Obj]
    tasks: list[Obj]
    node_names: dict[str, str]
    containers: list[Obj]
    node_id: str | None
    host: str | None


async def _snapshot(reader: ClusterReader, swarm: bool, compose: bool) -> Snapshot:
    services: list[Obj] = []
    tasks: list[Obj] = []
    names: dict[str, str] = {}
    if swarm:
        try:
            services = await reader.services()
            tasks = await reader.tasks()
        except NotSwarmManager:
            pass
        names = await _node_names(reader)
    containers = await reader.containers(COMPOSE_PROJECT) if compose else []
    node_id, host = await _host(reader)
    return Snapshot(services, tasks, names, containers, node_id, host)


def _swarm_rows(stack: Stack, snap: Snapshot) -> list[ServiceRow]:
    services = [
        s
        for s in snap.services
        if (s.get("Spec", {}).get("Labels") or {}).get(STACK_LABEL) == stack.name
    ]
    names = snap.node_names
    by_service: dict[str, list[Obj]] = {s["ID"]: [] for s in services}
    for t in snap.tasks:
        if t.get("ServiceID") in by_service:
            by_service[t["ServiceID"]].append(t)
    rows = []
    for raw in services:
        summary = views.service_summary(raw)
        tasks = views.tasks(by_service[raw["ID"]], names)
        current = [t for t in tasks if t.desired_state in ("running", "ready", "accepted")]
        history = [t for t in tasks if t not in current][:HISTORY]
        rows.append(
            ServiceRow(
                name=_short(summary.name, stack.name),
                full_name=summary.name,
                service_id=summary.id,
                image=summary.image,
                mode=summary.mode,
                replicas_desired=summary.replicas_desired,
                replicas_running=summary.replicas_running,
                ports=summary.ports,
                update_status=summary.update_status,
                containers=[
                    ContainerRow(
                        ref=t.id,
                        name=f"{summary.name}.{t.slot or (t.node or t.id[:12])}",
                        container_id=t.container_id,
                        node=t.node,
                        node_id=t.node_id,
                        state=t.state,
                        desired_state=t.desired_state,
                        status=t.message,
                        health=None,
                        error=t.error,
                        image=t.image,
                        updated_at=t.updated_at,
                        current=t in current,
                    )
                    for t in [*sorted(current, key=lambda t: t.slot or 0), *history]
                ],
            )
        )
    return sorted(rows, key=lambda r: r.name)


def _health(status_text: str | None) -> str | None:
    match = HEALTH.search(status_text or "")
    return match.group(1).replace("health: ", "") if match else None


async def _host(reader: ClusterReader) -> tuple[str | None, str | None]:
    info = await reader.info()
    return (info.get("Swarm") or {}).get("NodeID") or None, info.get("Name")


def _compose_rows(stack: Stack, snap: Snapshot) -> list[ServiceRow]:
    containers = [
        c for c in snap.containers if (c.get("Labels") or {}).get(COMPOSE_PROJECT) == stack.name
    ]
    node_id, host = snap.node_id, snap.host
    grouped: dict[str, list[Obj]] = {}
    for c in containers:
        grouped.setdefault((c.get("Labels") or {}).get(COMPOSE_SERVICE, "?"), []).append(c)
    rows = []
    for name, group in sorted(grouped.items()):
        group.sort(key=lambda c: (c.get("Names") or [""])[0])
        ports = {
            (p.get("PublicPort"), p["PrivatePort"], p.get("Type", "tcp"))
            for c in group
            for p in c.get("Ports") or []
            if p.get("PrivatePort")
        }
        rows.append(
            ServiceRow(
                name=name,
                full_name=name,
                service_id=None,
                image=group[0].get("Image", ""),
                mode="compose",
                replicas_desired=len(group),
                replicas_running=sum(c.get("State") == "running" for c in group),
                ports=[
                    views.Port(published=pub, target=target, protocol=proto, mode="host")
                    for pub, target, proto in sorted(ports, key=lambda p: (p[1], p[0] or 0))
                ],
                update_status=None,
                containers=[
                    ContainerRow(
                        ref=c["Id"],
                        name=(c.get("Names") or [c["Id"][:12]])[0].lstrip("/"),
                        container_id=c["Id"],
                        node=host,
                        node_id=node_id,
                        state=c.get("State", ""),
                        desired_state=None,
                        status=c.get("Status"),
                        health=_health(c.get("Status")),
                        error=None,
                        image=c.get("Image", ""),
                        updated_at=_time(c.get("Created")),
                        current=True,
                    )
                    for c in group
                ],
            )
        )
    return rows


@router.get("/stacks/{stack_id}/containers")
async def stack_containers(
    stack_id: uuid.UUID, request: Request, db: DB, access: CurrentAccess
) -> StackContainers:
    stack = await _stack(db, access, stack_id, Role.VIEWER, "stack.view")
    target, reader = _target(request.app, stack)
    swarm = stack.type == StackType.SWARM
    snap = await _snapshot(reader, swarm=swarm, compose=not swarm)
    services = _swarm_rows(stack, snap) if swarm else _compose_rows(stack, snap)
    agents = target.agents
    return StackContainers(
        type=stack.type,
        endpoint=stack.endpoint.name if stack.endpoint else None,
        agents=bool(agents and agents.enabled),
        health=stack_health(services),
        services=services,
    )


@router.get("/stacks-health")
async def stacks_health(request: Request, db: DB, access: CurrentAccess) -> dict[str, StackHealth]:
    """Health of every stack the user can see, by stack ID. Unreachable hosts are left out."""
    stacks = [
        s
        for s in await db.scalars(select(Stack).where(Stack.removed_at.is_(None)))
        if access.can(Role.VIEWER, s.id)
    ]
    by_endpoint: dict[uuid.UUID | None, list[Stack]] = {}
    for s in stacks:
        by_endpoint.setdefault(s.endpoint_id, []).append(s)
    result: dict[str, StackHealth] = {}
    for endpoint_id, group in by_endpoint.items():
        try:
            target: Target = request.app.state.targets(endpoint_id)
        except EndpointOffline:
            continue
        if target.reader is None:
            continue
        kinds = {s.type for s in group}
        try:
            snap = await _snapshot(
                target.reader,
                swarm=StackType.SWARM in kinds,
                compose=StackType.COMPOSE in kinds,
            )
        except DockerUnavailable:
            continue
        for s in group:
            rows = _swarm_rows(s, snap) if s.type == StackType.SWARM else _compose_rows(s, snap)
            result[str(s.id)] = stack_health(rows)
    return result


async def _locate(app: FastAPI, stack: Stack, ref: str) -> Located:
    target, reader = _target(app, stack)
    if stack.type == StackType.SWARM:
        task = await reader.task(ref)
        labels = task.get("Spec", {}).get("ContainerSpec", {}).get("Labels") or {}
        if labels.get(STACK_LABEL) != stack.name:
            raise NotFound("No such container in this stack.")
        container_id = (task.get("Status", {}).get("ContainerStatus") or {}).get("ContainerID")
        return Located(stack, target, task.get("NodeID") or "", container_id, task)
    container = await reader.container(ref)
    labels = (container.get("Config") or {}).get("Labels") or {}
    if labels.get(COMPOSE_PROJECT) != stack.name:
        raise NotFound("No such container in this stack.")
    node_id, _ = await _host(reader)
    return Located(stack, target, node_id or "", container.get("Id"), container)


def _swarm_info(located: Located, node_names: Mapping[str, str], service: Obj) -> ContainerInfo:
    task = located.raw
    spec = task.get("Spec", {}).get("ContainerSpec", {})
    state = task.get("Status", {})
    container_status = state.get("ContainerStatus") or {}
    summary = views.service_summary(service)
    node_id = task.get("NodeID")
    return ContainerInfo(
        ref=task["ID"],
        name=f"{summary.name}.{task.get('Slot') or task['ID'][:12]}",
        service=_short(summary.name, located.stack.name),
        container_id=located.container_id,
        node=node_names.get(node_id) if node_id else None,
        state=state.get("State", ""),
        status=state.get("Message"),
        health=None,
        health_log=None,
        error=state.get("Err") or None,
        exit_code=container_status.get("ExitCode"),
        image=spec.get("Image", "").split("@", 1)[0],
        command=[*(spec.get("Command") or []), *(spec.get("Args") or [])],
        env=list(spec.get("Env") or []),
        labels=dict(spec.get("Labels") or {}),
        mounts=[
            views.Mount(
                type=m.get("Type", "volume"),
                source=m.get("Source"),
                target=m.get("Target", ""),
                read_only=bool(m.get("ReadOnly")),
            )
            for m in spec.get("Mounts") or []
        ],
        ports=summary.ports,
        networks=[
            NetworkAddress(
                name=a.get("Network", {}).get("Spec", {}).get("Name", "?"),
                addresses=list(a.get("Addresses") or []),
            )
            for a in task.get("NetworksAttachments") or []
        ],
        restart_count=None,
        created_at=_time(task.get("CreatedAt")),
        started_at=_time(state.get("Timestamp")) if state.get("State") == "running" else None,
        finished_at=None,
    )


def _compose_info(located: Located) -> ContainerInfo:
    c = located.raw
    config = c.get("Config") or {}
    state = c.get("State") or {}
    health = state.get("Health") or {}
    last_check = (health.get("Log") or [{}])[-1]
    labels = config.get("Labels") or {}
    ports = []
    for key, bindings in ((c.get("NetworkSettings") or {}).get("Ports") or {}).items():
        target, _, protocol = key.partition("/")
        for b in bindings or [None]:
            published = int(b["HostPort"]) if b and b.get("HostPort") else None
            ports.append(
                views.Port(published=published, target=int(target), protocol=protocol, mode="host")
            )
    unique_ports = list({(p.published, p.target, p.protocol): p for p in ports}.values())
    return ContainerInfo(
        ref=c["Id"],
        name=c.get("Name", "").lstrip("/"),
        service=labels.get(COMPOSE_SERVICE, ""),
        container_id=c["Id"],
        node=None,
        state=state.get("Status", ""),
        status=None,
        health=health.get("Status"),
        health_log=(last_check.get("Output") or "").strip() or None,
        error=state.get("Error") or None,
        exit_code=state.get("ExitCode"),
        image=config.get("Image", ""),
        command=[*(config.get("Entrypoint") or []), *(config.get("Cmd") or [])],
        env=list(config.get("Env") or []),
        labels=dict(labels),
        mounts=[
            views.Mount(
                type=m.get("Type", "volume"),
                source=m.get("Name") or m.get("Source"),
                target=m.get("Destination", ""),
                read_only=m.get("RW") is False,
            )
            for m in c.get("Mounts") or []
        ],
        ports=sorted(unique_ports, key=lambda p: (p.target, p.published or 0)),
        networks=[
            NetworkAddress(name=name, addresses=[n["IPAddress"]] if n.get("IPAddress") else [])
            for name, n in sorted(((c.get("NetworkSettings") or {}).get("Networks") or {}).items())
        ],
        restart_count=c.get("RestartCount"),
        created_at=_time(c.get("Created")),
        started_at=_time(state.get("StartedAt")),
        finished_at=_time(state.get("FinishedAt")) if state.get("Status") != "running" else None,
    )


@router.get("/stacks/{stack_id}/containers/{ref}")
async def container_info(
    stack_id: uuid.UUID, ref: str, request: Request, db: DB, access: CurrentAccess
) -> ContainerInfo:
    stack = await _stack(db, access, stack_id, Role.VIEWER, "stack.view")
    located = await _locate(request.app, stack, ref)
    _, reader = _target(request.app, stack)
    if stack.type == StackType.SWARM:
        service = await reader.service(located.raw.get("ServiceID", ""))
        return _swarm_info(located, await _node_names(reader), service)
    info = _compose_info(located)
    _, info.node = await _host(reader)
    return info


def _agents(located: Located) -> Agents:
    agents = located.target.agents
    if agents is None or not agents.enabled:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Logs and terminals need the Novaport agent."
        )
    if located.container_id is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "This task has no container (yet).")
    return agents


@router.get("/stacks/{stack_id}/containers/{ref}/logs")
async def container_logs(
    stack_id: uuid.UUID,
    ref: str,
    request: Request,
    db: DB,
    access: CurrentAccess,
    tail: Tail = 200,
    timestamps: bool = True,
) -> Any:
    stack = await _stack(db, access, stack_id, Role.VIEWER, "stack.view")
    located = await _locate(request.app, stack, ref)
    return await _agents(located).get(
        located.node_id,
        f"/containers/{located.container_id}/logs",
        {"tail": tail, "timestamps": timestamps},
    )


@router.post("/stacks/{stack_id}/containers/{ref}/restart", status_code=status.HTTP_204_NO_CONTENT)
async def restart_container(
    stack_id: uuid.UUID, ref: str, request: Request, db: DB, access: CurrentAccess
) -> None:
    stack = await _stack(db, access, stack_id, Role.OPERATOR, "container.restart")
    if stack.type == StackType.SWARM:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Swarm replaces tasks itself; restart the service instead."
        )
    located = await _locate(request.app, stack, ref)
    try:
        result = await located.target.cli.restart_containers([located.container_id or ref])
    except CommandTimeout as exc:
        raise HTTPException(status.HTTP_504_GATEWAY_TIMEOUT, str(exc)) from exc
    await _finish(db, access, "container.restart", located.raw.get("Name", ref), stack, result.ok)
    if not result.ok:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, result.summary())


@router.post("/stacks/{stack_id}/services/{name}/restart", status_code=status.HTTP_204_NO_CONTENT)
async def restart_stack_service(
    stack_id: uuid.UUID, name: str, request: Request, db: DB, access: CurrentAccess
) -> None:
    """Replace a Swarm service's tasks, or restart a Compose service's containers."""
    stack = await _stack(db, access, stack_id, Role.OPERATOR, "service.restart")
    target, reader = _target(request.app, stack)
    async with try_lock(request.app.state.engine, f"deploy:{stack.id}") as acquired:
        if not acquired:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "A deployment of this stack is running. Try again later."
            )
        try:
            if stack.type == StackType.SWARM:
                services = await reader.services(label=f"{STACK_LABEL}={stack.name}")
                if f"{stack.name}_{name}" not in {s["Spec"]["Name"] for s in services}:
                    raise HTTPException(status.HTTP_404_NOT_FOUND, "No such service.")
                result = await target.cli.restart_service(f"{stack.name}_{name}")
            else:
                containers = await reader.containers(f"{COMPOSE_PROJECT}={stack.name}")
                ids = [
                    c["Id"]
                    for c in containers
                    if (c.get("Labels") or {}).get(COMPOSE_SERVICE) == name
                ]
                if not ids:
                    raise HTTPException(status.HTTP_404_NOT_FOUND, "No such service.")
                result = await target.cli.restart_containers(ids)
        except CommandTimeout as exc:
            raise HTTPException(status.HTTP_504_GATEWAY_TIMEOUT, str(exc)) from exc
    await _finish(db, access, "service.restart", f"{stack.name}/{name}", stack, result.ok)
    if not result.ok:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, result.summary())


async def _finish(db: DB, access: Access, action: str, target: str, stack: Stack, ok: bool) -> None:
    access.audit(
        db,
        action,
        outcome=Outcome.SUCCESS if ok else Outcome.FAILED,
        target=target,
        stack_id=stack.id,
    )
    await db.commit()


async def _proxy(
    websocket: WebSocket,
    stack_id: uuid.UUID,
    ref: str,
    agent_path: str,
    params: dict[str, Any],
    role: Role,
    audit_action: str | None = None,
) -> None:
    access = await websocket_access(websocket)
    if access is None:
        await websocket.close(code=1008, reason="Not logged in.")
        return
    maker = websocket.app.state.sessionmaker
    try:
        async with maker() as db:
            stack = await _stack(db, access, stack_id, role, audit_action or "stack.view")
        located = await _locate(websocket.app, stack, ref)
        agents = _agents(located)
    except Forbidden:
        if audit_action:
            async with maker() as db:
                access.audit(
                    db, audit_action, outcome=Outcome.DENIED, target=ref, stack_id=stack_id
                )
                await db.commit()
        await websocket.close(code=1008, reason="Not allowed.")
        return
    except HTTPException as exc:
        await websocket.accept()
        await websocket.close(code=4000 + exc.status_code, reason=str(exc.detail)[:120])
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
                    target=ref,
                    stack_id=stack_id,
                    details={**params, "node": located.node_id, **extra},
                )
                await db.commit()

    await websocket.accept()
    path = agent_path.format(container=located.container_id) + "?" + urlencode(params)
    try:
        async with agents.websocket(located.node_id, path) as agent:
            await audit(Outcome.SUCCESS)
            await bridge(websocket, agent)
    except (AgentUnavailable, OSError, InvalidHandshake, InvalidStatus) as exc:
        log.warning("Agent connection for container %s failed: %s", ref, exc)
        await audit(Outcome.FAILED, error=str(exc)[:500])
        with contextlib.suppress(RuntimeError):
            await websocket.close(code=1011, reason=str(exc)[:120])


@ws_router.websocket("/stacks/{stack_id}/containers/{ref}/logs/follow")
async def follow_container_logs(
    websocket: WebSocket, stack_id: uuid.UUID, ref: str, tail: Tail = 100, timestamps: bool = True
) -> None:
    await _proxy(
        websocket,
        stack_id,
        ref,
        "/containers/{container}/logs/follow",
        {"tail": tail, "timestamps": str(timestamps).lower()},
        Role.VIEWER,
    )


@ws_router.websocket("/stacks/{stack_id}/containers/{ref}/exec")
async def exec_container(
    websocket: WebSocket, stack_id: uuid.UUID, ref: str, cmd: str = "/bin/sh"
) -> None:
    await _proxy(
        websocket,
        stack_id,
        ref,
        "/containers/{container}/exec",
        {"cmd": cmd},
        Role.OPERATOR,
        audit_action="terminal.session",
    )
