"""Add 'banned word' pronunciation rule: replace шукаю with synonyms.

Chirp3-HD TTS mispronounces шукаю (stresses first syllable instead of
second). Add a rule to the pronunciation section of all active prompts
telling the LLM to use synonyms (дивлюся, підбираю, перевіряю) instead.

Revision ID: 029
Revises: 028
Create Date: 2026-02-20
"""

from collections.abc import Sequence

from alembic import op

revision: str = "029"
down_revision: str | None = "028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RULE = (
    '### Заборонені слова (TTS вимовляє з неправильним наголосом)\n'
    '- НІКОЛИ не використовуй слово "шукаю" '
    '— замість нього: "дивлюся", "підбираю", "перевіряю"\n\n'
)


def upgrade() -> None:
    # Insert the banned-word rule before "### Загальні правила" in all prompts
    op.execute(
        f"""
        UPDATE prompt_versions
        SET system_prompt = regexp_replace(
            system_prompt,
            E'### Загальні правила',
            E'{_RULE}### Загальні правила'
        )
        WHERE system_prompt LIKE '%### Загальні правила%'
          AND system_prompt NOT LIKE '%Заборонені слова%'
        """
    )


def downgrade() -> None:
    op.execute(
        f"""
        UPDATE prompt_versions
        SET system_prompt = replace(
            system_prompt,
            E'{_RULE}',
            ''
        )
        WHERE system_prompt LIKE '%Заборонені слова%'
        """
    )
