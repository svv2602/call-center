"""Add promo_summary to knowledge_articles.

Revision ID: 037
Revises: 036
"""

from alembic import op

revision = "037"
down_revision = "036"


def upgrade() -> None:
    op.execute("""
        ALTER TABLE knowledge_articles
            ADD COLUMN promo_summary TEXT;
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE knowledge_articles
            DROP COLUMN IF EXISTS promo_summary;
    """)
