"""Add tenants table and tenant_id FK in knowledge_sources.

Tenants represent different retail networks (Prokoleso, Tvoya Shina, etc.)
served by the same Asterisk instance. Each tenant has its own Store API
endpoints, enabled tools, greeting, and prompt customization.

Revision ID: 032
Revises: 031
Create Date: 2026-02-21
"""

from collections.abc import Sequence

from alembic import op

revision: str = "032"
down_revision: str | None = "031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE tenants (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            slug            VARCHAR(50)  NOT NULL UNIQUE,
            name            VARCHAR(200) NOT NULL,
            network_id      VARCHAR(50)  NOT NULL,
            agent_name      VARCHAR(100) NOT NULL DEFAULT 'Олена',
            greeting        TEXT,
            enabled_tools   TEXT[]       NOT NULL DEFAULT '{}',
            prompt_suffix   TEXT,
            config          JSONB        NOT NULL DEFAULT '{}',
            is_active       BOOLEAN      NOT NULL DEFAULT true,
            created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ
        )
    """)

    op.execute("CREATE INDEX ix_tenants_slug ON tenants (slug)")
    op.execute("CREATE INDEX ix_tenants_is_active ON tenants (is_active) WHERE is_active = true")

    # FK: knowledge_sources -> tenants (watched pages per-network)
    op.execute(
        "ALTER TABLE knowledge_sources "
        "ADD COLUMN tenant_id UUID REFERENCES tenants(id) ON DELETE SET NULL"
    )
    op.execute(
        "CREATE INDEX ix_knowledge_sources_tenant ON knowledge_sources (tenant_id) "
        "WHERE tenant_id IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_knowledge_sources_tenant")
    op.execute("ALTER TABLE knowledge_sources DROP COLUMN IF EXISTS tenant_id")
    op.execute("DROP INDEX IF EXISTS ix_tenants_is_active")
    op.execute("DROP INDEX IF EXISTS ix_tenants_slug")
    op.execute("DROP TABLE IF EXISTS tenants")
