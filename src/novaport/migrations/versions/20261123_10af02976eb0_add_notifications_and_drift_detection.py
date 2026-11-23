# SPDX-License-Identifier: AGPL-3.0-or-later
"""Add notifications and drift detection

Revision ID: 10af02976eb0
Revises: ec8fc902473c
Create Date: 2026-11-23 13:09:53.634171
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "10af02976eb0"
down_revision: str | Sequence[str] | None = "ec8fc902473c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "notification_channels",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("signing_secret", sa.Text(), nullable=True),
        sa.Column("recipients", sa.String(length=1000), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notification_channels")),
        sa.UniqueConstraint("name", name=op.f("uq_notification_channels_name")),
    )
    op.create_table(
        "notification_subscriptions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("channel_id", sa.Uuid(), nullable=False),
        sa.Column("event", sa.String(length=40), nullable=False),
        sa.Column("stack_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(
            ["channel_id"],
            ["notification_channels.id"],
            name=op.f("fk_notification_subscriptions_channel_id_notification_channels"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["stack_id"],
            ["stacks.id"],
            name=op.f("fk_notification_subscriptions_stack_id_stacks"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notification_subscriptions")),
        sa.UniqueConstraint(
            "channel_id",
            "event",
            "stack_id",
            name=op.f("uq_notification_subscriptions_channel_id_event_stack_id"),
            postgresql_nulls_not_distinct=True,
        ),
    )
    op.create_index(
        op.f("ix_notification_subscriptions_channel_id"),
        "notification_subscriptions",
        ["channel_id"],
        unique=False,
    )
    op.add_column(
        "deployments",
        sa.Column("approval_notified", sa.Boolean(), server_default="false", nullable=False),
    )
    # Deployments from before this upgrade were never announced and shouldn't be all at once.
    op.execute("UPDATE deployments SET approval_notified = true")
    op.add_column(
        "stacks", sa.Column("deployed_spec", postgresql.JSONB(astext_type=sa.Text()), nullable=True)
    )
    op.add_column(
        "stacks", sa.Column("drift", postgresql.JSONB(astext_type=sa.Text()), nullable=True)
    )
    op.add_column(
        "stacks", sa.Column("drift_checked_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("stacks", "drift_checked_at")
    op.drop_column("stacks", "drift")
    op.drop_column("stacks", "deployed_spec")
    op.drop_column("deployments", "approval_notified")
    op.drop_index(
        op.f("ix_notification_subscriptions_channel_id"), table_name="notification_subscriptions"
    )
    op.drop_table("notification_subscriptions")
    op.drop_table("notification_channels")
