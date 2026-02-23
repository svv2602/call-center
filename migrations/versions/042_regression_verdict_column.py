"""Add verdict column to sandbox_regression_runs.

Supports approve/reject workflow for regression run results.
Values: NULL (pending), 'approved', 'rejected'.

Revision ID: 042
Revises: 041
Create Date: 2026-02-23
"""

revision = "042"
down_revision = "041"

from alembic import op


def upgrade() -> None:
    op.execute("ALTER TABLE sandbox_regression_runs ADD COLUMN IF NOT EXISTS verdict VARCHAR(20)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_regression_runs_verdict "
        "ON sandbox_regression_runs (verdict) WHERE verdict IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_regression_runs_verdict")
    op.execute("ALTER TABLE sandbox_regression_runs DROP COLUMN IF EXISTS verdict")
