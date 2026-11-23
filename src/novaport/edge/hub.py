# SPDX-License-Identifier: AGPL-3.0-or-later
"""Server side of edge tunnels: one per connected endpoint, each with local sockets.

The docker CLI and aiodocker talk to `<dir>/docker.sock` as if it were the remote host's Docker
socket; `<dir>/agent.sock` reaches the agent's own API. Every connection to either socket becomes
a stream through the tunnel.
"""

import asyncio
import contextlib
import logging
import os
import shutil
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any

import aiodocker
import httpx
from websockets.asyncio.client import ClientConnection, unix_connect

from novaport.agents.pool import TIMEOUT, Agent, AgentError, Agents, AgentUnavailable
from novaport.deploy.cli import DockerCli, SubprocessDockerCli
from novaport.docker.client import AiodockerReader, ClusterReader
from novaport.edge.mux import Mux, Receive, Send, pipe

log = logging.getLogger(__name__)


@dataclass
class Target:
    """Where a stack's Docker commands and agent calls go."""

    name: str
    cli: DockerCli
    reader: ClusterReader | None
    agents: Agents | None


class EndpointOffline(Exception):
    pass


class TunnelAgents:
    """The single agent at the far end of a tunnel, whatever node ID it is asked for."""

    def __init__(self, socket: Path, token: str) -> None:
        self.socket = socket
        self.token = token
        self.node_id: str | None = None
        self.hostname: str | None = None
        self.version: str | None = None
        self.http = httpx.AsyncClient(
            base_url="http://agent",
            transport=httpx.AsyncHTTPTransport(uds=str(socket)),
            timeout=TIMEOUT,
            headers=self._headers(),
        )

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    @property
    def enabled(self) -> bool:
        return True

    async def list(self, refresh: bool = False) -> list[Agent]:
        return [Agent("edge", self.node_id, self.hostname, self.version)]

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = await self.http.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise AgentUnavailable(f"Agent on {self.hostname or 'the endpoint'}: {exc}") from exc
        if response.is_error:
            try:
                detail = response.json().get("detail", response.text)
            except ValueError:
                detail = response.text
            raise AgentError(response.status_code, str(detail))
        return response.json()

    async def get(self, node_id: str, path: str, params: dict[str, Any] | None = None) -> Any:
        return await self._request("GET", path, params=params)

    async def post(self, node_id: str, path: str, body: Any) -> Any:
        return await self._request("POST", path, json=body)

    @asynccontextmanager
    async def websocket(self, node_id: str, path: str) -> AsyncIterator[ClientConnection]:
        async with unix_connect(
            str(self.socket),
            f"ws://agent{path}",
            additional_headers=self._headers(),
            open_timeout=TIMEOUT,
            max_size=2**22,
        ) as ws:
            yield ws

    async def close(self) -> None:
        await self.http.aclose()


class Tunnel:
    def __init__(
        self,
        endpoint_id: uuid.UUID,
        name: str,
        directory: Path,
        send: Send,
        agent_token: str,
        command_timeout: float,
    ) -> None:
        self.endpoint_id = endpoint_id
        self.name = name
        self.directory = directory
        self.mux = Mux(send)
        self.docker_socket = directory / "docker.sock"
        self.agent_socket = directory / "agent.sock"
        self._servers: list[asyncio.Server] = []
        self._runner: asyncio.Task[None] | None = None
        self._docker: aiodocker.Docker | None = None
        self.agents = TunnelAgents(self.agent_socket, agent_token)
        self.command_timeout = command_timeout
        self.target: Target | None = None

    @property
    def docker_host(self) -> str:
        return f"unix://{self.docker_socket}"

    async def start(self, receive: Receive) -> Target:
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        for target, path in (("docker", self.docker_socket), ("agent", self.agent_socket)):
            path.unlink(missing_ok=True)
            server = await asyncio.start_unix_server(partial(self._serve, target), path=path)
            os.chmod(path, 0o600)
            self._servers.append(server)
        self._runner = asyncio.create_task(self.mux.run(receive), name=f"edge-{self.name}")
        self._docker = aiodocker.Docker(url=self.docker_host)
        self.target = Target(
            self.name,
            SubprocessDockerCli(self.docker_host, self.command_timeout),
            AiodockerReader(self._docker),
            self.agents,
        )
        return self.target

    async def _serve(
        self, target: str, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            stream = await self.mux.open(target)
        except ConnectionError:
            writer.close()
            return
        await pipe(stream, reader, writer)

    async def wait(self) -> None:
        if self._runner is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await self._runner

    async def close(self) -> None:
        if self._runner is not None:
            self._runner.cancel()
        self.mux.close()
        for server in self._servers:
            server.close()
        if self._docker is not None:
            await self._docker.close()
        await self.agents.close()
        shutil.rmtree(self.directory, ignore_errors=True)


class EdgeHub:
    def __init__(self, socket_root: Path, command_timeout: float) -> None:
        self.socket_root = socket_root
        self.command_timeout = command_timeout
        self.tunnels: dict[uuid.UUID, Tunnel] = {}

    def get(self, endpoint_id: uuid.UUID) -> Tunnel | None:
        return self.tunnels.get(endpoint_id)

    async def open(
        self, endpoint_id: uuid.UUID, name: str, send: Send, receive: Receive, agent_token: str
    ) -> Tunnel:
        """Start a tunnel for a freshly connected agent, replacing an older connection."""
        if (old := self.tunnels.pop(endpoint_id, None)) is not None:
            await old.close()
        # Short paths: unix socket paths are limited to about 100 bytes.
        directory = self.socket_root / endpoint_id.hex[:12]
        tunnel = Tunnel(endpoint_id, name, directory, send, agent_token, self.command_timeout)
        await tunnel.start(receive)
        self.tunnels[endpoint_id] = tunnel
        return tunnel

    async def release(self, tunnel: Tunnel) -> None:
        if self.tunnels.get(tunnel.endpoint_id) is tunnel:
            del self.tunnels[tunnel.endpoint_id]
        await tunnel.close()

    async def disconnect(self, endpoint_id: uuid.UUID) -> None:
        if (tunnel := self.tunnels.pop(endpoint_id, None)) is not None:
            await tunnel.close()

    async def close(self) -> None:
        for tunnel in list(self.tunnels.values()):
            await tunnel.close()
        self.tunnels.clear()
        shutil.rmtree(self.socket_root, ignore_errors=True)


class Targets:
    """Resolves an endpoint ID to its target; None is the Swarm or host Novaport runs on."""

    def __init__(self, local: Callable[[], Target], hub: EdgeHub) -> None:
        self.local = local
        self.hub = hub

    def __call__(self, endpoint_id: uuid.UUID | None) -> Target:
        if endpoint_id is None:
            return self.local()
        tunnel = self.hub.get(endpoint_id)
        if tunnel is None or tunnel.target is None:
            raise EndpointOffline("The agent on this stack's endpoint is not connected.")
        return tunnel.target
