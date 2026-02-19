"""Add sandbox regression runs table.

Tracks automated replay runs comparing agent responses across
different prompt versions.

Revision ID: 023
Revises: 022
Create Date: 2026-02-19
"""

from collections.abc import Sequence

from alembic import op

revision: str = "023"
down_revision: str | None = "022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE sandbox_regression_runs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            source_conversation_id UUID NOT NULL REFERENCES sandbox_conversations(id),
            source_branch_path UUID[],
            new_prompt_version_id UUID NOT NULL REFERENCES prompt_versions(id),
            new_conversation_id UUID REFERENCES sandbox_conversations(id),
            status VARCHAR(20) NOT NULL DEFAULT 'pending',
            turns_compared INTEGER,
            avg_source_rating FLOAT,
            avg_new_rating FLOAT,
            score_diff FLOAT,
            summary JSONB NOT NULL DEFAULT '{}',
            error_message TEXT,
            started_at TIMESTAMPTZ,
            completed_at TIMESTAMPTZ,
            created_by UUID REFERENCES admin_users(id),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE INDEX idx_sandbox_regression_source ON sandbox_regression_runs(source_conversation_id)
    """)
    op.execute("""
        CREATE INDEX idx_sandbox_regression_status ON sandbox_regression_runs(status)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS sandbox_regression_runs")
