"""Add callback_requests table for after-hours callbacks.

When a customer reaches the bot outside working hours and the LLM cannot
transfer them, it collects their phone and stores a callback request.
Operators pick these up from the admin UI the next morning.

Revision ID: 055
Revises: 054
Create Date: 2026-07-23
"""

from collections.abc import Sequence

from alembic import op

revision: str = "055"
down_revision: str | None = "054"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS callback_requests (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id UUID REFERENCES tenants(id) ON DELETE SET NULL,
            call_id UUID,
            phone VARCHAR(30) NOT NULL,
            preferred_time VARCHAR(100),
            note TEXT,
            reason VARCHAR(40) NOT NULL DEFAULT 'after_hours',
            status VARCHAR(20) NOT NULL DEFAULT 'pending',
            operator_id UUID REFERENCES operators(id) ON DELETE SET NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            called_back_at TIMESTAMPTZ,
            note_result TEXT
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_callback_requests_status_created
            ON callback_requests(status, created_at DESC)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_callback_requests_tenant_created
            ON callback_requests(tenant_id, created_at DESC)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_callback_requests_tenant_created")
    op.execute("DROP INDEX IF EXISTS idx_callback_requests_status_created")
    op.execute("DROP TABLE IF EXISTS callback_requests")
