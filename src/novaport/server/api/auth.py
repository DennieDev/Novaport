# SPDX-License-Identifier: AGPL-3.0-or-later
import logging
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.orm import selectinload
from starlette.requests import HTTPConnection

from novaport.auth.access import record
from novaport.auth.oidc import OidcClient, OidcError, new_pkce
from novaport.auth.passwords import hash_password, needs_rehash, verify_password
from novaport.auth.ratelimit import LoginThrottle
from novaport.auth.sessions import create_session, delete_session, hash_token, resolve_session
from novaport.auth.users import claim, claim_values, provision_oidc_user, sync_groups
from novaport.crypto import Cipher, MissingSecretKey
from novaport.models import LoginState, OidcProvider, Outcome, Role, User
from novaport.server.deps import DB, CurrentAccess, client_ip, get_settings

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])

LOGIN_TIMEOUT = timedelta(minutes=10)
BROWSER_COOKIE = "novaport_oidc"
OIDC_PATH = "/api/auth/oidc"


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=150)
    password: str = Field(min_length=1, max_length=1024)


class Me(BaseModel):
    id: uuid.UUID
    username: str
    name: str | None
    email: str | None
    local: bool
    global_role: Role | None
    stack_roles: dict[uuid.UUID, Role]


class ProviderLink(BaseModel):
    slug: str
    display_name: str
    login_url: str


def get_throttle(request: Request) -> LoginThrottle:
    throttle: LoginThrottle = request.app.state.login_throttle
    return throttle


def get_oidc(conn: HTTPConnection) -> OidcClient:
    client: OidcClient = conn.app.state.oidc
    return client


Oidc = Annotated[OidcClient, Depends(get_oidc)]


def _secure(request: Request) -> bool:
    return request.url.scheme == "https"


def _set_cookie(request: Request, response: Response, token: str) -> None:
    settings = get_settings(request)
    response.set_cookie(
        settings.session_cookie,
        token,
        max_age=int(settings.session_max_age.total_seconds()),
        httponly=True,
        secure=_secure(request),
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
    ip = client_ip(request) or "unknown"
    keys = (f"ip:{ip}", f"user:{body.username.lower()}")
    if throttle.blocked(*keys):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many failed attempts.")

    user = await db.scalar(select(User).where(User.username == body.username))
    valid = verify_password(user.password_hash if user else None, body.password)
    if user is None or not valid or user.disabled:
        throttle.failed(*keys)
        record(
            db,
            "auth.login",
            user=user,
            username=body.username[:150],
            ip=ip,
            outcome=Outcome.FAILED,
            details={"method": "password", "reason": "disabled" if valid else "bad credentials"},
        )
        await db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid username or password.")

    throttle.succeeded(*keys)
    if user.password_hash and needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)
    user.last_login_at = datetime.now(UTC)

    token = await create_session(
        db,
        user,
        idle_timeout=get_settings(request).session_idle_timeout,
        ip=ip,
        user_agent=request.headers.get("user-agent"),
    )
    record(db, "auth.login", user=user, ip=ip, details={"method": "password"})
    await db.commit()
    _set_cookie(request, response, token)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, response: Response, db: DB) -> None:
    settings = get_settings(request)
    token = request.cookies.get(settings.session_cookie)
    if token:
        session = await resolve_session(
            db,
            token,
            idle_timeout=settings.session_idle_timeout,
            max_age=settings.session_max_age,
        )
        if session is not None:
            record(db, "auth.logout", user=session.user, ip=client_ip(request))
        await delete_session(db, token)
    response.delete_cookie(settings.session_cookie, path="/")


@router.get("/me")
async def me(access: CurrentAccess) -> Me:
    user = access.user
    return Me(
        id=user.id,
        username=user.username,
        name=user.name,
        email=user.email,
        local=user.is_local,
        global_role=access.global_role,
        stack_roles=dict(access.stack_roles),
    )


@router.get("/providers")
async def providers(db: DB) -> list[ProviderLink]:
    """Enabled OIDC providers, for the login page. Needs no login."""
    found = await db.scalars(
        select(OidcProvider).where(OidcProvider.enabled).order_by(OidcProvider.display_name)
    )
    return [
        ProviderLink(
            slug=p.slug, display_name=p.display_name, login_url=f"{OIDC_PATH}/{p.slug}/login"
        )
        for p in found
    ]


def redirect_uri(conn: HTTPConnection, slug: str) -> str:
    public = get_settings(conn).public_url
    base = public.rstrip("/") if public else str(conn.base_url).rstrip("/")
    return f"{base}{OIDC_PATH}/{slug}/callback"


def safe_next(path: str | None) -> str:
    """Only same-site paths, so the login can't be used as an open redirect."""
    if not path or not path.startswith("/") or path.startswith(("//", "/\\")):
        return "/"
    return path[:500]


def _login_error(request: Request, message: str) -> RedirectResponse:
    response = RedirectResponse(f"/login?error={quote(message)}", status.HTTP_302_FOUND)
    response.delete_cookie(BROWSER_COOKIE, path=OIDC_PATH)
    return response


@router.get("/oidc/{slug}/login")
async def oidc_login(
    slug: str, request: Request, db: DB, oidc: Oidc, next: str | None = None
) -> RedirectResponse:
    provider = await db.scalar(
        select(OidcProvider).where(OidcProvider.slug == slug, OidcProvider.enabled)
    )
    if provider is None:
        return _login_error(request, "This login provider doesn't exist or is disabled.")
    try:
        discovery = await oidc.discover(provider.issuer)
    except OidcError as exc:
        log.warning("OIDC discovery for %s failed: %s", slug, exc)
        return _login_error(request, f"{provider.display_name} is unavailable: {exc}")

    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    browser = secrets.token_urlsafe(32)
    pkce = new_pkce()
    now = datetime.now(UTC)
    await db.execute(delete(LoginState).where(LoginState.expires_at <= now))
    db.add(
        LoginState(
            state_hash=hash_token(state),
            provider_id=provider.id,
            browser_hash=hash_token(browser),
            code_verifier=pkce.verifier,
            nonce=nonce,
            next_path=safe_next(next),
            expires_at=now + LOGIN_TIMEOUT,
        )
    )
    await db.commit()

    url = oidc.authorization_url(
        discovery,
        client_id=provider.client_id,
        redirect_uri=redirect_uri(request, slug),
        scopes=provider.scopes,
        state=state,
        nonce=nonce,
        challenge=pkce.challenge,
    )
    response = RedirectResponse(url, status.HTTP_302_FOUND)
    response.set_cookie(
        BROWSER_COOKIE,
        browser,
        max_age=int(LOGIN_TIMEOUT.total_seconds()),
        httponly=True,
        secure=_secure(request),
        samesite="lax",
        path=OIDC_PATH,
    )
    return response


class LoginFailed(Exception):
    def __init__(self, message: str, user: User | None = None) -> None:
        super().__init__(message)
        # Plain values: the ORM object is expired once the transaction is rolled back.
        self.user_id = user.id if user else None
        self.username = user.username if user else None


@router.get("/oidc/{slug}/callback")
async def oidc_callback(
    slug: str,
    request: Request,
    db: DB,
    oidc: Oidc,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
) -> RedirectResponse:
    ip = client_ip(request)
    try:
        user, token, next_path = await _complete_login(
            slug, request, db, oidc, code, state, error, error_description
        )
    except LoginFailed as exc:
        await db.rollback()
        record(
            db,
            "auth.login",
            user_id=exc.user_id,
            username=exc.username,
            ip=ip,
            outcome=Outcome.FAILED,
            details={"method": "oidc", "provider": slug, "reason": str(exc)},
        )
        await db.commit()
        return _login_error(request, str(exc))

    record(db, "auth.login", user=user, ip=ip, details={"method": "oidc", "provider": slug})
    await db.commit()
    response = RedirectResponse(next_path, status.HTTP_302_FOUND)
    _set_cookie(request, response, token)
    response.delete_cookie(BROWSER_COOKIE, path=OIDC_PATH)
    return response


async def _take_login_state(db: DB, state: str | None) -> LoginState | None:
    """Look up a pending login and delete it, so each state works once."""
    if not state:
        return None
    found = await db.get(LoginState, hash_token(state))
    if found is None:
        return None
    await db.delete(found)
    await db.commit()
    return found if found.expires_at > datetime.now(UTC) else None


async def _complete_login(
    slug: str,
    request: Request,
    db: DB,
    oidc: OidcClient,
    code: str | None,
    state: str | None,
    error: str | None,
    error_description: str | None,
) -> tuple[User, str, str]:
    pending = await _take_login_state(db, state)
    if error:
        raise LoginFailed(f"The provider refused the login: {error_description or error}")
    provider = await db.scalar(
        select(OidcProvider)
        .options(selectinload(OidcProvider.mappings))
        .where(OidcProvider.slug == slug)
    )
    if provider is None or not provider.enabled:
        raise LoginFailed("This login provider doesn't exist or is disabled.")
    if pending is None or pending.provider_id != provider.id:
        raise LoginFailed("This login link expired or was already used. Please try again.")
    browser = request.cookies.get(BROWSER_COOKIE, "")
    if not secrets.compare_digest(hash_token(browser), pending.browser_hash):
        raise LoginFailed("This login was started in a different browser. Please try again.")
    if not code:
        raise LoginFailed("The provider didn't return an authorization code.")

    cipher: Cipher = request.app.state.cipher
    try:
        client_secret = cipher.decrypt(provider.client_secret) if provider.client_secret else None
    except (MissingSecretKey, ValueError) as exc:
        raise LoginFailed(f"The client secret for {provider.display_name} is unusable.") from exc

    try:
        discovery = await oidc.discover(provider.issuer)
        tokens = await oidc.exchange_code(
            discovery,
            client_id=provider.client_id,
            client_secret=client_secret,
            code=code,
            redirect_uri=redirect_uri(request, slug),
            verifier=pending.code_verifier,
        )
        claims = await oidc.verify_id_token(
            discovery, tokens["id_token"], client_id=provider.client_id, nonce=pending.nonce
        )
        # Many providers leave groups out of the ID token and only return them from userinfo.
        access_token = tokens.get("access_token")
        if provider.groups_claim and claim(claims, provider.groups_claim) is None and access_token:
            info = await oidc.userinfo(discovery, access_token)
            if info.get("sub") == claims["sub"]:
                claims = {**info, **claims}
    except OidcError as exc:
        log.warning("OIDC login through %s failed: %s", slug, exc)
        raise LoginFailed(str(exc)) from exc

    user = await provision_oidc_user(db, provider, claims)
    if user.disabled:
        raise LoginFailed("This account is disabled.", user)
    await sync_groups(db, provider, user, claim_values(claims, provider.groups_claim))
    user.last_login_at = datetime.now(UTC)
    token = await create_session(
        db,
        user,
        idle_timeout=get_settings(request).session_idle_timeout,
        ip=client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return user, token, pending.next_path
