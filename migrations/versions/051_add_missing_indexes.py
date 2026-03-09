"""Add missing indexes for commonly filtered columns.

- sandbox_conversations.tenant_id (filtered in list endpoint)
- calls.transferred_to_operator (filtered in analytics)
- llm_usage_log.tenant_id (filtered in cost summary)

Revision ID: 051
Revises: 050
Create Date: 2026-03-05
"""

from collections.abc import Sequence

from alembic import op

revision: str = "051"
down_revision: str | None = "050"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_sandbox_conversations_tenant_id
            ON sandbox_conversations (tenant_id)
            WHERE tenant_id IS NOT NULL
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_calls_transferred
            ON calls (transferred_to_operator)
            WHERE transferred_to_operator = true
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_llm_usage_log_tenant_id
            ON llm_usage_log (tenant_id)
            WHERE tenant_id IS NOT NULL
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_sandbox_conversations_tenant_id")
    op.execute("DROP INDEX IF EXISTS ix_calls_transferred")
    op.execute("DROP INDEX IF EXISTS ix_llm_usage_log_tenant_id")
