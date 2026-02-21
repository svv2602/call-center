"""Add tenant_id column to knowledge_articles.

Enables per-tenant knowledge base filtering. Articles with tenant_id=NULL
are shared (visible to all tenants). Articles with a specific tenant_id
are only visible to that tenant.

Backfills tenant_id from linked knowledge_sources (watched pages).

Revision ID: 035
Revises: 034
Create Date: 2026-02-21
"""

from collections.abc import Sequence

from alembic import op

revision: str = "035"
down_revision: str | None = "034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE knowledge_articles "
        "ADD COLUMN tenant_id UUID REFERENCES tenants(id) ON DELETE SET NULL"
    )
    op.execute(
        "CREATE INDEX ix_knowledge_articles_tenant "
        "ON knowledge_articles(tenant_id) WHERE tenant_id IS NOT NULL"
    )
    # Backfill: propagate tenant_id from watched-page sources to their linked articles
    op.execute(
        "UPDATE knowledge_articles a "
        "SET tenant_id = ks.tenant_id "
        "FROM knowledge_sources ks "
        "WHERE ks.article_id = a.id "
        "AND ks.tenant_id IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_knowledge_articles_tenant")
    op.execute("ALTER TABLE knowledge_articles DROP COLUMN IF EXISTS tenant_id")
