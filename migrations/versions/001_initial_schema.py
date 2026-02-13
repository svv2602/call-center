"""Initial schema: calls, call_turns, call_tool_calls, customers.

Revision ID: 001
Revises: None
Create Date: 2026-02-13
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- customers ---
    op.create_table(
        "customers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("phone", sa.String(20), nullable=False),
        sa.Column("name", sa.String(200), nullable=True),
        sa.Column("total_calls", sa.Integer, server_default="0"),
        sa.Column("first_call_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_call_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("idx_customers_phone", "customers", ["phone"], unique=True)

    # --- calls (partitioned by started_at) ---
    op.execute("""
        CREATE TABLE calls (
            id UUID NOT NULL,
            caller_id VARCHAR(20),
            customer_id UUID,
            started_at TIMESTAMP WITH TIME ZONE NOT NULL,
            ended_at TIMESTAMP WITH TIME ZONE,
            duration_seconds INTEGER,
            scenario VARCHAR(50),
            transferred_to_operator BOOLEAN DEFAULT FALSE,
            transfer_reason VARCHAR(50),
            order_id UUID,
            fitting_booking_id UUID,
            prompt_version VARCHAR(50),
            quality_score FLOAT,
            cost_breakdown JSONB,
            total_cost_usd FLOAT,
            PRIMARY KEY (id, started_at)
        ) PARTITION BY RANGE (started_at)
    """)
    op.execute("""
        CREATE TABLE calls_2026_01 PARTITION OF calls
            FOR VALUES FROM ('2026-01-01') TO ('2026-02-01')
    """)
    op.execute("""
        CREATE TABLE calls_2026_02 PARTITION OF calls
            FOR VALUES FROM ('2026-02-01') TO ('2026-03-01')
    """)
    op.execute("""
        CREATE TABLE calls_2026_03 PARTITION OF calls
            FOR VALUES FROM ('2026-03-01') TO ('2026-04-01')
    """)
    op.execute("""
        CREATE TABLE calls_2026_04 PARTITION OF calls
            FOR VALUES FROM ('2026-04-01') TO ('2026-05-01')
    """)
    op.execute("""
        CREATE TABLE calls_2026_05 PARTITION OF calls
            FOR VALUES FROM ('2026-05-01') TO ('2026-06-01')
    """)
    op.create_index("idx_calls_caller_id", "calls", ["caller_id"])
    op.create_index("idx_calls_customer_id", "calls", ["customer_id"])
    op.create_index("idx_calls_started_at", "calls", ["started_at"])

    # --- call_turns (partitioned by created_at) ---
    op.execute("""
        CREATE TABLE call_turns (
            id UUID NOT NULL,
            call_id UUID NOT NULL,
            turn_number INTEGER,
            speaker VARCHAR(10),
            content TEXT,
            stt_confidence FLOAT,
            stt_latency_ms INTEGER,
            llm_latency_ms INTEGER,
            tts_latency_ms INTEGER,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL,
            PRIMARY KEY (id, created_at)
        ) PARTITION BY RANGE (created_at)
    """)
    op.execute("""
        CREATE TABLE call_turns_2026_01 PARTITION OF call_turns
            FOR VALUES FROM ('2026-01-01') TO ('2026-02-01')
    """)
    op.execute("""
        CREATE TABLE call_turns_2026_02 PARTITION OF call_turns
            FOR VALUES FROM ('2026-02-01') TO ('2026-03-01')
    """)
    op.execute("""
        CREATE TABLE call_turns_2026_03 PARTITION OF call_turns
            FOR VALUES FROM ('2026-03-01') TO ('2026-04-01')
    """)
    op.execute("""
        CREATE TABLE call_turns_2026_04 PARTITION OF call_turns
            FOR VALUES FROM ('2026-04-01') TO ('2026-05-01')
    """)
    op.execute("""
        CREATE TABLE call_turns_2026_05 PARTITION OF call_turns
            FOR VALUES FROM ('2026-05-01') TO ('2026-06-01')
    """)
    op.create_index("idx_call_turns_call_id", "call_turns", ["call_id"])

    # --- call_tool_calls (partitioned by created_at) ---
    op.execute("""
        CREATE TABLE call_tool_calls (
            id UUID NOT NULL,
            call_id UUID NOT NULL,
            turn_number INTEGER,
            tool_name VARCHAR(50),
            tool_args JSONB,
            tool_result JSONB,
            duration_ms INTEGER,
            success BOOLEAN,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL,
            PRIMARY KEY (id, created_at)
        ) PARTITION BY RANGE (created_at)
    """)
    op.execute("""
        CREATE TABLE call_tool_calls_2026_01 PARTITION OF call_tool_calls
            FOR VALUES FROM ('2026-01-01') TO ('2026-02-01')
    """)
    op.execute("""
        CREATE TABLE call_tool_calls_2026_02 PARTITION OF call_tool_calls
            FOR VALUES FROM ('2026-02-01') TO ('2026-03-01')
    """)
    op.execute("""
        CREATE TABLE call_tool_calls_2026_03 PARTITION OF call_tool_calls
            FOR VALUES FROM ('2026-03-01') TO ('2026-04-01')
    """)
    op.execute("""
        CREATE TABLE call_tool_calls_2026_04 PARTITION OF call_tool_calls
            FOR VALUES FROM ('2026-04-01') TO ('2026-05-01')
    """)
    op.execute("""
        CREATE TABLE call_tool_calls_2026_05 PARTITION OF call_tool_calls
            FOR VALUES FROM ('2026-05-01') TO ('2026-06-01')
    """)
    op.create_index("idx_call_tool_calls_call_id", "call_tool_calls", ["call_id"])


def downgrade() -> None:
    op.drop_table("call_tool_calls_2026_05")
    op.drop_table("call_tool_calls_2026_04")
    op.drop_table("call_tool_calls_2026_03")
    op.drop_table("call_tool_calls_2026_02")
    op.drop_table("call_tool_calls_2026_01")
    op.drop_table("call_tool_calls")
    op.drop_table("call_turns_2026_05")
    op.drop_table("call_turns_2026_04")
    op.drop_table("call_turns_2026_03")
    op.drop_table("call_turns_2026_02")
    op.drop_table("call_turns_2026_01")
    op.drop_table("call_turns")
    op.drop_table("calls_2026_05")
    op.drop_table("calls_2026_04")
    op.drop_table("calls_2026_03")
    op.drop_table("calls_2026_02")
    op.drop_table("calls_2026_01")
    op.drop_table("calls")
    op.drop_table("customers")
