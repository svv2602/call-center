"""Add the promotions table and the promotions:read/write permissions.

Promotions used to live in ``knowledge_articles`` (category ``promotions``)
and reached the prompt as free text via
``src.agent.prompt_manager.fetch_tenant_promotions``. ``promotions`` holds
them as data the admin enters by hand: the network, a title, the text the
bot speaks (``bot_text``, 1–3 sentences with the conditions), a mandatory
validity window and ``overrides`` — which standard network conditions the
promotion beats (``free_delivery`` bool, ``extended_warranty_brands`` list,
``discount`` bool, ``partner_service`` ``{service, network_label}``).
``mention_brands`` empty means «mention only when the caller asks about
promotions». Owner decisions 2026-09-28.

``overrides`` accepts only the four known keys (CHECK below): an unknown key
is rejected by the database instead of being silently ignored by the reader.

The 8 live articles are copied by ``scripts/migrate_promotions.py``, not by
this migration — it runs dry by default and leaves the articles untouched.

Permissions: ``admin_users.permissions`` NULL means role defaults and is not
touched (the defaults are extended in ``src/api/permissions.py``). An
explicit custom list gets both new permissions appended unless it already
has ``*`` or them. ``[]`` is an explicit «no permissions» and stays empty.
Redis permission cache (TTL 300 s) expires on its own.

Revision ID: 063
Revises: 062
Create Date: 2026-09-28
"""

from collections.abc import Sequence

from alembic import op

revision: str = "063"
down_revision: str | None = "062"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PROMOTION_PERMISSIONS = ("promotions:read", "promotions:write")

# A custom (non-NULL, non-empty) list without the wildcard.
_CUSTOM_LIST = """
    jsonb_typeof(permissions) = 'array'
    AND permissions <> '[]'::jsonb
    AND NOT permissions @> '["*"]'::jsonb
"""


def upgrade() -> None:
    op.execute("""
        CREATE TABLE promotions (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            title VARCHAR(300) NOT NULL,
            bot_text TEXT NOT NULL,
            valid_from DATE NOT NULL,
            valid_to DATE NOT NULL,
            overrides JSONB NOT NULL DEFAULT '{}'::jsonb,
            mention_brands TEXT[] NOT NULL DEFAULT '{}'::text[],
            active BOOLEAN NOT NULL DEFAULT true,
            source_article_id UUID REFERENCES knowledge_articles(id) ON DELETE SET NULL,
            created_by UUID REFERENCES admin_users(id) ON DELETE SET NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT promotions_tenant_title_key UNIQUE (tenant_id, title),
            CONSTRAINT promotions_title_not_blank CHECK (btrim(title) <> ''),
            CONSTRAINT promotions_bot_text_length CHECK (
                btrim(bot_text) <> '' AND char_length(bot_text) <= 600
            ),
            CONSTRAINT promotions_valid_range CHECK (valid_to >= valid_from),
            CONSTRAINT promotions_overrides_known_keys CHECK (
                jsonb_typeof(overrides) = 'object'
                AND overrides - ARRAY[
                    'free_delivery', 'extended_warranty_brands',
                    'discount', 'partner_service'
                ] = '{}'::jsonb
            )
        )
    """)
    op.execute("""
        CREATE INDEX idx_promotions_tenant_validity
            ON promotions(tenant_id, valid_from, valid_to) WHERE active
    """)

    # Append only the permissions the list lacks; the rest of the list and its
    # order are kept. Re-running is a no-op.
    op.execute(f"""
        UPDATE admin_users
        SET permissions = permissions || (
            SELECT jsonb_agg(p ORDER BY p)
            FROM unnest(ARRAY['{PROMOTION_PERMISSIONS[0]}', '{PROMOTION_PERMISSIONS[1]}']) AS p
            WHERE NOT admin_users.permissions @> jsonb_build_array(p)
        )
        WHERE {_CUSTOM_LIST}
          AND NOT permissions @> '["{PROMOTION_PERMISSIONS[0]}", "{PROMOTION_PERMISSIONS[1]}"]'::jsonb
    """)


def downgrade() -> None:
    # The permissions did not exist before 063: drop exactly these two
    # elements, keep every other element in its original order.
    op.execute(f"""
        UPDATE admin_users
        SET permissions = (
            SELECT COALESCE(jsonb_agg(e.elem ORDER BY e.ord), '[]'::jsonb)
            FROM jsonb_array_elements(admin_users.permissions) WITH ORDINALITY AS e(elem, ord)
            WHERE e.elem NOT IN (
                '"{PROMOTION_PERMISSIONS[0]}"'::jsonb, '"{PROMOTION_PERMISSIONS[1]}"'::jsonb
            )
        )
        WHERE jsonb_typeof(permissions) = 'array'
          AND (permissions @> '["{PROMOTION_PERMISSIONS[0]}"]'::jsonb
               OR permissions @> '["{PROMOTION_PERMISSIONS[1]}"]'::jsonb)
    """)
    op.execute("DROP TABLE IF EXISTS promotions")
