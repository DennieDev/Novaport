# SPDX-License-Identifier: AGPL-3.0-or-later
"""Finds the agents running on each node and talks to them."""

import asyncio
import logging
import socket
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
from websockets.asyncio.client import ClientConnection, connect

from swarmer.config import Settings

log = logging.getLogger(__name__)

CACHE_SECONDS = 30.0
TIMEOUT = 10.0


class AgentUnavailable(Exception):
    pass


class AgentError(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


@dataclass(frozen=True)
class Agent:
    url: str
    node_id: str | None
    hostname: str | None
    version: str | None
    error: str | None = None


class Agents(Protocol):
    @property
    def enabled(self) -> bool: ...
    async def list(self, refresh: bool = False) -> list[Agent]: ...
    async def get(self, node_id: str, path: str, params: dict[str, Any] | None = None) -> Any: ...
    async def post(self, node_id: str, path: str, body: Any) -> Any: ...
    def websocket(self, node_id: str, path: str) -> Any: ...


class AgentPool:
    def __init__(self, settings: Settings) -> None:
        token = settings.agent_token.get_secret_value() if settings.agent_token else ""
        self.token = token
        self.port = settings.agent_port
        self.dns = settings.agent_dns
        self.static = [
            u.strip().rstrip("/") for u in (settings.agent_urls or "").split(",") if u.strip()
        ]
        self.http = httpx.AsyncClient(timeout=TIMEOUT, headers=self._headers())
        self._cache: list[Agent] = []
        self._cached_at = 0.0
        self._lock = asyncio.Lock()

    @property
    def enabled(self) -> bool:
        return bool(self.token and (self.dns or self.static))

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    async def close(self) -> None:
        await self.http.aclose()

    async def _urls(self) -> list[str]:
        if self.static:
            return self.static
        if not self.dns:
            return []
        loop = asyncio.get_running_loop()
        try:
            infos = await loop.getaddrinfo(self.dns, self.port, type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            log.warning("Could not resolve agents at %s: %s", self.dns, exc)
            return []
        addresses = sorted({str(info[4][0]) for info in infos})
        return [
            f"http://[{a}]:{self.port}" if ":" in a else f"http://{a}:{self.port}"
            for a in addresses
        ]

    async def _probe(self, url: str) -> Agent:
        try:
            response = await self.http.get(f"{url}/info")
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            return Agent(url, None, None, None, error=str(exc) or type(exc).__name__)
        return Agent(url, data.get("node_id"), data.get("hostname"), data.get("version"))

    async def list(self, refresh: bool = False) -> list[Agent]:
        if not self.enabled:
            return []
        async with self._lock:
            if refresh or time.monotonic() - self._cached_at > CACHE_SECONDS:
                urls = await self._urls()
                self._cache = list(await asyncio.gather(*(self._probe(u) for u in urls)))
                self._cached_at = time.monotonic()
            return self._cache

    async def _for_node(self, node_id: str) -> Agent:
        for refresh in (False, True):
            for agent in await self.list(refresh=refresh):
                if agent.node_id == node_id and agent.error is None:
                    return agent
        raise AgentUnavailable(f"No agent is reachable on node {node_id}.")

    async def _request(self, method: str, node_id: str, path: str, **kwargs: Any) -> Any:
        agent = await self._for_node(node_id)
        try:
            response = await self.http.request(method, f"{agent.url}{path}", **kwargs)
        except httpx.HTTPError as exc:
            self._cached_at = 0.0
            raise AgentUnavailable(f"Agent on {agent.hostname or node_id}: {exc}") from exc
        if response.is_error:
            try:
                detail = response.json().get("detail", response.text)
            except ValueError:
                detail = response.text
            raise AgentError(response.status_code, str(detail))
        return response.json()

    async def get(self, node_id: str, path: str, params: dict[str, Any] | None = None) -> Any:
        return await self._request("GET", node_id, path, params=params)

    async def post(self, node_id: str, path: str, body: Any) -> Any:
        return await self._request("POST", node_id, path, json=body)

    @asynccontextmanager
    async def websocket(self, node_id: str, path: str) -> AsyncIterator[ClientConnection]:
        agent = await self._for_node(node_id)
        url = "ws" + agent.url.removeprefix("http") + path
        async with connect(
            url, additional_headers=self._headers(), open_timeout=TIMEOUT, max_size=2**22
        ) as ws:
            yield ws
