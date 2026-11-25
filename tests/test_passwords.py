# SPDX-License-Identifier: AGPL-3.0-or-later
import asyncio
import io
from collections.abc import AsyncIterator

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from swarmer.auth.passwords import hash_password
from swarmer.cli import main
from swarmer.config import get_settings
from swarmer.models import AuditEntry, User
from swarmer.server.app import create_app
from tests.conftest import client_for, make_settings

pytestmark = pytest.mark.postgres

OLD = "correct horse battery"
NEW = "another long password"


@pytest.fixture
async def app(clean_db: str) -> AsyncIterator[httpx.AsyncClient]:
    settings = make_settings(
        database_url=SecretStr(clean_db), initial_admin_password=SecretStr(OLD)
    )
    async for client in client_for(create_app(settings)):
        yield client


async def login(client: httpx.AsyncClient, username: str, password: str) -> int:
    response = await client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )
    return response.status_code


async def test_change_own_password(app: httpx.AsyncClient) -> None:
    assert await login(app, "admin", OLD) == 204
    other = httpx.AsyncClient(transport=app._transport, base_url="http://test")
    assert await login(other, "admin", OLD) == 204

    wrong = await app.post(
        "/api/auth/password", json={"current_password": "nope", "new_password": NEW}
    )
    assert wrong.status_code == 403
    weak = await app.post(
        "/api/auth/password", json={"current_password": OLD, "new_password": "short"}
    )
    assert weak.status_code == 422

    ok = await app.post("/api/auth/password", json={"current_password": OLD, "new_password": NEW})
    assert ok.status_code == 204
    assert (await app.get("/api/auth/me")).status_code == 200
    assert (await other.get("/api/auth/me")).status_code == 401
    await other.aclose()

    app.cookies.clear()
    assert await login(app, "admin", OLD) == 401
    assert await login(app, "admin", NEW) == 204


async def test_admin_resets_another_users_password(app: httpx.AsyncClient, clean_db: str) -> None:
    engine = create_async_engine(clean_db)
    async with async_sessionmaker(engine)() as db:
        db.add(User(username="vera", password_hash=hash_password(OLD)))
        db.add(User(username="olga", issuer="https://idp.example", subject="olga"))
        await db.commit()
        vera = await db.scalar(select(User).where(User.username == "vera"))
        olga = await db.scalar(select(User).where(User.username == "olga"))
    assert vera is not None
    assert olga is not None

    assert await login(app, "admin", OLD) == 204
    reset = await app.post(f"/api/users/{vera.id}/password", json={"new_password": NEW})
    assert reset.status_code == 204
    sso = await app.post(f"/api/users/{olga.id}/password", json={"new_password": NEW})
    assert sso.status_code == 422

    async with async_sessionmaker(engine)() as db:
        entry = await db.scalar(select(AuditEntry).where(AuditEntry.action == "user.password"))
        assert entry is not None
        assert entry.target == "vera"
    await engine.dispose()

    app.cookies.clear()
    assert await login(app, "vera", NEW) == 204


def test_cli_sets_a_password(clean_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    async def add_user() -> None:
        engine = create_async_engine(clean_db)
        async with async_sessionmaker(engine)() as db:
            db.add(User(username="ops", password_hash=hash_password(OLD)))
            await db.commit()
        await engine.dispose()

    asyncio.run(add_user())
    monkeypatch.setenv("SWARMER_DATABASE_URL", clean_db)
    get_settings.cache_clear()
    try:
        monkeypatch.setattr("sys.stdin", io.StringIO(NEW + "\n"))
        assert main(["set-password", "ops", "--password-stdin"]) == 0
        monkeypatch.setattr("sys.stdin", io.StringIO(NEW + "\n"))
        assert main(["set-password", "nobody", "--password-stdin"]) == 1
    finally:
        get_settings.cache_clear()
