"""Add cached_input_price_per_1m to the two pricing tables.

058 started recording how many input tokens came from the provider's cache but
left the rate hardcoded at half the standard price. That constant is wrong for
every model this project runs: LiteLLM's ``cache_read_input_token_cost`` puts
gpt-4.1-mini and gpt-4.1-nano at 0.25x and gpt-5-mini, gpt-5-nano, both Claude
models, deepseek-chat and both Gemini flashes at 0.10x. No single multiplier
fits, so the rate becomes a column and is synced from LiteLLM like the other
two.

Nullable on purpose: a model whose provider has no cache — or one LiteLLM does
not know — must be billed at the full input rate, not at an invented discount.

Revision ID: 060
Revises: 059
Create Date: 2026-09-14
"""

from collections.abc import Sequence

from alembic import op

revision: str = "060"
down_revision: str | None = "059"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE llm_model_pricing
        ADD COLUMN IF NOT EXISTS cached_input_price_per_1m NUMERIC(10, 4)
    """)
    op.execute("""
        ALTER TABLE llm_pricing_catalog
        ADD COLUMN IF NOT EXISTS cached_input_price_per_1m NUMERIC(10, 4)
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE llm_model_pricing
        DROP COLUMN IF EXISTS cached_input_price_per_1m
    """)
    op.execute("""
        ALTER TABLE llm_pricing_catalog
        DROP COLUMN IF EXISTS cached_input_price_per_1m
    """)
