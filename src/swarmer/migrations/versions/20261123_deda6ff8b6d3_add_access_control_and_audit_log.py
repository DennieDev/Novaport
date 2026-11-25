# SPDX-License-Identifier: AGPL-3.0-or-later
"""Add access control and audit log

Revision ID: deda6ff8b6d3
Revises: 3ae0963c9934
Create Date: 2026-11-23 12:09:54.826946
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "deda6ff8b6d3"
down_revision: str | Sequence[str] | None = "3ae0963c9934"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "groups",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_groups")),
        sa.UniqueConstraint("name", name=op.f("uq_groups_name")),
    )
    op.create_table(
        "oidc_providers",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("slug", sa.String(length=50), nullable=False),
        sa.Column("display_name", sa.String(length=100), nullable=False),
        sa.Column("issuer", sa.String(length=500), nullable=False),
        sa.Column("client_id", sa.String(length=255), nullable=False),
        sa.Column("client_secret", sa.Text(), nullable=True),
        sa.Column("scopes", sa.String(length=500), nullable=False),
        sa.Column("username_claim", sa.String(length=100), nullable=False),
        sa.Column("groups_claim", sa.String(length=100), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_oidc_providers")),
        sa.UniqueConstraint("slug", name=op.f("uq_oidc_providers_slug")),
    )
    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("username", sa.String(length=150), nullable=True),
        sa.Column("action", sa.String(length=50), nullable=False),
        sa.Column("target", sa.String(length=255), nullable=True),
        sa.Column("stack_id", sa.Uuid(), nullable=True),
        sa.Column("ip", sa.String(length=45), nullable=True),
        sa.Column("outcome", sa.String(length=10), nullable=False),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_audit_log_user_id_users"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_log")),
    )
    op.create_index(op.f("ix_audit_log_action"), "audit_log", ["action"], unique=False)
    op.create_index(op.f("ix_audit_log_at"), "audit_log", ["at"], unique=False)
    op.create_index(op.f("ix_audit_log_stack_id"), "audit_log", ["stack_id"], unique=False)
    op.create_index(op.f("ix_audit_log_user_id"), "audit_log", ["user_id"], unique=False)
    op.create_table(
        "group_members",
        sa.Column("group_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("provider_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(
            ["group_id"],
            ["groups.id"],
            name=op.f("fk_group_members_group_id_groups"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["provider_id"],
            ["oidc_providers.id"],
            name=op.f("fk_group_members_provider_id_oidc_providers"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_group_members_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("group_id", "user_id", name=op.f("pk_group_members")),
    )
    op.create_index(op.f("ix_group_members_user_id"), "group_members", ["user_id"], unique=False)
    op.create_table(
        "login_states",
        sa.Column("state_hash", sa.String(length=64), nullable=False),
        sa.Column("provider_id", sa.Uuid(), nullable=False),
        sa.Column("browser_hash", sa.String(length=64), nullable=False),
        sa.Column("code_verifier", sa.String(length=128), nullable=False),
        sa.Column("nonce", sa.String(length=64), nullable=False),
        sa.Column("next_path", sa.String(length=500), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["provider_id"],
            ["oidc_providers.id"],
            name=op.f("fk_login_states_provider_id_oidc_providers"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("state_hash", name=op.f("pk_login_states")),
    )
    op.create_index(
        op.f("ix_login_states_expires_at"), "login_states", ["expires_at"], unique=False
    )
    op.create_table(
        "oidc_group_mappings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("provider_id", sa.Uuid(), nullable=False),
        sa.Column("claim_value", sa.String(length=255), nullable=False),
        sa.Column("group_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["group_id"],
            ["groups.id"],
            name=op.f("fk_oidc_group_mappings_group_id_groups"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["provider_id"],
            ["oidc_providers.id"],
            name=op.f("fk_oidc_group_mappings_provider_id_oidc_providers"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_oidc_group_mappings")),
        sa.UniqueConstraint(
            "provider_id",
            "claim_value",
            "group_id",
            name=op.f("uq_oidc_group_mappings_provider_id_claim_value_group_id"),
        ),
    )
    op.create_table(
        "role_bindings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("group_id", sa.Uuid(), nullable=True),
        sa.Column("role", sa.String(length=10), nullable=False),
        sa.Column("stack_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(user_id IS NULL) <> (group_id IS NULL)", name=op.f("ck_role_bindings_one_subject")
        ),
        sa.ForeignKeyConstraint(
            ["group_id"],
            ["groups.id"],
            name=op.f("fk_role_bindings_group_id_groups"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["stack_id"],
            ["stacks.id"],
            name=op.f("fk_role_bindings_stack_id_stacks"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_role_bindings_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_role_bindings")),
        sa.UniqueConstraint(
            "user_id",
            "group_id",
            "stack_id",
            name=op.f("uq_role_bindings_user_id_group_id_stack_id"),
            postgresql_nulls_not_distinct=True,
        ),
    )
    op.create_index(op.f("ix_role_bindings_group_id"), "role_bindings", ["group_id"], unique=False)
    op.create_index(op.f("ix_role_bindings_user_id"), "role_bindings", ["user_id"], unique=False)
    op.add_column("users", sa.Column("name", sa.String(length=200), nullable=True))
    op.add_column("users", sa.Column("issuer", sa.String(length=500), nullable=True))
    op.add_column("users", sa.Column("subject", sa.String(length=255), nullable=True))
    op.create_unique_constraint(op.f("uq_users_issuer_subject"), "users", ["issuer", "subject"])
    op.execute(
        "INSERT INTO role_bindings (id, user_id, role, created_at)"
        " SELECT gen_random_uuid(), id, 'admin', now() FROM users WHERE is_admin"
    )
    op.drop_column("users", "is_admin")


def downgrade() -> None:
    op.add_column(
        "users", sa.Column("is_admin", sa.Boolean(), server_default=sa.false(), nullable=False)
    )
    op.execute(
        "UPDATE users SET is_admin = true WHERE id IN"
        " (SELECT user_id FROM role_bindings"
        "  WHERE role = 'admin' AND stack_id IS NULL AND user_id IS NOT NULL)"
    )
    op.alter_column("users", "is_admin", server_default=None)
    op.drop_constraint(op.f("uq_users_issuer_subject"), "users", type_="unique")
    op.drop_column("users", "subject")
    op.drop_column("users", "issuer")
    op.drop_column("users", "name")
    op.drop_index(op.f("ix_role_bindings_user_id"), table_name="role_bindings")
    op.drop_index(op.f("ix_role_bindings_group_id"), table_name="role_bindings")
    op.drop_table("role_bindings")
    op.drop_table("oidc_group_mappings")
    op.drop_index(op.f("ix_login_states_expires_at"), table_name="login_states")
    op.drop_table("login_states")
    op.drop_index(op.f("ix_group_members_user_id"), table_name="group_members")
    op.drop_table("group_members")
    op.drop_index(op.f("ix_audit_log_user_id"), table_name="audit_log")
    op.drop_index(op.f("ix_audit_log_stack_id"), table_name="audit_log")
    op.drop_index(op.f("ix_audit_log_at"), table_name="audit_log")
    op.drop_index(op.f("ix_audit_log_action"), table_name="audit_log")
    op.drop_table("audit_log")
    op.drop_table("oidc_providers")
    op.drop_table("groups")
