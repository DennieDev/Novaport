# SPDX-License-Identifier: AGPL-3.0-or-later
"""add revision author email

Revision ID: c6d9d4a1548b
Revises: 10af02976eb0
Create Date: 2026-11-23 15:09:08.808993
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c6d9d4a1548b"
down_revision: str | Sequence[str] | None = "10af02976eb0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "revisions",
        sa.Column("author_email", sa.String(length=320), server_default="", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("revisions", "author_email")
