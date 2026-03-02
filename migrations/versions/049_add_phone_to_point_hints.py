"""Add phone column to point_hints table.

Stores phone numbers for fitting stations / pickup points so the LLM
agent can tell the customer how to call the station directly (e.g. when
same-day booking is unavailable online).

Revision ID: 049
Revises: 048
Create Date: 2026-03-02
"""

from alembic import op

revision = "049"
down_revision = "048"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE point_hints ADD COLUMN phone VARCHAR(50) NOT NULL DEFAULT ''"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE point_hints DROP COLUMN phone")
