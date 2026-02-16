"""Add embedding_status column to knowledge_articles.

Revision ID: 009
"""

from alembic import op  # type: ignore[import-untyped]

revision = "009"
down_revision = "008"


def upgrade() -> None:
    op.execute("""
        ALTER TABLE knowledge_articles
        ADD COLUMN embedding_status VARCHAR(20) NOT NULL DEFAULT 'pending'
    """)
    op.execute("""
        CREATE INDEX idx_knowledge_articles_embedding_status
        ON knowledge_articles(embedding_status)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_knowledge_articles_embedding_status")
    op.execute("ALTER TABLE knowledge_articles DROP COLUMN IF EXISTS embedding_status")
