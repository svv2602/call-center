"""Add working_hours to tenants for per-tenant business-hours check.

NULL means "24/7, skip the check" — preserves existing behavior for tenants
that haven't configured a schedule yet.

Structure of working_hours (when set):
    {
      "timezone": "Europe/Kyiv",
      "mon": {"start": "09:00", "end": "18:00"},
      "tue": {"start": "09:00", "end": "18:00"},
      "wed": {"start": "09:00", "end": "18:00"},
      "thu": {"start": "09:00", "end": "18:00"},
      "fri": {"start": "09:00", "end": "18:00"},
      "sat": {"start": "10:00", "end": "16:00"},
      "sun": null
    }

`null` for a day = closed. Times are "HH:MM" strings in the tenant's timezone.

Revision ID: 054
Revises: 053
Create Date: 2026-07-23
"""

from collections.abc import Sequence

from alembic import op

revision: str = "054"
down_revision: str | None = "053"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE tenants ADD COLUMN IF NOT EXISTS working_hours JSONB DEFAULT NULL")


def downgrade() -> None:
    op.execute("ALTER TABLE tenants DROP COLUMN IF EXISTS working_hours")
