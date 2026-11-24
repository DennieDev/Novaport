# SPDX-License-Identifier: AGPL-3.0-or-later
import logging
import re
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from novaport.auth.passwords import check_strength, hash_password
from novaport.models import GroupMember, OidcProvider, Role, RoleBinding, Session, User

log = logging.getLogger(__name__)

USERNAME_UNSAFE = re.compile(r"[^\w.@+-]")


class UserExists(ValueError):
    pass


async def create_admin(db: AsyncSession, username: str, password: str) -> User:
    check_strength(password)
    if await db.scalar(select(User).where(User.username == username)):
        raise UserExists(f"User {username!r} already exists.")
    user = User(username=username, password_hash=hash_password(password))
    db.add(user)
    await db.flush()
    db.add(RoleBinding(user_id=user.id, role=Role.ADMIN))
    await db.commit()
    return user


class NotLocal(ValueError):
    pass


async def set_password(
    db: AsyncSession, user: User, password: str, *, keep_session: str | None = None
) -> None:
    """Set a local user's password and end their other sessions. Doesn't commit."""
    if not user.is_local:
        raise NotLocal("This user logs in through an identity provider.")
    check_strength(password)
    user.password_hash = hash_password(password)
    sessions = delete(Session).where(Session.user_id == user.id)
    if keep_session is not None:
        sessions = sessions.where(Session.token_hash != keep_session)
    await db.execute(sessions)


async def ensure_initial_admin(db: AsyncSession, username: str, password: str | None) -> None:
    """Create the first admin from configuration when the database has no users yet."""
    if await db.scalar(select(func.count()).select_from(User)):
        return
    if not password:
        log.warning(
            "No users exist. Set NOVAPORT_INITIAL_ADMIN_PASSWORD or run "
            "`novaport create-admin` to create the first admin."
        )
        return
    await create_admin(db, username, password)
    log.info("Created initial admin user %r", username)


def claim(claims: dict[str, Any], path: str) -> Any:
    """Look up a claim; dots step into nested objects, as in Keycloak's `realm_access.roles`."""
    if path in claims:
        return claims[path]
    value: Any = claims
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def claim_values(claims: dict[str, Any], path: str) -> set[str]:
    value = claim(claims, path) if path else None
    if isinstance(value, str):
        return {value}
    if isinstance(value, list):
        return {str(v) for v in value if isinstance(v, str | int)}
    return set()


def _username_base(claims: dict[str, Any], username_claim: str) -> str:
    for candidate in (claim(claims, username_claim), claims.get("email"), claims["sub"]):
        if isinstance(candidate, str) and candidate.strip():
            return USERNAME_UNSAFE.sub("-", candidate.strip())[:140]
    return str(claims["sub"])[:140]


async def _free_username(db: AsyncSession, base: str) -> str:
    candidate, n = base, 2
    while await db.scalar(select(User.id).where(User.username == candidate)):
        candidate = f"{base}-{n}"
        n += 1
    return candidate


async def provision_oidc_user(
    db: AsyncSession, provider: OidcProvider, claims: dict[str, Any]
) -> User:
    """Find or create the user for verified ID token claims and refresh their profile.

    Users are matched on issuer and subject only. Matching on email or username would let
    anyone who controls an identity provider take over existing accounts.
    """
    subject = str(claims["sub"])
    user = await db.scalar(
        select(User).where(User.issuer == provider.issuer, User.subject == subject)
    )
    if user is None:
        username = await _free_username(db, _username_base(claims, provider.username_claim))
        user = User(username=username, issuer=provider.issuer, subject=subject)
        db.add(user)
    name = claims.get("name")
    if isinstance(name, str) and name:
        user.name = name[:200]
    email = claims.get("email")
    if isinstance(email, str) and email and claims.get("email_verified") is not False:
        user.email = email[:320]
    await db.flush()
    return user


async def sync_groups(
    db: AsyncSession, provider: OidcProvider, user: User, values: set[str]
) -> None:
    """Make the user's memberships from this provider match the groups claim.

    Memberships added by hand, or synced from another provider, are left alone.
    """
    wanted = {m.group_id for m in provider.mappings if m.claim_value in values}
    current = await db.scalars(select(GroupMember).where(GroupMember.user_id == user.id))
    for member in current:
        if member.provider_id == provider.id and member.group_id not in wanted:
            await db.delete(member)
        wanted.discard(member.group_id)
    for group_id in wanted:
        db.add(GroupMember(group_id=group_id, user_id=user.id, provider_id=provider.id))
