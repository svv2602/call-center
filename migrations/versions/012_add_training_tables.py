"""Add training tables for agent training system.

Revision ID: 012

Tables: dialogue_examples, safety_rules, response_templates, tool_description_overrides
Extends: knowledge_articles (tags, priority, last_verified_at, meta)
"""

from alembic import op  # type: ignore[import-untyped]

revision = "012"
down_revision = "011"


def upgrade() -> None:
    # dialogue_examples — example conversations for evaluation/few-shot
    op.execute("""
        CREATE TABLE dialogue_examples (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            title VARCHAR(300) NOT NULL,
            scenario_type VARCHAR(50) NOT NULL,
            phase VARCHAR(20) NOT NULL,
            dialogue JSONB NOT NULL,
            tools_used TEXT[],
            description TEXT,
            is_active BOOLEAN NOT NULL DEFAULT true,
            sort_order INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    op.execute("""
        CREATE INDEX idx_dialogue_examples_scenario_type
        ON dialogue_examples(scenario_type)
    """)
    op.execute("""
        CREATE INDEX idx_dialogue_examples_phase
        ON dialogue_examples(phase)
    """)
    op.execute("""
        CREATE INDEX idx_dialogue_examples_is_active
        ON dialogue_examples(is_active)
    """)

    # safety_rules — adversarial test cases and behavioral boundaries
    op.execute("""
        CREATE TABLE safety_rules (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            title VARCHAR(300) NOT NULL,
            rule_type VARCHAR(50) NOT NULL,
            trigger_input TEXT NOT NULL,
            expected_behavior TEXT NOT NULL,
            severity VARCHAR(20) NOT NULL DEFAULT 'medium',
            is_active BOOLEAN NOT NULL DEFAULT true,
            sort_order INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    op.execute("""
        CREATE INDEX idx_safety_rules_rule_type
        ON safety_rules(rule_type)
    """)
    op.execute("""
        CREATE INDEX idx_safety_rules_severity
        ON safety_rules(severity)
    """)
    op.execute("""
        CREATE INDEX idx_safety_rules_is_active
        ON safety_rules(is_active)
    """)

    # response_templates — pre-defined agent responses (greeting, farewell, etc.)
    op.execute("""
        CREATE TABLE response_templates (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            template_key VARCHAR(100) NOT NULL UNIQUE,
            title VARCHAR(300) NOT NULL,
            content TEXT NOT NULL,
            description TEXT,
            is_active BOOLEAN NOT NULL DEFAULT true,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    op.execute("""
        CREATE INDEX idx_response_templates_template_key
        ON response_templates(template_key)
    """)
    op.execute("""
        CREATE INDEX idx_response_templates_is_active
        ON response_templates(is_active)
    """)

    # tool_description_overrides — DB overrides for tool descriptions
    op.execute("""
        CREATE TABLE tool_description_overrides (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tool_name VARCHAR(100) NOT NULL UNIQUE,
            description TEXT,
            input_schema_override JSONB,
            is_active BOOLEAN NOT NULL DEFAULT true,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    op.execute("""
        CREATE INDEX idx_tool_description_overrides_tool_name
        ON tool_description_overrides(tool_name)
    """)

    # Extend knowledge_articles with new columns
    op.execute("""
        ALTER TABLE knowledge_articles
        ADD COLUMN IF NOT EXISTS tags TEXT[],
        ADD COLUMN IF NOT EXISTS priority SMALLINT NOT NULL DEFAULT 5,
        ADD COLUMN IF NOT EXISTS last_verified_at DATE,
        ADD COLUMN IF NOT EXISTS meta JSONB NOT NULL DEFAULT '{}'
    """)

    op.execute("""
        CREATE INDEX idx_knowledge_articles_priority
        ON knowledge_articles(priority)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_knowledge_articles_priority")
    op.execute("ALTER TABLE knowledge_articles DROP COLUMN IF EXISTS tags")
    op.execute("ALTER TABLE knowledge_articles DROP COLUMN IF EXISTS priority")
    op.execute("ALTER TABLE knowledge_articles DROP COLUMN IF EXISTS last_verified_at")
    op.execute("ALTER TABLE knowledge_articles DROP COLUMN IF EXISTS meta")
    op.execute("DROP TABLE IF EXISTS tool_description_overrides")
    op.execute("DROP TABLE IF EXISTS response_templates")
    op.execute("DROP TABLE IF EXISTS safety_rules")
    op.execute("DROP TABLE IF EXISTS dialogue_examples")
