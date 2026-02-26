"""Add is_hidden flag to llm_pricing_catalog.

Allow hiding unwanted models from the catalog view. Hidden models are
preserved across syncs and can be toggled back via the Admin UI.

Revision ID: 047
Revises: 046
Create Date: 2026-02-26
"""

revision = "047"
down_revision = "046"

from alembic import op


def upgrade() -> None:
    op.execute("""
        ALTER TABLE llm_pricing_catalog
        ADD COLUMN is_hidden BOOLEAN NOT NULL DEFAULT false
    """)
    op.execute("""
        CREATE INDEX ix_catalog_hidden
        ON llm_pricing_catalog(is_hidden) WHERE is_hidden = true
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_catalog_hidden")
    op.execute("ALTER TABLE llm_pricing_catalog DROP COLUMN IF EXISTS is_hidden")
