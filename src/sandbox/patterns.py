"""Pattern bank: vector search over conversation patterns using pgvector.

Provides PatternSearch for finding similar patterns at runtime and
export_group_to_pattern for promoting turn groups to the pattern bank.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from uuid import UUID  # noqa: TC003

if TYPE_CHECKING:
    from src.knowledge.embeddings import EmbeddingGenerator

logger = logging.getLogger(__name__)


class PatternSearch:
    """Vector search over conversation patterns using pgvector."""

    def __init__(self, pool: Any, embedding_generator: EmbeddingGenerator) -> None:
        self._pool = pool
        self._generator = embedding_generator

    async def search(
        self,
        query: str,
        top_k: int = 3,
        min_similarity: float = 0.75,
    ) -> list[dict[str, Any]]:
        """Find similar patterns. Returns both positive and negative."""
        embedding = await self._generator.generate_single(query)
        embedding_str = "[" + ",".join(str(v) for v in embedding) + "]"

        sql = """
            SELECT id, intent_label, pattern_type, customer_messages,
                   agent_messages, guidance_note, rating, tags,
                   1 - (embedding <=> $1::vector) AS similarity
            FROM conversation_patterns
            WHERE is_active = true
              AND embedding IS NOT NULL
              AND 1 - (embedding <=> $1::vector) >= $3
            ORDER BY embedding <=> $1::vector
            LIMIT $2
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, embedding_str, top_k, min_similarity)
        return [dict(row) for row in rows]

    async def format_for_prompt(self, patterns: list[dict[str, Any]]) -> str:
        """Format found patterns as system prompt section."""
        if not patterns:
            return ""
        parts = ["\n## Інструкції з досвіду (автоматично підібрані)"]
        for p in patterns:
            if p["pattern_type"] == "positive":
                parts.append(f"\u2705 {p['intent_label']}: {p['guidance_note']}")
            else:
                parts.append(f"\u274c НЕ РОБИТИ ({p['intent_label']}): {p['guidance_note']}")
        return "\n".join(parts)

    async def increment_usage(self, pattern_ids: list[UUID]) -> None:
        """Increment times_used counter for matched patterns."""
        if not pattern_ids:
            return
        id_strs = [str(pid) for pid in pattern_ids]
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE conversation_patterns
                SET times_used = times_used + 1, updated_at = now()
                WHERE id = ANY($1::uuid[])
                """,
                id_strs,
            )


async def export_group_to_pattern(
    engine: Any,
    generator: EmbeddingGenerator,
    group_id: UUID,
    guidance_note: str,
) -> dict[str, Any]:
    """Export a turn group to a conversation pattern with embedding.

    1. Load group + turns
    2. Concatenate customer messages, agent messages
    3. Generate embedding from customer_messages + intent_label
    4. INSERT into conversation_patterns
    5. Mark group as is_exported = true
    """
    from sqlalchemy import text as sa_text

    async with engine.begin() as conn:
        # Load group
        grp_result = await conn.execute(
            sa_text("""
                SELECT id, conversation_id, turn_ids, intent_label, pattern_type,
                       rating, tags, correction
                FROM sandbox_turn_groups
                WHERE id = :id
            """),
            {"id": str(group_id)},
        )
        group = grp_result.first()
        if not group:
            msg = f"Turn group {group_id} not found"
            raise ValueError(msg)

        if not group.turn_ids:
            msg = "Turn group has no turns"
            raise ValueError(msg)

        # Load conversation scenario_type
        conv_result = await conn.execute(
            sa_text("""
                SELECT scenario_type FROM sandbox_conversations
                WHERE id = :id
            """),
            {"id": str(group.conversation_id)},
        )
        conv_row = conv_result.first()
        scenario_type = conv_row.scenario_type if conv_row else None

        # Load turns
        turn_ids_str = [str(tid) for tid in group.turn_ids]
        turns_result = await conn.execute(
            sa_text("""
                SELECT id, speaker, content FROM sandbox_turns
                WHERE id = ANY(:turn_ids)
                ORDER BY turn_number, created_at
            """),
            {"turn_ids": turn_ids_str},
        )
        turns = [dict(row._mapping) for row in turns_result]

    # Concatenate messages by speaker
    customer_msgs = [t["content"] for t in turns if t["speaker"] == "customer"]
    agent_msgs = [t["content"] for t in turns if t["speaker"] == "agent"]
    customer_text = "\n".join(customer_msgs)
    agent_text = "\n".join(agent_msgs) if agent_msgs else None

    # Generate embedding from customer messages + intent label
    embed_text = f"{group.intent_label}: {customer_text}"
    embedding = await generator.generate_single(embed_text)
    embedding_str = "[" + ",".join(str(v) for v in embedding) + "]"

    # Insert pattern and mark group exported
    async with engine.begin() as conn:
        pattern_result = await conn.execute(
            sa_text("""
                INSERT INTO conversation_patterns
                    (source_group_id, intent_label, pattern_type, customer_messages,
                     agent_messages, guidance_note, scenario_type, tags, rating, embedding)
                VALUES
                    (:group_id, :intent_label, :pattern_type, :customer_messages,
                     :agent_messages, :guidance_note, :scenario_type, :tags, :rating,
                     CAST(:embedding AS vector))
                RETURNING id, intent_label, pattern_type, is_active, created_at
            """),
            {
                "group_id": str(group_id),
                "intent_label": group.intent_label,
                "pattern_type": group.pattern_type,
                "customer_messages": customer_text,
                "agent_messages": agent_text,
                "guidance_note": guidance_note,
                "scenario_type": scenario_type,
                "tags": group.tags,
                "rating": group.rating,
                "embedding": embedding_str,
            },
        )
        pattern_row = pattern_result.first()

        # Mark group as exported
        await conn.execute(
            sa_text("UPDATE sandbox_turn_groups SET is_exported = true WHERE id = :id"),
            {"id": str(group_id)},
        )

    return dict(pattern_row._mapping)
