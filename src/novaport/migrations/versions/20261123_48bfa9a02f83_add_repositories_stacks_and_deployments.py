# SPDX-License-Identifier: AGPL-3.0-or-later
"""Add repositories, stacks and deployments

Revision ID: 48bfa9a02f83
Revises: 63c23d225bfd
Create Date: 2026-11-23 11:22:46.565570
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "48bfa9a02f83"
down_revision: str | Sequence[str] | None = "63c23d225bfd"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "repositories",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("url", sa.String(length=500), nullable=False),
        sa.Column("branch", sa.String(length=255), nullable=False),
        sa.Column("stacks_root", sa.String(length=255), nullable=False),
        sa.Column("auth", sa.String(length=10), nullable=False),
        sa.Column("ssh_private_key", sa.Text(), nullable=True),
        sa.Column("ssh_public_key", sa.Text(), nullable=True),
        sa.Column("token", sa.Text(), nullable=True),
        sa.Column("webhook_secret", sa.String(length=64), nullable=False),
        sa.Column("poll_interval", sa.Integer(), nullable=False),
        sa.Column("default_deploy_mode", sa.String(length=10), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_commit", sa.String(length=64), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_repositories")),
        sa.UniqueConstraint("name", name=op.f("uq_repositories_name")),
    )
    op.create_table(
        "stacks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("repository_id", sa.Uuid(), nullable=False),
        sa.Column("path", sa.String(length=500), nullable=False),
        sa.Column("name", sa.String(length=63), nullable=False),
        sa.Column("type", sa.String(length=10), nullable=False),
        sa.Column("deploy_mode", sa.String(length=10), nullable=False),
        sa.Column("config_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_good_revision_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(
            ["repository_id"],
            ["repositories.id"],
            name=op.f("fk_stacks_repository_id_repositories"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_stacks")),
        sa.UniqueConstraint("name", name=op.f("uq_stacks_name")),
        sa.UniqueConstraint("repository_id", "path", name=op.f("uq_stacks_repository_id_path")),
    )
    op.create_table(
        "revisions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("stack_id", sa.Uuid(), nullable=False),
        sa.Column("commit", sa.String(length=64), nullable=False),
        sa.Column("tree", sa.String(length=64), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("author", sa.String(length=255), nullable=False),
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["stack_id"],
            ["stacks.id"],
            name=op.f("fk_revisions_stack_id_stacks"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_revisions")),
    )
    op.create_index(
        "ix_revisions_stack_id_created_at", "revisions", ["stack_id", "created_at"], unique=False
    )
    # Stacks and revisions reference each other, so this one is added once both exist.
    op.create_foreign_key(
        op.f("fk_stacks_last_good_revision_id_revisions"),
        "stacks",
        "revisions",
        ["last_good_revision_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_table(
        "deployments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("stack_id", sa.Uuid(), nullable=False),
        sa.Column("revision_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("stage", sa.String(length=20), nullable=True),
        sa.Column("trigger", sa.String(length=20), nullable=False),
        sa.Column("triggered_by_id", sa.Uuid(), nullable=True),
        sa.Column("approved_by_id", sa.Uuid(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["approved_by_id"],
            ["users.id"],
            name=op.f("fk_deployments_approved_by_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["revision_id"],
            ["revisions.id"],
            name=op.f("fk_deployments_revision_id_revisions"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["stack_id"],
            ["stacks.id"],
            name=op.f("fk_deployments_stack_id_stacks"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["triggered_by_id"],
            ["users.id"],
            name=op.f("fk_deployments_triggered_by_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_deployments")),
    )
    op.create_index(
        "ix_deployments_stack_id_created_at",
        "deployments",
        ["stack_id", "created_at"],
        unique=False,
    )
    op.create_index("ix_deployments_status", "deployments", ["status"], unique=False)
    op.create_table(
        "deployment_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("deployment_id", sa.Uuid(), nullable=False),
        sa.Column(
            "at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("stage", sa.String(length=20), nullable=False),
        sa.Column("level", sa.String(length=10), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["deployment_id"],
            ["deployments.id"],
            name=op.f("fk_deployment_events_deployment_id_deployments"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_deployment_events")),
    )
    op.create_index(
        op.f("ix_deployment_events_deployment_id"),
        "deployment_events",
        ["deployment_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_deployment_events_deployment_id"), table_name="deployment_events")
    op.drop_table("deployment_events")
    op.drop_index("ix_deployments_status", table_name="deployments")
    op.drop_index("ix_deployments_stack_id_created_at", table_name="deployments")
    op.drop_table("deployments")
    op.drop_constraint(
        op.f("fk_stacks_last_good_revision_id_revisions"), "stacks", type_="foreignkey"
    )
    op.drop_index("ix_revisions_stack_id_created_at", table_name="revisions")
    op.drop_table("revisions")
    op.drop_table("stacks")
    op.drop_table("repositories")
