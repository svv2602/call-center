"""Add cached_input_tokens to llm_usage_log.

Tracks how many of the input tokens were served from the provider's automatic
prompt cache. For OpenAI this comes from
``usage.prompt_tokens_details.cached_tokens`` (enabled by default since
Oct 2024 for prefixes ≥1024 tokens, ~50% cheaper for gpt-4.1-mini and
~90% cheaper for gpt-5-mini/gpt-5.4-mini). Anthropic reports it as
``usage.cache_read_input_tokens``.

Enables per-provider cache-hit ratio analysis and accurate cost accounting
(cached tokens must be billed at the cached rate, not the standard rate).

Revision ID: 058
Revises: 057
Create Date: 2026-08-14
"""

from collections.abc import Sequence

from alembic import op

revision: str = "058"
down_revision: str | None = "057"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE llm_usage_log
        ADD COLUMN IF NOT EXISTS cached_input_tokens INTEGER NOT NULL DEFAULT 0
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE llm_usage_log
        DROP COLUMN IF EXISTS cached_input_tokens
    """)
