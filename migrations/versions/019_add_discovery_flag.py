"""Add is_discovery flag and parent_id for watched page discovery mode.

Discovery pages (e.g. /promotions/) automatically discover child pages
and create watched page entries for each.

Revision ID: 019
Revises: 018
Create Date: 2026-02-18
"""

from collections.abc import Sequence

from alembic import op

revision: str = "019"
down_revision: str | None = "018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE knowledge_sources
            ADD COLUMN is_discovery BOOLEAN NOT NULL DEFAULT false,
            ADD COLUMN parent_id UUID REFERENCES knowledge_sources(id) ON DELETE CASCADE
    """)
    op.execute(
        "CREATE INDEX ix_knowledge_sources_parent_id ON knowledge_sources (parent_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_knowledge_sources_parent_id")
    op.execute("""
        ALTER TABLE knowledge_sources
            DROP COLUMN IF EXISTS is_discovery,
            DROP COLUMN IF EXISTS parent_id
    """)
