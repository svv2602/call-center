"""Add guards_fired column to call_turns for a per-turn guard trace.

Which backend guards fired on a turn was visible only as a Prometheus
counter (no call or turn attached) and a log line that a deploy wipes with
the container. ``call_turns`` had no column for turn metadata, so a guard
could not be tied to the reply it shaped once the logs were gone.

Schema only: nothing writes the column yet. This revision is rolled out
and applied before the code that fills it.

Revision ID: 069
Revises: 068
Create Date: 2026-11-20
"""

from collections.abc import Sequence

from alembic import op

revision: str = "069"
down_revision: str | None = "068"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Column addition on a partitioned parent propagates to all existing
    # partitions in PostgreSQL; future monthly partitions are created as
    # PARTITION OF call_turns and inherit it.
    #
    # Nullable and deliberately without a DEFAULT — the two empty states
    # mean different things and a default would merge them:
    #   NULL — no trace for this turn (historical rows, rows written by
    #          code that does not record guards);
    #   []   — the trace was recorded and no guard fired.
    op.execute("ALTER TABLE call_turns ADD COLUMN IF NOT EXISTS guards_fired JSONB")


def downgrade() -> None:
    op.execute("ALTER TABLE call_turns DROP COLUMN IF EXISTS guards_fired")
