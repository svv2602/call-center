"""Split system:read/write into configuration, monitoring, onec_data permissions.

Replaces coarse system:read/write with granular permissions:
- system:read  → configuration:read, monitoring:read, onec_data:read
- system:write → configuration:write

Only affects users with custom permissions (non-NULL JSONB column).
Redis permission cache (TTL 300s) expires automatically.

Revision ID: 041
Revises: 040
Create Date: 2026-02-23
"""

revision = "041"
down_revision = "040"

from alembic import op


def upgrade() -> None:
    op.execute("""
        UPDATE admin_users
        SET permissions = (
            SELECT jsonb_agg(DISTINCT elem)
            FROM (
                SELECT e.elem
                FROM jsonb_array_elements_text(admin_users.permissions) AS e(elem)
                WHERE e.elem NOT IN ('system:read', 'system:write')
                UNION ALL
                SELECT unnest(ARRAY['configuration:read', 'monitoring:read', 'onec_data:read'])
                WHERE admin_users.permissions @> '"system:read"'::jsonb
                UNION ALL
                SELECT 'configuration:write'
                WHERE admin_users.permissions @> '"system:write"'::jsonb
            ) AS expanded(elem)
        )
        WHERE permissions IS NOT NULL
          AND permissions != '[]'::jsonb
          AND (permissions @> '"system:read"'::jsonb
               OR permissions @> '"system:write"'::jsonb)
    """)


def downgrade() -> None:
    op.execute("""
        UPDATE admin_users
        SET permissions = (
            SELECT jsonb_agg(DISTINCT elem)
            FROM (
                SELECT e.elem
                FROM jsonb_array_elements_text(admin_users.permissions) AS e(elem)
                WHERE e.elem NOT IN (
                    'configuration:read', 'configuration:write',
                    'monitoring:read', 'onec_data:read'
                )
                UNION ALL
                SELECT 'system:read'
                WHERE admin_users.permissions @> '"configuration:read"'::jsonb
                   OR admin_users.permissions @> '"monitoring:read"'::jsonb
                   OR admin_users.permissions @> '"onec_data:read"'::jsonb
                UNION ALL
                SELECT 'system:write'
                WHERE admin_users.permissions @> '"configuration:write"'::jsonb
            ) AS expanded(elem)
        )
        WHERE permissions IS NOT NULL
          AND permissions != '[]'::jsonb
          AND (permissions @> '"configuration:read"'::jsonb
               OR permissions @> '"configuration:write"'::jsonb
               OR permissions @> '"monitoring:read"'::jsonb
               OR permissions @> '"onec_data:read"'::jsonb)
    """)
