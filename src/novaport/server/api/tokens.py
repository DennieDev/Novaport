# SPDX-License-Identifier: AGPL-3.0-or-later
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import Select, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload

from novaport.auth.tokens import create_token
from novaport.models import ApiToken, Role
from novaport.server.deps import DB, AdminAccess, CurrentAccess, SessionAccess

router = APIRouter(prefix="/api/tokens", tags=["tokens"])


class TokenIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    role: Role
    # None for a token that doesn't expire.
    expires_in_days: int | None = Field(default=90, ge=1, le=3650)


class TokenOut(BaseModel):
    id: uuid.UUID
    name: str
    prefix: str
    role: Role
    username: str
    created_at: datetime
    expires_at: datetime | None
    last_used_at: datetime | None
    last_used_ip: str | None


class TokenCreated(TokenOut):
    token: str


def _out(token: ApiToken) -> TokenOut:
    return TokenOut(
        id=token.id,
        name=token.name,
        prefix=token.prefix,
        role=Role(token.role),
        username=token.user.username,
        created_at=token.created_at,
        expires_at=token.expires_at,
        last_used_at=token.last_used_at,
        last_used_ip=token.last_used_ip,
    )


def _tokens(user_id: uuid.UUID | None = None) -> Select[ApiToken]:
    query = select(ApiToken).options(joinedload(ApiToken.user))
    if user_id is not None:
        query = query.where(ApiToken.user_id == user_id)
    return query.order_by(ApiToken.created_at)


@router.get("")
async def list_tokens(db: DB, access: CurrentAccess) -> list[TokenOut]:
    """Your own tokens."""
    return [_out(t) for t in await db.scalars(_tokens(access.user.id))]


@router.get("/all")
async def list_all_tokens(db: DB, access: AdminAccess) -> list[TokenOut]:
    """Every user's tokens."""
    return [_out(t) for t in await db.scalars(_tokens())]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create(body: TokenIn, db: DB, access: SessionAccess) -> TokenCreated:
    """Create a token for yourself. Its value is in the response and can't be shown again.

    The token acts as you, with no role higher than `role`.
    """
    expires_at = (
        datetime.now(UTC) + timedelta(days=body.expires_in_days) if body.expires_in_days else None
    )
    token, value = create_token(
        db, access.user, name=body.name, role=body.role, expires_at=expires_at
    )
    access.audit(db, "token.create", target=body.name, details={"role": body.role})
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, "You already have a token with this name."
        ) from exc
    await db.refresh(token, ["user"])
    return TokenCreated(**_out(token).model_dump(), token=value)


@router.delete("/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke(token_id: uuid.UUID, db: DB, access: CurrentAccess) -> None:
    """Revoke one of your tokens, or anyone's as an admin."""
    token = await db.scalar(_tokens().where(ApiToken.id == token_id))
    if token is None or (token.user_id != access.user.id and not access.is_admin):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such token.")
    await db.delete(token)
    access.audit(
        db,
        "token.revoke",
        target=token.name,
        details={"owner": token.user.username},
    )
    await db.commit()
