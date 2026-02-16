"""Add prompt versioning and A/B testing tables.

Revision ID: 006
Revises: 005
Create Date: 2026-02-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "006"
down_revision: str | None = "005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- prompt_versions ---
    op.create_table(
        "prompt_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("system_prompt", sa.Text, nullable=False),
        sa.Column("tools_config", postgresql.JSONB, nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("metadata", postgresql.JSONB, server_default="{}"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
    )

    op.create_index("idx_prompt_versions_is_active", "prompt_versions", ["is_active"])

    # --- prompt_ab_tests ---
    op.create_table(
        "prompt_ab_tests",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("test_name", sa.String(200), nullable=False),
        sa.Column("variant_a_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("prompt_versions.id"), nullable=False),
        sa.Column("variant_b_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("prompt_versions.id"), nullable=False),
        sa.Column("traffic_split", sa.Float, nullable=False, server_default="0.5"),
        sa.Column("calls_a", sa.Integer, nullable=False, server_default="0"),
        sa.Column("calls_b", sa.Integer, nullable=False, server_default="0"),
        sa.Column("quality_a", sa.Float, nullable=False, server_default="0"),
        sa.Column("quality_b", sa.Float, nullable=False, server_default="0"),
        sa.Column("status", sa.String(20), nullable=False, server_default="'active'"),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("ended_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )

    op.create_index("idx_prompt_ab_tests_status", "prompt_ab_tests", ["status"])


def downgrade() -> None:
    op.drop_table("prompt_ab_tests")
    op.drop_index("idx_prompt_versions_is_active", table_name="prompt_versions")
    op.drop_table("prompt_versions")
