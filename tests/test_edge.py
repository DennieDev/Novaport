# SPDX-License-Identifier: AGPL-3.0-or-later
import asyncio
import contextlib
import json
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from pydantic import SecretStr

from swarmer.agent.app import create_app as create_agent_app
from swarmer.edge.agent import EdgeAgent
from swarmer.edge.hub import EndpointOffline
from swarmer.edge.keys import EdgeKey, hash_secret, parse_token
from swarmer.edge.mux import Mux, Stream, StreamReset
from swarmer.server.app import create_app
from tests.conftest import make_settings
from tests.fakes import FakeReader
from tests.gitrepo import COMPOSE, Repo
from tests.test_agent import FakeNode
from tests.test_pipeline import FakeCli

PASSWORD = "correct horse battery"
DOCKER_INFO = {
    "Name": "edge-host",
    "ServerVersion": "28.5.0",
    "Swarm": {"LocalNodeState": "inactive", "NodeID": ""},
}


def test_edge_key_round_trip() -> None:
    key = EdgeKey("https://swarmer.example.com/", uuid.uuid4(), "s3cret")
    decoded = EdgeKey.decode(key.encode())
    assert decoded == key
    assert decoded.connect_url == "wss://swarmer.example.com/api/edge/connect"
    assert parse_token(f"Bearer {key.token}") == (key.endpoint_id, "s3cret")
    assert parse_token("Bearer nope") is None
    with pytest.raises(ValueError, match="Not a valid edge key"):
        EdgeKey.decode("garbage")


async def until(check: Callable[[], Awaitable[bool]], seconds: float = 10.0) -> None:
    for _ in range(int(seconds / 0.05)):
        if await check():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("condition not reached in time")


@contextlib.asynccontextmanager
async def mux_pair(
    on_open: Callable[[Stream, str], Awaitable[None]],
) -> AsyncIterator[Mux]:
    to_agent: asyncio.Queue[bytes | None] = asyncio.Queue()
    to_server: asyncio.Queue[bytes | None] = asyncio.Queue()

    async def send_to_agent(data: bytes) -> None:
        await to_agent.put(data)

    async def send_to_server(data: bytes) -> None:
        await to_server.put(data)

    server = Mux(send_to_agent)
    agent = Mux(send_to_server, on_open=on_open)
    tasks = [
        asyncio.create_task(server.run(to_server.get)),
        asyncio.create_task(agent.run(to_agent.get)),
    ]
    try:
        yield server
    finally:
        await to_agent.put(None)
        await to_server.put(None)
        await asyncio.gather(*tasks)


async def test_mux_carries_streams_both_ways() -> None:
    async def echo(stream: Stream, target: str) -> None:
        if target == "refuse":
            await stream.reset()
            return
        received = b""
        while chunk := await stream.read():
            received += chunk
        await stream.write(target.encode() + b":" + received)
        await stream.write_eof()

    async with mux_pair(echo) as server:
        first, second = await server.open("one"), await server.open("two")
        big = b"x" * 200_000
        await first.write(big)
        await second.write(b"hi")
        await first.write_eof()
        await second.write_eof()

        async def read_all(stream: Stream) -> bytes:
            data = b""
            while chunk := await stream.read():
                data += chunk
            return data

        assert await read_all(second) == b"two:hi"
        assert await read_all(first) == b"one:" + big
        assert server.streams == {}

        refused = await server.open("refuse")
        with pytest.raises(StreamReset):
            await refused.read()


class FakeDocker:
    """Answers HTTP requests on a unix socket the way the Docker API would, for a few paths."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.requests: list[str] = []

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        with contextlib.suppress(ConnectionError, asyncio.IncompleteReadError):
            while head := await reader.readuntil(b"\r\n\r\n"):
                lines = head.decode().split("\r\n")
                method, target, _ = lines[0].split(" ", 2)
                headers = dict(line.split(": ", 1) for line in lines[1:] if ": " in line)
                length = int(headers.get("Content-Length") or headers.get("content-length") or 0)
                if length:
                    await reader.readexactly(length)
                path = target.split("?", 1)[0]
                self.requests.append(f"{method} {path}")
                if path.endswith("/version"):
                    status, body = 200, {"ApiVersion": "1.47", "Version": "28.5.0"}
                elif path.endswith("/info"):
                    status, body = 200, DOCKER_INFO
                else:
                    status, body = 404, {"message": "not here"}
                payload = json.dumps(body).encode()
                writer.write(
                    f"HTTP/1.1 {status} X\r\nContent-Type: application/json\r\n"
                    f"Content-Length: {len(payload)}\r\n\r\n".encode()
                    + payload
                )
                await writer.drain()
        writer.close()


async def serve(app: FastAPI, **config: Any) -> tuple[uvicorn.Server, asyncio.Task[None]]:
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", **config))
    task = asyncio.create_task(server.serve())
    await until(lambda: asyncio.sleep(0, result=server.started))
    return server, task


async def stop(server: uvicorn.Server, task: asyncio.Task[None]) -> None:
    server.should_exit = True
    with contextlib.suppress(asyncio.CancelledError):
        await asyncio.wait_for(task, 10)


@dataclass
class World:
    app: FastAPI
    api: httpx.AsyncClient
    base_url: str
    tmp: Path

    async def start_agent(self, key: str) -> asyncio.Task[None]:
        """An edge agent with a fake Docker socket and the real agent API on a unix socket."""
        docker = FakeDocker(self.tmp / "docker.sock")
        docker_server = await asyncio.start_unix_server(docker.handle, path=docker.path)
        host = self.tmp / "host"
        (host / "srv" / "www").mkdir(parents=True, exist_ok=True)
        token = "agent-token"
        settings = make_settings(
            agent_token=SecretStr(token),
            host_root=host,
            docker_host=f"unix://{docker.path}",
            edge_key=SecretStr(key),
        )
        agent_socket = self.tmp / "agent.sock"
        agent_api, agent_task = await serve(
            create_agent_app(settings, node=FakeNode(host)), uds=str(agent_socket)
        )
        edge = EdgeAgent(settings, EdgeKey.decode(key), agent_socket, token)

        async def run() -> None:
            try:
                await edge.run()
            finally:
                docker_server.close()
                await stop(agent_api, agent_task)

        return asyncio.create_task(run())


@pytest.fixture
async def world(clean_db: str, tmp_path: Path) -> AsyncIterator[World]:
    settings = make_settings(
        database_url=SecretStr(clean_db), initial_admin_password=SecretStr(PASSWORD)
    )
    app = create_app(settings, reader=FakeReader(), cli=FakeCli())
    server, task = await serve(app, host="127.0.0.1", port=0)
    port = server.servers[0].sockets[0].getsockname()[1]
    base_url = f"http://127.0.0.1:{port}"
    async with httpx.AsyncClient(base_url=base_url) as api:
        response = await api.post(
            "/api/auth/login", json={"username": "admin", "password": PASSWORD}
        )
        assert response.status_code == 204
        try:
            yield World(app, api, base_url, tmp_path)
        finally:
            await stop(server, task)


async def endpoint(world: World, endpoint_id: str) -> dict[str, Any]:
    listed = (await world.api.get("/api/endpoints")).json()
    found: dict[str, Any] = next(e for e in listed if e["id"] == endpoint_id)
    return found


@pytest.mark.postgres
async def test_agent_connects_out_and_tunnels_docker(world: World) -> None:
    created = await world.api.post("/api/endpoints", json={"name": "edge-01"})
    assert created.status_code == 201, created.text
    data = created.json()
    key = EdgeKey.decode(data["key"])
    assert key.url == world.base_url
    assert "SWARMER_EDGE_KEY=" in data["docker_command"]
    assert data["endpoint"]["connected"] is False

    agent = await world.start_agent(data["key"])
    try:
        endpoint_id = data["endpoint"]["id"]

        async def connected() -> bool:
            found = await endpoint(world, endpoint_id)
            return bool(found["connected"] and found["hostname"])

        await until(connected)
        found = await endpoint(world, endpoint_id)
        assert found["hostname"] == "edge-host"
        assert found["docker_version"] == "28.5.0"
        assert found["swarm"] is False

        target = world.app.state.targets(uuid.UUID(endpoint_id))
        assert (await target.reader.info())["Name"] == "edge-host"
        [checked] = await target.agents.post("ignored", "/paths", {"paths": ["/srv/www"]})
        assert checked["exists"] is True

        rotated = await world.api.post(f"/api/endpoints/{endpoint_id}/key")
        assert rotated.json()["key"] != data["key"]

        async def disconnected() -> bool:
            return not (await endpoint(world, endpoint_id))["connected"]

        await until(disconnected)
        with pytest.raises(EndpointOffline):
            world.app.state.targets(uuid.UUID(endpoint_id))
        # The agent keeps retrying with the old key, which the server refuses.
        await asyncio.sleep(1.5)
        assert (await endpoint(world, endpoint_id))["connected"] is False
    finally:
        agent.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await agent


@pytest.mark.postgres
async def test_endpoint_names(world: World) -> None:
    assert (await world.api.post("/api/endpoints", json={"name": "local"})).status_code == 422
    assert (await world.api.post("/api/endpoints", json={"name": "Bad Name"})).status_code == 422
    assert (await world.api.post("/api/endpoints", json={"name": "edge"})).status_code == 201
    assert (await world.api.post("/api/endpoints", json={"name": "edge"})).status_code == 409


@pytest.mark.postgres
async def test_stacks_pick_an_endpoint(world: World) -> None:
    origin = Repo(world.tmp / "origin")
    origin.write("stacks/web/compose.yaml", COMPOSE)
    origin.write("stacks/web/swarmer.yaml", "type: compose\nendpoint: edge\n")
    origin.commit("Add web")
    repo = (
        await world.api.post("/api/repositories", json={"name": "infra", "url": origin.url})
    ).json()
    await world.api.post(f"/api/repositories/{repo['id']}/sync")
    [stack] = (await world.api.get("/api/stacks")).json()
    assert "unknown endpoint 'edge'" in stack["config_error"]

    edge = (await world.api.post("/api/endpoints", json={"name": "edge"})).json()["endpoint"]
    await world.api.post(f"/api/repositories/{repo['id']}/sync")
    stack = (await world.api.get(f"/api/stacks/{stack['id']}")).json()
    assert stack["config_error"] is None
    assert stack["endpoint"] == "edge"

    deployment = (await world.api.post(f"/api/stacks/{stack['id']}/deploy", json={})).json()
    assert await world.app.state.pipeline.run(uuid.UUID(deployment["id"])) == "failed"
    detail = (await world.api.get(f"/api/deployments/{deployment['id']}")).json()
    assert "not connected" in detail["error"]

    refused = await world.api.delete(f"/api/endpoints/{edge['id']}")
    assert refused.status_code == 409
    assert "web" in refused.json()["detail"]


@pytest.mark.postgres
async def test_wrong_key_is_refused(world: World) -> None:
    edge = (await world.api.post("/api/endpoints", json={"name": "edge"})).json()
    key = EdgeKey.decode(edge["key"])
    forged = EdgeKey(key.url, key.endpoint_id, "wrong").encode()
    assert hash_secret("wrong") != hash_secret(key.secret)
    agent = await world.start_agent(forged)
    try:
        await asyncio.sleep(1.5)
        assert (await endpoint(world, edge["endpoint"]["id"]))["connected"] is False
    finally:
        agent.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await agent
