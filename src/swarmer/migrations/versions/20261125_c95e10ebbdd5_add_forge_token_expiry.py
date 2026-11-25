# SPDX-License-Identifier: AGPL-3.0-or-later
"""add forge token expiry

Revision ID: c95e10ebbdd5
Revises: 40a8f0e8a226
Create Date: 2026-11-25 16:45:14.016274
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c95e10ebbdd5"
down_revision: str | Sequence[str] | None = "40a8f0e8a226"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "repositories",
        sa.Column("forge_token_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "repositories",
        sa.Column("forge_token_checked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("repositories", sa.Column("forge_token_error", sa.Text(), nullable=True))
    op.add_column(
        "repositories",
        sa.Column("forge_token_warned_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("repositories", "forge_token_warned_at")
    op.drop_column("repositories", "forge_token_error")
    op.drop_column("repositories", "forge_token_checked_at")
    op.drop_column("repositories", "forge_token_expires_at")
