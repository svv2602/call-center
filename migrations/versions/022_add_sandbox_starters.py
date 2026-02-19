"""Add sandbox scenario starters table.

Template conversations for quick-start sandbox testing.

Revision ID: 022
Revises: 021
Create Date: 2026-02-19
"""

from collections.abc import Sequence

from alembic import op

revision: str = "022"
down_revision: str | None = "021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE sandbox_scenario_starters (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            title VARCHAR(300) NOT NULL,
            first_message TEXT NOT NULL,
            scenario_type VARCHAR(50),
            tags TEXT[] NOT NULL DEFAULT '{}',
            customer_persona VARCHAR(50) DEFAULT 'neutral',
            description TEXT,
            mock_overrides JSONB,
            is_active BOOLEAN NOT NULL DEFAULT true,
            sort_order INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE INDEX idx_sandbox_starters_active ON sandbox_scenario_starters(is_active)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS sandbox_scenario_starters")
