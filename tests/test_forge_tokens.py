# SPDX-License-Identifier: AGPL-3.0-or-later
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from swarmer import forge_tokens, forges
from swarmer.crypto import Cipher
from swarmer.forges import Forge, ForgeError
from swarmer.models import Repository
from swarmer.notifications import Event
from swarmer.server.app import create_app
from tests.conftest import TEST_SECRET_KEY, client_for, make_settings

REPO = "git@github.com:acme/infra.git"
NOW = datetime(2027, 3, 1, tzinfo=UTC)


def answer(status: int = 200, **headers: str) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer secret"
        return httpx.Response(status, headers=headers, json={})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_github_reports_when_a_token_expires() -> None:
    async with answer(**{"github-authentication-token-expiration": "2027-03-12 10:00:00 UTC"}) as c:
        expires = await forges.token_expiry(Forge.GITHUB, None, "secret", REPO, client=c)
    assert expires == datetime(2027, 3, 12, 10, tzinfo=UTC)


async def test_tokens_without_expiry_and_refused_tokens() -> None:
    async with answer() as c:
        assert await forges.token_expiry(Forge.GITHUB, None, "secret", REPO, client=c) is None
    async with answer(401) as c:
        with pytest.raises(ForgeError, match="refused the token"):
            await forges.token_expiry(Forge.GITHUB, None, "secret", REPO, client=c)


async def test_gitlab_token_expiry() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v4/personal_access_tokens/self"
        return httpx.Response(200, json={"expires_at": "2027-04-01"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        expires = await forges.token_expiry(
            Forge.GITLAB, None, "secret", "https://gitlab.com/acme/infra.git", client=c
        )
    assert expires == datetime(2027, 4, 1, tzinfo=UTC)


def repository(**fields: object) -> Repository:
    return Repository(id=uuid.uuid4(), name="infra", url=REPO, forge="github", **fields)


def test_warns_once_shortly_before_expiry() -> None:
    assert (
        forge_tokens.warning(repository(forge_token_expires_at=NOW + timedelta(days=40)), NOW)
        is None
    )
    soon = repository(forge_token_expires_at=NOW + timedelta(days=10))
    event = forge_tokens.warning(soon, NOW)
    assert event is not None
    assert "expires on 2027-03-11" in event.title
    assert event.link == f"/repositories/{soon.id}"
    soon.forge_token_warned_at = NOW
    assert forge_tokens.warning(soon, NOW) is None


def test_warns_when_the_token_stopped_working() -> None:
    event = forge_tokens.warning(repository(forge_token_error="github refused the token"), NOW)
    assert event is not None
    assert "no longer works" in event.title


@pytest.fixture
def expiry(monkeypatch: pytest.MonkeyPatch) -> list[datetime | None]:
    """What the fake forge says about any token; append to change it."""
    answers: list[datetime | None] = [NOW + timedelta(days=200)]

    async def token_expiry(*_: object, **__: object) -> datetime | None:
        return answers[-1]

    monkeypatch.setattr(forges, "token_expiry", token_expiry)
    return answers


@pytest.mark.postgres
async def test_daily_check_notifies_once(clean_db: str, expiry: list[datetime | None]) -> None:
    engine = create_async_engine(clean_db)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    cipher = Cipher(TEST_SECRET_KEY)
    async with maker() as db:
        db.add(repository(forge_token=cipher.encrypt("secret"), webhook_secret="x"))
        await db.commit()
    sent: list[Event] = []

    await forge_tokens.check_all(maker, cipher, sent.append, now=NOW)
    assert sent == []

    expiry.append(NOW + timedelta(days=5))
    await forge_tokens.check_all(maker, cipher, sent.append, now=NOW + timedelta(hours=2))
    assert sent == []  # checked less than a day ago, so the forge isn't asked again yet

    await forge_tokens.check_all(maker, cipher, sent.append, now=NOW + timedelta(days=1))
    await forge_tokens.check_all(maker, cipher, sent.append, now=NOW + timedelta(days=2))
    assert len(sent) == 1
    assert "expires on 2027-03-06" in sent[0].title
    await engine.dispose()


PASSWORD = "correct horse battery"


@pytest.fixture
async def api(clean_db: str) -> AsyncIterator[httpx.AsyncClient]:
    settings = make_settings(
        database_url=SecretStr(clean_db), initial_admin_password=SecretStr(PASSWORD)
    )
    async for client in client_for(create_app(settings)):
        await client.post("/api/auth/login", json={"username": "admin", "password": PASSWORD})
        yield client


@pytest.mark.postgres
async def test_replace_check_and_remove_the_token(
    api: httpx.AsyncClient, expiry: list[datetime | None]
) -> None:
    created = await api.post(
        "/api/repositories",
        json={"name": "infra", "url": REPO, "forge": "github", "forge_token": "old"},
    )
    assert created.status_code == 201, created.text
    repo = created.json()
    assert repo["forge_token_expires_at"].startswith("2027-09-17")

    expiry.append(NOW + timedelta(days=365))
    replaced = (
        await api.patch(f"/api/repositories/{repo['id']}", json={"forge_token": "new"})
    ).json()
    assert replaced["has_forge_token"] is True
    assert replaced["forge_token_expires_at"].startswith("2028-02-29")

    expiry.append(None)
    checked = (await api.post(f"/api/repositories/{repo['id']}/forge-token/check")).json()
    assert checked["forge_token_expires_at"] is None
    assert checked["forge_token_checked_at"] is not None

    removed = (
        await api.patch(f"/api/repositories/{repo['id']}", json={"remove_forge_token": True})
    ).json()
    assert removed["has_forge_token"] is False
    assert removed["forge_token_checked_at"] is None
