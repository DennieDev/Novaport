# SPDX-License-Identifier: AGPL-3.0-or-later
import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from novaport.auth.passwords import hash_password, needs_rehash, verify_password
from novaport.auth.ratelimit import LoginThrottle
from novaport.auth.sessions import create_session, delete_session
from novaport.models import User
from novaport.server.deps import DB, CurrentUser, get_settings

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=150)
    password: str = Field(min_length=1, max_length=1024)


class Me(BaseModel):
    id: uuid.UUID
    username: str
    is_admin: bool


def get_throttle(request: Request) -> LoginThrottle:
    throttle: LoginThrottle = request.app.state.login_throttle
    return throttle


def _set_cookie(request: Request, response: Response, token: str) -> None:
    settings = get_settings(request)
    response.set_cookie(
        settings.session_cookie,
        token,
        max_age=int(settings.session_max_age.total_seconds()),
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="lax",
        path="/",
    )


@router.post("/login", status_code=status.HTTP_204_NO_CONTENT)
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    db: DB,
    throttle: Annotated[LoginThrottle, Depends(get_throttle)],
) -> None:
    client_ip = request.client.host if request.client else "unknown"
    keys = (f"ip:{client_ip}", f"user:{body.username.lower()}")
    if throttle.blocked(*keys):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many failed attempts.")

    user = await db.scalar(select(User).where(User.username == body.username))
    valid = verify_password(user.password_hash if user else None, body.password)
    if user is None or not valid or user.disabled:
        throttle.failed(*keys)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid username or password.")

    throttle.succeeded(*keys)
    if user.password_hash and needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)
    user.last_login_at = datetime.now(UTC)

    token = await create_session(
        db,
        user,
        idle_timeout=get_settings(request).session_idle_timeout,
        ip=client_ip,
        user_agent=request.headers.get("user-agent"),
    )
    await db.commit()
    _set_cookie(request, response, token)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, response: Response, db: DB) -> None:
    name = get_settings(request).session_cookie
    token = request.cookies.get(name)
    if token:
        await delete_session(db, token)
    response.delete_cookie(name, path="/")


@router.get("/me")
async def me(user: CurrentUser) -> Me:
    return Me(id=user.id, username=user.username, is_admin=user.is_admin)
