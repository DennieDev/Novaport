# SPDX-License-Identifier: AGPL-3.0-or-later
import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from novaport.db import Base


class Role(StrEnum):
    VIEWER = "viewer"
    OPERATOR = "operator"
    EDITOR = "editor"
    ADMIN = "admin"

    @property
    def rank(self) -> int:
        return ROLE_ORDER.index(self)

    def includes(self, other: "Role") -> bool:
        return self.rank >= other.rank


ROLE_ORDER = (Role.VIEWER, Role.OPERATOR, Role.EDITOR, Role.ADMIN)


class Group(Base):
    __tablename__ = "groups"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    description: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    members: Mapped[list["GroupMember"]] = relationship(
        back_populates="group", cascade="all, delete-orphan", passive_deletes=True
    )


class GroupMember(Base):
    __tablename__ = "group_members"

    group_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    # Memberships synced from an OIDC provider's claims; NULL for ones added by hand.
    provider_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("oidc_providers.id", ondelete="CASCADE")
    )

    group: Mapped[Group] = relationship(back_populates="members")


class OidcProvider(Base):
    __tablename__ = "oidc_providers"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(String(50), unique=True)
    display_name: Mapped[str] = mapped_column(String(100))
    issuer: Mapped[str] = mapped_column(String(500))
    client_id: Mapped[str] = mapped_column(String(255))
    client_secret: Mapped[str | None] = mapped_column(Text)  # encrypted
    scopes: Mapped[str] = mapped_column(String(500), default="openid profile email")
    username_claim: Mapped[str] = mapped_column(String(100), default="preferred_username")
    groups_claim: Mapped[str] = mapped_column(String(100), default="groups")
    enabled: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    mappings: Mapped[list["GroupMapping"]] = relationship(
        back_populates="provider",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="GroupMapping.claim_value",
    )


class GroupMapping(Base):
    """A value of the provider's groups claim that grants membership of a Novaport group."""

    __tablename__ = "oidc_group_mappings"
    __table_args__ = (UniqueConstraint("provider_id", "claim_value", "group_id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    provider_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("oidc_providers.id", ondelete="CASCADE")
    )
    claim_value: Mapped[str] = mapped_column(String(255))
    group_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("groups.id", ondelete="CASCADE"))

    provider: Mapped[OidcProvider] = relationship(back_populates="mappings")
    group: Mapped[Group] = relationship()


class RoleBinding(Base):
    __tablename__ = "role_bindings"
    __table_args__ = (
        CheckConstraint("(user_id IS NULL) <> (group_id IS NULL)", name="one_subject"),
        UniqueConstraint("user_id", "group_id", "stack_id", postgresql_nulls_not_distinct=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    group_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("groups.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[Role] = mapped_column(String(10))
    # NULL means the binding applies to everything.
    stack_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("stacks.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LoginState(Base):
    """An OIDC login in progress, between the redirect to the provider and the callback."""

    __tablename__ = "login_states"

    state_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    provider_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("oidc_providers.id", ondelete="CASCADE")
    )
    # Ties the callback to the browser that started the login.
    browser_hash: Mapped[str] = mapped_column(String(64))
    code_verifier: Mapped[str] = mapped_column(String(128))
    nonce: Mapped[str] = mapped_column(String(64))
    next_path: Mapped[str] = mapped_column(String(500), default="/")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class Outcome(StrEnum):
    SUCCESS = "success"
    FAILED = "failed"
    DENIED = "denied"


class AuditEntry(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    # Kept so entries stay readable after the user is deleted or renamed.
    username: Mapped[str | None] = mapped_column(String(150))
    action: Mapped[str] = mapped_column(String(50), index=True)
    target: Mapped[str | None] = mapped_column(String(255))
    # No foreign key: entries outlive the stacks they mention.
    stack_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    ip: Mapped[str | None] = mapped_column(String(45))
    outcome: Mapped[Outcome] = mapped_column(String(10))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
