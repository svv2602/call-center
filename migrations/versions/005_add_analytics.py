"""Add analytics tables: daily_stats, quality_details field.

Revision ID: 005
Revises: 004
Create Date: 2026-02-14
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- daily_stats ---
    op.create_table(
        "daily_stats",
        sa.Column("stat_date", sa.Date, primary_key=True),
        sa.Column("total_calls", sa.Integer, nullable=False, server_default="0"),
        sa.Column("resolved_by_bot", sa.Integer, nullable=False, server_default="0"),
        sa.Column("transferred", sa.Integer, nullable=False, server_default="0"),
        sa.Column("avg_duration_seconds", sa.Float, nullable=False, server_default="0"),
        sa.Column("avg_quality_score", sa.Float, nullable=False, server_default="0"),
        sa.Column("total_cost_usd", sa.Float, nullable=False, server_default="0"),
        sa.Column("scenario_breakdown", postgresql.JSONB, server_default="{}"),
        sa.Column("transfer_reasons", postgresql.JSONB, server_default="{}"),
        sa.Column("hourly_distribution", postgresql.JSONB, server_default="{}"),
    )

    # --- Add quality_details JSONB to calls ---
    op.add_column(
        "calls",
        sa.Column("quality_details", postgresql.JSONB, nullable=True),
    )

    # Index for quality filtering
    op.create_index(
        "idx_calls_quality_score",
        "calls",
        ["quality_score"],
    )


def downgrade() -> None:
    op.drop_index("idx_calls_quality_score", table_name="calls")
    op.drop_column("calls", "quality_details")
    op.drop_table("daily_stats")
