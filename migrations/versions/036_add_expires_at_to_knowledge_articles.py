"""Add expires_at to knowledge_articles.

Revision ID: 036
Revises: 035
"""

from alembic import op

revision = "036"
down_revision = "035"


def upgrade() -> None:
    op.execute("""
        ALTER TABLE knowledge_articles
            ADD COLUMN expires_at TIMESTAMPTZ;
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE knowledge_articles
            DROP COLUMN IF EXISTS expires_at;
    """)
