"""Change sandbox_regression_runs conversation FKs to ON DELETE SET NULL.

Prevents dangling FK errors when a sandbox conversation is deleted.
source_conversation_id becomes nullable (was NOT NULL).

Revision ID: 039
Revises: 038
Create Date: 2026-02-22
"""

from collections.abc import Sequence

from alembic import op

revision: str = "039"
down_revision: str | None = "038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # source_conversation_id: drop NOT NULL, replace FK with ON DELETE SET NULL
    op.execute("""
        ALTER TABLE sandbox_regression_runs
            ALTER COLUMN source_conversation_id DROP NOT NULL
    """)
    op.execute("""
        ALTER TABLE sandbox_regression_runs
            DROP CONSTRAINT sandbox_regression_runs_source_conversation_id_fkey,
            ADD CONSTRAINT sandbox_regression_runs_source_conversation_id_fkey
                FOREIGN KEY (source_conversation_id) REFERENCES sandbox_conversations(id)
                ON DELETE SET NULL
    """)

    # new_conversation_id: replace FK with ON DELETE SET NULL (already nullable)
    op.execute("""
        ALTER TABLE sandbox_regression_runs
            DROP CONSTRAINT sandbox_regression_runs_new_conversation_id_fkey,
            ADD CONSTRAINT sandbox_regression_runs_new_conversation_id_fkey
                FOREIGN KEY (new_conversation_id) REFERENCES sandbox_conversations(id)
                ON DELETE SET NULL
    """)


def downgrade() -> None:
    # Restore original FKs (no ON DELETE action)
    op.execute("""
        ALTER TABLE sandbox_regression_runs
            DROP CONSTRAINT sandbox_regression_runs_new_conversation_id_fkey,
            ADD CONSTRAINT sandbox_regression_runs_new_conversation_id_fkey
                FOREIGN KEY (new_conversation_id) REFERENCES sandbox_conversations(id)
    """)
    op.execute("""
        ALTER TABLE sandbox_regression_runs
            DROP CONSTRAINT sandbox_regression_runs_source_conversation_id_fkey,
            ADD CONSTRAINT sandbox_regression_runs_source_conversation_id_fkey
                FOREIGN KEY (source_conversation_id) REFERENCES sandbox_conversations(id)
    """)
    op.execute("""
        ALTER TABLE sandbox_regression_runs
            ALTER COLUMN source_conversation_id SET NOT NULL
    """)
