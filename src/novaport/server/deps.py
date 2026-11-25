# SPDX-License-Identifier: AGPL-3.0-or-later
import uuid
from collections import defaultdict
from collections.abc import AsyncIterator, Callable, Coroutine, Mapping
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.requests import HTTPConnection

from novaport.auth.access import Access, Forbidden, load_access
from novaport.auth.sessions import resolve_session
from novaport.auth.tokens import resolve_token
from novaport.config import Settings
from novaport.docker.client import ClusterReader
from novaport.docker.views import STACK_LABEL
from novaport.models import ApiToken, Role, Stack, User


def get_settings(conn: HTTPConnection) -> Settings:
    settings: Settings = conn.app.state.settings
    return settings


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    maker: async_sessionmaker[AsyncSession] = request.app.state.sessionmaker
    async with maker() as db:
        yield db


DB = Annotated[AsyncSession, Depends(get_db)]


@dataclass(frozen=True)
class Principal:
    user: User
    token: ApiToken | None = None


def bearer_token(conn: HTTPConnection) -> str | None:
    scheme, _, value = conn.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer":
        return None
    return value.strip() or None


async def authenticate(conn: HTTPConnection, db: AsyncSession) -> Principal | None:
    """The user behind a request: an API token when one is sent, otherwise the session cookie.

    A request with a bearer token never falls back to the cookie.
    """
    if conn.headers.get("authorization"):
        value = bearer_token(conn)
        token = await resolve_token(db, value, client_ip(conn)) if value else None
        return Principal(token.user, token) if token else None
    settings = get_settings(conn)
    cookie = conn.cookies.get(settings.session_cookie)
    if not cookie:
        return None
    session = await resolve_session(
        db,
        cookie,
        idle_timeout=settings.session_idle_timeout,
        max_age=settings.session_max_age,
    )
    return Principal(session.user) if session else None


async def current_principal(request: Request, db: DB) -> Principal:
    principal = await authenticate(request, db)
    if principal is None:
        detail = (
            "Invalid or expired API token."
            if request.headers.get("authorization")
            else "Not logged in."
        )
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail)
    return principal


CurrentPrincipal = Annotated[Principal, Depends(current_principal)]


async def current_user(principal: CurrentPrincipal) -> User:
    return principal.user


CurrentUser = Annotated[User, Depends(current_user)]


def get_reader(conn: HTTPConnection) -> ClusterReader:
    reader: ClusterReader | None = conn.app.state.docker
    if reader is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "No Docker connection configured.")
    return reader


Reader = Annotated[ClusterReader, Depends(get_reader)]


def client_ip(conn: HTTPConnection) -> str | None:
    return conn.client.host if conn.client else None


async def access_for(db: AsyncSession, principal: Principal, ip: str | None) -> Access:
    token = principal.token
    return await load_access(
        db,
        principal.user,
        ip,
        cap=Role(token.role) if token else None,
        token=token.name if token else None,
    )


async def current_access(request: Request, principal: CurrentPrincipal, db: DB) -> Access:
    access = await access_for(db, principal, client_ip(request))
    # Read by the Forbidden handler to attribute denied requests.
    request.state.access = access
    return access


CurrentAccess = Annotated[Access, Depends(current_access)]


async def session_access(access: CurrentAccess) -> Access:
    """For actions that would let a leaked token outlive its revocation, such as creating
    tokens or changing the password."""
    if access.token is not None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "API tokens can't do this. Log in instead.")
    return access


SessionAccess = Annotated[Access, Depends(session_access)]


def require_role(role: Role) -> Callable[[Access], Coroutine[Any, Any, Access]]:
    """Dependency that requires a role on everything, not just on some stacks."""

    async def check(access: CurrentAccess) -> Access:
        access.require(role)
        return access

    return check


ViewerAccess = Annotated[Access, Depends(require_role(Role.VIEWER))]
OperatorAccess = Annotated[Access, Depends(require_role(Role.OPERATOR))]
AdminAccess = Annotated[Access, Depends(require_role(Role.ADMIN))]


TERMINAL_ACTION = "terminal.session"


def refuse_token_terminal(access: Access, action: str | None) -> None:
    """A shell in a container is more than scripts should get from a token, so terminals
    need a login."""
    if action == TERMINAL_ACTION and access.token is not None:
        raise Forbidden("API tokens can't open terminals.", action=action)


async def websocket_access(conn: HTTPConnection) -> Access | None:
    async with conn.app.state.sessionmaker() as db:
        principal = await authenticate(conn, db)
        if principal is None:
            return None
        return await access_for(db, principal, client_ip(conn))


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
