"""Add model column to sandbox_conversations for LLM model selection.

Revision ID: 024
Revises: 023
Create Date: 2026-02-20
"""

from collections.abc import Sequence

from alembic import op

revision: str = "024"
down_revision: str | None = "023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE sandbox_conversations
        ADD COLUMN model VARCHAR(100)
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE sandbox_conversations
        DROP COLUMN IF EXISTS model
    """)
