# SPDX-License-Identifier: AGPL-3.0-or-later
"""add endpoints

Revision ID: 8ab3fad5498b
Revises: c6d9d4a1548b
Create Date: 2026-11-23 15:59:22.042619
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8ab3fad5498b"
down_revision: str | Sequence[str] | None = "c6d9d4a1548b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "endpoints",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=63), nullable=False),
        sa.Column("secret_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("connected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("disconnected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("hostname", sa.String(length=255), nullable=True),
        sa.Column("docker_version", sa.String(length=50), nullable=True),
        sa.Column("agent_version", sa.String(length=50), nullable=True),
        sa.Column("swarm", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_endpoints")),
        sa.UniqueConstraint("name", name=op.f("uq_endpoints_name")),
    )
    op.add_column("stacks", sa.Column("endpoint_id", sa.Uuid(), nullable=True))
    op.create_index(op.f("ix_stacks_endpoint_id"), "stacks", ["endpoint_id"], unique=False)
    op.create_foreign_key(
        op.f("fk_stacks_endpoint_id_endpoints"),
        "stacks",
        "endpoints",
        ["endpoint_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("fk_stacks_endpoint_id_endpoints"), "stacks", type_="foreignkey")
    op.drop_index(op.f("ix_stacks_endpoint_id"), table_name="stacks")
    op.drop_column("stacks", "endpoint_id")
    op.drop_table("endpoints")
