"""Add tenant_id column to calls table.

Links each call to the tenant that handled it.
PostgreSQL propagates ALTER TABLE to all partitions automatically.

Revision ID: 033
Revises: 032
Create Date: 2026-02-21
"""

from collections.abc import Sequence

from alembic import op

revision: str = "033"
down_revision: str | None = "032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE calls ADD COLUMN tenant_id UUID REFERENCES tenants(id) ON DELETE SET NULL"
    )
    op.execute("CREATE INDEX ix_calls_tenant_id ON calls (tenant_id) WHERE tenant_id IS NOT NULL")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_calls_tenant_id")
    op.execute("ALTER TABLE calls DROP COLUMN IF EXISTS tenant_id")
