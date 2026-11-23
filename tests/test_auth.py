# SPDX-License-Identifier: AGPL-3.0-or-later
from collections.abc import AsyncIterator
from datetime import timedelta

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from novaport.auth.passwords import WeakPassword
from novaport.auth.users import create_admin
from novaport.models import Session
from novaport.server.app import create_app
from tests.conftest import client_for, make_settings
from tests.fakes import FakeReader

pytestmark = pytest.mark.postgres

PASSWORD = "correct horse battery"


@pytest.fixture
async def app_client(clean_db: str) -> AsyncIterator[httpx.AsyncClient]:
    settings = make_settings(
        database_url=SecretStr(clean_db),
        initial_admin_password=SecretStr(PASSWORD),
    )
    async for client in client_for(create_app(settings, reader=FakeReader())):
        yield client


async def login(client: httpx.AsyncClient, password: str = PASSWORD) -> httpx.Response:
    return await client.post("/api/auth/login", json={"username": "admin", "password": password})


async def test_initial_admin_can_log_in(app_client: httpx.AsyncClient) -> None:
    response = await login(app_client)
    assert response.status_code == 204
    cookie = response.headers["set-cookie"]
    assert "novaport_session=" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie

    me = await app_client.get("/api/auth/me")
    assert me.json()["username"] == "admin"
    assert me.json()["global_role"] == "admin"
    assert me.json()["local"]

    assert (await app_client.get("/api/services")).status_code == 200


async def test_wrong_password(app_client: httpx.AsyncClient) -> None:
    response = await login(app_client, "wrong password!")
    assert response.status_code == 401
    assert (await app_client.get("/api/auth/me")).status_code == 401


async def test_unknown_user_same_error(app_client: httpx.AsyncClient) -> None:
    response = await app_client.post(
        "/api/auth/login", json={"username": "ghost", "password": PASSWORD}
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid username or password."


async def test_logout_ends_session(app_client: httpx.AsyncClient) -> None:
    await login(app_client)
    assert (await app_client.post("/api/auth/logout")).status_code == 204
    assert (await app_client.get("/api/auth/me")).status_code == 401


async def test_throttles_repeated_failures(app_client: httpx.AsyncClient) -> None:
    for _ in range(10):
        await login(app_client, "wrong password!")
    response = await login(app_client)
    assert response.status_code == 429


async def test_cross_site_login_refused(app_client: httpx.AsyncClient) -> None:
    response = await app_client.post(
        "/api/auth/login",
        json={"username": "admin", "password": PASSWORD},
        headers={"Sec-Fetch-Site": "cross-site"},
    )
    assert response.status_code == 403


async def test_expired_session_is_rejected_and_removed(
    app_client: httpx.AsyncClient, clean_db: str
) -> None:
    await login(app_client)
    engine = create_async_engine(clean_db)
    try:
        async with async_sessionmaker(engine)() as db:
            await db.execute(update(Session).values(expires_at=Session.created_at))
            await db.commit()
            assert (await app_client.get("/api/auth/me")).status_code == 401
            assert await db.scalar(select(Session)) is None
    finally:
        await engine.dispose()


async def test_session_past_max_age_is_rejected(
    app_client: httpx.AsyncClient, clean_db: str
) -> None:
    await login(app_client)
    engine = create_async_engine(clean_db)
    try:
        async with async_sessionmaker(engine)() as db:
            await db.execute(
                update(Session).values(created_at=Session.created_at - timedelta(days=8))
            )
            await db.commit()
    finally:
        await engine.dispose()
    assert (await app_client.get("/api/auth/me")).status_code == 401


async def test_create_admin_rejects_short_password(clean_db: str) -> None:
    engine = create_async_engine(clean_db)
    try:
        async with async_sessionmaker(engine)() as db:
            with pytest.raises(WeakPassword):
                await create_admin(db, "admin", "short")
    finally:
        await engine.dispose()
