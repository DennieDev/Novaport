# SPDX-License-Identifier: AGPL-3.0-or-later
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

import httpx
import pytest
from fastapi import FastAPI
from pydantic import SecretStr

from novaport.server.app import create_app
from tests.conftest import client_for, make_settings
from tests.fakes import FakeReader
from tests.oidc_provider import CLIENT_ID, CLIENT_SECRET, ISSUER, FakeProvider, rsa_key

pytestmark = pytest.mark.postgres

PASSWORD = "correct horse battery"


@dataclass
class Env:
    app: FastAPI
    admin: httpx.AsyncClient
    idp: FakeProvider
    group_id: str

    async def browser(self) -> AsyncIterator[httpx.AsyncClient]:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://test"
        ) as client:
            yield client


@pytest.fixture
async def env(clean_db: str) -> AsyncIterator[Env]:
    idp = FakeProvider()
    settings = make_settings(
        database_url=SecretStr(clean_db), initial_admin_password=SecretStr(PASSWORD)
    )
    app = create_app(settings, reader=FakeReader(), http_transport=idp.transport())
    async for admin in client_for(app):
        login = await admin.post(
            "/api/auth/login", json={"username": "admin", "password": PASSWORD}
        )
        assert login.status_code == 204
        group = (await admin.post("/api/groups", json={"name": "ops"})).json()
        provider = await admin.post(
            "/api/oidc-providers",
            json={
                "slug": "corp",
                "display_name": "Corp SSO",
                "issuer": ISSUER,
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "mappings": [{"claim_value": "ops-team", "group_id": group["id"]}],
            },
        )
        assert provider.status_code == 201, provider.text
        binding = await admin.post(
            "/api/role-bindings", json={"group_id": group["id"], "role": "operator"}
        )
        assert binding.status_code == 201
        yield Env(app, admin, idp, group["id"])


async def start(client: httpx.AsyncClient, next_path: str = "/stacks") -> dict[str, str]:
    response = await client.get("/api/auth/oidc/corp/login", params={"next": next_path})
    assert response.status_code == 302, response.text
    location = urlsplit(response.headers["location"])
    assert f"{location.scheme}://{location.netloc}{location.path}" == f"{ISSUER}/authorize"
    return {k: v[0] for k, v in parse_qs(location.query).items()}


async def finish(client: httpx.AsyncClient, query: dict[str, str], code: str) -> httpx.Response:
    return await client.get(
        "/api/auth/oidc/corp/callback", params={"code": code, "state": query["state"]}
    )


def error_of(response: httpx.Response) -> str:
    assert response.status_code == 302
    location = response.headers["location"]
    assert location.startswith("/login?error="), location
    return unquote(location.removeprefix("/login?error="))


async def log_in(env: Env, client: httpx.AsyncClient, **claims: Any) -> httpx.Response:
    env.idp.claims.update(claims)
    query = await start(client)
    return await finish(client, query, env.idp.authorize(query))


async def test_provider_listed_for_login_page(env: Env) -> None:
    async for browser in env.browser():
        providers = (await browser.get("/api/auth/providers")).json()
        assert providers == [
            {
                "slug": "corp",
                "display_name": "Corp SSO",
                "login_url": "/api/auth/oidc/corp/login",
            }
        ]


async def test_authorization_request(env: Env) -> None:
    async for browser in env.browser():
        query = await start(browser)
        assert query["client_id"] == CLIENT_ID
        assert query["code_challenge_method"] == "S256"
        assert query["redirect_uri"] == "http://test/api/auth/oidc/corp/callback"
        assert query["scope"] == "openid profile email"


async def test_login_creates_user_and_syncs_groups(env: Env) -> None:
    async for browser in env.browser():
        response = await log_in(
            env, browser, groups=["ops-team", "other"], email="alice@example.com", name="Alice"
        )
        assert response.status_code == 302, response.headers.get("location")
        assert response.headers["location"] == "/stacks"

        me = (await browser.get("/api/auth/me")).json()
        assert me["username"] == "alice"
        assert me["name"] == "Alice"
        assert me["local"] is False
        assert me["global_role"] == "operator"
        assert env.idp.token_requests[0]["code_verifier"]

    async for browser in env.browser():
        await log_in(env, browser, groups=["other"])
        me = (await browser.get("/api/auth/me")).json()
        assert me["username"] == "alice"
        assert me["global_role"] is None

    users = (await env.admin.get("/api/users")).json()
    assert [u["username"] for u in users] == ["admin", "alice"]
    audit = (await env.admin.get("/api/audit", params={"action": "auth.login"})).json()
    assert audit[0]["details"] == {"method": "oidc", "provider": "corp"}


async def test_manual_membership_survives_login(env: Env) -> None:
    async for browser in env.browser():
        await log_in(env, browser, groups=[])
        me = (await browser.get("/api/auth/me")).json()
        added = await env.admin.put(f"/api/groups/{env.group_id}/members/{me['id']}")
        assert added.status_code == 200

    async for browser in env.browser():
        await log_in(env, browser, groups=[])
        assert (await browser.get("/api/auth/me")).json()["global_role"] == "operator"


async def test_groups_from_userinfo(env: Env) -> None:
    env.idp.userinfo = {"sub": "user-1", "groups": ["ops-team"]}
    async for browser in env.browser():
        await log_in(env, browser)
        assert (await browser.get("/api/auth/me")).json()["global_role"] == "operator"


async def test_username_collision_gets_suffix(env: Env) -> None:
    async for browser in env.browser():
        await log_in(env, browser, preferred_username="admin")
        assert (await browser.get("/api/auth/me")).json()["username"] == "admin-2"


async def test_state_works_once(env: Env) -> None:
    async for browser in env.browser():
        query = await start(browser)
        code = env.idp.authorize(query)
        assert (await finish(browser, query, code)).headers["location"] == "/stacks"
        replay = await finish(browser, query, env.idp.authorize(query, "code-2"))
        assert "expired or was already used" in error_of(replay)


async def test_callback_in_another_browser_is_refused(env: Env) -> None:
    async for browser in env.browser():
        query = await start(browser)
        code = env.idp.authorize(query)
    async for victim in env.browser():
        assert "different browser" in error_of(await finish(victim, query, code))
        assert (await victim.get("/api/auth/me")).status_code == 401


async def test_wrong_nonce_is_refused(env: Env) -> None:
    env.idp.nonce_override = "something else"
    async for browser in env.browser():
        assert "nonce" in error_of(await log_in(env, browser))


async def test_forged_signature_is_refused(env: Env) -> None:
    env.idp.sign_with = rsa_key()
    async for browser in env.browser():
        assert "invalid" in error_of(await log_in(env, browser))
        assert (await browser.get("/api/auth/me")).status_code == 401
    failed = (await env.admin.get("/api/audit", params={"outcome": "failed"})).json()
    assert failed[0]["details"]["provider"] == "corp"


async def test_provider_error_is_shown(env: Env) -> None:
    async for browser in env.browser():
        query = await start(browser)
        response = await browser.get(
            "/api/auth/oidc/corp/callback",
            params={"state": query["state"], "error": "access_denied"},
        )
        assert "access_denied" in error_of(response)


async def test_disabled_user_cannot_log_in(env: Env) -> None:
    async for browser in env.browser():
        await log_in(env, browser)
        me = (await browser.get("/api/auth/me")).json()
    await env.admin.patch(f"/api/users/{me['id']}", json={"disabled": True})
    async for browser in env.browser():
        assert "disabled" in error_of(await log_in(env, browser))


async def test_next_must_be_a_local_path(env: Env) -> None:
    async for browser in env.browser():
        query = await start(browser, "//evil.example/steal")
        response = await finish(browser, query, env.idp.authorize(query))
        assert response.headers["location"] == "/"


async def test_disabled_provider(env: Env) -> None:
    [provider] = (await env.admin.get("/api/oidc-providers")).json()
    assert provider["has_client_secret"] is True
    assert "client_secret" not in provider
    await env.admin.patch(f"/api/oidc-providers/{provider['id']}", json={"enabled": False})
    async for browser in env.browser():
        assert (await browser.get("/api/auth/providers")).json() == []
        response = await browser.get("/api/auth/oidc/corp/login")
        assert "disabled" in error_of(response)


async def test_check_provider(env: Env) -> None:
    [provider] = (await env.admin.get("/api/oidc-providers")).json()
    check = (await env.admin.post(f"/api/oidc-providers/{provider['id']}/check")).json()
    assert check["ok"] is True
    assert check["token_endpoint"] == f"{ISSUER}/token"

    await env.admin.patch(
        f"/api/oidc-providers/{provider['id']}", json={"issuer": "https://other.example"}
    )
    check = (await env.admin.post(f"/api/oidc-providers/{provider['id']}/check")).json()
    assert check["ok"] is False
    assert "issuer" in check["error"]
