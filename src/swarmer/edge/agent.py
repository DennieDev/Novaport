# SPDX-License-Identifier: AGPL-3.0-or-later
"""Agent side of an edge tunnel: dial out to the server and serve the streams it opens."""

import asyncio
import contextlib
import json
import logging
import secrets
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

import uvicorn
from pydantic import SecretStr
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidHandshake, InvalidStatus

from swarmer import __version__
from swarmer.agent.app import create_app
from swarmer.config import Settings
from swarmer.edge.keys import EdgeKey
from swarmer.edge.mux import Mux, Stream, pipe

log = logging.getLogger(__name__)

DEFAULT_DOCKER_SOCKET = "/var/run/docker.sock"
MAX_BACKOFF = 30.0


async def open_docker(
    docker_host: str | None,
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    if not docker_host:
        return await asyncio.open_unix_connection(DEFAULT_DOCKER_SOCKET)
    parts = urlsplit(docker_host)
    if parts.scheme == "unix":
        return await asyncio.open_unix_connection(parts.path)
    if parts.scheme == "tcp" and parts.hostname:
        return await asyncio.open_connection(parts.hostname, parts.port or 2375)
    raise ValueError(f"Unsupported DOCKER_HOST for an edge agent: {docker_host}")


class EdgeAgent:
    def __init__(self, settings: Settings, key: EdgeKey, agent_socket: Path, token: str) -> None:
        self.settings = settings
        self.key = key
        self.agent_socket = agent_socket
        self.token = token

    async def _open(self, stream: Stream, target: str) -> None:
        if target == "docker":
            reader, writer = await open_docker(self.settings.docker_host)
        elif target == "agent":
            reader, writer = await asyncio.open_unix_connection(str(self.agent_socket))
        else:
            await stream.reset()
            return
        await pipe(stream, reader, writer)

    async def session(self, ws: ClientConnection) -> None:
        await ws.send(json.dumps({"version": __version__, "agent_token": self.token}))

        async def receive() -> bytes | None:
            try:
                message = await ws.recv()
            except ConnectionClosed:
                return None
            return message if isinstance(message, bytes) else message.encode()

        await Mux(ws.send, on_open=self._open).run(receive)

    async def run(self) -> None:
        """Stay connected, reconnecting with a growing delay after each failure."""
        backoff = 1.0
        url = self.key.connect_url
        while True:
            try:
                async with connect(
                    url,
                    additional_headers={"Authorization": f"Bearer {self.key.token}"},
                    max_size=2**21,
                    open_timeout=15,
                ) as ws:
                    log.info("Connected to %s", self.key.url)
                    backoff = 1.0
                    await self.session(ws)
                log.warning("Connection to %s closed", self.key.url)
            except InvalidStatus as exc:
                log.error("The server refused the connection: HTTP %s", exc.response.status_code)
            except (OSError, InvalidHandshake, TimeoutError) as exc:
                log.warning("Could not connect to %s: %s", self.key.url, exc)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, MAX_BACKOFF)


async def run_edge_agent(settings: Settings) -> None:
    if settings.edge_key is None:
        raise RuntimeError("SWARMER_EDGE_KEY is not set.")
    key = EdgeKey.decode(settings.edge_key.get_secret_value())
    token = secrets.token_urlsafe(32)
    with tempfile.TemporaryDirectory(prefix="swarmer-agent-") as tmp:
        socket = Path(tmp) / "agent.sock"
        app = create_app(settings.model_copy(update={"agent_token": SecretStr(token)}))
        server = uvicorn.Server(uvicorn.Config(app, uds=str(socket), log_level=settings.log_level))
        serving = asyncio.create_task(server.serve())
        agent = asyncio.create_task(EdgeAgent(settings, key, socket, token).run())
        await asyncio.wait([serving, agent], return_when=asyncio.FIRST_COMPLETED)
        agent.cancel()
        server.should_exit = True
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.gather(serving, agent)
