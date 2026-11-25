# SPDX-License-Identifier: AGPL-3.0-or-later
"""add deploy options

Revision ID: 40a8f0e8a226
Revises: c8b41e0d6ed9
Create Date: 2026-11-25 14:07:45.026320
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "40a8f0e8a226"
down_revision: str | Sequence[str] | None = "c8b41e0d6ed9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "deployments", sa.Column("prune", sa.Boolean(), server_default="true", nullable=False)
    )
    op.add_column(
        "deployments", sa.Column("pull_images", sa.Boolean(), server_default="true", nullable=False)
    )


def downgrade() -> None:
    op.drop_column("deployments", "pull_images")
    op.drop_column("deployments", "prune")
