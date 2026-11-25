# SPDX-License-Identifier: AGPL-3.0-or-later
"""Deploys through the API to a real multi-node Swarm. Start one with tests/integration/swarm.sh."""

import asyncio
import json
import os
import subprocess
import time
import uuid
from collections.abc import AsyncIterator, Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from swarmer.server.app import create_app
from swarmer.sops import Sops, generate_age_key
from tests.conftest import SOPS_BINARY, TEST_DOCKER_HOST, client_for, make_settings
from tests.gitrepo import Repo

DOCKER_HOST = TEST_DOCKER_HOST
AGENT_URLS = os.environ.get("SWARMER_TEST_AGENT_URLS")
AGENT_TOKEN = os.environ.get("SWARMER_TEST_AGENT_TOKEN")
NODE_PREFIX = os.environ.get("SWARMER_SWARM_NAME", "swarmer-swarm")
PASSWORD = "integration admin password"
FINISHED = {"succeeded", "failed", "cancelled"}

pytestmark = [pytest.mark.postgres, pytest.mark.swarm]

needs_agents = pytest.mark.skipif(not AGENT_URLS, reason="no agents in the test Swarm")


def docker(*args: str, check: bool = True) -> str:
    assert DOCKER_HOST
    return subprocess.run(
        ["docker", *args],
        env={**os.environ, "DOCKER_HOST": DOCKER_HOST},
        check=check,
        capture_output=True,
        text=True,
    ).stdout


def on_node(node: str, *args: str) -> None:
    subprocess.run(["docker", "exec", f"{NODE_PREFIX}-{node}", *args], check=True)


def service(stack: str, name: str = "app") -> dict[str, Any]:
    result: dict[str, Any] = json.loads(docker("service", "inspect", f"{stack}_{name}"))[0]
    return result


def compose(command: str = "sleep 3600", replicas: int = 1, extra: str = "", top: str = "") -> str:
    return (
        "services:\n"
        "  app:\n"
        "    image: busybox:1.37\n"
        f"    command: {command}\n"
        "    deploy:\n"
        f"      replicas: {replicas}\n"
        "      update_config:\n"
        "        monitor: 3s\n"
        f"{extra}{top}"
    )


class Swarmer:
    def __init__(self, client: httpx.AsyncClient, origin: Repo, stack: str) -> None:
        self.client = client
        self.origin = origin
        self.stack = stack
        self.repo_id: str | None = None

    def write(self, content: str, file: str = "compose.yaml") -> None:
        self.origin.write(f"stacks/{self.stack}/{file}", content)

    async def add_repository(self, mode: str = "auto") -> None:
        response = await self.client.post(
            "/api/repositories",
            json={
                "name": "infra",
                "url": self.origin.url,
                "poll_interval": 0,
                "default_deploy_mode": mode,
            },
        )
        assert response.status_code == 201, response.text
        self.repo_id = response.json()["id"]

    async def push(self, message: str) -> None:
        self.origin.commit(message)
        response = await self.client.post(f"/api/repositories/{self.repo_id}/sync")
        assert response.status_code == 200, response.text

    async def stack_info(self) -> dict[str, Any]:
        stacks = (await self.client.get("/api/stacks")).json()
        [found] = [s for s in stacks if s["name"] == self.stack]
        result: dict[str, Any] = found
        return result

    async def latest_deployment(self) -> dict[str, Any]:
        info = await self.stack_info()
        deployments = (await self.client.get(f"/api/stacks/{info['id']}/deployments")).json()
        assert deployments, "no deployment was queued"
        result: dict[str, Any] = deployments[0]
        return result

    async def wait(self, deployment_id: str, timeout: float = 180) -> dict[str, Any]:
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            d: dict[str, Any] = (await self.client.get(f"/api/deployments/{deployment_id}")).json()
            if d["status"] in FINISHED:
                return d
            assert asyncio.get_running_loop().time() < deadline, f"stuck at {d['stage']}"
            await asyncio.sleep(1)

    async def deployed(self, timeout: float = 180) -> dict[str, Any]:
        return await self.wait((await self.latest_deployment())["id"], timeout)

    async def deploy(self, revision_id: str | None = None) -> dict[str, Any]:
        info = await self.stack_info()
        response = await self.client.post(
            f"/api/stacks/{info['id']}/deploy", json={"revision_id": revision_id}
        )
        assert response.status_code == 202, response.text
        return await self.wait(response.json()["id"])

    async def until(
        self, check: Callable[[dict[str, Any]], bool], timeout: float = 60
    ) -> dict[str, Any]:
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            info = await self.stack_info()
            if check(info):
                return info
            assert asyncio.get_running_loop().time() < deadline, info
            await asyncio.sleep(1)


NODES = ("manager", "worker1", "worker2")


def _cleanup(stack: str) -> None:
    docker("stack", "rm", stack, check=False)
    docker("compose", "--project-name", stack, "down", "--remove-orphans", check=False)
    # Workers drop a stack's network a while after `stack rm` returns. A new stack created
    # before that can get the same subnet and fail with "Pool overlaps".
    for _ in range(60):
        leftover = [
            node
            for node in NODES
            if subprocess.run(
                [
                    "docker",
                    "exec",
                    f"{NODE_PREFIX}-{node}",
                    "docker",
                    "network",
                    "ls",
                    "--quiet",
                    "--filter",
                    f"label=com.docker.stack.namespace={stack}",
                ],
                capture_output=True,
                text=True,
                check=False,
            ).stdout.strip()
        ]
        if not leftover:
            break
        time.sleep(1)
    secrets = docker(
        "secret", "ls", "--quiet", "--filter", f"label=com.docker.stack.namespace={stack}"
    ).split()
    if secrets:
        docker("secret", "rm", *secrets, check=False)


@pytest.fixture
def sops() -> Sops | None:
    return Sops(SOPS_BINARY, generate_age_key()) if SOPS_BINARY else None


@pytest.fixture
async def swarmer(clean_db: str, tmp_path: Path, sops: Sops | None) -> AsyncIterator[Swarmer]:
    stack = f"it{uuid.uuid4().hex[:8]}"
    settings = make_settings(
        database_url=SecretStr(clean_db),
        initial_admin_password=SecretStr(PASSWORD),
        docker_host=DOCKER_HOST,
        background_jobs=True,
        drift_interval=timedelta(seconds=2),
        deploy_timeout=timedelta(seconds=120),
        agent_urls=AGENT_URLS,
        agent_token=SecretStr(AGENT_TOKEN) if AGENT_TOKEN else None,
        age_key=SecretStr(sops.age_key) if sops and sops.age_key else None,
        sops_binary=SOPS_BINARY or "sops",
    )
    try:
        async for client in client_for(create_app(settings)):
            response = await client.post(
                "/api/auth/login", json={"username": "admin", "password": PASSWORD}
            )
            assert response.status_code == 204, response.text
            yield Swarmer(client, Repo(tmp_path / "origin"), stack)
    finally:
        _cleanup(stack)


async def test_deploys_and_updates_from_git(swarmer: Swarmer) -> None:
    swarmer.write(compose(replicas=3))
    await swarmer.add_repository()
    await swarmer.push("Add app")

    first = await swarmer.deployed()
    assert first["status"] == "succeeded", first["error"]
    svc = service(swarmer.stack)
    assert svc["Spec"]["Mode"]["Replicated"]["Replicas"] == 3
    labels = svc["Spec"]["Labels"]
    assert labels["swarmer.stack"] == swarmer.stack
    assert labels["swarmer.commit"] == swarmer.origin.head()
    tasks = docker(
        "service",
        "ps",
        "--filter",
        "desired-state=running",
        "--format",
        "{{.Node}}",
        f"{swarmer.stack}_app",
    )
    assert len(set(tasks.split())) > 1, "tasks should spread over several nodes"

    swarmer.write(compose(replicas=2, extra="    environment:\n      MODE: prod\n"))
    await swarmer.push("Scale down")
    second = await swarmer.deployed()
    assert second["id"] != first["id"]
    assert second["status"] == "succeeded", second["error"]
    svc = service(swarmer.stack)
    assert svc["Spec"]["Mode"]["Replicated"]["Replicas"] == 2
    assert "MODE=prod" in svc["Spec"]["TaskTemplate"]["ContainerSpec"]["Env"]

    info = await swarmer.stack_info()
    assert info["last_good_revision"]["commit"] == swarmer.origin.head()


async def test_failed_rollout_keeps_desired_state_until_rollback(swarmer: Swarmer) -> None:
    swarmer.write(compose())
    await swarmer.add_repository()
    await swarmer.push("Add app")
    good = await swarmer.deployed()
    assert good["status"] == "succeeded", good["error"]
    good_commit = swarmer.origin.head()

    swarmer.write(compose(command="sh -c 'exit 3'"))
    await swarmer.push("Break it")
    bad = await swarmer.deployed()
    assert bad["status"] == "failed"
    assert bad["stage"] == "converge"

    info = await swarmer.stack_info()
    assert info["latest_revision"]["commit"] == swarmer.origin.head()
    assert info["last_good_revision"]["commit"] == good_commit

    rollback = await swarmer.deploy(info["last_good_revision"]["id"])
    assert rollback["status"] == "succeeded", rollback["error"]
    args = service(swarmer.stack)["Spec"]["TaskTemplate"]["ContainerSpec"]["Args"]
    assert args == ["sleep", "3600"]


async def test_preflight_refuses_missing_external_network(swarmer: Swarmer) -> None:
    swarmer.write(
        compose(
            extra="    networks: [shared]\n",
            top="networks:\n  shared:\n    external: true\n    name: does-not-exist\n",
        )
    )
    await swarmer.add_repository()
    await swarmer.push("Add app")
    d = await swarmer.deployed()
    assert d["status"] == "failed"
    assert d["stage"] == "preflight"
    assert "does-not-exist" in d["error"]
    assert docker("service", "ls", "--quiet", "--filter", f"name={swarmer.stack}_") == ""


async def test_preflight_refuses_a_port_another_stack_publishes(
    swarmer: Swarmer, tmp_path: Path
) -> None:
    other = f"{swarmer.stack}x"
    (tmp_path / "other.yaml").write_text(compose(extra='    ports: ["28431:80"]\n'))
    docker("stack", "deploy", "--detach=true", "-c", str(tmp_path / "other.yaml"), other)
    try:
        swarmer.write(compose(extra='    ports: ["28431:80"]\n'))
        await swarmer.add_repository()
        await swarmer.push("Add app")
        d = await swarmer.deployed()
        assert d["status"] == "failed"
        assert d["stage"] == "preflight"
        assert d["error"] == f"Port 28431/tcp is already published by {other}_app."
    finally:
        _cleanup(other)


@needs_agents
async def test_preflight_checks_bind_mounts_on_every_node(swarmer: Swarmer) -> None:
    path = f"/srv/{swarmer.stack}"
    on_node("manager", "mkdir", "-p", path)
    swarmer.write(
        compose(
            replicas=3,
            extra=(
                f"    volumes:\n      - type: bind\n        source: {path}\n        target: /data\n"
            ),
        )
    )
    await swarmer.add_repository()
    await swarmer.push("Add app")
    d = await swarmer.deployed()
    assert d["status"] == "failed"
    assert d["stage"] == "preflight"
    assert "worker1" in d["error"]
    assert "worker2" in d["error"]
    assert "manager" not in d["error"]

    for node in ("worker1", "worker2"):
        on_node(node, "mkdir", "-p", path)
    retry = await swarmer.deploy()
    assert retry["status"] == "succeeded", retry["error"]


async def test_manual_changes_show_up_as_drift(swarmer: Swarmer) -> None:
    swarmer.write(compose())
    await swarmer.add_repository()
    await swarmer.push("Add app")
    assert (await swarmer.deployed())["status"] == "succeeded"
    assert (await swarmer.stack_info())["drift"] == []

    docker("service", "scale", "--detach", f"{swarmer.stack}_app=2")
    drifted = await swarmer.until(lambda s: bool(s["drift"]))
    assert any("app" in line for line in drifted["drift"])

    assert (await swarmer.deploy())["status"] == "succeeded"
    await swarmer.until(lambda s: s["drift"] == [])
    assert service(swarmer.stack)["Spec"]["Mode"]["Replicated"]["Replicas"] == 1


@pytest.mark.sops
async def test_secrets_are_rotated_with_a_rolling_update(
    swarmer: Swarmer, sops: Sops | None
) -> None:
    assert sops is not None
    recipients = [str(sops.recipient)]
    secret_compose = compose(
        extra="    secrets: [token]\n",
        top="secrets:\n  token:\n    environment: TOKEN\n",
    )
    swarmer.write(secret_compose)
    swarmer.write((await sops.encrypt({"TOKEN": "one"}, recipients)).decode(), "secrets.env")
    await swarmer.add_repository()
    await swarmer.push("Add app")
    assert (await swarmer.deployed())["status"] == "succeeded"
    [first] = docker(
        "secret", "ls", "--format", "{{.Name}}", "--filter", f"name={swarmer.stack}_token_"
    ).split()

    swarmer.write((await sops.encrypt({"TOKEN": "two"}, recipients)).decode(), "secrets.env")
    await swarmer.push("Rotate")
    d = await swarmer.deployed()
    assert d["status"] == "succeeded", d["error"]
    [second] = docker(
        "secret", "ls", "--format", "{{.Name}}", "--filter", f"name={swarmer.stack}_token_"
    ).split()
    assert second != first
    mounted = service(swarmer.stack)["Spec"]["TaskTemplate"]["ContainerSpec"]["Secrets"]
    assert [s["SecretName"] for s in mounted] == [second]


async def test_compose_stack_runs_on_the_host(swarmer: Swarmer) -> None:
    swarmer.write("type: compose\n", "swarmer.yaml")
    swarmer.write("services:\n  app:\n    image: busybox:1.37\n    command: sleep 3600\n")
    await swarmer.add_repository()
    await swarmer.push("Add app")
    d = await swarmer.deployed()
    assert d["status"] == "succeeded", d["error"]
    running = docker(
        "ps",
        "--format",
        "{{.Names}}",
        "--filter",
        f"label=com.docker.compose.project={swarmer.stack}",
    )
    assert running.split() == [f"{swarmer.stack}-app-1"]


LEGACY = """services:
  api:
    image: busybox:1.37
    command: ["sh", "-c", "echo $$GREETING; sleep 3600"]
    environment:
      GREETING: hello
    volumes:
      - data:/data
    networks: [backend]
    healthcheck:
      test: ["CMD", "true"]
      interval: 5s
    deploy:
      replicas: 2
      resources:
        limits:
          memory: 64M
      update_config:
        order: start-first
  worker:
    image: busybox:1.37
    command: sleep 3600
    networks: [backend]
    deploy:
      mode: global
networks:
  backend:
    driver: overlay
volumes:
  data:
"""


async def test_adopted_stack_deploys_without_restarting_tasks(
    swarmer: Swarmer, tmp_path: Path
) -> None:
    legacy = f"{swarmer.stack}legacy"
    (tmp_path / "legacy.yaml").write_text(LEGACY)
    docker("stack", "deploy", "--detach=false", "-c", str(tmp_path / "legacy.yaml"), legacy)
    try:
        before = set(
            docker("stack", "ps", "--quiet", "--filter", "desired-state=running", legacy).split()
        )
        assert len(before) == 5

        swarmer.write(compose())
        await swarmer.add_repository(mode="manual")
        await swarmer.push("Add app")
        found = (await swarmer.client.get("/api/unmanaged")).json()
        assert {
            "name": legacy,
            "type": "swarm",
            "endpoint": None,
            "services": 2,
            "name_taken": False,
        } in found

        generated = (await swarmer.client.get(f"/api/unmanaged/swarm/{legacy}")).json()
        response = await swarmer.client.post(
            f"/api/unmanaged/swarm/{legacy}/adopt",
            json={"repository_id": swarmer.repo_id, "compose": generated["compose"]},
        )
        assert response.status_code == 200, response.text

        adopted = Swarmer(swarmer.client, swarmer.origin, legacy)
        d = await adopted.deploy()
        assert d["status"] == "succeeded", d["error"]
        assert service(legacy, "api")["Spec"]["Labels"]["swarmer.stack"] == legacy
        after = set(
            docker("stack", "ps", "--quiet", "--filter", "desired-state=running", legacy).split()
        )
        assert after == before, "adopting should not replace running tasks"
    finally:
        _cleanup(legacy)


@needs_agents
async def test_stack_page_shows_containers_with_logs(swarmer: Swarmer) -> None:
    swarmer.write(compose(command="sh -c 'echo hello from the app; sleep 3600'", replicas=2))
    await swarmer.add_repository()
    await swarmer.push("Add app")
    assert (await swarmer.deployed())["status"] == "succeeded"

    info = await swarmer.stack_info()
    data = (await swarmer.client.get(f"/api/stacks/{info['id']}/containers")).json()
    [app] = data["services"]
    running = [c for c in app["containers"] if c["state"] == "running"]
    assert len(running) == 2
    assert {c["name"] for c in running} == {f"{swarmer.stack}_app.1", f"{swarmer.stack}_app.2"}

    for c in running:
        logs = await swarmer.client.get(f"/api/stacks/{info['id']}/containers/{c['ref']}/logs")
        assert logs.status_code == 200, logs.text
        assert any("hello from the app" in line for line in logs.json()["lines"])
    detail = (
        await swarmer.client.get(f"/api/stacks/{info['id']}/containers/{running[0]['ref']}")
    ).json()
    assert detail["service"] == "app"
    assert detail["node"] in {"manager", "worker1", "worker2"}
