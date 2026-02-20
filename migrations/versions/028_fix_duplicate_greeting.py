"""Fix duplicate greeting — remove 'Привітайся' instruction, add 'НЕ вітайся повторно'.

The seeded v3.0 prompt tells the LLM to greet ("Привітайся і запитай, чим можеш
допомогти") as step 1 of the tire search scenario.  But the pipeline already plays
a TTS greeting before the LLM is invoked, so the LLM produces a *second* greeting.

Fix:
1. Add "НЕ вітайся повторно" to the ## Правила section
2. Remove "Привітайся і" from step 1 of the tire search scenario

Revision ID: 028
Revises: 027
Create Date: 2026-02-20
"""

from collections.abc import Sequence

from alembic import op

revision: str = "028"
down_revision: str | None = "027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NO_GREET_RULE = "- НЕ вітайся повторно — привітання вже озвучено на початку дзвінка"


def upgrade() -> None:
    # 1. Add "НЕ вітайся повторно" rule after "Максимум 2-3 речення" in all prompts
    op.execute(
        f"""
        UPDATE prompt_versions
        SET system_prompt = regexp_replace(
            system_prompt,
            E'- Максимум 2-3 речення у відповіді\\n',
            E'- Максимум 2-3 речення у відповіді\\n{_NO_GREET_RULE}\\n'
        )
        WHERE system_prompt LIKE '%Максимум 2-3 речення%'
          AND system_prompt NOT LIKE '%НЕ вітайся повторно%'
        """
    )
    # Also handle v3.1-concise (slightly different wording)
    op.execute(
        f"""
        UPDATE prompt_versions
        SET system_prompt = regexp_replace(
            system_prompt,
            E'- Максимум 2-3 речення, говори як по телефону\\n',
            E'- Максимум 2-3 речення, говори як по телефону\\n{_NO_GREET_RULE}\\n'
        )
        WHERE system_prompt LIKE '%Максимум 2-3 речення, говори як по телефону%'
          AND system_prompt NOT LIKE '%НЕ вітайся повторно%'
        """
    )

    # 2. Change "Привітайся і запитай, чим можеш допомогти" →
    #    "Запитай клієнта, чим можеш допомогти" (remove greeting instruction)
    op.execute(
        """
        UPDATE prompt_versions
        SET system_prompt = replace(
            system_prompt,
            'Привітайся і запитай, чим можеш допомогти',
            'Запитай клієнта, чим можеш допомогти'
        )
        WHERE system_prompt LIKE '%Привітайся і запитай%'
        """
    )


def downgrade() -> None:
    # Revert "НЕ вітайся повторно" rule
    op.execute(
        f"""
        UPDATE prompt_versions
        SET system_prompt = replace(
            system_prompt,
            E'{_NO_GREET_RULE}\\n',
            ''
        )
        WHERE system_prompt LIKE '%НЕ вітайся повторно%'
        """
    )
    # Revert greeting instruction
    op.execute(
        """
        UPDATE prompt_versions
        SET system_prompt = replace(
            system_prompt,
            'Запитай клієнта, чим можеш допомогти',
            'Привітайся і запитай, чим можеш допомогти'
        )
        WHERE system_prompt LIKE '%Запитай клієнта, чим можеш допомогти%'
        """
    )
