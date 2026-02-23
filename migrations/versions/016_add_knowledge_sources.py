"""Add knowledge_sources table for article scraping pipeline.

Tracks URLs discovered from external sites (prokoleso.ua), their
processing status, and links to created knowledge_articles.

Revision ID: 016
Revises: 015
Create Date: 2026-02-18
"""

from collections.abc import Sequence

from alembic import op

revision: str = "016"
down_revision: str | None = "015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE knowledge_sources (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            url TEXT NOT NULL UNIQUE,
            source_site VARCHAR(100) NOT NULL DEFAULT 'prokoleso.ua',
            article_id UUID REFERENCES knowledge_articles(id) ON DELETE SET NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'new',
            original_title TEXT,
            skip_reason TEXT,
            fetched_at TIMESTAMPTZ,
            processed_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX ix_knowledge_sources_status ON knowledge_sources (status)")
    op.execute("CREATE INDEX ix_knowledge_sources_source_site ON knowledge_sources (source_site)")
    op.execute("CREATE INDEX ix_knowledge_sources_article_id ON knowledge_sources (article_id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS knowledge_sources")
