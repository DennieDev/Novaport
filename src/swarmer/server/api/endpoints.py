# SPDX-License-Identifier: AGPL-3.0-or-later
import asyncio
import contextlib
import hmac
import json
import logging
import shlex
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, status
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from starlette.requests import HTTPConnection

from swarmer.edge.hub import EdgeHub
from swarmer.edge.keys import EdgeKey, hash_secret, new_secret, parse_token
from swarmer.models import Endpoint, Stack
from swarmer.server.deps import DB, AdminAccess, ViewerAccess, current_user, get_settings
from swarmer.stacks.settings import LOCAL_ENDPOINT, NAME_PATTERN

log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/endpoints", tags=["endpoints"], dependencies=[Depends(current_user)]
)
ws_router = APIRouter(prefix="/api", tags=["endpoints"])

IMAGE = "ghcr.io/dennielabs/swarmer:latest"
HELLO_TIMEOUT = 15.0


class EndpointIn(BaseModel):
    name: str = Field(pattern=NAME_PATTERN.pattern)


class EndpointOut(BaseModel):
    id: uuid.UUID
    name: str
    connected: bool
    hostname: str | None
    docker_version: str | None
    agent_version: str | None
    swarm: bool
    connected_at: datetime | None
    disconnected_at: datetime | None
    stacks: list[str]


class EndpointKey(BaseModel):
    endpoint: EndpointOut
    key: str
    # How to start the agent on a single host, and as a service on a Swarm manager.
    docker_command: str
    swarm_command: str


def get_edge(conn: HTTPConnection) -> EdgeHub:
    edge: EdgeHub = conn.app.state.edge
    return edge


async def _out(db: DB, edge: EdgeHub, endpoint: Endpoint) -> EndpointOut:
    stacks = await db.scalars(
        select(Stack.name).where(Stack.endpoint_id == endpoint.id).order_by(Stack.name)
    )
    return EndpointOut(
        id=endpoint.id,
        name=endpoint.name,
        connected=edge.get(endpoint.id) is not None,
        hostname=endpoint.hostname,
        docker_version=endpoint.docker_version,
        agent_version=endpoint.agent_version,
        swarm=endpoint.swarm,
        connected_at=endpoint.connected_at,
        disconnected_at=endpoint.disconnected_at,
        stacks=list(stacks),
    )


def _commands(key: str) -> tuple[str, str]:
    env = shlex.quote(f"SWARMER_EDGE_KEY={key}")
    docker = (
        "docker run -d --name swarmer-agent --restart always "
        "-v /var/run/docker.sock:/var/run/docker.sock -v /:/host:ro "
        f"-e {env} {IMAGE} agent"
    )
    swarm = (
        "docker service create --name swarmer-agent --constraint node.role==manager "
        "--mount type=bind,src=/var/run/docker.sock,dst=/var/run/docker.sock "
        "--mount type=bind,src=/,dst=/host,readonly "
        f"-e {env} {IMAGE} agent"
    )
    return docker, swarm


async def _issue_key(request: Request, db: DB, endpoint: Endpoint) -> EndpointKey:
    secret = new_secret()
    endpoint.secret_hash = hash_secret(secret)
    public = get_settings(request).public_url
    url = public.rstrip("/") if public else str(request.base_url).rstrip("/")
    key = EdgeKey(url, endpoint.id, secret).encode()
    docker, swarm = _commands(key)
    return EndpointKey(
        endpoint=await _out(db, get_edge(request), endpoint),
        key=key,
        docker_command=docker,
        swarm_command=swarm,
    )


async def _get(db: DB, endpoint_id: uuid.UUID) -> Endpoint:
    endpoint = await db.get(Endpoint, endpoint_id)
    if endpoint is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Endpoint not found.")
    return endpoint


@router.get("")
async def list_endpoints(request: Request, db: DB, _: ViewerAccess) -> list[EndpointOut]:
    edge = get_edge(request)
    endpoints = await db.scalars(select(Endpoint).order_by(Endpoint.name))
    return [await _out(db, edge, e) for e in endpoints]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_endpoint(
    body: EndpointIn, request: Request, db: DB, access: AdminAccess
) -> EndpointKey:
    if body.name == LOCAL_ENDPOINT:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, f"{LOCAL_ENDPOINT!r} is reserved."
        )
    endpoint = Endpoint(id=uuid.uuid4(), name=body.name, secret_hash="")
    db.add(endpoint)
    try:
        await db.flush()
    except IntegrityError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "An endpoint with this name exists.") from exc
    result = await _issue_key(request, db, endpoint)
    access.audit(db, "endpoint.create", target=endpoint.name)
    await db.commit()
    return result


@router.post("/{endpoint_id}/key")
async def rotate_key(
    endpoint_id: uuid.UUID, request: Request, db: DB, access: AdminAccess
) -> EndpointKey:
    """Issue a new key. The agent connected with the old one is disconnected."""
    endpoint = await _get(db, endpoint_id)
    await get_edge(request).disconnect(endpoint.id)
    result = await _issue_key(request, db, endpoint)
    access.audit(db, "endpoint.rotate_key", target=endpoint.name)
    await db.commit()
    result.endpoint.connected = False
    return result


@router.delete("/{endpoint_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_endpoint(
    endpoint_id: uuid.UUID, request: Request, db: DB, access: AdminAccess
) -> None:
    endpoint = await _get(db, endpoint_id)
    used = list(await db.scalars(select(Stack.name).where(Stack.endpoint_id == endpoint.id)))
    if used:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Stacks still deploy to this endpoint: {', '.join(sorted(used))}.",
        )
    await get_edge(request).disconnect(endpoint.id)
    await db.delete(endpoint)
    access.audit(db, "endpoint.delete", target=endpoint.name)
    await db.commit()


@ws_router.websocket("/edge/connect")
async def edge_connect(websocket: WebSocket) -> None:
    """Agents outside Swarmer's own Swarm connect here and keep the connection open."""
    maker = websocket.app.state.sessionmaker
    parsed = parse_token(websocket.headers.get("authorization", ""))
    endpoint = None
    if parsed is not None:
        async with maker() as db:
            endpoint = await db.get(Endpoint, parsed[0])
    if (
        parsed is None
        or endpoint is None
        or not hmac.compare_digest(endpoint.secret_hash, hash_secret(parsed[1]))
    ):
        await websocket.close(code=1008)
        return
    await websocket.accept()
    try:
        hello = json.loads(await asyncio.wait_for(websocket.receive_text(), HELLO_TIMEOUT))
        agent_token = str(hello["agent_token"])
        agent_version = str(hello.get("version") or "")[:50] or None
    except (TimeoutError, ValueError, KeyError, TypeError, RuntimeError):
        await websocket.close(code=1002)
        return

    async def receive() -> bytes | None:
        message = await websocket.receive()
        if message["type"] == "websocket.disconnect":
            return None
        data: bytes | None = message.get("bytes")
        return data if data is not None else str(message.get("text") or "").encode()

    edge = get_edge(websocket)
    tunnel = await edge.open(endpoint.id, endpoint.name, websocket.send_bytes, receive, agent_token)
    try:
        info: Mapping[str, Any] = {}
        if tunnel.target is not None and tunnel.target.reader is not None:
            with contextlib.suppress(Exception):
                info = await asyncio.wait_for(tunnel.target.reader.info(), HELLO_TIMEOUT)
        swarm = info.get("Swarm") or {}
        tunnel.agents.node_id = swarm.get("NodeID") or None
        tunnel.agents.hostname = info.get("Name")
        tunnel.agents.version = agent_version
        async with maker() as db:
            await db.execute(
                update(Endpoint)
                .where(Endpoint.id == endpoint.id)
                .values(
                    connected_at=datetime.now(UTC),
                    hostname=(info.get("Name") or None),
                    docker_version=(info.get("ServerVersion") or None),
                    agent_version=agent_version,
                    swarm=bool(swarm.get("ControlAvailable")),
                )
            )
            await db.commit()
        log.info("Agent for endpoint %s connected from %s", endpoint.name, info.get("Name"))
        await tunnel.wait()
    finally:
        await edge.release(tunnel)
        # A reconnect may already have replaced this tunnel.
        if edge.get(endpoint.id) is None:
            async with maker() as db:
                await db.execute(
                    update(Endpoint)
                    .where(Endpoint.id == endpoint.id)
                    .values(disconnected_at=datetime.now(UTC))
                )
                await db.commit()
            log.info("Agent for endpoint %s disconnected", endpoint.name)
        with contextlib.suppress(RuntimeError):
            await websocket.close()
