"""Add variant support to response_templates.

Revision ID: 013

Allow multiple variants per template_key with random selection.
Removes UNIQUE on template_key, adds variant_number column,
new unique constraint on (template_key, variant_number).
"""

from alembic import op  # type: ignore[import-untyped]

revision = "013"
down_revision = "012"


def upgrade() -> None:
    # Add variant_number column
    op.execute("""
        ALTER TABLE response_templates
        ADD COLUMN variant_number SMALLINT NOT NULL DEFAULT 1
    """)

    # Drop old unique constraint on template_key
    op.execute("""
        ALTER TABLE response_templates
        DROP CONSTRAINT IF EXISTS response_templates_template_key_key
    """)

    # Add new unique constraint on (template_key, variant_number)
    op.execute("""
        ALTER TABLE response_templates
        ADD CONSTRAINT uq_response_templates_key_variant
        UNIQUE (template_key, variant_number)
    """)


def downgrade() -> None:
    # Drop the new constraint
    op.execute("""
        ALTER TABLE response_templates
        DROP CONSTRAINT IF EXISTS uq_response_templates_key_variant
    """)

    # Drop the variant_number column
    op.execute("""
        ALTER TABLE response_templates
        DROP COLUMN IF EXISTS variant_number
    """)

    # Restore UNIQUE on template_key
    op.execute("""
        ALTER TABLE response_templates
        ADD CONSTRAINT response_templates_template_key_key
        UNIQUE (template_key)
    """)
