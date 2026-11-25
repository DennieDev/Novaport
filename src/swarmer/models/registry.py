# SPDX-License-Identifier: AGPL-3.0-or-later
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from swarmer.db import Base
from swarmer.models.stack import Stack


class Registry(Base):
    __tablename__ = "registries"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # Normalised host: docker.io for Docker Hub, otherwise host[:port].
    host: Mapped[str] = mapped_column(String(255), unique=True)
    username: Mapped[str] = mapped_column(String(255))
    password: Mapped[str] = mapped_column(Text)  # encrypted
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ImageCheck(Base):
    """The latest update check of one service with an `x-updates` policy."""

    __tablename__ = "image_checks"
    __table_args__ = (UniqueConstraint("stack_id", "service"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    stack_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stacks.id", ondelete="CASCADE"))
    service: Mapped[str] = mapped_column(String(255))
    current_ref: Mapped[str | None] = mapped_column(String(500))
    candidate_ref: Mapped[str | None] = mapped_column(String(500))
    # current, available, or error
    status: Mapped[str] = mapped_column(String(10))
    policy: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # What was done about candidate_ref: committed, pr_opened, notified, recorded, or failed.
    action_taken: Mapped[str | None] = mapped_column(String(20))
    # The candidate the action ran for, so the same update isn't committed or proposed twice.
    acted_on_ref: Mapped[str | None] = mapped_column(String(500))
    # Commit SHA or pull request URL.
    action_ref: Mapped[str | None] = mapped_column(String(500))

    stack: Mapped[Stack] = relationship()
