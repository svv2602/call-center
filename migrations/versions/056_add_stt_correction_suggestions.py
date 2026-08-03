"""Add stt_correction_suggestions table for auto-suggested STT rules.

A weekly Celery task scans call_turns pairs where the bot re-asked
("повторіть", "не розчула") and clusters recurring garbage tokens.
Each cluster becomes a suggestion — content-manager approves it via
Admin UI, which promotes the suggestion into a real rule in the
Redis-backed `stt:corrections` list.

Revision ID: 056
Revises: 055
Create Date: 2026-08-03
"""

from collections.abc import Sequence

from alembic import op

revision: str = "056"
down_revision: str | None = "055"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS stt_correction_suggestions (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            bad_token VARCHAR(100) NOT NULL,
            detected_context VARCHAR(20) NOT NULL DEFAULT 'any',
            occurrence_count INTEGER NOT NULL DEFAULT 1,
            sample_transcripts JSONB NOT NULL DEFAULT '[]'::jsonb,
            proposed_pattern VARCHAR(500),
            proposed_replacement VARCHAR(500),
            match_distance INTEGER,
            status VARCHAR(20) NOT NULL DEFAULT 'pending',
            created_rule_id VARCHAR(20),
            reviewer VARCHAR(100),
            reviewed_at TIMESTAMPTZ,
            reject_reason TEXT,
            first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT stt_suggestion_status_check
                CHECK (status IN ('pending','approved','rejected','promoted'))
        )
    """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_stt_suggestion_token_context
            ON stt_correction_suggestions(bad_token, detected_context)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_stt_suggestion_status_count
            ON stt_correction_suggestions(status, occurrence_count DESC)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_stt_suggestion_last_seen
            ON stt_correction_suggestions(last_seen_at DESC)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_stt_suggestion_last_seen")
    op.execute("DROP INDEX IF EXISTS idx_stt_suggestion_status_count")
    op.execute("DROP INDEX IF EXISTS uq_stt_suggestion_token_context")
    op.execute("DROP TABLE IF EXISTS stt_correction_suggestions")
