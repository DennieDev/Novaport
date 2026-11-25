# SPDX-License-Identifier: AGPL-3.0-or-later
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from novaport.models import AuditEntry, GroupMember, Outcome, Role, RoleBinding, Stack, User


class Forbidden(Exception):
    """Raised when the current user lacks a role. Denied state-changing requests are audited."""

    def __init__(
        self,
        message: str = "You don't have permission to do this.",
        *,
        action: str | None = None,
        target: str | None = None,
        stack_id: uuid.UUID | None = None,
    ) -> None:
        super().__init__(message)
        self.action = action
        self.target = target
        self.stack_id = stack_id


def _highest(roles: Iterable[Role | None]) -> Role | None:
    present = [r for r in roles if r is not None]
    return max(present, key=lambda r: r.rank) if present else None


def _capped(role: Role, cap: Role) -> Role:
    return role if cap.includes(role) else cap


@dataclass(frozen=True)
class Access:
    user: User
    global_role: Role | None
    stack_roles: Mapping[uuid.UUID, Role] = field(default_factory=dict)
    ip: str | None = None
    # Name of the API token the request came with, if it didn't come with a session.
    token: str | None = None

    def role_for(self, stack_id: uuid.UUID | None = None) -> Role | None:
        stack_role = self.stack_roles.get(stack_id) if stack_id else None
        return _highest((self.global_role, stack_role))

    def can(self, role: Role, stack_id: uuid.UUID | None = None) -> bool:
        have = self.role_for(stack_id)
        return have is not None and have.includes(role)

    def require(
        self,
        role: Role,
        stack_id: uuid.UUID | None = None,
        *,
        action: str | None = None,
        target: str | None = None,
    ) -> None:
        if not self.can(role, stack_id):
            raise Forbidden(action=action, target=target, stack_id=stack_id)

    @property
    def is_admin(self) -> bool:
        return self.can(Role.ADMIN)

    def stacks_with(self, role: Role, stacks: Iterable[Stack]) -> list[Stack]:
        return [s for s in stacks if self.can(role, s.id)]

    def audit(
        self,
        db: AsyncSession,
        action: str,
        *,
        outcome: Outcome = Outcome.SUCCESS,
        target: str | None = None,
        stack_id: uuid.UUID | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        record(
            db,
            action,
            user=self.user,
            ip=self.ip,
            outcome=outcome,
            target=target,
            stack_id=stack_id,
            details=self.with_token(details),
        )

    def with_token(self, details: dict[str, Any] | None) -> dict[str, Any] | None:
        if self.token is None:
            return details
        return {**(details or {}), "token": self.token}


def record(
    db: AsyncSession,
    action: str,
    *,
    user: User | None = None,
    user_id: uuid.UUID | None = None,
    username: str | None = None,
    ip: str | None = None,
    outcome: Outcome = Outcome.SUCCESS,
    target: str | None = None,
    stack_id: uuid.UUID | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    """Add an audit entry to the session. The caller commits."""
    db.add(
        AuditEntry(
            user_id=user.id if user else user_id,
            username=user.username if user else username,
            action=action,
            target=target[:255] if target else None,
            stack_id=stack_id,
            ip=ip,
            outcome=outcome,
            details=details or {},
        )
    )


def _group_ids(user_id: uuid.UUID) -> Any:
    return select(GroupMember.group_id).where(GroupMember.user_id == user_id)


async def load_access(
    db: AsyncSession,
    user: User,
    ip: str | None = None,
    *,
    cap: Role | None = None,
    token: str | None = None,
) -> Access:
    """The user's roles. With `cap`, no role is higher than it (API tokens)."""
    bindings = await db.scalars(
        select(RoleBinding).where(
            or_(RoleBinding.user_id == user.id, RoleBinding.group_id.in_(_group_ids(user.id)))
        )
    )
    global_role: Role | None = None
    stack_roles: dict[uuid.UUID, Role] = {}
    for binding in bindings:
        role = Role(binding.role)
        if binding.stack_id is None:
            global_role = _highest((global_role, role))
        else:
            current = stack_roles.get(binding.stack_id)
            if current is None or role.rank > current.rank:
                stack_roles[binding.stack_id] = role
    if cap is not None:
        global_role = _capped(global_role, cap) if global_role else None
        stack_roles = {stack: _capped(role, cap) for stack, role in stack_roles.items()}
    return Access(user=user, global_role=global_role, stack_roles=stack_roles, ip=ip, token=token)


async def admin_count(db: AsyncSession) -> int:
    """Enabled users holding the global admin role, directly or through a group."""
    global_admin = (RoleBinding.role == Role.ADMIN) & RoleBinding.stack_id.is_(None)
    direct = select(RoleBinding.user_id.label("user_id")).where(
        global_admin, RoleBinding.user_id.is_not(None)
    )
    via_group = (
        select(GroupMember.user_id.label("user_id"))
        .join(RoleBinding, RoleBinding.group_id == GroupMember.group_id)
        .where(global_admin)
    )
    admins = direct.union(via_group).subquery()
    count = await db.scalar(
        select(func.count())
        .select_from(User)
        .where(User.id.in_(select(admins.c.user_id)), User.disabled.is_(False))
    )
    return count or 0
