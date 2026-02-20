"""Add prompt_optimization_results table.

Stores results from the automatic prompt optimizer Celery task.
Each row records an analysis run with patterns found and recommendations.

Revision ID: 030
Revises: 029
Create Date: 2026-02-20
"""

from collections.abc import Sequence

from alembic import op

revision: str = "030"
down_revision: str | None = "029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS prompt_optimization_results (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            days_analyzed INTEGER NOT NULL DEFAULT 7,
            calls_analyzed INTEGER NOT NULL DEFAULT 0,
            patterns JSONB NOT NULL DEFAULT '[]'::jsonb,
            overall_recommendation TEXT,
            status TEXT NOT NULL DEFAULT 'completed',
            error TEXT,
            triggered_by TEXT NOT NULL DEFAULT 'manual',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );

        CREATE INDEX idx_prompt_opt_created ON prompt_optimization_results (created_at DESC);
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS prompt_optimization_results;")
