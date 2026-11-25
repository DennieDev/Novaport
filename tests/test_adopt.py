# SPDX-License-Identifier: AGPL-3.0-or-later
import socket
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from swarmer.models import AuditEntry, Deployment, Stack
from swarmer.server.app import create_app
from swarmer.sops import Sops, generate_age_key
from swarmer.stacks.adopt import compose_project, duration, looks_secret, size, swarm_compose
from tests.conftest import SOPS_BINARY, client_for, make_settings
from tests.fakes import FakeReader
from tests.gitrepo import Repo
from tests.test_pipeline import FakeCli

S = 1_000_000_000

SWARM_SERVICE: dict[str, Any] = {
    "ID": "svc-api",
    "Spec": {
        "Name": "shop_api",
        "Labels": {"com.docker.stack.namespace": "shop", "traefik.enable": "true"},
        "TaskTemplate": {
            "ContainerSpec": {
                "Image": "ghcr.io/acme/api:2.4.1@sha256:" + "a" * 64,
                "Labels": {"com.docker.stack.namespace": "shop"},
                "Args": ["serve", "--port", "8080"],
                "Env": ["MODE=prod", "EMPTY=", "PRICE=$5"],
                "Mounts": [
                    {"Type": "volume", "Source": "shop_data", "Target": "/data"},
                    {"Type": "volume", "Source": "shared-cache", "Target": "/cache"},
                    {"Type": "bind", "Source": "/srv/shop", "Target": "/srv", "ReadOnly": True},
                ],
                "Secrets": [
                    {
                        "SecretName": "shop_db_password",
                        "File": {"Name": "db_password", "UID": "0", "GID": "0", "Mode": 292},
                    },
                    {"SecretName": "tls_cert", "File": {"Name": "/certs/tls.pem"}},
                ],
                "Healthcheck": {
                    "Test": ["CMD", "wget", "-q", "-O-", "http://localhost:8080/health"],
                    "Interval": 30 * S,
                    "Timeout": 5 * S,
                    "Retries": 3,
                },
                "StopGracePeriod": 90 * S,
            },
            "Resources": {"Limits": {"NanoCPUs": 500_000_000, "MemoryBytes": 512 * 1024**2}},
            "RestartPolicy": {"Condition": "on-failure", "Delay": 5 * S, "MaxAttempts": 3},
            "Placement": {
                "Constraints": ["node.role == worker"],
                "Preferences": [{"Spread": {"SpreadDescriptor": "node.labels.zone"}}],
            },
            "Networks": [
                {"Target": "net-default", "Aliases": ["api"]},
                {"Target": "net-proxy", "Aliases": ["api"]},
            ],
        },
        "Mode": {"Replicated": {"Replicas": 3}},
        "UpdateConfig": {"Parallelism": 1, "FailureAction": "rollback", "Order": "start-first"},
        "EndpointSpec": {
            "Mode": "vip",
            "Ports": [
                {"Protocol": "tcp", "TargetPort": 8080, "PublishedPort": 8080},
                {"Protocol": "udp", "TargetPort": 53, "PublishedPort": 53, "PublishMode": "host"},
            ],
        },
    },
}

NETWORKS = [
    {"Id": "net-default", "Name": "shop_default", "Driver": "overlay"},
    {"Id": "net-proxy", "Name": "proxy", "Driver": "overlay", "Attachable": True},
]


def test_durations_and_sizes() -> None:
    assert duration(90 * S) == "1m30s"
    assert duration(1500 * 1_000_000) == "1s500ms"
    assert duration(3600 * S) == "1h"
    assert duration(None) is None
    assert size(512 * 1024**2) == "512m"
    assert size(2 * 1024**3) == "2g"
    assert size(1000) == "1000b"


def test_swarm_service_becomes_compose() -> None:
    generated = swarm_compose("shop", [SWARM_SERVICE], NETWORKS)
    doc = yaml.safe_load(generated.compose)
    api = doc["services"]["api"]
    assert api["image"] == "ghcr.io/acme/api:2.4.1"
    assert api["command"] == ["serve", "--port", "8080"]
    assert api["environment"] == {"MODE": "prod", "EMPTY": "", "PRICE": "$$5"}
    assert "labels" not in api
    assert api["volumes"] == [
        {"type": "volume", "target": "/data", "source": "data"},
        {"type": "volume", "target": "/cache", "source": "shared-cache"},
        {"type": "bind", "target": "/srv", "source": "/srv/shop", "read_only": True},
    ]
    assert api["secrets"] == ["db_password", {"source": "tls_cert", "target": "/certs/tls.pem"}]
    assert api["healthcheck"]["interval"] == "30s"
    assert api["stop_grace_period"] == "1m30s"
    assert api["networks"] == ["default", "proxy"]
    assert api["ports"] == [
        {"target": 8080, "published": 8080},
        {"target": 53, "published": 53, "protocol": "udp", "mode": "host"},
    ]
    deploy = api["deploy"]
    assert deploy["replicas"] == 3
    assert deploy["labels"] == {"traefik.enable": "true"}
    assert deploy["resources"] == {"limits": {"cpus": "0.5", "memory": "512m"}}
    assert deploy["restart_policy"] == {"condition": "on-failure", "delay": "5s", "max_attempts": 3}
    assert deploy["placement"] == {
        "constraints": ["node.role == worker"],
        "preferences": [{"spread": "node.labels.zone"}],
    }
    assert deploy["update_config"] == {"failure_action": "rollback", "order": "start-first"}

    assert doc["volumes"] == {"data": None, "shared-cache": {"external": True}}
    assert doc["networks"] == {"proxy": {"external": True}}
    assert doc["secrets"] == {
        "db_password": {"external": True, "name": "shop_db_password"},
        "tls_cert": {"external": True, "name": "tls_cert"},
    }
    assert any("secrets.env" in n for n in generated.notes)


def test_global_service_and_digest_only_image() -> None:
    service = {
        "Spec": {
            "Name": "mon_exporter",
            "TaskTemplate": {"ContainerSpec": {"Image": "prom/node-exporter@sha256:" + "b" * 64}},
            "Mode": {"Global": {}},
        }
    }
    doc = yaml.safe_load(swarm_compose("mon", [service], []).compose)
    exporter = doc["services"]["exporter"]
    assert exporter["image"] == "prom/node-exporter@sha256:" + "b" * 64
    assert exporter["deploy"] == {"mode": "global"}
    assert set(doc) == {"services"}


BLOG_LABELS = {
    "com.docker.compose.project": "blog",
    "com.docker.compose.service": "web",
    "com.docker.compose.depends_on": "db:service_healthy:false",
    "maintainer": "someone",
}
# Inspect output, plus the top-level Labels the container list has, so the fake can list it.
CONTAINER: dict[str, Any] = {
    "Id": "c1",
    "Name": "/blog-web-1",
    "Labels": BLOG_LABELS,
    "Config": {"Image": "nginx:1.27", "Env": ["PATH=/usr/bin", "SITE=blog"], "Labels": BLOG_LABELS},
    "HostConfig": {
        "RestartPolicy": {"Name": "unless-stopped"},
        "PortBindings": {"80/tcp": [{"HostIp": "", "HostPort": "8080"}]},
        "LogConfig": {"Type": "json-file"},
    },
    "Mounts": [
        {"Type": "volume", "Name": "blog_html", "Destination": "/usr/share/nginx/html", "RW": True},
        {"Type": "bind", "Source": "/etc/blog", "Destination": "/etc/nginx/conf.d", "RW": False},
    ],
    "NetworkSettings": {"Networks": {"blog_default": {}, "proxy": {}}},
}


def test_compose_project_from_containers() -> None:
    generated = compose_project("blog", [CONTAINER, {**CONTAINER, "Id": "c2"}])
    doc = yaml.safe_load(generated.compose)
    web = doc["services"]["web"]
    assert web["image"] == "nginx:1.27"
    assert web["environment"] == {"SITE": "blog"}
    assert web["labels"] == {"maintainer": "someone"}
    assert web["restart"] == "unless-stopped"
    assert web["depends_on"] == {"db": {"condition": "service_healthy"}}
    assert web["ports"] == [{"target": 80, "published": "8080"}]
    assert web["volumes"] == [
        {"type": "volume", "target": "/usr/share/nginx/html", "source": "html"},
        {"type": "bind", "target": "/etc/nginx/conf.d", "source": "/etc/blog", "read_only": True},
    ]
    assert web["networks"] == ["default", "proxy"]
    assert web["deploy"] == {"replicas": 2}
    assert doc["volumes"] == {"html": None}
    assert doc["networks"] == {"proxy": {"external": True}}


PASSWORD = "correct horse battery"


@pytest.fixture
async def app(clean_db: str, tmp_path: Path) -> AsyncIterator[tuple[httpx.AsyncClient, Repo]]:
    origin = Repo(tmp_path / "origin")
    origin.write("stacks/shop/compose.yaml", "services:\n  web:\n    image: nginx\n")
    origin.commit("Add shop")
    reader = FakeReader()
    legacy_spec = {
        **SWARM_SERVICE["Spec"],
        "Name": "legacy_api",
        "Labels": {"com.docker.stack.namespace": "legacy"},
    }
    shop_spec = {"Name": "shop_web", "Labels": {"com.docker.stack.namespace": "shop"}}
    reader.service_list = [{"ID": "s1", "Spec": legacy_spec}, {"ID": "s2", "Spec": shop_spec}]
    reader.network_list = NETWORKS
    reader.container_list = [CONTAINER]
    settings = make_settings(
        database_url=SecretStr(clean_db), initial_admin_password=SecretStr(PASSWORD)
    )
    async for client in client_for(create_app(settings, reader=reader, cli=FakeCli())):
        response = await client.post(
            "/api/auth/login", json={"username": "admin", "password": PASSWORD}
        )
        assert response.status_code == 204
        response = await client.post(
            "/api/repositories", json={"name": "infra", "url": origin.url, "poll_interval": 0}
        )
        assert response.status_code == 201, response.text
        await client.post(f"/api/repositories/{response.json()['id']}/sync")
        yield client, origin


@pytest.mark.postgres
async def test_lists_only_unmanaged_stacks(app: tuple[httpx.AsyncClient, Repo]) -> None:
    client, _ = app
    found = (await client.get("/api/unmanaged")).json()
    assert [(u["name"], u["type"], u["services"]) for u in found] == [
        ("blog", "compose", 1),
        ("legacy", "swarm", 1),
    ]


@pytest.mark.postgres
async def test_adopting_commits_the_stack_for_manual_deploys(
    app: tuple[httpx.AsyncClient, Repo], clean_db: str
) -> None:
    client, origin = app
    generated = (await client.get("/api/unmanaged/swarm/legacy")).json()
    assert "ghcr.io/acme/api:2.4.1" in generated["compose"]

    repos = (await client.get("/api/repositories")).json()
    response = await client.post(
        "/api/unmanaged/swarm/legacy/adopt",
        json={"repository_id": repos[0]["id"], "compose": generated["compose"]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["queued_deployments"] == 0

    assert origin.show("stacks/legacy/compose.yaml") == generated["compose"]
    assert yaml.safe_load(origin.show("stacks/legacy/swarmer.yaml")) == {"deploy": "manual"}
    assert origin.log("%an") == "admin"
    assert [u["name"] for u in (await client.get("/api/unmanaged")).json()] == ["blog"]

    engine = create_async_engine(clean_db)
    try:
        async with async_sessionmaker(engine)() as db:
            stack = await db.scalar(select(Stack).where(Stack.name == "legacy"))
            assert stack is not None
            assert stack.deploy_mode == "manual"
            # Committed as generated, so what git says is what runs.
            assert stack.last_good_revision_id is not None
            assert stack.deployed_spec is not None
            assert "api" in stack.deployed_spec
            adopted = await db.scalar(select(Deployment).where(Deployment.stack_id == stack.id))
            assert adopted is not None
            assert (adopted.status, adopted.trigger) == ("succeeded", "adopt")
            assert adopted.revision_id == stack.last_good_revision_id
            entry = await db.scalar(select(AuditEntry).where(AuditEntry.action == "stack.adopt"))
            assert entry is not None
            assert entry.target == "legacy"
    finally:
        await engine.dispose()

    again = await client.post(
        "/api/unmanaged/swarm/legacy/adopt",
        json={"repository_id": repos[0]["id"], "compose": generated["compose"]},
    )
    assert again.status_code == 409


@pytest.mark.postgres
async def test_an_edited_file_is_not_marked_as_running(
    app: tuple[httpx.AsyncClient, Repo], clean_db: str
) -> None:
    client, _ = app
    generated = (await client.get("/api/unmanaged/swarm/legacy")).json()
    edited = generated["compose"].replace("MODE: prod", "MODE: staging")
    assert edited != generated["compose"]
    repos = (await client.get("/api/repositories")).json()
    response = await client.post(
        "/api/unmanaged/swarm/legacy/adopt",
        json={"repository_id": repos[0]["id"], "compose": edited},
    )
    assert response.status_code == 200, response.text

    engine = create_async_engine(clean_db)
    try:
        async with async_sessionmaker(engine)() as db:
            stack = await db.scalar(select(Stack).where(Stack.name == "legacy"))
            assert stack is not None
            assert stack.last_good_revision_id is None
            assert await db.scalar(select(Deployment)) is None
    finally:
        await engine.dispose()


@pytest.mark.postgres
async def test_adopting_a_compose_project_sets_its_type(
    app: tuple[httpx.AsyncClient, Repo],
) -> None:
    client, origin = app
    generated = (await client.get("/api/unmanaged/compose/blog")).json()
    repos = (await client.get("/api/repositories")).json()
    response = await client.post(
        "/api/unmanaged/compose/blog/adopt",
        json={"repository_id": repos[0]["id"], "compose": generated["compose"], "deploy": "auto"},
    )
    assert response.status_code == 200, response.text
    assert yaml.safe_load(origin.show("stacks/blog/swarmer.yaml")) == {
        "deploy": "auto",
        "type": "compose",
    }


def test_secret_looking_variables() -> None:
    for name, value in [
        ("POSTGRES_PASSWORD", "x"),
        ("SWARMER_SECRET_KEY", "x"),
        ("SWARMER_AGE_KEY", "x"),
        ("API_KEY", "x"),
        ("GITHUB_TOKEN", "x"),
        ("DATABASE_URL", "postgresql://app:hunter2@db/app"),
    ]:
        assert looks_secret(name, value), name
    for name, value in [
        ("POSTGRES_PASSWORD_FILE", "/run/secrets/pw"),
        ("POSTGRES_PASSWORD", ""),
        ("KEYBOARD", "us"),
        ("PUBLIC_URL", "https://example.com"),
        ("DATABASE_URL", "postgresql://db/app"),
    ]:
        assert not looks_secret(name, value), name


def _service(name: str, env: list[str], order: str = "stop-first") -> dict[str, Any]:
    return {
        "Spec": {
            "Name": f"app_{name}",
            "Labels": {"com.docker.stack.namespace": "app"},
            "TaskTemplate": {"ContainerSpec": {"Image": "app:1", "Env": env}},
            "UpdateConfig": {"Order": order},
        }
    }


def test_secrets_move_out_of_the_compose_file() -> None:
    generated = swarm_compose(
        "app",
        [
            _service("server", ["SECRET_KEY=s$cret", "TOKEN=one", "MODE=prod"]),
            _service("worker", ["SECRET_KEY=s$cret", "TOKEN=two"]),
        ],
        [],
        move_secrets=True,
        start_first=["app_server"],
    )
    services = yaml.safe_load(generated.compose)["services"]
    assert services["server"]["environment"] == {
        "SECRET_KEY": "${SECRET_KEY}",
        "TOKEN": "one",
        "MODE": "prod",
    }
    assert services["worker"]["environment"]["SECRET_KEY"] == "${SECRET_KEY}"
    assert generated.secrets == {"SECRET_KEY": "s$cret"}
    assert any("TOKEN" in n and "different values" in n for n in generated.notes)
    assert services["server"]["deploy"]["update_config"] == {"order": "start-first"}
    assert "deploy" not in services["worker"]


def test_secrets_stay_without_an_age_key() -> None:
    generated = swarm_compose("app", [_service("server", ["SECRET_KEY=x"])], [])
    assert generated.secrets == {}
    assert yaml.safe_load(generated.compose)["services"]["server"]["environment"] == {
        "SECRET_KEY": "x"
    }


OWN_SERVICES = [
    {
        "ID": "np1",
        "Spec": {
            **_service("server", ["SWARMER_SECRET_KEY=k3y", "SWARMER_LOG_LEVEL=info"])["Spec"],
            "Name": "swarmer_server",
            "Labels": {"com.docker.stack.namespace": "swarmer"},
        },
    }
]


@pytest.fixture
async def own_app(
    clean_db: str, tmp_path: Path
) -> AsyncIterator[tuple[httpx.AsyncClient, Repo, Sops]]:
    """Swarmer running in the stack `swarmer`, with an age key."""
    age_key = generate_age_key()
    origin = Repo(tmp_path / "origin")
    origin.write("README.md", "infra")
    origin.commit("Start")
    reader = FakeReader()
    reader.service_list = OWN_SERVICES
    reader.container_inspect = {
        socket.gethostname(): {
            "Config": {
                "Labels": {
                    "com.docker.stack.namespace": "swarmer",
                    "com.docker.swarm.service.name": "swarmer_server",
                }
            }
        }
    }
    settings = make_settings(
        database_url=SecretStr(clean_db),
        initial_admin_password=SecretStr(PASSWORD),
        age_key=SecretStr(age_key),
        sops_binary=SOPS_BINARY or "sops",
    )
    async for client in client_for(create_app(settings, reader=reader, cli=FakeCli())):
        await client.post("/api/auth/login", json={"username": "admin", "password": PASSWORD})
        response = await client.post(
            "/api/repositories", json={"name": "infra", "url": origin.url, "poll_interval": 0}
        )
        await client.post(f"/api/repositories/{response.json()['id']}/sync")
        yield client, origin, Sops(SOPS_BINARY or "sops", age_key)


@pytest.mark.postgres
@pytest.mark.sops
async def test_adopting_swarmer_itself_keeps_its_secrets_out_of_git(
    own_app: tuple[httpx.AsyncClient, Repo, Sops],
) -> None:
    client, origin, sops = own_app
    generated = (await client.get("/api/unmanaged/swarm/swarmer")).json()
    assert generated["own"] is True
    assert generated["secrets"] == ["SWARMER_SECRET_KEY"]
    assert "k3y" not in generated["compose"]
    assert "Swarmer itself runs in" in generated["notes"][0]
    server = yaml.safe_load(generated["compose"])["services"]["server"]
    assert server["deploy"]["update_config"]["order"] == "start-first"

    repos = (await client.get("/api/repositories")).json()
    response = await client.post(
        "/api/unmanaged/swarm/swarmer/adopt",
        json={"repository_id": repos[0]["id"], "compose": generated["compose"]},
    )
    assert response.status_code == 200, response.text

    assert "k3y" not in origin.show("stacks/swarmer/compose.yaml")
    encrypted = origin.show("stacks/swarmer/secrets.env")
    assert "k3y" not in encrypted
    assert await sops.decrypt(encrypted.encode()) == {"SWARMER_SECRET_KEY": "k3y"}
