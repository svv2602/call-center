"""Sandbox regression runner.

Replays customer messages from a baseline conversation using a different
prompt version, then computes per-turn diffs and aggregate scores.
"""

from __future__ import annotations

import difflib
import json
import logging
import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from sqlalchemy import text

from src.sandbox.agent_runner import create_sandbox_agent, process_sandbox_turn

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncEngine

    from src.knowledge.embeddings import EmbeddingGenerator

logger = logging.getLogger(__name__)


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


@dataclass
class TurnDiff:
    """Per-turn comparison between source and new agent response."""

    turn_number: int
    customer_message: str
    source_response: str
    new_response: str
    source_rating: int | None
    diff_lines: list[str]
    similarity_score: float | None = None


@dataclass
class RegressionResult:
    """Result of a full regression run."""

    turns_compared: int
    avg_source_rating: float | None
    avg_new_rating: float | None
    score_diff: float | None
    turn_diffs: list[TurnDiff] = field(default_factory=list)
    new_conversation_id: str | None = None
    error: str | None = None
    avg_similarity: float | None = None


async def run_regression(
    engine: AsyncEngine,
    source_conversation_id: UUID,
    new_prompt_version_id: UUID,
    branch_turn_ids: list[UUID] | None = None,
    created_by: str | None = None,
    embedding_generator: EmbeddingGenerator | None = None,
) -> RegressionResult:
    """Run a regression test: replay source conversation with a new prompt.

    Args:
        engine: Database engine.
        source_conversation_id: ID of the baseline conversation to replay.
        new_prompt_version_id: ID of the prompt version to test.
        branch_turn_ids: Optional ordered list of turn IDs to replay.
                        If None, replays the main branch (no parent_turn_id chain).
        created_by: User ID of the person who initiated the run.
        embedding_generator: Optional embedding generator for semantic similarity.

    Returns:
        RegressionResult with per-turn diffs and aggregate scores.
    """
    # Load source conversation and turns
    async with engine.begin() as conn:
        conv_result = await conn.execute(
            text("""
                SELECT id, title, tool_mode, prompt_version_name
                FROM sandbox_conversations WHERE id = :id
            """),
            {"id": str(source_conversation_id)},
        )
        source_conv = conv_result.first()
        if not source_conv:
            return RegressionResult(
                turns_compared=0,
                avg_source_rating=None,
                avg_new_rating=None,
                score_diff=None,
                error="Source conversation not found",
            )

        # Load source turns (customer messages + agent responses)
        if branch_turn_ids:
            # Load specific branch path
            turns_result = await conn.execute(
                text("""
                    SELECT id, turn_number, speaker, content, rating
                    FROM sandbox_turns
                    WHERE id = ANY(:ids) AND conversation_id = :conv_id
                    ORDER BY turn_number, created_at
                """),
                {
                    "ids": [str(tid) for tid in branch_turn_ids],
                    "conv_id": str(source_conversation_id),
                },
            )
        else:
            # Load main branch (all turns, no branching filter)
            turns_result = await conn.execute(
                text("""
                    SELECT id, turn_number, speaker, content, rating
                    FROM sandbox_turns
                    WHERE conversation_id = :conv_id
                    ORDER BY turn_number, created_at
                """),
                {"conv_id": str(source_conversation_id)},
            )
        source_turns = [dict(row._mapping) for row in turns_result]

    # Extract customer messages and source agent responses
    customer_messages: list[str] = []
    source_responses: list[dict[str, Any]] = []
    for turn in source_turns:
        if turn["speaker"] == "customer":
            customer_messages.append(turn["content"])
        elif turn["speaker"] == "agent":
            source_responses.append(turn)

    if not customer_messages:
        return RegressionResult(
            turns_compared=0,
            avg_source_rating=None,
            avg_new_rating=None,
            score_diff=None,
            error="No customer messages in source conversation",
        )

    # Create new conversation for the regression
    async with engine.begin() as conn:
        new_conv_result = await conn.execute(
            text("""
                INSERT INTO sandbox_conversations
                    (title, prompt_version_id, tool_mode, tags, status, metadata)
                VALUES
                    (:title, :prompt_version_id, :tool_mode, :tags, 'active', :metadata)
                RETURNING id
            """),
            {
                "title": f"[Regression] {source_conv.title}",
                "prompt_version_id": str(new_prompt_version_id),
                "tool_mode": source_conv.tool_mode,
                "tags": ["regression", "auto"],
                "metadata": json.dumps(
                    {
                        "source_conversation_id": str(source_conversation_id),
                        "regression": True,
                    }
                ),
            },
        )
        new_conv_row = new_conv_result.first()
        assert new_conv_row is not None
        new_conv_id = str(new_conv_row.id)

    # Create agent with new prompt
    agent = await create_sandbox_agent(
        engine,
        prompt_version_id=new_prompt_version_id,
        tool_mode=source_conv.tool_mode,
    )

    # Replay each customer message
    history: list[dict[str, Any]] = []
    turn_diffs: list[TurnDiff] = []
    is_mock = source_conv.tool_mode == "mock"

    for i, msg in enumerate(customer_messages):
        try:
            result = await process_sandbox_turn(agent, msg, history, is_mock=is_mock)
            history = result.updated_history

            # Save turns to new conversation
            turn_num_customer = (i * 2) + 1
            turn_num_agent = (i * 2) + 2

            async with engine.begin() as conn:
                await conn.execute(
                    text("""
                        INSERT INTO sandbox_turns
                            (conversation_id, turn_number, speaker, content)
                        VALUES (:conv_id, :turn_number, 'customer', :content)
                    """),
                    {"conv_id": new_conv_id, "turn_number": turn_num_customer, "content": msg},
                )
                await conn.execute(
                    text("""
                        INSERT INTO sandbox_turns
                            (conversation_id, turn_number, speaker, content,
                             llm_latency_ms, input_tokens, output_tokens, model, conversation_history)
                        VALUES (:conv_id, :turn_number, 'agent', :content,
                                :latency, :input_tokens, :output_tokens, :model, :history)
                    """),
                    {
                        "conv_id": new_conv_id,
                        "turn_number": turn_num_agent,
                        "content": result.response_text,
                        "latency": result.latency_ms,
                        "input_tokens": result.input_tokens,
                        "output_tokens": result.output_tokens,
                        "model": result.model,
                        "history": json.dumps(result.updated_history),
                    },
                )

            # Compute diff
            source_resp = source_responses[i] if i < len(source_responses) else None
            source_text = source_resp["content"] if source_resp else ""
            source_rating = source_resp["rating"] if source_resp else None

            diff = list(
                difflib.unified_diff(
                    source_text.splitlines(),
                    result.response_text.splitlines(),
                    fromfile="source",
                    tofile="new",
                    lineterm="",
                )
            )

            turn_diffs.append(
                TurnDiff(
                    turn_number=i + 1,
                    customer_message=msg,
                    source_response=source_text,
                    new_response=result.response_text,
                    source_rating=source_rating,
                    diff_lines=diff,
                )
            )

        except Exception:
            logger.exception("Regression turn %d failed", i + 1)
            turn_diffs.append(
                TurnDiff(
                    turn_number=i + 1,
                    customer_message=msg,
                    source_response=source_responses[i]["content"]
                    if i < len(source_responses)
                    else "",
                    new_response="[ERROR]",
                    source_rating=None,
                    diff_lines=[],
                )
            )

    # Compute semantic similarity if embedding generator is available
    avg_similarity: float | None = None
    if embedding_generator is not None:
        try:
            pairs = [
                (td.source_response, td.new_response)
                for td in turn_diffs
                if td.source_response and td.new_response and td.new_response != "[ERROR]"
            ]
            if pairs:
                all_texts = []
                for src, new in pairs:
                    all_texts.extend([src, new])
                embeddings = await embedding_generator.generate(all_texts)
                pair_idx = 0
                for td in turn_diffs:
                    if td.source_response and td.new_response and td.new_response != "[ERROR]":
                        src_emb = embeddings[pair_idx * 2]
                        new_emb = embeddings[pair_idx * 2 + 1]
                        td.similarity_score = round(_cosine_similarity(src_emb, new_emb), 4)
                        pair_idx += 1
                scores = [
                    td.similarity_score for td in turn_diffs if td.similarity_score is not None
                ]
                if scores:
                    avg_similarity = round(sum(scores) / len(scores), 4)
        except Exception:
            logger.warning("Failed to compute semantic similarity", exc_info=True)

    # Compute aggregate scores
    source_ratings = [sr["rating"] for sr in source_responses if sr.get("rating") is not None]
    avg_source = sum(source_ratings) / len(source_ratings) if source_ratings else None

    return RegressionResult(
        turns_compared=len(turn_diffs),
        avg_source_rating=avg_source,
        avg_new_rating=None,  # New responses not rated yet
        score_diff=None,
        turn_diffs=turn_diffs,
        new_conversation_id=new_conv_id,
        avg_similarity=avg_similarity,
    )
