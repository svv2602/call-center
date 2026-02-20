"""Add search indexes for call_turns content and calls scenario.

- GIN index on call_turns.content for full-text search (replaces ILIKE seq scan)
- BTREE index on calls.scenario for filtering in analytics endpoints

Revision ID: 031
Revises: 030
Create Date: 2026-02-20
"""

from collections.abc import Sequence

from alembic import op

revision: str = "031"
down_revision: str | None = "030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_calls_scenario ON calls (scenario)"
    )
    # GIN index for full-text search on call_turns content
    # Using 'simple' config to handle Ukrainian/Russian without stemming issues
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_call_turns_content_fts "
        "ON call_turns USING gin(to_tsvector('simple', content))"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_call_turns_content_fts")
    op.execute("DROP INDEX IF EXISTS idx_calls_scenario")
