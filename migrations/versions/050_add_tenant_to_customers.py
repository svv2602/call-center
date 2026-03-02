"""Add tenant_id to customers table.

Isolate customer profiles per tenant: one phone number can have separate
profiles in different networks (e.g. ProKoleso vs Tvoya Shina).

Revision ID: 050
Revises: 049
Create Date: 2026-03-02
"""

from alembic import op

revision = "050"
down_revision = "049"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add nullable tenant_id first
    op.execute("""
        ALTER TABLE customers
        ADD COLUMN IF NOT EXISTS tenant_id UUID REFERENCES tenants(id) ON DELETE RESTRICT
    """)

    # Backfill: assign existing customers to the first active tenant
    op.execute("""
        UPDATE customers
        SET tenant_id = (
            SELECT id FROM tenants WHERE is_active ORDER BY created_at LIMIT 1
        )
        WHERE tenant_id IS NULL
    """)

    # Make NOT NULL after backfill
    op.execute("""
        ALTER TABLE customers
        ALTER COLUMN tenant_id SET NOT NULL
    """)

    # Replace old phone-only unique index with (tenant_id, phone)
    op.execute("DROP INDEX IF EXISTS idx_customers_phone")
    op.execute("""
        CREATE UNIQUE INDEX idx_customers_tenant_phone
        ON customers (tenant_id, phone)
    """)
    op.execute("""
        CREATE INDEX idx_customers_tenant_id
        ON customers (tenant_id)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_customers_tenant_id")
    op.execute("DROP INDEX IF EXISTS idx_customers_tenant_phone")
    # Restore old unique index (may fail if duplicates exist)
    op.execute("""
        CREATE UNIQUE INDEX idx_customers_phone ON customers (phone)
    """)
    op.execute("ALTER TABLE customers DROP COLUMN IF EXISTS tenant_id")
