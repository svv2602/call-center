"""Add orders and order_items tables.

Revision ID: 002
Revises: 001
Create Date: 2026-02-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "002"
down_revision: str | None = "001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- orders ---
    op.create_table(
        "orders",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("order_number", sa.String(20), nullable=True),
        sa.Column(
            "customer_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("customers.id"),
            nullable=True,
        ),
        sa.Column(
            "status",
            sa.String(30),
            nullable=False,
            server_default="draft",
        ),
        sa.Column("items", postgresql.JSONB, nullable=True),
        sa.Column("subtotal", sa.Numeric(10, 2), nullable=True),
        sa.Column("delivery_cost", sa.Numeric(10, 2), nullable=True),
        sa.Column("total", sa.Numeric(10, 2), nullable=True),
        sa.Column("delivery_type", sa.String(20), nullable=True),
        sa.Column("delivery_address", postgresql.JSONB, nullable=True),
        sa.Column(
            "payment_method",
            sa.String(30),
            nullable=True,
        ),
        sa.Column("source", sa.String(20), server_default="ai_agent"),
        sa.Column(
            "source_call_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("idempotency_key", sa.String(50), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
        ),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.create_index("idx_orders_customer_id", "orders", ["customer_id"])
    op.create_index("idx_orders_order_number", "orders", ["order_number"], unique=True)
    op.create_index("idx_orders_idempotency_key", "orders", ["idempotency_key"])

    # --- order_items ---
    op.create_table(
        "order_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "order_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("product_id", sa.String(50), nullable=False),
        sa.Column("product_name", sa.String(300), nullable=True),
        sa.Column("quantity", sa.Integer, nullable=False),
        sa.Column("price_per_unit", sa.Numeric(10, 2), nullable=True),
        sa.Column("total", sa.Numeric(10, 2), nullable=True),
    )
    op.create_index("idx_order_items_order_id", "order_items", ["order_id"])


def downgrade() -> None:
    op.drop_table("order_items")
    op.drop_table("orders")
