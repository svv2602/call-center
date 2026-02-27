"""Add profile columns to customers table.

Store customer profile data (city, vehicles, delivery_address) to avoid
asking the same questions on every call.

Revision ID: 048
Revises: 047
Create Date: 2026-02-27
"""

revision = "048"
down_revision = "047"

from alembic import op


def upgrade() -> None:
    op.execute("""
        ALTER TABLE customers
        ADD COLUMN IF NOT EXISTS city VARCHAR(100)
    """)
    op.execute("""
        ALTER TABLE customers
        ADD COLUMN IF NOT EXISTS vehicles JSONB DEFAULT '[]'::jsonb
    """)
    op.execute("""
        ALTER TABLE customers
        ADD COLUMN IF NOT EXISTS delivery_address VARCHAR(500)
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE customers DROP COLUMN IF EXISTS delivery_address")
    op.execute("ALTER TABLE customers DROP COLUMN IF EXISTS vehicles")
    op.execute("ALTER TABLE customers DROP COLUMN IF EXISTS city")
