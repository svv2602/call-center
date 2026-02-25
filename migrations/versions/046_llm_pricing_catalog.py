"""Add llm_pricing_catalog table and extend llm_model_pricing.

Full catalog of LLM models from LiteLLM for price comparison.
New columns on llm_model_pricing: provider_type, include_in_comparison,
catalog_model_key (FK to catalog).

Revision ID: 046
Revises: 045
Create Date: 2026-02-25
"""

revision = "046"
down_revision = "045"

from alembic import op


def upgrade() -> None:
    # -- LLM pricing catalog (full model list from LiteLLM) --
    op.execute("""
        CREATE TABLE llm_pricing_catalog (
            model_key           VARCHAR(300) PRIMARY KEY,
            provider_type       VARCHAR(50) NOT NULL,
            display_name        VARCHAR(300),
            input_price_per_1m  NUMERIC(10,4) NOT NULL,
            output_price_per_1m NUMERIC(10,4) NOT NULL,
            max_input_tokens    INTEGER,
            max_output_tokens   INTEGER,
            is_new              BOOLEAN NOT NULL DEFAULT true,
            synced_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE INDEX ix_catalog_provider
        ON llm_pricing_catalog(provider_type)
    """)
    op.execute("""
        CREATE INDEX ix_catalog_new
        ON llm_pricing_catalog(is_new) WHERE is_new = true
    """)

    # -- Extend llm_model_pricing --
    op.execute("""
        ALTER TABLE llm_model_pricing
        ADD COLUMN provider_type VARCHAR(50)
    """)
    op.execute("""
        ALTER TABLE llm_model_pricing
        ADD COLUMN include_in_comparison BOOLEAN NOT NULL DEFAULT true
    """)
    op.execute("""
        ALTER TABLE llm_model_pricing
        ADD COLUMN catalog_model_key VARCHAR(300)
    """)

    # -- Backfill provider_type from provider_key --
    op.execute("""
        UPDATE llm_model_pricing SET provider_type = 'anthropic'
        WHERE provider_key LIKE 'anthropic-%'
    """)
    op.execute("""
        UPDATE llm_model_pricing SET provider_type = 'openai'
        WHERE provider_key LIKE 'openai-%'
    """)
    op.execute("""
        UPDATE llm_model_pricing SET provider_type = 'deepseek'
        WHERE provider_key LIKE 'deepseek%'
    """)
    op.execute("""
        UPDATE llm_model_pricing SET provider_type = 'gemini'
        WHERE provider_key LIKE 'gemini%'
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE llm_model_pricing DROP COLUMN IF EXISTS catalog_model_key")
    op.execute("ALTER TABLE llm_model_pricing DROP COLUMN IF EXISTS include_in_comparison")
    op.execute("ALTER TABLE llm_model_pricing DROP COLUMN IF EXISTS provider_type")
    op.execute("DROP TABLE IF EXISTS llm_pricing_catalog")
