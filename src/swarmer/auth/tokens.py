# SPDX-License-Identifier: AGPL-3.0-or-later
import secrets
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from swarmer.auth.sessions import TOUCH_INTERVAL, hash_token
from swarmer.models import ApiToken, Role, User

# Recognizable in leaked text, so secret scanners can flag it.
TOKEN_PREFIX = "sw_"  # noqa: S105
SHOWN_PREFIX_LENGTH = 10


def create_token(
    db: AsyncSession, user: User, *, name: str, role: Role, expires_at: datetime | None
) -> tuple[ApiToken, str]:
    """Add a token to the session and return it with its value, which is never stored."""
    value = TOKEN_PREFIX + secrets.token_urlsafe(32)
    token = ApiToken(
        user_id=user.id,
        name=name,
        token_hash=hash_token(value),
        prefix=value[:SHOWN_PREFIX_LENGTH],
        role=role,
        created_at=datetime.now(UTC),
        expires_at=expires_at,
    )
    db.add(token)
    return token, value


async def resolve_token(db: AsyncSession, value: str, ip: str | None) -> ApiToken | None:
    """Return the token for a bearer value if it is valid, recording when it was used."""
    if not value.startswith(TOKEN_PREFIX):
        return None
    token = await db.scalar(
        select(ApiToken)
        .options(joinedload(ApiToken.user))
        .where(ApiToken.token_hash == hash_token(value))
    )
    now = datetime.now(UTC)
    if token is None or token.user.disabled or (token.expires_at and token.expires_at <= now):
        return None
    if (
        token.last_used_at is None
        or now - token.last_used_at >= TOUCH_INTERVAL
        or token.last_used_ip != ip
    ):
        token.last_used_at = now
        token.last_used_ip = ip
        await db.commit()
    return token
