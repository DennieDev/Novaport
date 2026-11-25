# SPDX-License-Identifier: AGPL-3.0-or-later
"""Keeping track of when the token a repository uses for its forge's API expires."""

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from swarmer import forges
from swarmer.crypto import Cipher
from swarmer.forges import Forge, ForgeError
from swarmer.models import EventType, Repository
from swarmer.notifications import Event

log = logging.getLogger(__name__)

CHECK_EVERY = timedelta(days=1)
WARN_BEFORE = timedelta(days=14)


def api_token(repo: Repository, cipher: Cipher) -> str | None:
    """The token pull requests are opened with: the forge token, else the HTTPS git token."""
    encrypted = repo.forge_token or repo.token
    return cipher.decrypt(encrypted) if encrypted else None


def forget(repo: Repository) -> None:
    """Drop what is known about the old token, after it was replaced or removed."""
    repo.forge_token_expires_at = None
    repo.forge_token_checked_at = None
    repo.forge_token_error = None
    repo.forge_token_warned_at = None


async def check(
    repo: Repository,
    cipher: Cipher,
    *,
    client: httpx.AsyncClient | None = None,
    now: datetime | None = None,
) -> None:
    """Ask the forge about the repository's token and record the answer. The caller commits."""
    token = api_token(repo, cipher)
    if token is None or repo.forge == Forge.NONE:
        forget(repo)
        return
    try:
        repo.forge_token_expires_at = await forges.token_expiry(
            Forge(repo.forge), repo.forge_api_url, token, repo.url, client=client
        )
        repo.forge_token_error = None
    except ForgeError as exc:
        repo.forge_token_error = str(exc)
    repo.forge_token_checked_at = now or datetime.now(UTC)


def warning(repo: Repository, now: datetime) -> Event | None:
    """A notification for a token that stopped working or expires soon, once per token."""
    if repo.forge_token_warned_at is not None:
        return None
    if repo.forge_token_error:
        title = f"The {repo.forge} token for {repo.name} no longer works"
        text = repo.forge_token_error
    elif (expires := repo.forge_token_expires_at) and expires - now <= WARN_BEFORE:
        title = f"The {repo.forge} token for {repo.name} expires on {expires:%Y-%m-%d}"
        text = "After that, pull requests can't be opened. Replace it on the repository's page."
    else:
        return None
    return Event(
        EventType.FORGE_TOKEN,
        title,
        text,
        link=f"/repositories/{repo.id}",
        data={
            "repository": repo.name,
            "expires_at": repo.forge_token_expires_at and repo.forge_token_expires_at.isoformat(),
        },
    )


async def check_all(
    sessionmaker: async_sessionmaker[AsyncSession],
    cipher: Cipher,
    notify: Callable[[Event], None] | None,
    now: datetime | None = None,
) -> None:
    """Check the tokens not checked for a day, and warn about those that need attention."""
    now = now or datetime.now(UTC)
    async with sessionmaker() as db:
        repos = await db.scalars(
            select(Repository).where(
                Repository.forge != Forge.NONE,
                or_(Repository.forge_token.is_not(None), Repository.token.is_not(None)),
            )
        )
        async with httpx.AsyncClient(timeout=forges.TIMEOUT) as client:
            for repo in repos:
                checked = repo.forge_token_checked_at
                if checked is None or now - checked >= CHECK_EVERY:
                    await check(repo, cipher, client=client, now=now)
                if (event := warning(repo, now)) is not None:
                    repo.forge_token_warned_at = now
                    if notify is not None:
                        notify(event)
                    log.warning("%s", event.title)
        await db.commit()
