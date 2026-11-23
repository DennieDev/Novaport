# SPDX-License-Identifier: AGPL-3.0-or-later
import asyncio
from collections.abc import Iterator

import pytest
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from novaport.migrate import upgrade
from novaport.server.app import create_app
from tests.conftest import TEST_DATABASE_URL, make_settings
from tests.fakes import FakeReader

PASSWORD = "correct horse battery"
FOLLOW = "/api/services/svc1/logs/follow"


async def _truncate(url: str) -> None:
    engine = create_async_engine(url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("TRUNCATE users, sessions CASCADE"))
    finally:
        await engine.dispose()


@pytest.fixture
def client() -> Iterator[TestClient]:
    settings = make_settings(initial_admin_password=SecretStr(PASSWORD))
    if TEST_DATABASE_URL:
        upgrade(TEST_DATABASE_URL)
        asyncio.run(_truncate(TEST_DATABASE_URL))
    with TestClient(create_app(settings, reader=FakeReader())) as client:
        yield client


@pytest.mark.postgres
def test_follow_streams_log_chunks(client: TestClient) -> None:
    response = client.post("/api/auth/login", json={"username": "admin", "password": PASSWORD})
    assert response.status_code == 204
    with client.websocket_connect(FOLLOW) as ws:
        assert ws.receive_text() == "line one\nline two\n"
        assert ws.receive_text() == "line three\n"


@pytest.mark.postgres
def test_follow_requires_login(client: TestClient) -> None:
    with pytest.raises(WebSocketDisconnect) as exc, client.websocket_connect(FOLLOW) as ws:
        ws.receive_text()
    assert exc.value.code == 1008


def test_cross_site_websocket_refused(client: TestClient) -> None:
    with (
        pytest.raises(WebSocketDisconnect) as exc,
        client.websocket_connect(FOLLOW, headers={"Origin": "https://evil.example"}) as ws,
    ):
        ws.receive_text()
    assert exc.value.code == 1008
