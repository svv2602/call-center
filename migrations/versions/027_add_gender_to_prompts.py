"""Add explicit feminine gender instruction to seeded prompt versions.

The agent (Олена) has a female TTS voice but the prompt lacked an explicit
instruction to use feminine grammatical gender in Ukrainian, causing the LLM
to occasionally use masculine verb/adjective forms.

Revision ID: 027
Revises: 026
Create Date: 2026-02-20
"""

from collections.abc import Sequence

from alembic import op

revision: str = "027"
down_revision: str | None = "026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_GENDER_LINE = (
    "Ти — жінка. ЗАВЖДИ говори про себе у жіночому роді: "
    '"я знайшла", "я перевірила", "я готова допомогти", "я рада".'
)


def upgrade() -> None:
    # Insert the gender instruction after "Тебе звати Олена." in both seeded prompts.
    # Uses regexp_replace to add the line after the name introduction.
    op.execute(
        f"""
        UPDATE prompt_versions
        SET system_prompt = regexp_replace(
            system_prompt,
            E'Тебе звати Олена\\.\\n',
            E'Тебе звати Олена.\\n{_GENDER_LINE}\\n'
        )
        WHERE system_prompt LIKE '%Тебе звати Олена.%'
          AND system_prompt NOT LIKE '%жіночому роді%'
        """
    )
    # Also handle v3.1-concise which uses "Ти — Олена,"
    op.execute(
        f"""
        UPDATE prompt_versions
        SET system_prompt = regexp_replace(
            system_prompt,
            E'Ти — Олена, голосовий асистент інтернет-магазину шин\\.',
            E'Ти — Олена, голосовий асистент інтернет-магазину шин.\\n{_GENDER_LINE}'
        )
        WHERE system_prompt LIKE '%Ти — Олена, голосовий асистент%'
          AND system_prompt NOT LIKE '%жіночому роді%'
        """
    )


def downgrade() -> None:
    # Remove the gender instruction line
    op.execute(
        f"""
        UPDATE prompt_versions
        SET system_prompt = replace(
            system_prompt,
            E'\\n{_GENDER_LINE}',
            ''
        )
        WHERE system_prompt LIKE '%жіночому роді%'
        """
    )
