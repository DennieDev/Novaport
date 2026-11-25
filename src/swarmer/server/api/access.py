# SPDX-License-Identifier: AGPL-3.0-or-later
import re
import uuid
from datetime import datetime
from typing import Annotated, Any, Self

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from swarmer.auth.access import admin_count
from swarmer.auth.oidc import OidcClient, OidcError
from swarmer.auth.passwords import WeakPassword
from swarmer.auth.users import NotLocal, set_password
from swarmer.crypto import Cipher, MissingSecretKey
from swarmer.models import (
    AuditEntry,
    Group,
    GroupMapping,
    GroupMember,
    OidcProvider,
    Outcome,
    Role,
    RoleBinding,
    Stack,
    User,
)
from swarmer.server.api.auth import get_oidc, redirect_uri
from swarmer.server.deps import DB, AdminAccess, require_role

router = APIRouter(prefix="/api", tags=["access"], dependencies=[Depends(require_role(Role.ADMIN))])

SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,49}$")


async def _keep_an_admin(db: DB) -> None:
    """Refuse a change that would leave nobody able to administer Swarmer."""
    await db.flush()
    if await admin_count(db) == 0:
        await db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This would leave no enabled user with the global admin role.",
        )


async def _commit(db: DB, conflict: str) -> None:
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, conflict) from exc


# Users


class GroupRef(BaseModel):
    id: uuid.UUID
    name: str
    synced: bool


class UserOut(BaseModel):
    id: uuid.UUID
    username: str
    name: str | None
    email: str | None
    local: bool
    issuer: str | None
    disabled: bool
    created_at: datetime
    last_login_at: datetime | None
    groups: list[GroupRef]


class UserPatch(BaseModel):
    disabled: bool | None = None


async def _users_out(db: DB, users: list[User]) -> list[UserOut]:
    memberships: dict[uuid.UUID, list[GroupRef]] = {u.id: [] for u in users}
    rows = await db.execute(
        select(GroupMember, Group.name)
        .join(Group, Group.id == GroupMember.group_id)
        .where(GroupMember.user_id.in_(memberships))
        .order_by(Group.name)
    )
    for member, name in rows:
        memberships[member.user_id].append(
            GroupRef(id=member.group_id, name=name, synced=member.provider_id is not None)
        )
    return [
        UserOut(
            id=u.id,
            username=u.username,
            name=u.name,
            email=u.email,
            local=u.is_local,
            issuer=u.issuer,
            disabled=u.disabled,
            created_at=u.created_at,
            last_login_at=u.last_login_at,
            groups=memberships[u.id],
        )
        for u in users
    ]


async def _user(db: DB, user_id: uuid.UUID) -> User:
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found.")
    return user


@router.get("/users")
async def list_users(db: DB) -> list[UserOut]:
    users = list(await db.scalars(select(User).order_by(User.username)))
    return await _users_out(db, users)


@router.patch("/users/{user_id}")
async def update_user(user_id: uuid.UUID, body: UserPatch, db: DB, access: AdminAccess) -> UserOut:
    user = await _user(db, user_id)
    if body.disabled is not None and body.disabled != user.disabled:
        if user.id == access.user.id:
            raise HTTPException(status.HTTP_409_CONFLICT, "You can't disable your own account.")
        user.disabled = body.disabled
        await _keep_an_admin(db)
        access.audit(db, "user.disable" if body.disabled else "user.enable", target=user.username)
    await db.commit()
    return (await _users_out(db, [user]))[0]


class PasswordReset(BaseModel):
    new_password: str = Field(min_length=1, max_length=1024)


@router.post("/users/{user_id}/password", status_code=status.HTTP_204_NO_CONTENT)
async def reset_password(
    user_id: uuid.UUID, body: PasswordReset, db: DB, access: AdminAccess
) -> None:
    """Set another local user's password. All their sessions end."""
    user = await _user(db, user_id)
    try:
        await set_password(db, user, body.new_password)
    except (WeakPassword, NotLocal) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    access.audit(db, "user.password", target=user.username)
    await db.commit()


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(user_id: uuid.UUID, db: DB, access: AdminAccess) -> None:
    """Remove a user, their sessions, memberships, and bindings.

    OIDC users are created again on their next login, without the old roles.
    """
    user = await _user(db, user_id)
    if user.id == access.user.id:
        raise HTTPException(status.HTTP_409_CONFLICT, "You can't delete your own account.")
    await db.delete(user)
    await _keep_an_admin(db)
    access.audit(db, "user.delete", target=user.username)
    await db.commit()


# Groups


class GroupIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=500)


class GroupPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)


class MemberOut(BaseModel):
    user_id: uuid.UUID
    username: str
    synced: bool


class GroupOut(BaseModel):
    id: uuid.UUID
    name: str
    description: str
    created_at: datetime
    members: list[MemberOut]


async def _group(db: DB, group_id: uuid.UUID) -> Group:
    group = await db.scalar(
        select(Group).options(selectinload(Group.members)).where(Group.id == group_id)
    )
    if group is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Group not found.")
    return group


async def _group_out(db: DB, group: Group) -> GroupOut:
    rows = await db.execute(
        select(User.id, User.username).where(User.id.in_([m.user_id for m in group.members]))
    )
    names = {user_id: username for user_id, username in rows}
    members = sorted(
        (
            MemberOut(
                user_id=m.user_id, username=names.get(m.user_id, "?"), synced=bool(m.provider_id)
            )
            for m in group.members
        ),
        key=lambda m: m.username,
    )
    return GroupOut(
        id=group.id,
        name=group.name,
        description=group.description,
        created_at=group.created_at,
        members=members,
    )


@router.get("/groups")
async def list_groups(db: DB) -> list[GroupOut]:
    groups = await db.scalars(
        select(Group).options(selectinload(Group.members)).order_by(Group.name)
    )
    return [await _group_out(db, g) for g in groups]


@router.post("/groups", status_code=status.HTTP_201_CREATED)
async def create_group(body: GroupIn, db: DB, access: AdminAccess) -> GroupOut:
    group = Group(id=uuid.uuid4(), name=body.name, description=body.description)
    db.add(group)
    access.audit(db, "group.create", target=group.name)
    await _commit(db, "A group with this name exists.")
    return await _group_out(db, await _group(db, group.id))


@router.patch("/groups/{group_id}")
async def update_group(
    group_id: uuid.UUID, body: GroupPatch, db: DB, access: AdminAccess
) -> GroupOut:
    group = await _group(db, group_id)
    before = group.name
    if body.name is not None:
        group.name = body.name
    if body.description is not None:
        group.description = body.description
    access.audit(db, "group.update", target=before, details=body.model_dump(exclude_unset=True))
    await _commit(db, "A group with this name exists.")
    return await _group_out(db, group)


@router.delete("/groups/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_group(group_id: uuid.UUID, db: DB, access: AdminAccess) -> None:
    group = await _group(db, group_id)
    await db.delete(group)
    await _keep_an_admin(db)
    access.audit(db, "group.delete", target=group.name)
    await db.commit()


@router.put("/groups/{group_id}/members/{user_id}")
async def add_member(
    group_id: uuid.UUID, user_id: uuid.UUID, db: DB, access: AdminAccess
) -> GroupOut:
    group = await _group(db, group_id)
    user = await _user(db, user_id)
    member = next((m for m in group.members if m.user_id == user_id), None)
    if member is None:
        group.members.append(GroupMember(user_id=user_id))
    else:
        # Added by hand now, so the next login doesn't take it away.
        member.provider_id = None
    access.audit(db, "group.add_member", target=group.name, details={"user": user.username})
    await db.commit()
    return await _group_out(db, await _group(db, group_id))


@router.delete("/groups/{group_id}/members/{user_id}")
async def remove_member(
    group_id: uuid.UUID, user_id: uuid.UUID, db: DB, access: AdminAccess
) -> GroupOut:
    group = await _group(db, group_id)
    user = await _user(db, user_id)
    member = next((m for m in group.members if m.user_id == user_id), None)
    if member is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not a member of this group.")
    group.members.remove(member)
    await _keep_an_admin(db)
    access.audit(db, "group.remove_member", target=group.name, details={"user": user.username})
    await db.commit()
    return await _group_out(db, await _group(db, group_id))


# OIDC providers


class MappingIn(BaseModel):
    claim_value: str = Field(min_length=1, max_length=255)
    group_id: uuid.UUID


class MappingOut(MappingIn):
    group_name: str


class ProviderIn(BaseModel):
    slug: str = Field(min_length=1, max_length=50)
    display_name: str = Field(min_length=1, max_length=100)
    issuer: str = Field(min_length=1, max_length=500, pattern=r"^https?://\S+$")
    client_id: str = Field(min_length=1, max_length=255)
    client_secret: str | None = Field(default=None, max_length=2000)
    scopes: str = Field(default="openid profile email", max_length=500)
    username_claim: str = Field(default="preferred_username", min_length=1, max_length=100)
    groups_claim: str = Field(default="groups", max_length=100)
    enabled: bool = True
    mappings: list[MappingIn] = Field(default_factory=list, max_length=500)

    @model_validator(mode="after")
    def _check(self) -> Self:
        if not SLUG.match(self.slug):
            raise ValueError("The slug may only contain lower-case letters, digits, and dashes.")
        if "openid" not in self.scopes.split():
            raise ValueError("The scopes must include 'openid'.")
        return self


class ProviderPatch(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=100)
    issuer: str | None = Field(
        default=None, min_length=1, max_length=500, pattern=r"^https?://\S+$"
    )
    client_id: str | None = Field(default=None, min_length=1, max_length=255)
    # Empty string removes the secret (public client).
    client_secret: str | None = Field(default=None, max_length=2000)
    scopes: str | None = Field(default=None, max_length=500)
    username_claim: str | None = Field(default=None, min_length=1, max_length=100)
    groups_claim: str | None = Field(default=None, max_length=100)
    enabled: bool | None = None
    mappings: list[MappingIn] | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.scopes is not None and "openid" not in self.scopes.split():
            raise ValueError("The scopes must include 'openid'.")
        return self


class ProviderOut(BaseModel):
    id: uuid.UUID
    slug: str
    display_name: str
    issuer: str
    client_id: str
    has_client_secret: bool
    scopes: str
    username_claim: str
    groups_claim: str
    enabled: bool
    redirect_uri: str
    mappings: list[MappingOut]


class ProviderCheck(BaseModel):
    ok: bool
    error: str | None
    authorization_endpoint: str | None
    token_endpoint: str | None
    userinfo_endpoint: str | None


PROVIDER_LOAD = selectinload(OidcProvider.mappings).selectinload(GroupMapping.group)


def get_cipher(request: Request) -> Cipher:
    cipher: Cipher = request.app.state.cipher
    return cipher


CipherDep = Annotated[Cipher, Depends(get_cipher)]


def _provider_out(request: Request, p: OidcProvider) -> ProviderOut:
    return ProviderOut(
        id=p.id,
        slug=p.slug,
        display_name=p.display_name,
        issuer=p.issuer,
        client_id=p.client_id,
        has_client_secret=p.client_secret is not None,
        scopes=p.scopes,
        username_claim=p.username_claim,
        groups_claim=p.groups_claim,
        enabled=p.enabled,
        redirect_uri=redirect_uri(request, p.slug),
        mappings=[
            MappingOut(claim_value=m.claim_value, group_id=m.group_id, group_name=m.group.name)
            for m in p.mappings
        ],
    )


async def _provider(db: DB, provider_id: uuid.UUID) -> OidcProvider:
    provider = await db.scalar(
        select(OidcProvider).options(PROVIDER_LOAD).where(OidcProvider.id == provider_id)
    )
    if provider is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Provider not found.")
    return provider


def _set_secret(provider: OidcProvider, secret: str | None, cipher: Cipher) -> None:
    if secret is None:
        return
    if secret == "":
        provider.client_secret = None
        return
    try:
        provider.client_secret = cipher.encrypt(secret)
    except MissingSecretKey as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc


async def _set_mappings(db: DB, provider: OidcProvider, mappings: list[MappingIn]) -> None:
    group_ids = {m.group_id for m in mappings}
    found = set(await db.scalars(select(Group.id).where(Group.id.in_(group_ids))))
    if missing := group_ids - found:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, f"Unknown group: {next(iter(missing))}"
        )
    unique = {(m.claim_value, m.group_id) for m in mappings}
    provider.mappings.clear()
    await db.flush()
    provider.mappings.extend(
        GroupMapping(claim_value=value, group_id=group_id)
        for value, group_id in sorted(unique, key=str)
    )


@router.get("/oidc-providers")
async def list_providers(request: Request, db: DB) -> list[ProviderOut]:
    found = await db.scalars(
        select(OidcProvider).options(PROVIDER_LOAD).order_by(OidcProvider.display_name)
    )
    return [_provider_out(request, p) for p in found]


@router.post("/oidc-providers", status_code=status.HTTP_201_CREATED)
async def create_provider(
    body: ProviderIn, request: Request, db: DB, cipher: CipherDep, access: AdminAccess
) -> ProviderOut:
    provider = OidcProvider(
        id=uuid.uuid4(),
        slug=body.slug,
        display_name=body.display_name,
        issuer=body.issuer,
        client_id=body.client_id,
        scopes=body.scopes,
        username_claim=body.username_claim,
        groups_claim=body.groups_claim,
        enabled=body.enabled,
        mappings=[],
    )
    _set_secret(provider, body.client_secret, cipher)
    db.add(provider)
    await _set_mappings(db, provider, body.mappings)
    access.audit(
        db, "oidc_provider.create", target=provider.slug, details={"issuer": provider.issuer}
    )
    await _commit(db, "A provider with this slug exists.")
    return _provider_out(request, await _provider(db, provider.id))


@router.get("/oidc-providers/{provider_id}")
async def get_provider(provider_id: uuid.UUID, request: Request, db: DB) -> ProviderOut:
    return _provider_out(request, await _provider(db, provider_id))


@router.patch("/oidc-providers/{provider_id}")
async def update_provider(
    provider_id: uuid.UUID,
    body: ProviderPatch,
    request: Request,
    db: DB,
    cipher: CipherDep,
    access: AdminAccess,
) -> ProviderOut:
    provider = await _provider(db, provider_id)
    for field in (
        "display_name",
        "issuer",
        "client_id",
        "scopes",
        "username_claim",
        "groups_claim",
        "enabled",
    ):
        value = getattr(body, field)
        if value is not None:
            setattr(provider, field, value)
    _set_secret(provider, body.client_secret, cipher)
    if body.mappings is not None:
        await _set_mappings(db, provider, body.mappings)
    changed = sorted(body.model_dump(exclude_unset=True))
    access.audit(db, "oidc_provider.update", target=provider.slug, details={"fields": changed})
    await db.commit()
    db.expire_all()
    return _provider_out(request, await _provider(db, provider_id))


@router.delete("/oidc-providers/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_provider(provider_id: uuid.UUID, db: DB, access: AdminAccess) -> None:
    """Remove a provider and the group memberships it synced. Its users stay."""
    provider = await _provider(db, provider_id)
    await db.delete(provider)
    await _keep_an_admin(db)
    access.audit(db, "oidc_provider.delete", target=provider.slug)
    await db.commit()


@router.post("/oidc-providers/{provider_id}/check")
async def check_provider(
    provider_id: uuid.UUID, db: DB, oidc: Annotated[OidcClient, Depends(get_oidc)]
) -> ProviderCheck:
    """Fetch the provider's discovery document, to catch a wrong issuer before anyone logs in."""
    provider = await _provider(db, provider_id)
    try:
        found = await oidc.discover(provider.issuer, refresh=True)
    except OidcError as exc:
        return ProviderCheck(
            ok=False,
            error=str(exc),
            authorization_endpoint=None,
            token_endpoint=None,
            userinfo_endpoint=None,
        )
    return ProviderCheck(
        ok=True,
        error=None,
        authorization_endpoint=found.authorization_endpoint,
        token_endpoint=found.token_endpoint,
        userinfo_endpoint=found.userinfo_endpoint,
    )


# Role bindings


class BindingIn(BaseModel):
    user_id: uuid.UUID | None = None
    group_id: uuid.UUID | None = None
    role: Role
    stack_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _one_subject(self) -> Self:
        if (self.user_id is None) == (self.group_id is None):
            raise ValueError("Give either a user or a group.")
        return self


class BindingPatch(BaseModel):
    role: Role


class BindingOut(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID | None
    group_id: uuid.UUID | None
    subject: str
    role: Role
    stack_id: uuid.UUID | None
    stack_name: str | None
    created_at: datetime


async def _bindings_out(db: DB, bindings: list[RoleBinding]) -> list[BindingOut]:
    users = {i: name for i, name in await db.execute(select(User.id, User.username))}
    groups = {i: name for i, name in await db.execute(select(Group.id, Group.name))}
    stacks = {i: name for i, name in await db.execute(select(Stack.id, Stack.name))}

    def subject(b: RoleBinding) -> str:
        if b.user_id is not None:
            return users.get(b.user_id, "?")
        return groups.get(b.group_id, "?") if b.group_id is not None else "?"

    return [
        BindingOut(
            id=b.id,
            user_id=b.user_id,
            group_id=b.group_id,
            subject=subject(b),
            role=b.role,
            stack_id=b.stack_id,
            stack_name=stacks.get(b.stack_id) if b.stack_id else None,
            created_at=b.created_at,
        )
        for b in bindings
    ]


def _binding_target(out: BindingOut) -> str:
    kind = "user" if out.user_id else "group"
    return f"{kind} {out.subject} on {out.stack_name or 'everything'}"


async def _binding(db: DB, binding_id: uuid.UUID) -> RoleBinding:
    binding = await db.get(RoleBinding, binding_id)
    if binding is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Role binding not found.")
    return binding


@router.get("/role-bindings")
async def list_bindings(db: DB, stack_id: uuid.UUID | None = None) -> list[BindingOut]:
    query = select(RoleBinding).order_by(RoleBinding.stack_id.nulls_first(), RoleBinding.created_at)
    if stack_id is not None:
        query = query.where(RoleBinding.stack_id == stack_id)
    return await _bindings_out(db, list(await db.scalars(query)))


@router.post("/role-bindings", status_code=status.HTTP_201_CREATED)
async def create_binding(body: BindingIn, db: DB, access: AdminAccess) -> BindingOut:
    if body.user_id:
        await _user(db, body.user_id)
    if body.group_id:
        await _group(db, body.group_id)
    if body.stack_id and await db.get(Stack, body.stack_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Stack not found.")
    binding = RoleBinding(
        id=uuid.uuid4(),
        user_id=body.user_id,
        group_id=body.group_id,
        role=body.role,
        stack_id=body.stack_id,
    )
    db.add(binding)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This user or group already has a role here. Change that one instead.",
        ) from exc
    await db.refresh(binding)
    out = (await _bindings_out(db, [binding]))[0]
    access.audit(
        db,
        "role_binding.create",
        target=_binding_target(out),
        stack_id=binding.stack_id,
        details={"role": binding.role},
    )
    await db.commit()
    return out


@router.patch("/role-bindings/{binding_id}")
async def update_binding(
    binding_id: uuid.UUID, body: BindingPatch, db: DB, access: AdminAccess
) -> BindingOut:
    binding = await _binding(db, binding_id)
    before = binding.role
    binding.role = body.role
    await _keep_an_admin(db)
    out = (await _bindings_out(db, [binding]))[0]
    access.audit(
        db,
        "role_binding.update",
        target=_binding_target(out),
        stack_id=binding.stack_id,
        details={"from": before, "to": body.role},
    )
    await db.commit()
    return out


@router.delete("/role-bindings/{binding_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_binding(binding_id: uuid.UUID, db: DB, access: AdminAccess) -> None:
    binding = await _binding(db, binding_id)
    out = (await _bindings_out(db, [binding]))[0]
    await db.delete(binding)
    await _keep_an_admin(db)
    access.audit(
        db,
        "role_binding.delete",
        target=_binding_target(out),
        stack_id=out.stack_id,
        details={"role": out.role},
    )
    await db.commit()


# Audit log


class AuditOut(BaseModel):
    id: int
    at: datetime
    user_id: uuid.UUID | None
    username: str | None
    action: str
    target: str | None
    stack_id: uuid.UUID | None
    ip: str | None
    outcome: Outcome
    details: dict[str, Any]


@router.get("/audit")
async def audit_log(
    db: DB,
    user_id: uuid.UUID | None = None,
    stack_id: uuid.UUID | None = None,
    action: str | None = Query(default=None, max_length=50),
    outcome: Outcome | None = None,
    before: int | None = Query(default=None, description="Return entries older than this id."),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[AuditOut]:
    query = select(AuditEntry)
    if user_id is not None:
        query = query.where(AuditEntry.user_id == user_id)
    if stack_id is not None:
        query = query.where(AuditEntry.stack_id == stack_id)
    if action:
        # "stack." matches every stack action.
        query = query.where(
            AuditEntry.action.startswith(action, autoescape=True)
            if action.endswith(".")
            else AuditEntry.action == action
        )
    if outcome is not None:
        query = query.where(AuditEntry.outcome == outcome)
    if before is not None:
        query = query.where(AuditEntry.id < before)
    entries = await db.scalars(query.order_by(AuditEntry.id.desc()).limit(limit))
    return [AuditOut.model_validate(e, from_attributes=True) for e in entries]
