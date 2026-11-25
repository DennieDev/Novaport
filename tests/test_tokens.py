# SPDX-License-Identifier: AGPL-3.0-or-later
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from novaport.auth.passwords import hash_password
from novaport.models import ApiToken, AuditEntry, User
from novaport.server.app import create_app
from tests.conftest import client_for, make_settings

pytestmark = pytest.mark.postgres

PASSWORD = "correct horse battery"


@pytest.fixture
async def app(clean_db: str) -> AsyncIterator[httpx.AsyncClient]:
    settings = make_settings(
        database_url=SecretStr(clean_db), initial_admin_password=SecretStr(PASSWORD)
    )
    async for client in client_for(create_app(settings)):
        assert (
            await client.post("/api/auth/login", json={"username": "admin", "password": PASSWORD})
        ).status_code == 204
        yield client


def bearer(app: httpx.AsyncClient, token: str) -> httpx.AsyncClient:
    """A client without the session cookie that sends `token`."""
    return httpx.AsyncClient(
        transport=app._transport,
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    )


async def create(app: httpx.AsyncClient, name: str, role: str = "viewer", **body: object) -> str:
    response = await app.post("/api/tokens", json={"name": name, "role": role, **body})
    assert response.status_code == 201, response.text
    token: str = response.json()["token"]
    return token


async def test_token_acts_as_its_user_with_capped_role(app: httpx.AsyncClient) -> None:
    value = await create(app, "reader")
    assert value.startswith("np_")

    listed = (await app.get("/api/tokens")).json()
    assert [t["name"] for t in listed] == ["reader"]
    assert "token" not in listed[0]
    assert value.startswith(listed[0]["prefix"])

    async with bearer(app, value) as client:
        me = (await client.get("/api/auth/me")).json()
        assert me["username"] == "admin"
        assert me["global_role"] == "viewer"
        assert me["token"] == "reader"
        assert (await client.get("/api/stacks")).status_code == 200
        assert (await client.get("/api/users")).status_code == 403

    async with bearer(app, await create(app, "admin-script", "admin")) as client:
        assert (await client.get("/api/users")).status_code == 200

    used = {t["name"]: t for t in (await app.get("/api/tokens")).json()}
    assert used["reader"]["last_used_at"] is not None


async def test_token_cannot_create_tokens_or_change_the_password(app: httpx.AsyncClient) -> None:
    async with bearer(app, await create(app, "full", "admin")) as client:
        made = await client.post("/api/tokens", json={"name": "another", "role": "admin"})
        assert made.status_code == 403
        password = await client.post(
            "/api/auth/password",
            json={"current_password": PASSWORD, "new_password": "a new long password"},
        )
        assert password.status_code == 403


async def test_bad_token_never_falls_back_to_the_cookie(app: httpx.AsyncClient) -> None:
    for header in ("Bearer np_wrong", "Bearer ", "Basic abc"):
        response = await app.get("/api/auth/me", headers={"Authorization": header})
        assert response.status_code == 401, header
    assert (await app.get("/api/auth/me")).status_code == 200


async def test_revoked_expired_and_disabled(app: httpx.AsyncClient, clean_db: str) -> None:
    revoked = await create(app, "revoked")
    token_id = (await app.get("/api/tokens")).json()[0]["id"]
    assert (await app.delete(f"/api/tokens/{token_id}")).status_code == 204
    async with bearer(app, revoked) as client:
        assert (await client.get("/api/auth/me")).status_code == 401

    expired = await create(app, "expired")
    engine = create_async_engine(clean_db)
    async with async_sessionmaker(engine)() as db:
        await db.execute(
            update(ApiToken)
            .where(ApiToken.name == "expired")
            .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        db.add(User(username="vera", password_hash=hash_password(PASSWORD)))
        await db.commit()
    async with bearer(app, expired) as client:
        assert (await client.get("/api/auth/me")).status_code == 401

    vera = httpx.AsyncClient(transport=app._transport, base_url="http://test")
    await vera.post("/api/auth/login", json={"username": "vera", "password": PASSWORD})
    value = await create(vera, "vera-script")
    await vera.aclose()
    async with async_sessionmaker(engine)() as db:
        await db.execute(update(User).where(User.username == "vera").values(disabled=True))
        await db.commit()
    await engine.dispose()
    async with bearer(app, value) as client:
        assert (await client.get("/api/auth/me")).status_code == 401


async def test_tokens_without_expiry_and_duplicate_names(app: httpx.AsyncClient) -> None:
    await create(app, "forever", expires_in_days=None)
    assert (await app.get("/api/tokens")).json()[0]["expires_at"] is None
    again = await app.post("/api/tokens", json={"name": "forever", "role": "viewer"})
    assert again.status_code == 409


async def test_only_owner_or_admin_revokes(app: httpx.AsyncClient, clean_db: str) -> None:
    engine = create_async_engine(clean_db)
    async with async_sessionmaker(engine)() as db:
        db.add(User(username="vera", password_hash=hash_password(PASSWORD)))
        await db.commit()

    vera = httpx.AsyncClient(transport=app._transport, base_url="http://test")
    await vera.post("/api/auth/login", json={"username": "vera", "password": PASSWORD})
    await create(vera, "vera-script")
    await create(app, "admin-script")
    tokens = {t["name"]: t["id"] for t in (await app.get("/api/tokens/all")).json()}
    assert set(tokens) == {"vera-script", "admin-script"}
    assert (await vera.get("/api/tokens/all")).status_code == 403

    assert (await vera.delete(f"/api/tokens/{tokens['admin-script']}")).status_code == 404
    async with bearer(app, await create(app, "cleanup", "admin")) as client:
        assert (await client.delete(f"/api/tokens/{tokens['vera-script']}")).status_code == 204
    await vera.aclose()

    async with async_sessionmaker(engine)() as db:
        entry = await db.scalar(select(AuditEntry).where(AuditEntry.action == "token.revoke"))
        assert entry is not None
        assert entry.target == "vera-script"
        assert entry.details == {"owner": "vera", "token": "cleanup"}
    await engine.dispose()
