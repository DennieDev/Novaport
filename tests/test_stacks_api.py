# SPDX-License-Identifier: AGPL-3.0-or-later
import asyncio
import hashlib
import hmac
import json
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from novaport.server.app import create_app
from tests.conftest import client_for, make_settings
from tests.fakes import FakeReader
from tests.gitrepo import COMPOSE, Repo
from tests.test_pipeline import FakeCli

pytestmark = pytest.mark.postgres

PASSWORD = "correct horse battery"


@pytest.fixture
def origin(tmp_path: Path) -> Repo:
    repo = Repo(tmp_path / "origin")
    repo.write("stacks/web/compose.yaml", COMPOSE)
    repo.write("stacks/web/novaport.yaml", "deploy: approve\n")
    repo.commit("Add web")
    return repo


@pytest.fixture
async def api(clean_db: str) -> AsyncIterator[httpx.AsyncClient]:
    settings = make_settings(
        database_url=SecretStr(clean_db), initial_admin_password=SecretStr(PASSWORD)
    )
    app = create_app(settings, reader=FakeReader(), cli=FakeCli())
    async for client in client_for(app):
        response = await client.post(
            "/api/auth/login", json={"username": "admin", "password": PASSWORD}
        )
        assert response.status_code == 204
        client.app = app  # type: ignore[attr-defined]
        yield client


async def create_repo(api: httpx.AsyncClient, origin: Repo, **extra: object) -> dict[str, object]:
    response = await api.post(
        "/api/repositories", json={"name": "infra", "url": origin.url, **extra}
    )
    assert response.status_code == 201, response.text
    data: dict[str, object] = response.json()
    return data


async def test_repository_crud(api: httpx.AsyncClient, origin: Repo) -> None:
    repo = await create_repo(api, origin)
    assert repo["auth"] == "none"
    assert repo["webhook_path"] == f"/api/webhooks/git/{repo['id']}"
    assert len(str(repo["webhook_secret"])) == 64

    duplicate = await api.post("/api/repositories", json={"name": "infra", "url": origin.url})
    assert duplicate.status_code == 409

    patched = await api.patch(f"/api/repositories/{repo['id']}", json={"poll_interval": 60})
    assert patched.json()["poll_interval"] == 60

    assert (await api.delete(f"/api/repositories/{repo['id']}")).status_code == 204
    assert (await api.get(f"/api/repositories/{repo['id']}")).status_code == 404


async def test_ssh_auth_generates_a_deploy_key(api: httpx.AsyncClient, origin: Repo) -> None:
    repo = await create_repo(api, origin, auth="ssh")
    assert str(repo["ssh_public_key"]).startswith("ssh-ed25519 ")
    rotated = await api.post(f"/api/repositories/{repo['id']}/ssh-key")
    assert rotated.json()["ssh_public_key"] != repo["ssh_public_key"]


async def test_token_is_stored_but_never_returned(api: httpx.AsyncClient, origin: Repo) -> None:
    repo = await create_repo(api, origin, auth="token", token="ghp_example")
    assert repo["has_token"] is True
    assert "ghp_example" not in json.dumps(repo)


async def test_token_auth_requires_a_token(api: httpx.AsyncClient, origin: Repo) -> None:
    response = await api.post(
        "/api/repositories", json={"name": "infra", "url": origin.url, "auth": "token"}
    )
    assert response.status_code == 422


async def test_sync_deploy_approve_flow(api: httpx.AsyncClient, origin: Repo) -> None:
    repo = await create_repo(api, origin)
    sync = (await api.post(f"/api/repositories/{repo['id']}/sync")).json()
    assert sync["new_revisions"] == 1
    assert sync["queued_deployments"] == 1

    stacks = (await api.get("/api/stacks")).json()
    assert [s["name"] for s in stacks] == ["web"]
    stack = stacks[0]
    assert stack["deploy_mode"] == "approve"
    assert stack["last_deployment"]["status"] == "awaiting_approval"

    deployment_id = stack["last_deployment"]["id"]
    approved = await api.post(f"/api/deployments/{deployment_id}/approve")
    assert approved.json()["status"] == "pending"
    again = await api.post(f"/api/deployments/{deployment_id}/approve")
    assert again.status_code == 409

    app = api.app  # type: ignore[attr-defined]
    assert await app.state.pipeline.run(deployment_id) == "succeeded"
    detail = (await api.get(f"/api/deployments/{deployment_id}")).json()
    assert detail["status"] == "succeeded"
    assert detail["events"][0]["stage"] == "resolve"

    stack = (await api.get(f"/api/stacks/{stack['id']}")).json()
    assert stack["last_good_revision"]["id"] == stack["latest_revision"]["id"]


async def test_manual_deploy_and_cancel(api: httpx.AsyncClient, origin: Repo) -> None:
    repo = await create_repo(api, origin)
    await api.post(f"/api/repositories/{repo['id']}/sync")
    stack = (await api.get("/api/stacks")).json()[0]
    revisions = (await api.get(f"/api/stacks/{stack['id']}/revisions")).json()

    queued = await api.post(
        f"/api/stacks/{stack['id']}/deploy", json={"revision_id": revisions[0]["id"]}
    )
    assert queued.status_code == 202
    assert queued.json()["trigger"] == "manual"

    history = (await api.get(f"/api/stacks/{stack['id']}/deployments")).json()
    assert [d["status"] for d in history] == ["pending", "cancelled"]

    cancelled = await api.post(f"/api/deployments/{queued.json()['id']}/cancel")
    assert cancelled.json()["status"] == "cancelled"


async def test_webhook_triggers_sync(api: httpx.AsyncClient, origin: Repo) -> None:
    repo = await create_repo(api, origin)
    secret = str(repo["webhook_secret"])
    path = str(repo["webhook_path"])

    def signed(payload: dict[str, object]) -> tuple[bytes, dict[str, str]]:
        body = json.dumps(payload).encode()
        digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        return body, {"X-Hub-Signature-256": f"sha256={digest}"}

    body, headers = signed({"ref": "refs/heads/main"})
    bad = await api.post(path, content=body, headers={"X-Hub-Signature-256": "sha256=00"})
    assert bad.status_code == 401

    other_body, other_headers = signed({"ref": "refs/heads/feature"})
    other = await api.post(path, content=other_body, headers=other_headers)
    assert other.json()["status"] == "ignored: other branch"

    ok = await api.post(path, content=body, headers=headers)
    assert ok.status_code == 202
    tasks = api.app.state.webhook_tasks  # type: ignore[attr-defined]
    await asyncio.gather(*tasks)
    assert [s["name"] for s in (await api.get("/api/stacks")).json()] == ["web"]


async def test_viewing_requires_login(clean_db: str) -> None:
    app = create_app(make_settings(database_url=SecretStr(clean_db)), reader=FakeReader())
    async for client in client_for(app):
        assert (await client.get("/api/stacks")).status_code == 401
        assert (await client.get("/api/repositories")).status_code == 401


async def test_edit_files_through_the_api(api: httpx.AsyncClient, origin: Repo) -> None:
    origin.write("stacks/web/novaport.yaml", "deploy: manual\n")
    origin.commit("Manual")
    repo = await create_repo(api, origin)
    await api.post(f"/api/repositories/{repo['id']}/sync")
    stack = (await api.get("/api/stacks")).json()[0]
    assert stack["edit_mode"] == "commit"

    files = (await api.get(f"/api/stacks/{stack['id']}/files")).json()
    assert [f["path"] for f in files["files"]] == ["compose.yaml", "novaport.yaml"]
    assert files["has_secrets"] is False
    base = files["commit"]

    saved = await api.post(
        f"/api/stacks/{stack['id']}/files",
        json={"base_commit": base, "changes": [{"path": ".env", "content": "A=1\n"}]},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["new_revisions"] == 1
    assert origin.log("%s") == "Update .env in web"

    stale = await api.post(
        f"/api/stacks/{stack['id']}/files",
        json={"base_commit": base, "changes": [{"path": ".env", "content": "A=2\n"}]},
    )
    assert stale.status_code == 409
    conflict = stale.json()
    assert conflict["tip"] == origin.head()
    assert conflict["conflicts"] == [
        {"path": ".env", "base": None, "theirs": "A=1\n", "yours": "A=2\n"}
    ]

    bad = await api.post(
        f"/api/stacks/{stack['id']}/files",
        json={"base_commit": origin.head(), "changes": [{"path": "../x", "content": ""}]},
    )
    assert bad.status_code == 422


async def test_sops_info_without_key(api: httpx.AsyncClient) -> None:
    info = (await api.get("/api/settings/sops")).json()
    assert info == {"available": False, "recipient": None, "example": None}


async def test_save_and_deploy_with_options(api: httpx.AsyncClient, origin: Repo) -> None:
    origin.write("stacks/web/novaport.yaml", "deploy: manual\n")
    origin.commit("Manual")
    repo = await create_repo(api, origin)
    await api.post(f"/api/repositories/{repo['id']}/sync")
    stack = (await api.get("/api/stacks")).json()[0]
    base = (await api.get(f"/api/stacks/{stack['id']}/files")).json()["commit"]

    saved = await api.post(
        f"/api/stacks/{stack['id']}/files",
        json={
            "base_commit": base,
            "changes": [{"path": ".env", "content": "A=1\n"}],
            "deploy": {"prune": False, "pull_images": False},
        },
    )
    assert saved.status_code == 200, saved.text
    deployment_id = saved.json()["deployment_id"]
    assert deployment_id is not None

    deployment = (await api.get(f"/api/deployments/{deployment_id}")).json()
    assert deployment["trigger"] == "manual"
    assert deployment["revision"]["commit"] == origin.head()
    assert (deployment["prune"], deployment["pull_images"]) == (False, False)


async def test_deploy_options_default_to_on(api: httpx.AsyncClient, origin: Repo) -> None:
    repo = await create_repo(api, origin)
    await api.post(f"/api/repositories/{repo['id']}/sync")
    stack = (await api.get("/api/stacks")).json()[0]
    queued = await api.post(f"/api/stacks/{stack['id']}/deploy", json={})
    assert (queued.json()["prune"], queued.json()["pull_images"]) == (True, True)
