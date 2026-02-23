"""Fix missing DEFAULT gen_random_uuid() on knowledge tables.

Tables knowledge_articles and knowledge_embeddings were created in
migration 004 without a default UUID generator on the id column.
This caused NULL constraint violations on INSERT without explicit id.

Revision ID: 015
Revises: 014
Create Date: 2026-02-17
"""

from collections.abc import Sequence

from alembic import op

revision: str = "015"
down_revision: str | None = "014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE knowledge_articles ALTER COLUMN id SET DEFAULT gen_random_uuid()")
    op.execute("ALTER TABLE knowledge_embeddings ALTER COLUMN id SET DEFAULT gen_random_uuid()")


def downgrade() -> None:
    op.execute("ALTER TABLE knowledge_embeddings ALTER COLUMN id DROP DEFAULT")
    op.execute("ALTER TABLE knowledge_articles ALTER COLUMN id DROP DEFAULT")
