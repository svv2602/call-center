"""Add point_hints table for persistent hint storage.

Moves point hints (district/landmarks/description) from Redis-only to PostgreSQL
as primary storage with Redis as write-through cache.

Revision ID: 044
Revises: 043
Create Date: 2026-02-24
"""

revision = "044"
down_revision = "043"

from alembic import op


def upgrade() -> None:
    op.execute("""
        CREATE TABLE point_hints (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            point_type  VARCHAR(20) NOT NULL,
            point_id    VARCHAR(200) NOT NULL,
            district    TEXT NOT NULL DEFAULT '',
            landmarks   TEXT NOT NULL DEFAULT '',
            description TEXT NOT NULL DEFAULT '',
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at  TIMESTAMPTZ,
            CONSTRAINT chk_point_type CHECK (point_type IN ('fitting_station', 'pickup_point')),
            CONSTRAINT uq_point_hints_type_id UNIQUE (point_type, point_id)
        )
    """)
    op.execute("""
        CREATE INDEX ix_point_hints_type ON point_hints (point_type)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS point_hints")
