# SPDX-License-Identifier: AGPL-3.0-or-later
import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from novaport.models import Session, User

# Avoid a write on every request; last_seen and expiry only move after this much time.
TOUCH_INTERVAL = timedelta(minutes=1)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(UTC)


async def create_session(
    db: AsyncSession,
    user: User,
    *,
    idle_timeout: timedelta,
    ip: str | None,
    user_agent: str | None,
) -> str:
    token = secrets.token_urlsafe(32)
    now = _now()
    db.add(
        Session(
            token_hash=hash_token(token),
            user_id=user.id,
            created_at=now,
            last_seen_at=now,
            expires_at=now + idle_timeout,
            ip=ip,
            user_agent=user_agent[:512] if user_agent else None,
        )
    )
    return token


async def resolve_session(
    db: AsyncSession, token: str, *, idle_timeout: timedelta, max_age: timedelta
) -> Session | None:
    """Return the live session for a token, extending it on use. Expired sessions are deleted."""
    session = await db.scalar(
        select(Session)
        .options(joinedload(Session.user))
        .where(Session.token_hash == hash_token(token))
    )
    if session is None:
        return None

    now = _now()
    if session.expires_at <= now or session.created_at + max_age <= now or session.user.disabled:
        await db.delete(session)
        await db.commit()
        return None

    if now - session.last_seen_at >= TOUCH_INTERVAL:
        session.last_seen_at = now
        session.expires_at = min(now + idle_timeout, session.created_at + max_age)
        await db.commit()
    return session


async def delete_session(db: AsyncSession, token: str) -> None:
    await db.execute(delete(Session).where(Session.token_hash == hash_token(token)))
    await db.commit()


async def delete_expired_sessions(db: AsyncSession) -> int:
    result = await db.execute(delete(Session).where(Session.expires_at <= _now()))
    await db.commit()
    return result.rowcount or 0  # type: ignore[attr-defined]
