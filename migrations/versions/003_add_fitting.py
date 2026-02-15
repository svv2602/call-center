"""Add fitting_stations and fitting_bookings tables.

Revision ID: 003
Revises: 002
Create Date: 2026-02-14
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- fitting_stations ---
    op.create_table(
        "fitting_stations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("city", sa.String(100), nullable=False),
        sa.Column("district", sa.String(100), nullable=True),
        sa.Column("address", sa.String(300), nullable=False),
        sa.Column("phone", sa.String(20), nullable=True),
        sa.Column("working_hours", sa.String(200), nullable=True),
        sa.Column("services", postgresql.JSONB, nullable=True),
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
    )
    op.create_index(
        "idx_fitting_stations_city", "fitting_stations", ["city"]
    )
    op.create_index(
        "idx_fitting_stations_active", "fitting_stations", ["active"]
    )

    # --- fitting_bookings ---
    op.create_table(
        "fitting_bookings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "station_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("fitting_stations.id"),
            nullable=False,
        ),
        sa.Column(
            "customer_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("customers.id"),
            nullable=True,
        ),
        sa.Column(
            "linked_order_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("booking_date", sa.Date, nullable=False),
        sa.Column("booking_time", sa.Time, nullable=False),
        sa.Column("service_type", sa.String(30), nullable=True),
        sa.Column("tire_diameter", sa.Integer, nullable=True),
        sa.Column("vehicle_info", sa.String(200), nullable=True),
        sa.Column(
            "status",
            sa.String(30),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("price", sa.Numeric(10, 2), nullable=True),
        sa.Column("source", sa.String(20), server_default="ai_agent"),
        sa.Column(
            "source_call_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "idx_fitting_bookings_station_id",
        "fitting_bookings",
        ["station_id"],
    )
    op.create_index(
        "idx_fitting_bookings_customer_id",
        "fitting_bookings",
        ["customer_id"],
    )
    op.create_index(
        "idx_fitting_bookings_date",
        "fitting_bookings",
        ["booking_date"],
    )
    op.create_index(
        "idx_fitting_bookings_status",
        "fitting_bookings",
        ["status"],
    )



def downgrade() -> None:
    op.drop_table("fitting_bookings")
    op.drop_table("fitting_stations")
