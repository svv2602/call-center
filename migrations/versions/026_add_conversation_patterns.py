"""Add conversation_patterns table for pattern bank with pgvector embeddings.

Revision ID: 026
Revises: 025
Create Date: 2026-02-20
"""

from collections.abc import Sequence

from alembic import op

revision: str = "026"
down_revision: str | None = "025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.execute("""
        CREATE TABLE conversation_patterns (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            source_group_id UUID REFERENCES sandbox_turn_groups(id) ON DELETE SET NULL,
            intent_label VARCHAR(200) NOT NULL,
            pattern_type VARCHAR(20) NOT NULL,
            customer_messages TEXT NOT NULL,
            agent_messages TEXT,
            guidance_note TEXT NOT NULL,
            scenario_type VARCHAR(50),
            tags TEXT[] NOT NULL DEFAULT '{}',
            rating SMALLINT,
            embedding vector(1536),
            is_active BOOLEAN NOT NULL DEFAULT true,
            times_used INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE INDEX idx_conv_patterns_embedding
            ON conversation_patterns USING hnsw (embedding vector_cosine_ops)
    """)
    op.execute("""
        CREATE INDEX idx_conv_patterns_type_active
            ON conversation_patterns (pattern_type, is_active)
    """)
    op.execute("""
        CREATE INDEX idx_conv_patterns_tags
            ON conversation_patterns USING GIN (tags)
    """)
    op.execute("""
        CREATE INDEX idx_conv_patterns_intent
            ON conversation_patterns (intent_label)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS conversation_patterns")
