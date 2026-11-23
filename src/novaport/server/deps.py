# SPDX-License-Identifier: AGPL-3.0-or-later
import uuid
from collections import defaultdict
from collections.abc import AsyncIterator, Callable, Coroutine, Mapping
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.requests import HTTPConnection

from novaport.auth.access import Access, Forbidden, load_access
from novaport.auth.sessions import resolve_session
from novaport.config import Settings
from novaport.docker.client import ClusterReader
from novaport.docker.views import STACK_LABEL
from novaport.models import Role, Session, Stack, User


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


def client_ip(conn: HTTPConnection) -> str | None:
    return conn.client.host if conn.client else None


async def current_access(request: Request, user: CurrentUser, db: DB) -> Access:
    access = await load_access(db, user, client_ip(request))
    # Read by the Forbidden handler to attribute denied requests.
    request.state.access = access
    return access


CurrentAccess = Annotated[Access, Depends(current_access)]


def require_role(role: Role) -> Callable[[Access], Coroutine[Any, Any, Access]]:
    """Dependency that requires a role on everything, not just on some stacks."""

    async def check(access: CurrentAccess) -> Access:
        access.require(role)
        return access

    return check


ViewerAccess = Annotated[Access, Depends(require_role(Role.VIEWER))]
OperatorAccess = Annotated[Access, Depends(require_role(Role.OPERATOR))]
AdminAccess = Annotated[Access, Depends(require_role(Role.ADMIN))]


async def websocket_access(conn: HTTPConnection) -> Access | None:
    async with conn.app.state.sessionmaker() as db:
        session = await authenticate(conn, db)
        if session is None:
            return None
        return await load_access(db, session.user, client_ip(conn))


async def visible_stack_names(db: AsyncSession, access: Access, role: Role) -> set[str] | None:
    """Docker stack names the user holds a role on, or None when the role is global.

    Several Novaport stacks can share a name; the role must cover all of them.
    """
    if access.can(role):
        return None
    ids_by_name: dict[str, list[uuid.UUID]] = defaultdict(list)
    for stack_id, name in await db.execute(select(Stack.id, Stack.name)):
        ids_by_name[name].append(stack_id)
    return {name for name, ids in ids_by_name.items() if all(access.can(role, i) for i in ids)}


def service_stack(raw_service: Mapping[str, Any]) -> str | None:
    labels: Mapping[str, str] = raw_service.get("Spec", {}).get("Labels") or {}
    return labels.get(STACK_LABEL)


async def require_service_role(
    db: AsyncSession,
    access: Access,
    role: Role,
    raw_service: Mapping[str, Any],
    *,
    action: str | None = None,
) -> None:
    if access.can(role):
        return
    stack = service_stack(raw_service)
    allowed = await visible_stack_names(db, access, role) or set()
    if stack is None or stack not in allowed:
        target = raw_service.get("Spec", {}).get("Name") or raw_service.get("ID")
        raise Forbidden(action=action, target=target)
