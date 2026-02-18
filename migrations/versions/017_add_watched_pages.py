"""Add watched pages support to knowledge_sources.

Extends knowledge_sources with source_type, rescrape_interval_hours,
content_hash, and next_scrape_at for periodic re-scraping of static pages.

Revision ID: 017
Revises: 016
Create Date: 2026-02-18
"""

from collections.abc import Sequence

from alembic import op

revision: str = "017"
down_revision: str | None = "016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # source_type: 'article' (scraped from listings) or 'watched_page' (manually added, periodic rescrape)
    op.execute("""
        ALTER TABLE knowledge_sources
            ADD COLUMN source_type VARCHAR(20) NOT NULL DEFAULT 'article',
            ADD COLUMN rescrape_interval_hours INTEGER,
            ADD COLUMN content_hash VARCHAR(64),
            ADD COLUMN next_scrape_at TIMESTAMPTZ
    """)
    op.execute("""
        CREATE INDEX ix_knowledge_sources_watched
        ON knowledge_sources (source_type, next_scrape_at)
        WHERE source_type = 'watched_page'
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_knowledge_sources_watched")
    op.execute("""
        ALTER TABLE knowledge_sources
            DROP COLUMN IF EXISTS source_type,
            DROP COLUMN IF EXISTS rescrape_interval_hours,
            DROP COLUMN IF EXISTS content_hash,
            DROP COLUMN IF EXISTS next_scrape_at
    """)
