"""Add sandbox_turn_groups table for marking conversation fragments.

Revision ID: 025
Revises: 024
Create Date: 2026-02-20
"""

from collections.abc import Sequence

from alembic import op

revision: str = "025"
down_revision: str | None = "024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE sandbox_turn_groups (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            conversation_id UUID NOT NULL REFERENCES sandbox_conversations(id) ON DELETE CASCADE,
            turn_ids UUID[] NOT NULL,
            intent_label VARCHAR(200) NOT NULL,
            pattern_type VARCHAR(20) NOT NULL DEFAULT 'positive',
            rating SMALLINT CHECK (rating >= 1 AND rating <= 5),
            rating_comment TEXT,
            correction TEXT,
            tags TEXT[] NOT NULL DEFAULT '{}',
            is_exported BOOLEAN NOT NULL DEFAULT false,
            created_by UUID REFERENCES admin_users(id),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE INDEX idx_turn_groups_conversation
            ON sandbox_turn_groups(conversation_id)
    """)
    op.execute("""
        CREATE INDEX idx_turn_groups_intent
            ON sandbox_turn_groups(intent_label)
    """)
    op.execute("""
        CREATE INDEX idx_turn_groups_type
            ON sandbox_turn_groups(pattern_type)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS sandbox_turn_groups")
