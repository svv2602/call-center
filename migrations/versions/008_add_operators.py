"""Add operators and operator_status_log tables.

Revision ID: 008
"""

from alembic import op  # type: ignore[import-untyped]

revision = "008"
down_revision = "007"


def upgrade() -> None:
    op.execute("""
        CREATE TABLE operators (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name VARCHAR(100) NOT NULL,
            extension VARCHAR(20) NOT NULL UNIQUE,
            is_active BOOLEAN NOT NULL DEFAULT true,
            skills JSONB DEFAULT '[]'::jsonb,
            shift_start TIME DEFAULT '09:00',
            shift_end TIME DEFAULT '18:00',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX idx_operators_extension ON operators(extension)")
    op.execute("""
        CREATE TABLE operator_status_log (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            operator_id UUID NOT NULL REFERENCES operators(id),
            status VARCHAR(20) NOT NULL DEFAULT 'offline',
            changed_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE INDEX idx_op_status_log_operator_changed
            ON operator_status_log(operator_id, changed_at DESC)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS operator_status_log")
    op.execute("DROP TABLE IF EXISTS operators")
