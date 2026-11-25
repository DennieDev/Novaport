# SPDX-License-Identifier: AGPL-3.0-or-later
"""Add editing settings

Revision ID: 3ae0963c9934
Revises: 48bfa9a02f83
Create Date: 2026-11-23 11:49:57.991858
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "3ae0963c9934"
down_revision: str | Sequence[str] | None = "48bfa9a02f83"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "repositories",
        sa.Column(
            "default_edit_mode", sa.String(length=10), server_default="commit", nullable=False
        ),
    )
    op.add_column(
        "repositories",
        sa.Column("forge", sa.String(length=10), server_default="none", nullable=False),
    )
    op.add_column("repositories", sa.Column("forge_api_url", sa.String(length=500), nullable=True))
    op.add_column("repositories", sa.Column("forge_token", sa.Text(), nullable=True))
    op.add_column(
        "stacks",
        sa.Column("edit_mode", sa.String(length=10), server_default="commit", nullable=False),
    )
    op.add_column("users", sa.Column("email", sa.String(length=320), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "email")
    op.drop_column("stacks", "edit_mode")
    op.drop_column("repositories", "forge_token")
    op.drop_column("repositories", "forge_api_url")
    op.drop_column("repositories", "forge")
    op.drop_column("repositories", "default_edit_mode")
