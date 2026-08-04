"""Add generated_by column to stt_correction_suggestions.

Tracks how the final regex was produced:
  'ai'     — LLM-generated via /suggestions/{id}/generate-regex
  'manual' — content-manager typed or edited the regex by hand
  NULL     — auto-populated from bad_token by the scanner, unedited

Used for future quality analysis (which source produces the most false
positives when the rule ships to production).

Revision ID: 057
Revises: 056
Create Date: 2026-08-03
"""

from collections.abc import Sequence

from alembic import op

revision: str = "057"
down_revision: str | None = "056"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE stt_correction_suggestions
        ADD COLUMN IF NOT EXISTS generated_by VARCHAR(10)
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE stt_correction_suggestions
        DROP COLUMN IF EXISTS generated_by
    """)
