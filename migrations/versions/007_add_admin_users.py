"""Add admin_users and admin_audit_log tables for RBAC.

Revision ID: 007
Revises: 006
Create Date: 2026-02-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "007"
down_revision: str | None = "006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- admin_users ---
    op.create_table(
        "admin_users",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("username", sa.String(100), unique=True, nullable=False),
        sa.Column("password_hash", sa.String(256), nullable=False),
        sa.Column("role", sa.String(20), nullable=False, server_default="operator"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_index("idx_admin_users_username", "admin_users", ["username"])

    # --- admin_audit_log ---
    op.create_table(
        "admin_audit_log",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("username", sa.String(100), nullable=True),
        sa.Column("action", sa.String(20), nullable=False),
        sa.Column("resource_type", sa.String(50), nullable=True),
        sa.Column("resource_id", sa.String(100), nullable=True),
        sa.Column("details", postgresql.JSONB, nullable=True),
        sa.Column("ip_address", postgresql.INET, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    op.create_index("idx_audit_log_user_id", "admin_audit_log", ["user_id"])
    op.create_index("idx_audit_log_created_at", "admin_audit_log", ["created_at"])


def downgrade() -> None:
    op.drop_index("idx_audit_log_created_at", table_name="admin_audit_log")
    op.drop_index("idx_audit_log_user_id", table_name="admin_audit_log")
    op.drop_table("admin_audit_log")
    op.drop_index("idx_admin_users_username", table_name="admin_users")
    op.drop_table("admin_users")
