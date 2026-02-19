"""Add sandbox tables for agent testing sandbox.

Creates sandbox_conversations, sandbox_turns, sandbox_tool_calls tables
with appropriate indexes for the chat-based agent testing interface.

Revision ID: 021
Revises: 020
Create Date: 2026-02-19
"""

from collections.abc import Sequence

from alembic import op

revision: str = "021"
down_revision: str | None = "020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE sandbox_conversations (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            title VARCHAR(300) NOT NULL,
            prompt_version_id UUID REFERENCES prompt_versions(id),
            prompt_version_name VARCHAR(100),
            tool_mode VARCHAR(20) NOT NULL DEFAULT 'mock',
            tags TEXT[] NOT NULL DEFAULT '{}',
            scenario_type VARCHAR(50),
            status VARCHAR(20) NOT NULL DEFAULT 'active',
            is_baseline BOOLEAN NOT NULL DEFAULT false,
            created_by UUID REFERENCES admin_users(id),
            metadata JSONB NOT NULL DEFAULT '{}',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE INDEX idx_sandbox_conversations_status ON sandbox_conversations(status)
    """)
    op.execute("""
        CREATE INDEX idx_sandbox_conversations_tags ON sandbox_conversations USING GIN(tags)
    """)

    op.execute("""
        CREATE TABLE sandbox_turns (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            conversation_id UUID NOT NULL REFERENCES sandbox_conversations(id) ON DELETE CASCADE,
            parent_turn_id UUID REFERENCES sandbox_turns(id),
            turn_number INTEGER NOT NULL,
            speaker VARCHAR(20) NOT NULL,
            content TEXT NOT NULL,
            llm_latency_ms INTEGER,
            input_tokens INTEGER,
            output_tokens INTEGER,
            model VARCHAR(100),
            conversation_history JSONB,
            rating SMALLINT CHECK (rating >= 1 AND rating <= 5),
            rating_comment TEXT,
            branch_label VARCHAR(200),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE INDEX idx_sandbox_turns_conversation_id ON sandbox_turns(conversation_id)
    """)
    op.execute("""
        CREATE INDEX idx_sandbox_turns_parent_turn_id ON sandbox_turns(parent_turn_id)
    """)
    op.execute("""
        CREATE INDEX idx_sandbox_turns_conv_turn ON sandbox_turns(conversation_id, turn_number)
    """)

    op.execute("""
        CREATE TABLE sandbox_tool_calls (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            turn_id UUID NOT NULL REFERENCES sandbox_turns(id) ON DELETE CASCADE,
            tool_name VARCHAR(100) NOT NULL,
            tool_args JSONB NOT NULL DEFAULT '{}',
            tool_result JSONB NOT NULL DEFAULT '{}',
            duration_ms INTEGER,
            is_mock BOOLEAN NOT NULL DEFAULT true,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE INDEX idx_sandbox_tool_calls_turn_id ON sandbox_tool_calls(turn_id)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS sandbox_tool_calls")
    op.execute("DROP TABLE IF EXISTS sandbox_turns")
    op.execute("DROP TABLE IF EXISTS sandbox_conversations")
