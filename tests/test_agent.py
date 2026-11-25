# SPDX-License-Identifier: AGPL-3.0-or-later
import os
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from swarmer.agent import paths
from swarmer.agent.app import create_app
from tests.conftest import client_for, make_settings

TOKEN = "agent-token-for-tests"
CONTAINER = "a" * 64


@pytest.fixture
def host(tmp_path: Path) -> Path:
    root = tmp_path / "host"
    (root / "srv" / "www").mkdir(parents=True)
    (root / "srv" / "www" / "index.html").write_text("hi")
    (root / "etc").mkdir()
    (root / "etc" / "app.conf").write_text("x=1")
    (root / "srv" / "conf").symlink_to("/etc")
    (root / "srv" / "rel").symlink_to("www")
    (root / "srv" / "loop").symlink_to("loop")
    volume = root / "var" / "lib" / "docker" / "volumes" / "data" / "_data"
    volume.mkdir(parents=True)
    (volume / "db.sqlite").write_text("db")
    (volume / "sub").mkdir()
    (volume / "escape").symlink_to("/etc")
    return root


def test_resolve_follows_links_inside_the_root(host: Path) -> None:
    assert paths.resolve(host, "/srv/www/index.html") == host / "srv/www/index.html"
    assert paths.resolve(host, "/srv/rel/index.html") == host / "srv/www/index.html"
    # Absolute link targets mean the host's /etc, not the agent container's.
    assert paths.resolve(host, "/srv/conf/app.conf") == host / "etc/app.conf"


def test_resolve_never_leaves_the_root(host: Path) -> None:
    assert paths.resolve(host, "/../../../etc/app.conf") == host / "etc/app.conf"
    assert paths.resolve(host, "/srv/www/../../..") == host


def test_resolve_rejects_bad_input(host: Path) -> None:
    with pytest.raises(paths.InvalidPath):
        paths.resolve(host, "relative/path")
    with pytest.raises(paths.InvalidPath, match="symbolic links"):
        paths.resolve(host, "/srv/loop")


def test_check(host: Path) -> None:
    directory = paths.check(host, "/srv/www")
    assert directory.exists
    assert directory.kind == "directory"
    linked = paths.check(host, "/srv/conf/app.conf")
    assert linked.kind == "file"
    assert linked.resolved == "/etc/app.conf"
    missing = paths.check(host, "/srv/nope")
    assert not missing.exists
    assert missing.resolved == "/srv/nope"
    assert paths.check(host, "nope").error is not None


class FakeNode:
    def __init__(self, host: Path) -> None:
        self.host = host
        self.exec_calls: list[tuple[str, list[str]]] = []

    async def info(self) -> Mapping[str, Any]:
        return {"Name": "node-a", "Swarm": {"NodeID": "n1"}}

    async def disk_usage(self) -> Mapping[str, Any]:
        return {
            "Images": [
                {
                    "Id": "sha256:1",
                    "RepoTags": ["nginx:alpine"],
                    "Size": 50,
                    "Created": 1,
                    "Containers": 2,
                },
                {
                    "Id": "sha256:2",
                    "RepoTags": ["<none>:<none>"],
                    "Size": 500,
                    "Created": 2,
                    "Containers": -1,
                },
            ],
            "Containers": [{"SizeRw": 10}, {"SizeRw": None}],
            "Volumes": [
                {
                    "Name": "data",
                    "Driver": "local",
                    "Mountpoint": "/var/lib/docker/volumes/data/_data",
                    "UsageData": {"Size": 2, "RefCount": 1},
                }
            ],
            "BuildCache": [],
        }

    async def volume(self, name: str) -> Mapping[str, Any]:
        return {
            "Name": name,
            "Driver": "local" if name == "data" else "nfs",
            "Mountpoint": f"/var/lib/docker/volumes/{name}/_data",
        }

    async def container(self, container_id: str) -> Mapping[str, Any]:
        return {"Id": container_id}

    async def logs(self, container_id: str, *, tail: int, timestamps: bool) -> list[str]:
        return ["one\ntwo\n"]

    async def follow_logs(
        self, container_id: str, *, tail: int, timestamps: bool
    ) -> AsyncIterator[str]:
        yield "one\n"

    async def exec(self, container_id: str, cmd: list[str]) -> Any:
        self.exec_calls.append((container_id, cmd))
        raise NotImplementedError


@pytest.fixture
async def agent(host: Path) -> AsyncIterator[httpx.AsyncClient]:
    settings = make_settings(agent_token=SecretStr(TOKEN), host_root=host)
    app = create_app(settings, node=FakeNode(host))
    async for client in client_for(app):
        client.headers["Authorization"] = f"Bearer {TOKEN}"
        yield client


def test_agent_requires_a_token() -> None:
    with pytest.raises(RuntimeError, match="SWARMER_AGENT_TOKEN"):
        create_app(make_settings(agent_token=None))


async def test_endpoints_need_the_token(agent: httpx.AsyncClient) -> None:
    assert (await agent.get("/healthz", headers={"Authorization": ""})).status_code == 200
    for header in ("", "Bearer wrong", f"Basic {TOKEN}"):
        response = await agent.get("/info", headers={"Authorization": header})
        assert response.status_code == 401


async def test_info(agent: httpx.AsyncClient) -> None:
    data = (await agent.get("/info")).json()
    assert data["node_id"] == "n1"
    assert data["hostname"] == "node-a"


async def test_paths(agent: httpx.AsyncClient) -> None:
    response = await agent.post("/paths", json={"paths": ["/srv/www", "/srv/missing"]})
    result = {r["path"]: r["exists"] for r in response.json()}
    assert result == {"/srv/www": True, "/srv/missing": False}


async def test_disk_and_images(agent: httpx.AsyncClient) -> None:
    disk = (await agent.get("/disk")).json()
    assert disk["images"] == {"count": 2, "size": 550, "reclaimable": None}
    assert disk["containers"]["size"] == 10
    assert disk["filesystem"]["total"] > 0
    images = (await agent.get("/images")).json()
    assert images[0]["tags"] == []
    assert images[0]["containers"] == 0
    assert images[1]["tags"] == ["nginx:alpine"]


async def test_volumes(agent: httpx.AsyncClient) -> None:
    volumes = (await agent.get("/volumes")).json()
    assert volumes == [
        {
            "name": "data",
            "driver": "local",
            "mountpoint": "/var/lib/docker/volumes/data/_data",
            "size": 2,
            "containers": 1,
            "labels": {},
        }
    ]


async def test_volume_files(agent: httpx.AsyncClient) -> None:
    listing = (await agent.get("/volumes/data/files")).json()
    assert [e["name"] for e in listing["entries"]] == ["sub", "db.sqlite", "escape"]
    assert listing["entries"][2]["kind"] == "symlink"
    nested = (await agent.get("/volumes/data/files", params={"path": "/sub"})).json()
    assert nested["path"] == "/sub"
    assert nested["entries"] == []


async def test_volume_files_stay_inside_the_volume(agent: httpx.AsyncClient) -> None:
    climbed = (await agent.get("/volumes/data/files", params={"path": "../../../../.."})).json()
    assert climbed["path"] == "/"
    escaped = await agent.get("/volumes/data/files", params={"path": "/escape"})
    assert escaped.status_code == 403
    assert (
        await agent.get("/volumes/data/files", params={"path": "/db.sqlite"})
    ).status_code == 409
    assert (await agent.get("/volumes/other/files")).status_code == 409
    assert (await agent.get("/volumes/..%2F..%2Fetc/files")).status_code in (404, 422)


async def test_container_logs(agent: httpx.AsyncClient) -> None:
    response = await agent.get(f"/containers/{CONTAINER}/logs")
    assert response.json() == {"lines": ["one", "two"]}
    assert (await agent.get("/containers/../logs")).status_code in (404, 422)
    assert (await agent.get("/containers/not-hex/logs")).status_code == 422


def test_exec_socket_requires_token(host: Path) -> None:
    from starlette.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    settings = make_settings(agent_token=SecretStr(TOKEN), host_root=host)
    with (
        TestClient(create_app(settings, node=FakeNode(host))) as client,
        pytest.raises(WebSocketDisconnect) as exc,
        client.websocket_connect(f"/containers/{CONTAINER}/exec") as ws,
    ):
        ws.receive_text()
    assert exc.value.code == 1008


def test_scandir_limit(host: Path) -> None:
    directory = host / "many"
    directory.mkdir()
    for i in range(5):
        (directory / f"f{i}").write_text("")
    entries, truncated = paths.list_dir(host, "/many", limit=3)
    assert truncated
    assert len(entries) == 3
    assert os.path.exists(directory)
