# SPDX-License-Identifier: AGPL-3.0-or-later
"""Add registries and image checks

Revision ID: ec8fc902473c
Revises: deda6ff8b6d3
Create Date: 2026-11-23 13:01:49.637956
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "ec8fc902473c"
down_revision: str | Sequence[str] | None = "deda6ff8b6d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "registries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("host", sa.String(length=255), nullable=False),
        sa.Column("username", sa.String(length=255), nullable=False),
        sa.Column("password", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_registries")),
        sa.UniqueConstraint("host", name=op.f("uq_registries_host")),
    )
    op.create_table(
        "image_checks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("stack_id", sa.Uuid(), nullable=False),
        sa.Column("service", sa.String(length=255), nullable=False),
        sa.Column("current_ref", sa.String(length=500), nullable=True),
        sa.Column("candidate_ref", sa.String(length=500), nullable=True),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("policy", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("action_taken", sa.String(length=20), nullable=True),
        sa.Column("acted_on_ref", sa.String(length=500), nullable=True),
        sa.Column("action_ref", sa.String(length=500), nullable=True),
        sa.ForeignKeyConstraint(
            ["stack_id"],
            ["stacks.id"],
            name=op.f("fk_image_checks_stack_id_stacks"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_image_checks")),
        sa.UniqueConstraint("stack_id", "service", name=op.f("uq_image_checks_stack_id_service")),
    )


def downgrade() -> None:
    op.drop_table("image_checks")
    op.drop_table("registries")
