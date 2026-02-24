"""Add partial unique index on knowledge_articles title.

Deactivates duplicate articles (keeping oldest per lower(title) group),
then creates partial unique index to prevent future duplicates.

Revision ID: 043
Revises: 042
Create Date: 2026-02-24
"""

revision = "043"
down_revision = "042"

from alembic import op


def upgrade() -> None:
    # Deactivate duplicates: keep the oldest active article per lower(title)
    op.execute("""
        UPDATE knowledge_articles SET active = false, updated_at = now()
        WHERE id IN (
            SELECT id FROM (
                SELECT id, ROW_NUMBER() OVER (
                    PARTITION BY lower(title) ORDER BY created_at ASC
                ) AS rn
                FROM knowledge_articles
                WHERE active = true
            ) sub WHERE rn > 1
        )
    """)

    # Create partial unique index — only active articles must have unique titles
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_knowledge_articles_title_active
        ON knowledge_articles (lower(title))
        WHERE active = true
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_knowledge_articles_title_active")
