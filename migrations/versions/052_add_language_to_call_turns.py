"""Add language column to call_turns for STT language observability.

The STT engine already detects language per final transcript (uk-UA / ru-RU)
via Google's multilingual mode, but until now this was discarded before
persistence. Storing it enables per-language A/B tests on STT models and
answers "what fraction of callers speak Russian vs Ukrainian".

Revision ID: 052
Revises: 051
Create Date: 2026-07-20
"""

from collections.abc import Sequence

from alembic import op

revision: str = "052"
down_revision: str | None = "051"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Column addition on a partitioned parent propagates to all existing
    # partitions in PostgreSQL. Nullable — historical rows won't have it.
    op.execute("ALTER TABLE call_turns ADD COLUMN IF NOT EXISTS language VARCHAR(10)")


def downgrade() -> None:
    op.execute("ALTER TABLE call_turns DROP COLUMN IF EXISTS language")
