"""Add extensions column to tenants table.

Stores Asterisk extension numbers per tenant for dynamic tenant resolution.
Extensions are looked up via WHERE :exten = ANY(extensions).

Revision ID: 040
Revises: 039
Create Date: 2026-02-23
"""

revision = "040"
down_revision = "039"

from alembic import op


def upgrade() -> None:
    op.execute("ALTER TABLE tenants ADD COLUMN extensions TEXT[] NOT NULL DEFAULT '{}'")
    op.execute("CREATE INDEX ix_tenants_extensions ON tenants USING GIN(extensions)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_tenants_extensions")
    op.execute("ALTER TABLE tenants DROP COLUMN IF EXISTS extensions")
