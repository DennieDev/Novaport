# SPDX-License-Identifier: AGPL-3.0-or-later
import asyncio
import json
from collections.abc import AsyncIterator, Iterator

import httpx
import pytest
from fastapi import FastAPI
from pydantic import SecretStr
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from swarmer.auth.passwords import hash_password
from swarmer.migrate import upgrade
from swarmer.models import AuditEntry, Role, RoleBinding, User
from swarmer.server.app import create_app
from tests.conftest import TEST_DATABASE_URL, as_admin, client_for, make_settings
from tests.fakes import FakeAgents, FakeReader

PASSWORD = "correct horse battery"


def _admin_app(agents: FakeAgents, reader: FakeReader) -> FastAPI:
    return as_admin(create_app(make_settings(), reader=reader, agents=agents))


@pytest.fixture
def reader() -> FakeReader:
    reader = FakeReader()
    reader.task_list = [
        {
            "ID": "task1",
            "ServiceID": "svc1",
            "NodeID": "node1",
            "Spec": {"ContainerSpec": {"Image": "nginx"}},
            "DesiredState": "running",
            "Status": {
                "Timestamp": "2026-11-23T10:00:00Z",
                "State": "running",
                "ContainerStatus": {"ContainerID": "c" * 64},
            },
        },
        {
            "ID": "task2",
            "ServiceID": "svc1",
            "Spec": {"ContainerSpec": {"Image": "nginx"}},
            "DesiredState": "running",
            "Status": {"Timestamp": "2026-11-23T10:00:00Z", "State": "pending"},
        },
    ]
    return reader


@pytest.fixture
async def api(reader: FakeReader) -> AsyncIterator[tuple[httpx.AsyncClient, FakeAgents]]:
    agents = FakeAgents({"node1": set()})
    async for client in client_for(_admin_app(agents, reader)):
        yield client, agents


async def test_list_agents(api: tuple[httpx.AsyncClient, FakeAgents]) -> None:
    client, _ = api
    data = (await client.get("/api/agents")).json()
    assert data["enabled"] is True
    assert data["agents"][0]["node_id"] == "node1"


async def test_node_endpoints_are_proxied(api: tuple[httpx.AsyncClient, FakeAgents]) -> None:
    client, agents = api
    for suffix in ("disk", "images", "volumes"):
        assert (await client.get(f"/api/nodes/node1/{suffix}")).status_code == 200
    files = await client.get("/api/nodes/node1/volumes/data/files", params={"path": "/sub"})
    assert files.json()["params"] == {"path": "/sub"}
    assert [path for _, path, _ in agents.requests] == [
        "/disk",
        "/images",
        "/volumes",
        "/volumes/data/files",
    ]


async def test_unknown_node_is_unavailable(api: tuple[httpx.AsyncClient, FakeAgents]) -> None:
    client, _ = api
    response = await client.get("/api/nodes/elsewhere/disk")
    assert response.status_code == 503
    assert "elsewhere" in response.json()["detail"]


async def test_task_logs_go_to_the_tasks_node(api: tuple[httpx.AsyncClient, FakeAgents]) -> None:
    client, agents = api
    response = await client.get("/api/tasks/task1/logs", params={"tail": 50})
    assert response.status_code == 200
    node, path, params = agents.requests[-1]
    assert node == "node1"
    assert path == f"/containers/{'c' * 64}/logs"
    assert params == {"tail": 50, "timestamps": True}


async def test_task_without_container(api: tuple[httpx.AsyncClient, FakeAgents]) -> None:
    client, _ = api
    assert (await client.get("/api/tasks/task2/logs")).status_code == 404


async def test_task_detail(api: tuple[httpx.AsyncClient, FakeAgents]) -> None:
    client, _ = api
    task = (await client.get("/api/tasks/task1")).json()
    assert task["container_id"] == "c" * 64


async def test_agents_disabled(reader: FakeReader) -> None:
    async for client in client_for(_admin_app(FakeAgents(enabled=False), reader)):
        response = await client.get("/api/nodes/node1/disk")
        assert response.status_code == 503
        assert "SWARMER_AGENT_TOKEN" in response.json()["detail"]


async def _truncate(url: str) -> None:
    engine = create_async_engine(url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("TRUNCATE users, sessions, audit_log CASCADE"))
    finally:
        await engine.dispose()


@pytest.fixture
def ws_client(reader: FakeReader) -> Iterator[tuple[TestClient, FakeAgents]]:
    agents = FakeAgents({"node1": set()})
    settings = make_settings(initial_admin_password=SecretStr(PASSWORD))
    if TEST_DATABASE_URL:
        upgrade(TEST_DATABASE_URL)
        asyncio.run(_truncate(TEST_DATABASE_URL))
    with TestClient(create_app(settings, reader=reader, agents=agents)) as client:
        yield client, agents


def _login(client: TestClient) -> None:
    response = client.post("/api/auth/login", json={"username": "admin", "password": PASSWORD})
    assert response.status_code == 204


@pytest.mark.postgres
def test_exec_is_bridged_both_ways(ws_client: tuple[TestClient, FakeAgents]) -> None:
    client, agents = ws_client
    agents.socket.messages = [b"$ ", "prompt"]
    _login(client)
    with client.websocket_connect("/api/tasks/task1/exec?cmd=/bin/bash") as ws:
        ws.send_text(json.dumps({"type": "input", "data": "ls\n"}))
        assert ws.receive_bytes() == b"$ "
        assert ws.receive_text() == "prompt"
    node, path = agents.socket_paths[0]
    assert node == "node1"
    assert path == f"/containers/{'c' * 64}/exec?cmd=%2Fbin%2Fbash"


@pytest.mark.postgres
def test_log_follow_is_bridged(ws_client: tuple[TestClient, FakeAgents]) -> None:
    client, agents = ws_client
    _login(client)
    with client.websocket_connect("/api/tasks/task1/logs/follow?tail=10") as ws:
        assert ws.receive_text() == "hello\n"
    assert agents.socket_paths[0][1].endswith("/logs/follow?tail=10&timestamps=true")


@pytest.mark.postgres
def test_exec_requires_login(ws_client: tuple[TestClient, FakeAgents]) -> None:
    client, agents = ws_client
    with (
        pytest.raises(WebSocketDisconnect) as exc,
        client.websocket_connect("/api/tasks/task1/exec") as ws,
    ):
        ws.receive_text()
    assert exc.value.code == 1008
    assert agents.socket_paths == []


async def _add_viewer(url: str) -> None:
    engine = create_async_engine(url)
    try:
        async with async_sessionmaker(engine)() as db:
            user = User(username="vera", password_hash=hash_password(PASSWORD))
            db.add(user)
            await db.flush()
            db.add(RoleBinding(user_id=user.id, role=Role.VIEWER))
            await db.commit()
    finally:
        await engine.dispose()


async def _terminal_audit(url: str) -> list[tuple[str | None, str]]:
    engine = create_async_engine(url)
    try:
        async with async_sessionmaker(engine)() as db:
            entries = await db.scalars(
                select(AuditEntry)
                .where(AuditEntry.action == "terminal.session")
                .order_by(AuditEntry.id)
            )
            return [(e.username, e.outcome) for e in entries]
    finally:
        await engine.dispose()


@pytest.mark.postgres
def test_exec_needs_operator(ws_client: tuple[TestClient, FakeAgents]) -> None:
    assert TEST_DATABASE_URL
    client, agents = ws_client
    asyncio.run(_add_viewer(TEST_DATABASE_URL))
    response = client.post("/api/auth/login", json={"username": "vera", "password": PASSWORD})
    assert response.status_code == 204

    with client.websocket_connect("/api/tasks/task1/logs/follow") as ws:
        assert ws.receive_text() == "hello\n"
    with (
        pytest.raises(WebSocketDisconnect) as exc,
        client.websocket_connect("/api/tasks/task1/exec") as ws,
    ):
        ws.receive_text()
    assert exc.value.code == 1008
    assert len(agents.socket_paths) == 1
    assert asyncio.run(_terminal_audit(TEST_DATABASE_URL)) == [("vera", "denied")]


@pytest.mark.postgres
def test_exec_is_audited(ws_client: tuple[TestClient, FakeAgents]) -> None:
    assert TEST_DATABASE_URL
    client, agents = ws_client
    agents.socket.messages = ["bye"]
    _login(client)
    with client.websocket_connect("/api/tasks/task1/exec") as ws:
        assert ws.receive_text() == "bye"
    assert asyncio.run(_terminal_audit(TEST_DATABASE_URL)) == [("admin", "success")]


@pytest.mark.postgres
def test_api_tokens_cannot_open_terminals(ws_client: tuple[TestClient, FakeAgents]) -> None:
    assert TEST_DATABASE_URL
    client, agents = ws_client
    _login(client)
    created = client.post("/api/tokens", json={"name": "script", "role": "admin"})
    assert created.status_code == 201
    client.cookies.clear()
    headers = {"Authorization": f"Bearer {created.json()['token']}"}

    with client.websocket_connect("/api/tasks/task1/logs/follow", headers=headers) as ws:
        assert ws.receive_text() == "hello\n"
    with (
        pytest.raises(WebSocketDisconnect) as exc,
        client.websocket_connect("/api/tasks/task1/exec", headers=headers) as ws,
    ):
        ws.receive_text()
    assert exc.value.code == 1008
    assert len(agents.socket_paths) == 1
    assert asyncio.run(_terminal_audit(TEST_DATABASE_URL)) == [("admin", "denied")]
