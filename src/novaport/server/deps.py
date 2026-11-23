# SPDX-License-Identifier: AGPL-3.0-or-later
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.requests import HTTPConnection

from novaport.auth.sessions import resolve_session
from novaport.config import Settings
from novaport.docker.client import ClusterReader
from novaport.models import Session, User


def get_settings(conn: HTTPConnection) -> Settings:
    settings: Settings = conn.app.state.settings
    return settings


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    maker: async_sessionmaker[AsyncSession] = request.app.state.sessionmaker
    async with maker() as db:
        yield db


DB = Annotated[AsyncSession, Depends(get_db)]


async def authenticate(conn: HTTPConnection, db: AsyncSession) -> Session | None:
    settings = get_settings(conn)
    token = conn.cookies.get(settings.session_cookie)
    if not token:
        return None
    return await resolve_session(
        db,
        token,
        idle_timeout=settings.session_idle_timeout,
        max_age=settings.session_max_age,
    )


async def current_session(request: Request, db: DB) -> Session:
    session = await authenticate(request, db)
    if session is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not logged in.")
    return session


async def current_user(session: Annotated[Session, Depends(current_session)]) -> User:
    return session.user


CurrentUser = Annotated[User, Depends(current_user)]


def get_reader(conn: HTTPConnection) -> ClusterReader:
    reader: ClusterReader | None = conn.app.state.docker
    if reader is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "No Docker connection configured.")
    return reader


Reader = Annotated[ClusterReader, Depends(get_reader)]
