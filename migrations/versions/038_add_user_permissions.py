"""Add permissions JSONB column to admin_users and content_manager role.

Revision ID: 038
Revises: 037
"""

from alembic import op

revision = "038"
down_revision = "037"


def upgrade() -> None:
    op.execute("""
        ALTER TABLE admin_users
            ADD COLUMN permissions JSONB DEFAULT NULL;
    """)
    # Extend role CHECK constraint to include content_manager
    op.execute("""
        ALTER TABLE admin_users
            DROP CONSTRAINT IF EXISTS admin_users_role_check;
    """)
    op.execute("""
        ALTER TABLE admin_users
            ADD CONSTRAINT admin_users_role_check
            CHECK (role IN ('admin', 'analyst', 'operator', 'content_manager'));
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE admin_users
            DROP COLUMN IF EXISTS permissions;
    """)
    op.execute("""
        ALTER TABLE admin_users
            DROP CONSTRAINT IF EXISTS admin_users_role_check;
    """)
    op.execute("""
        ALTER TABLE admin_users
            ADD CONSTRAINT admin_users_role_check
            CHECK (role IN ('admin', 'analyst', 'operator'));
    """)
