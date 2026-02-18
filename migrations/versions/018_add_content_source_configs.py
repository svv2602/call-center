"""Add content_source_configs table for multi-source scraper.

New table content_source_configs stores registry of external content sources
(ProKoleso, ADAC, Auto Bild, etc.) with per-source scraping settings.
Also adds source_config_id FK to knowledge_sources.

Revision ID: 018
Revises: 017
Create Date: 2026-02-18
"""

from collections.abc import Sequence

from alembic import op

revision: str = "018"
down_revision: str | None = "017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE content_source_configs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name VARCHAR(200) NOT NULL,
            source_type VARCHAR(30) NOT NULL,
            source_url TEXT NOT NULL,
            language VARCHAR(10) NOT NULL DEFAULT 'uk',
            enabled BOOLEAN NOT NULL DEFAULT false,
            auto_approve BOOLEAN NOT NULL DEFAULT false,
            request_delay FLOAT NOT NULL DEFAULT 2.0,
            max_articles_per_run INT NOT NULL DEFAULT 20,
            schedule_enabled BOOLEAN NOT NULL DEFAULT true,
            schedule_hour INT NOT NULL DEFAULT 6,
            schedule_day_of_week VARCHAR(20) NOT NULL DEFAULT 'monday',
            settings JSONB NOT NULL DEFAULT '{}',
            last_run_at TIMESTAMPTZ,
            last_run_status VARCHAR(30),
            last_run_stats JSONB,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ
        )
    """)
    op.execute("""
        CREATE UNIQUE INDEX ix_csconfig_source_url
        ON content_source_configs (source_url)
    """)
    op.execute("""
        CREATE INDEX ix_csconfig_enabled
        ON content_source_configs (enabled)
    """)

    # Add FK from knowledge_sources to content_source_configs
    op.execute("""
        ALTER TABLE knowledge_sources
            ADD COLUMN source_config_id UUID REFERENCES content_source_configs(id) ON DELETE SET NULL
    """)
    op.execute("""
        CREATE INDEX ix_ks_source_config_id
        ON knowledge_sources (source_config_id)
    """)

    # Seed 8 source configs (all disabled by default)
    op.execute("""
        INSERT INTO content_source_configs (name, source_type, source_url, language, settings) VALUES
        ('ProKoleso.ua', 'prokoleso', 'https://prokoleso.ua', 'uk',
         '{"info_path": "/ua/info/", "max_pages": 3}'),
        ('Auto Bild Reifentests', 'rss', 'https://www.autobild.de/rss/22590773.xml', 'de',
         '{"title_filter_regex": "(?i)reifen|tire|pneu"}'),
        ('ADAC Reifentest', 'generic_html', 'https://www.adac.de', 'de',
         '{"listing_urls": ["https://www.adac.de/rund-ums-fahrzeug/tests/reifentest/"]}'),
        ('Bridgestone EMEA News', 'generic_html', 'https://www.bridgestone.eu', 'en',
         '{"listing_urls": ["https://press.bridgestone-emea.com/?h=1&t=tyres"]}'),
        ('GTU Reifentest', 'generic_html', 'https://www.gtue.de', 'de',
         '{"listing_urls": ["https://www.gtue.de/auto/reifen"]}'),
        ('OAMTC Reifentest', 'generic_html', 'https://www.oeamtc.at', 'de',
         '{"listing_urls": ["https://www.oeamtc.at/tests/reifentests/"]}'),
        ('TCS Tests Pneus', 'generic_html', 'https://www.tcs.ch', 'de',
         '{"listing_urls": ["https://www.tcs.ch/de/tests-ratgeber/tests/reifentests/"]}'),
        ('TyreReviews', 'generic_html', 'https://www.tyrereviews.com', 'en',
         '{"listing_urls": ["https://www.tyrereviews.com/Article/"]}')
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_ks_source_config_id")
    op.execute("ALTER TABLE knowledge_sources DROP COLUMN IF EXISTS source_config_id")
    op.execute("DROP TABLE IF EXISTS content_source_configs")
