"""Add knowledge_articles and knowledge_embeddings tables.

Revision ID: 004
Revises: 003
Create Date: 2026-02-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "004"
down_revision: str | None = "003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Enable pgvector extension
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # --- knowledge_articles ---
    op.create_table(
        "knowledge_articles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column(
            "category",
            sa.String(50),
            nullable=False,
        ),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column(
            "active",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
        ),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.create_index(
        "idx_knowledge_articles_category",
        "knowledge_articles",
        ["category"],
    )
    op.create_index(
        "idx_knowledge_articles_active",
        "knowledge_articles",
        ["active"],
    )

    # --- knowledge_embeddings ---
    op.create_table(
        "knowledge_embeddings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "article_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("knowledge_articles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("chunk_text", sa.Text, nullable=False),
        sa.Column("chunk_index", sa.Integer, nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
        ),
    )

    # Add vector column via raw SQL (SQLAlchemy doesn't natively support vector type)
    op.execute("ALTER TABLE knowledge_embeddings ADD COLUMN embedding vector(1536)")

    op.create_index(
        "idx_knowledge_embeddings_article_id",
        "knowledge_embeddings",
        ["article_id"],
    )

    # HNSW index for cosine similarity search
    op.execute(
        "CREATE INDEX idx_knowledge_embeddings_vector "
        "ON knowledge_embeddings "
        "USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.drop_table("knowledge_embeddings")
    op.drop_table("knowledge_articles")
    op.execute("DROP EXTENSION IF EXISTS vector")
