"""Add llm_model_pricing + llm_usage_log tables for cost analysis.

Tracks per-model pricing and logs every LLM call with token counts,
enabling cost comparison across models and task types.

Revision ID: 045
Revises: 044
Create Date: 2026-02-25
"""

revision = "045"
down_revision = "044"

from alembic import op


def upgrade() -> None:
    # -- Model pricing catalog --
    op.execute("""
        CREATE TABLE llm_model_pricing (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            provider_key    VARCHAR(100) NOT NULL,
            model_name      VARCHAR(200) NOT NULL,
            display_name    VARCHAR(200) NOT NULL,
            input_price_per_1m  NUMERIC(10,4) NOT NULL,
            output_price_per_1m NUMERIC(10,4) NOT NULL,
            is_system       BOOLEAN NOT NULL DEFAULT false,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ,
            CONSTRAINT uq_llm_model_pricing_provider UNIQUE (provider_key)
        )
    """)

    # -- Usage log (partitioned by month) --
    op.execute("""
        CREATE TABLE llm_usage_log (
            id              UUID NOT NULL DEFAULT gen_random_uuid(),
            task_type       VARCHAR(50) NOT NULL,
            provider_key    VARCHAR(100) NOT NULL,
            model_name      VARCHAR(200) NOT NULL,
            input_tokens    INTEGER NOT NULL,
            output_tokens   INTEGER NOT NULL,
            latency_ms      INTEGER,
            call_id         UUID,
            tenant_id       UUID,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (id, created_at)
        ) PARTITION BY RANGE (created_at)
    """)

    # Partitions
    op.execute("""
        CREATE TABLE llm_usage_log_2026_02 PARTITION OF llm_usage_log
        FOR VALUES FROM ('2026-02-01') TO ('2026-03-01')
    """)
    op.execute("""
        CREATE TABLE llm_usage_log_2026_03 PARTITION OF llm_usage_log
        FOR VALUES FROM ('2026-03-01') TO ('2026-04-01')
    """)

    # Indexes on parent (propagate to partitions)
    op.execute("""
        CREATE INDEX ix_llm_usage_log_task_created
        ON llm_usage_log (task_type, created_at)
    """)
    op.execute("""
        CREATE INDEX ix_llm_usage_log_provider
        ON llm_usage_log (provider_key, created_at)
    """)

    # -- Seed system pricing --
    op.execute("""
        INSERT INTO llm_model_pricing
            (provider_key, model_name, display_name, input_price_per_1m, output_price_per_1m, is_system)
        VALUES
            ('anthropic-sonnet', 'claude-sonnet-4-5-20250929', 'Claude Sonnet 4.5', 3.0000, 15.0000, true),
            ('anthropic-haiku',  'claude-haiku-4-5-20251001',  'Claude Haiku 4.5',  1.0000,  5.0000, true),
            ('openai-gpt41-mini','gpt-4.1-mini',               'GPT-4.1 Mini',      0.4000,  1.6000, true),
            ('openai-gpt41-nano','gpt-4.1-nano',               'GPT-4.1 Nano',      0.1000,  0.4000, true),
            ('deepseek-chat',    'deepseek-chat',               'DeepSeek Chat',     0.2700,  1.1000, true),
            ('gemini-flash',     'gemini-2.5-flash',            'Gemini 2.5 Flash',  0.3000,  2.5000, true),
            ('openai-gpt5-mini', 'gpt-5-mini',                  'GPT-5 Mini',        0.2500,  2.0000, true),
            ('openai-gpt5-nano', 'gpt-5-nano',                  'GPT-5 Nano',        0.0500,  0.4000, true)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS llm_usage_log_2026_02")
    op.execute("DROP TABLE IF EXISTS llm_usage_log_2026_03")
    op.execute("DROP TABLE IF EXISTS llm_usage_log")
    op.execute("DROP TABLE IF EXISTS llm_model_pricing")
