"""Add calls.audio_stats — what the caller's side of the line carried.

Ten calls on 2026-09-21..23 ended after the greeting with no customer turn, and
it could not be told whether the callers were silent or their audio never
reached STT: nothing about inbound audio was stored, and the container logs
were lost on the next redeploy. Written once at call end from
`src.core.audio_stats.InboundAudioStats`.

`calls` is partitioned; ALTER on the parent propagates to every partition.

Revision ID: 061
Revises: 060
Create Date: 2026-09-24
"""

from collections.abc import Sequence

from alembic import op

revision: str = "061"
down_revision: str | None = "060"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE calls ADD COLUMN IF NOT EXISTS audio_stats JSONB")


def downgrade() -> None:
    op.execute("ALTER TABLE calls DROP COLUMN IF EXISTS audio_stats")
