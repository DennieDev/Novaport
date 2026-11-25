# SPDX-License-Identifier: AGPL-3.0-or-later
import asyncio
import contextlib
import hmac
import json
import logging
import os
import re
import shlex
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any

import aiodocker
from fastapi import Depends, FastAPI, HTTPException, Query, Request, WebSocket, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.requests import HTTPConnection
from starlette.websockets import WebSocketDisconnect

from swarmer import __version__
from swarmer.agent import paths
from swarmer.agent.node import AiodockerNode, NodeDocker
from swarmer.config import Settings, get_settings
from swarmer.server.api.health import Health

log = logging.getLogger(__name__)

CONTAINER_ID = re.compile(r"^[0-9a-f]{12,64}$")
VOLUME_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
MAX_PATHS = 500

Tail = Annotated[int, Query(ge=1, le=10_000)]


class AgentInfo(BaseModel):
    node_id: str | None
    hostname: str
    version: str


class PathsRequest(BaseModel):
    paths: list[str] = Field(max_length=MAX_PATHS)


class Usage(BaseModel):
    count: int
    size: int
    reclaimable: int | None = None


class Filesystem(BaseModel):
    total: int
    used: int
    free: int


class DiskUsage(BaseModel):
    filesystem: Filesystem | None
    images: Usage
    containers: Usage
    volumes: Usage
    build_cache: Usage


class Image(BaseModel):
    id: str
    tags: list[str]
    size: int
    created: int
    containers: int


class Volume(BaseModel):
    name: str
    driver: str
    mountpoint: str
    size: int | None
    containers: int | None
    labels: dict[str, str]


class VolumeFiles(BaseModel):
    path: str
    entries: list[paths.DirEntry]
    truncated: bool


class LogLines(BaseModel):
    lines: list[str]


def _token_ok(conn: HTTPConnection) -> bool:
    expected = conn.app.state.token
    header = conn.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    return scheme.lower() == "bearer" and hmac.compare_digest(token.encode(), expected.encode())


async def require_token(request: Request) -> None:
    if not _token_ok(request):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid agent token.")


def _container_id(value: str) -> str:
    if not CONTAINER_ID.match(value):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Invalid container ID.")
    return value


def _usage(items: list[dict[str, Any]], size_key: str = "Size") -> Usage:
    return Usage(count=len(items), size=sum(max(i.get(size_key) or 0, 0) for i in items))


def create_app(settings: Settings | None = None, node: NodeDocker | None = None) -> FastAPI:
    settings = settings or get_settings()
    if settings.agent_token is None or not settings.agent_token.get_secret_value():
        raise RuntimeError("SWARMER_AGENT_TOKEN must be set to run the agent.")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        docker = None
        if node is None:
            docker = aiodocker.Docker(url=settings.docker_host)
            app.state.node = AiodockerNode(docker)
        else:
            app.state.node = node
        try:
            yield
        finally:
            if docker is not None:
                await docker.close()

    app = FastAPI(
        title="Swarmer agent",
        version=__version__,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.settings = settings
    app.state.token = settings.agent_token.get_secret_value()
    root = settings.host_root
    auth = [Depends(require_token)]

    @app.exception_handler(aiodocker.DockerError)
    async def docker_error(_: Request, exc: aiodocker.DockerError) -> JSONResponse:
        code = exc.status if exc.status in (404, 409) else status.HTTP_502_BAD_GATEWAY
        return JSONResponse({"detail": exc.message}, status_code=code)

    def node_docker() -> NodeDocker:
        result: NodeDocker = app.state.node
        return result

    @app.get("/healthz")
    async def healthz() -> Health:
        return Health(status="ok", version=__version__)

    @app.get("/info", dependencies=auth)
    async def info() -> AgentInfo:
        data = await node_docker().info()
        return AgentInfo(
            node_id=(data.get("Swarm") or {}).get("NodeID") or None,
            hostname=data.get("Name", ""),
            version=__version__,
        )

    @app.post("/paths", dependencies=auth)
    async def check_paths(body: PathsRequest) -> list[paths.PathInfo]:
        return await asyncio.to_thread(lambda: [paths.check(root, p) for p in body.paths])

    @app.get("/disk", dependencies=auth)
    async def disk() -> DiskUsage:
        df = await node_docker().disk_usage()
        filesystem = None
        with contextlib.suppress(OSError):
            st = os.statvfs(root)
            total = st.f_blocks * st.f_frsize
            free = st.f_bavail * st.f_frsize
            filesystem = Filesystem(total=total, used=total - st.f_bfree * st.f_frsize, free=free)
        volumes = [v.get("UsageData") or {} for v in df.get("Volumes") or []]
        return DiskUsage(
            filesystem=filesystem,
            images=_usage(df.get("Images") or []),
            containers=_usage(df.get("Containers") or [], "SizeRw"),
            volumes=_usage(volumes),
            build_cache=_usage(df.get("BuildCache") or []),
        )

    @app.get("/images", dependencies=auth)
    async def images() -> list[Image]:
        df = await node_docker().disk_usage()
        result = [
            Image(
                id=i["Id"],
                tags=[t for t in i.get("RepoTags") or [] if t != "<none>:<none>"],
                size=i.get("Size", 0),
                created=i.get("Created", 0),
                containers=max(i.get("Containers", 0), 0),
            )
            for i in df.get("Images") or []
        ]
        result.sort(key=lambda i: i.size, reverse=True)
        return result

    @app.get("/volumes", dependencies=auth)
    async def volumes() -> list[Volume]:
        df = await node_docker().disk_usage()
        result = []
        for v in df.get("Volumes") or []:
            usage = v.get("UsageData") or {}
            size, refs = usage.get("Size", -1), usage.get("RefCount", -1)
            result.append(
                Volume(
                    name=v["Name"],
                    driver=v.get("Driver", ""),
                    mountpoint=v.get("Mountpoint", ""),
                    size=size if size >= 0 else None,
                    containers=refs if refs >= 0 else None,
                    labels=v.get("Labels") or {},
                )
            )
        result.sort(key=lambda v: v.name)
        return result

    @app.get("/volumes/{name}/files", dependencies=auth)
    async def volume_files(name: str, path: str = "/") -> VolumeFiles:
        if not VOLUME_NAME.match(name):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Invalid volume name.")
        volume = await node_docker().volume(name)
        if volume.get("Driver") != "local":
            raise HTTPException(status.HTTP_409_CONFLICT, "Only local volumes can be browsed.")
        relative = "/".join(p for p in path.split("/") if p not in ("", ".", ".."))
        mountpoint = volume["Mountpoint"].rstrip("/")
        target = f"{mountpoint}/{relative}" if relative else mountpoint
        # Resolve the mountpoint first so links inside the volume can't climb out of it.
        base = paths.resolve(root, mountpoint)
        resolved = paths.resolve(root, target)
        if resolved != base and base not in resolved.parents:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Path leaves the volume.")
        try:
            entries, truncated = await asyncio.to_thread(paths.list_dir, root, target)
        except FileNotFoundError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No such directory.") from exc
        except NotADirectoryError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, "Not a directory.") from exc
        return VolumeFiles(path="/" + relative, entries=entries, truncated=truncated)

    @app.get("/containers/{container_id}/logs", dependencies=auth)
    async def container_logs(
        container_id: str, tail: Tail = 200, timestamps: bool = True
    ) -> LogLines:
        chunks = await node_docker().logs(
            _container_id(container_id), tail=tail, timestamps=timestamps
        )
        return LogLines(lines="".join(chunks).splitlines())

    @app.websocket("/containers/{container_id}/logs/follow")
    async def follow_logs(
        websocket: WebSocket, container_id: str, tail: Tail = 100, timestamps: bool = True
    ) -> None:
        if not _token_ok(websocket) or not CONTAINER_ID.match(container_id):
            await websocket.close(code=1008)
            return
        await websocket.accept()
        try:
            async for chunk in node_docker().follow_logs(
                container_id, tail=tail, timestamps=timestamps
            ):
                await websocket.send_text(chunk)
        except WebSocketDisconnect:
            return
        except aiodocker.DockerError as exc:
            await websocket.close(code=1011, reason=exc.message[:120])
            return
        with contextlib.suppress(RuntimeError):
            await websocket.close()

    @app.websocket("/containers/{container_id}/exec")
    async def exec_shell(websocket: WebSocket, container_id: str, cmd: str = "/bin/sh") -> None:
        if not _token_ok(websocket) or not CONTAINER_ID.match(container_id):
            await websocket.close(code=1008)
            return
        await websocket.accept()
        try:
            execution, stream = await node_docker().exec(container_id, shlex.split(cmd))
        except aiodocker.DockerError as exc:
            await websocket.close(code=1011, reason=exc.message[:120])
            return

        async def output() -> None:
            while message := await stream.read_out():
                await websocket.send_bytes(message.data)

        async def input() -> None:
            while True:
                event = json.loads(await websocket.receive_text())
                if event.get("type") == "input":
                    await stream.write_in(str(event.get("data", "")).encode())
                elif event.get("type") == "resize":
                    await execution.resize(h=int(event["rows"]), w=int(event["cols"]))

        async with stream:
            tasks = [asyncio.create_task(output()), asyncio.create_task(input())]
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            for task in [*done, *pending]:
                with contextlib.suppress(
                    asyncio.CancelledError, WebSocketDisconnect, ValueError, KeyError
                ):
                    await task
        with contextlib.suppress(RuntimeError):
            await websocket.close()

    return app
