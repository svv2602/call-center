"""Add soft-delete flag to customers table.

Admins may hide customer profiles from the admin UI without losing
call-history linkage. Existing agent-side upsert logic is unchanged —
if a hidden customer calls again, the same row is reused.

Revision ID: 053
Revises: 052
Create Date: 2026-07-22
"""

from collections.abc import Sequence

from alembic import op

revision: str = "053"
down_revision: str | None = "052"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE customers ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ")
    # Partial index accelerates the "active only" list view (default filter)
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_customers_active
        ON customers (tenant_id, last_call_at DESC)
        WHERE deleted_at IS NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_customers_active")
    op.execute("ALTER TABLE customers DROP COLUMN IF EXISTS deleted_at")
