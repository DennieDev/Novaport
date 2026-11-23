# SPDX-License-Identifier: AGPL-3.0-or-later
import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from novaport.db import Base


class GitAuth(StrEnum):
    NONE = "none"
    SSH = "ssh"
    TOKEN = "token"  # noqa: S105


class StackType(StrEnum):
    SWARM = "swarm"
    COMPOSE = "compose"


class DeployMode(StrEnum):
    AUTO = "auto"
    APPROVE = "approve"
    MANUAL = "manual"


class EditMode(StrEnum):
    COMMIT = "commit"
    PR = "pr"


class DeploymentStatus(StrEnum):
    PENDING = "pending"
    AWAITING_APPROVAL = "awaiting_approval"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def finished(self) -> bool:
        return self in (self.SUCCEEDED, self.FAILED, self.CANCELLED)


class Repository(Base):
    __tablename__ = "repositories"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    url: Mapped[str] = mapped_column(String(500))
    branch: Mapped[str] = mapped_column(String(255), default="main")
    stacks_root: Mapped[str] = mapped_column(String(255), default="stacks")
    auth: Mapped[GitAuth] = mapped_column(String(10), default=GitAuth.NONE)
    ssh_private_key: Mapped[str | None] = mapped_column(Text)  # encrypted
    ssh_public_key: Mapped[str | None] = mapped_column(Text)
    token: Mapped[str | None] = mapped_column(Text)  # encrypted
    webhook_secret: Mapped[str] = mapped_column(String(64))
    poll_interval: Mapped[int] = mapped_column(default=300)  # seconds, 0 disables polling
    default_deploy_mode: Mapped[DeployMode] = mapped_column(String(10), default=DeployMode.MANUAL)
    default_edit_mode: Mapped[EditMode] = mapped_column(
        String(10), default=EditMode.COMMIT, server_default=EditMode.COMMIT
    )
    forge: Mapped[str] = mapped_column(String(10), default="none", server_default="none")
    forge_api_url: Mapped[str | None] = mapped_column(String(500))
    forge_token: Mapped[str | None] = mapped_column(Text)  # encrypted
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_commit: Mapped[str | None] = mapped_column(String(64))
    last_error: Mapped[str | None] = mapped_column(Text)

    stacks: Mapped[list["Stack"]] = relationship(
        back_populates="repository", cascade="all, delete-orphan", passive_deletes=True
    )


class Stack(Base):
    __tablename__ = "stacks"
    __table_args__ = (UniqueConstraint("repository_id", "path"), UniqueConstraint("name"))

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    repository_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE")
    )
    path: Mapped[str] = mapped_column(String(500))
    name: Mapped[str] = mapped_column(String(63))
    type: Mapped[StackType] = mapped_column(String(10), default=StackType.SWARM)
    deploy_mode: Mapped[DeployMode] = mapped_column(String(10), default=DeployMode.MANUAL)
    edit_mode: Mapped[EditMode] = mapped_column(
        String(10), default=EditMode.COMMIT, server_default=EditMode.COMMIT
    )
    config_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Service specs as the last successful deploy left them, keyed by service name (Swarm only).
    deployed_spec: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # Differences between the running services and deployed_spec; empty when in sync.
    drift: Mapped[list[str] | None] = mapped_column(JSONB)
    drift_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_good_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("revisions.id", ondelete="SET NULL", use_alter=True)
    )

    repository: Mapped[Repository] = relationship(back_populates="stacks")
    revisions: Mapped[list["Revision"]] = relationship(
        back_populates="stack",
        foreign_keys="Revision.stack_id",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    last_good_revision: Mapped["Revision | None"] = relationship(
        foreign_keys=[last_good_revision_id], post_update=True
    )


class Revision(Base):
    """A stack folder as it was at one commit. The tree hash identifies its exact content."""

    __tablename__ = "revisions"
    __table_args__ = (Index("ix_revisions_stack_id_created_at", "stack_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    stack_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stacks.id", ondelete="CASCADE"))
    commit: Mapped[str] = mapped_column(String(64))
    tree: Mapped[str] = mapped_column(String(64))
    message: Mapped[str] = mapped_column(Text, default="")
    author: Mapped[str] = mapped_column(String(255), default="")
    committed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    stack: Mapped[Stack] = relationship(back_populates="revisions", foreign_keys=[stack_id])


class Deployment(Base):
    __tablename__ = "deployments"
    __table_args__ = (
        Index("ix_deployments_stack_id_created_at", "stack_id", "created_at"),
        Index("ix_deployments_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    stack_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stacks.id", ondelete="CASCADE"))
    revision_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("revisions.id", ondelete="CASCADE"))
    status: Mapped[DeploymentStatus] = mapped_column(String(20))
    stage: Mapped[str | None] = mapped_column(String(20))
    trigger: Mapped[str] = mapped_column(String(20))
    triggered_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    approval_notified: Mapped[bool] = mapped_column(default=False, server_default="false")
    approved_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    stack: Mapped[Stack] = relationship()
    revision: Mapped[Revision] = relationship()
    events: Mapped[list["DeploymentEvent"]] = relationship(
        back_populates="deployment",
        order_by="DeploymentEvent.id",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class DeploymentEvent(Base):
    __tablename__ = "deployment_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    deployment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("deployments.id", ondelete="CASCADE"), index=True
    )
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    stage: Mapped[str] = mapped_column(String(20))
    level: Mapped[str] = mapped_column(String(10))
    message: Mapped[str] = mapped_column(Text)

    deployment: Mapped[Deployment] = relationship(back_populates="events")
