# SPDX-License-Identifier: AGPL-3.0-or-later
import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from swarmer.db import Base


class ChannelKind(StrEnum):
    WEBHOOK = "webhook"
    SLACK = "slack"
    EMAIL = "email"


class EventType(StrEnum):
    DEPLOY_SUCCEEDED = "deploy.succeeded"
    DEPLOY_FAILED = "deploy.failed"
    APPROVAL_NEEDED = "deploy.awaiting_approval"
    DRIFT = "drift.detected"
    UPDATE_AVAILABLE = "update.available"
    UPDATE_COMMITTED = "update.committed"
    UPDATE_PR = "update.pr_opened"
    NODE_DOWN = "node.down"
    AGENT_UNREACHABLE = "agent.unreachable"
    ENDPOINT_DOWN = "endpoint.disconnected"


class NotificationChannel(Base):
    __tablename__ = "notification_channels"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    kind: Mapped[ChannelKind] = mapped_column(String(10))
    enabled: Mapped[bool] = mapped_column(default=True)
    # Webhook and Slack URLs often carry a token, so they are encrypted like other credentials.
    url: Mapped[str | None] = mapped_column(Text)
    # Signs webhook bodies with HMAC-SHA256 when set; encrypted.
    signing_secret: Mapped[str | None] = mapped_column(Text)
    # Comma-separated email addresses.
    recipients: Mapped[str | None] = mapped_column(String(1000))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)

    subscriptions: Mapped[list["NotificationSubscription"]] = relationship(
        back_populates="channel", cascade="all, delete-orphan", passive_deletes=True
    )


class NotificationSubscription(Base):
    __tablename__ = "notification_subscriptions"
    __table_args__ = (
        UniqueConstraint("channel_id", "event", "stack_id", postgresql_nulls_not_distinct=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    channel_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("notification_channels.id", ondelete="CASCADE"), index=True
    )
    # An EventType value, or "*" for every event.
    event: Mapped[str] = mapped_column(String(40))
    # NULL means every stack (and events that belong to no stack).
    stack_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("stacks.id", ondelete="CASCADE"))

    channel: Mapped[NotificationChannel] = relationship(back_populates="subscriptions")
