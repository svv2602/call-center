"""Call logger — writes call records to PostgreSQL.

Logs calls, turns, and tool calls asynchronously to avoid blocking
the main call processing loop. Supports graceful degradation when
PostgreSQL is unavailable.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

if TYPE_CHECKING:
    from redis.asyncio import Redis

logger = logging.getLogger(__name__)

_REDIS_LOG_BUFFER_KEY = "call_log_buffer"
_REDIS_LOG_BUFFER_TTL = 3600  # 1 hour buffer


class CallLogger:
    """Asynchronous call logger writing to PostgreSQL.

    Falls back to Redis buffer when PostgreSQL is unavailable.
    """

    def __init__(self, database_url: str, redis: Redis | None = None) -> None:
        self._engine = create_async_engine(database_url, pool_size=5, max_overflow=5)
        self._session_factory = async_sessionmaker(self._engine, expire_on_commit=False)
        self._redis = redis
        self._db_available = True

    async def close(self) -> None:
        """Close the database engine."""
        await self._engine.dispose()

    # --- Call lifecycle ---

    async def log_call_start(
        self,
        call_id: uuid.UUID,
        caller_id: str | None,
        customer_id: str | None,
        started_at: datetime,
        prompt_version: str,
    ) -> None:
        """Log the start of a call."""
        await self._execute(
            """
            INSERT INTO calls (id, caller_id, customer_id, started_at, prompt_version)
            VALUES (:id, :caller_id, :customer_id, :started_at, :prompt_version)
            """,
            {
                "id": str(call_id),
                "caller_id": caller_id,
                "customer_id": customer_id,
                "started_at": started_at,
                "prompt_version": prompt_version,
            },
        )

    async def log_call_end(
        self,
        call_id: uuid.UUID,
        ended_at: datetime,
        duration_seconds: int,
        scenario: str | None = None,
        transferred: bool = False,
        transfer_reason: str | None = None,
    ) -> None:
        """Log the end of a call."""
        await self._execute(
            """
            UPDATE calls
            SET ended_at = :ended_at,
                duration_seconds = :duration_seconds,
                scenario = :scenario,
                transferred_to_operator = :transferred,
                transfer_reason = :transfer_reason
            WHERE id = :id
            """,
            {
                "id": str(call_id),
                "ended_at": ended_at,
                "duration_seconds": duration_seconds,
                "scenario": scenario,
                "transferred": transferred,
                "transfer_reason": transfer_reason,
            },
        )

    async def log_turn(
        self,
        call_id: uuid.UUID,
        turn_number: int,
        speaker: str,
        content: str,
        stt_confidence: float | None = None,
        stt_latency_ms: int | None = None,
        llm_latency_ms: int | None = None,
        tts_latency_ms: int | None = None,
    ) -> None:
        """Log a single dialog turn."""
        await self._execute(
            """
            INSERT INTO call_turns (id, call_id, turn_number, speaker, content,
                                     stt_confidence, stt_latency_ms, llm_latency_ms,
                                     tts_latency_ms, created_at)
            VALUES (:id, :call_id, :turn_number, :speaker, :content,
                    :stt_confidence, :stt_latency_ms, :llm_latency_ms,
                    :tts_latency_ms, :created_at)
            """,
            {
                "id": str(uuid.uuid4()),
                "call_id": str(call_id),
                "turn_number": turn_number,
                "speaker": speaker,
                "content": content,
                "stt_confidence": stt_confidence,
                "stt_latency_ms": stt_latency_ms,
                "llm_latency_ms": llm_latency_ms,
                "tts_latency_ms": tts_latency_ms,
                "created_at": datetime.now(UTC),
            },
        )

    async def log_tool_call(
        self,
        call_id: uuid.UUID,
        turn_number: int,
        tool_name: str,
        tool_args: dict[str, Any],
        tool_result: dict[str, Any],
        duration_ms: int,
        success: bool,
    ) -> None:
        """Log a tool call."""
        await self._execute(
            """
            INSERT INTO call_tool_calls (id, call_id, turn_number, tool_name,
                                          tool_args, tool_result, duration_ms,
                                          success, created_at)
            VALUES (:id, :call_id, :turn_number, :tool_name,
                    :tool_args, :tool_result, :duration_ms,
                    :success, :created_at)
            """,
            {
                "id": str(uuid.uuid4()),
                "call_id": str(call_id),
                "turn_number": turn_number,
                "tool_name": tool_name,
                "tool_args": json.dumps(tool_args, ensure_ascii=False),
                "tool_result": json.dumps(tool_result, ensure_ascii=False),
                "duration_ms": duration_ms,
                "success": success,
                "created_at": datetime.now(UTC),
            },
        )

    # --- Customer tracking ---

    async def upsert_customer(
        self,
        phone: str,
        name: str | None = None,
    ) -> str:
        """Create or update customer by phone. Returns customer_id."""
        result = await self._fetch_one(
            "SELECT id FROM customers WHERE phone = :phone",
            {"phone": phone},
        )

        if result:
            customer_id = result["id"]
            await self._execute(
                """
                UPDATE customers
                SET total_calls = total_calls + 1,
                    last_call_at = :now
                WHERE id = :id
                """,
                {"id": customer_id, "now": datetime.now(UTC)},
            )
            return str(customer_id)

        customer_id = str(uuid.uuid4())
        now = datetime.now(UTC)
        await self._execute(
            """
            INSERT INTO customers (id, phone, name, total_calls, first_call_at, last_call_at)
            VALUES (:id, :phone, :name, 1, :now, :now)
            """,
            {
                "id": customer_id,
                "phone": phone,
                "name": name,
                "now": now,
            },
        )
        return customer_id

    # --- Internals ---

    async def _execute(self, query: str, params: dict[str, Any]) -> None:
        """Execute a write query with PG fallback to Redis buffer."""
        try:
            async with self._session_factory() as session, session.begin():
                await session.execute(text(query), params)
            self._db_available = True
        except Exception:
            self._db_available = False
            logger.warning("PostgreSQL unavailable, buffering log to Redis")
            await self._buffer_to_redis(query, params)

    async def _fetch_one(self, query: str, params: dict[str, Any]) -> dict[str, Any] | None:
        """Execute a read query and return one row."""
        try:
            async with self._session_factory() as session:
                result = await session.execute(text(query), params)
                row = result.mappings().first()
                return dict(row) if row else None
        except Exception:
            logger.warning("PostgreSQL read failed")
            return None

    async def _buffer_to_redis(self, query: str, params: dict[str, Any]) -> None:
        """Buffer a log entry in Redis when PostgreSQL is unavailable."""
        if self._redis is None:
            return

        entry = json.dumps({"query": query, "params": params}, default=str)
        try:
            await self._redis.rpush(_REDIS_LOG_BUFFER_KEY, entry)  # type: ignore[misc]
            await self._redis.expire(_REDIS_LOG_BUFFER_KEY, _REDIS_LOG_BUFFER_TTL)
        except Exception:
            logger.error("Redis buffer also unavailable — log entry lost")

    async def flush_redis_buffer(self) -> int:
        """Flush buffered log entries from Redis to PostgreSQL.

        Returns the number of entries flushed.
        """
        if self._redis is None:
            return 0

        count = 0
        while True:
            entry_raw = await self._redis.lpop(_REDIS_LOG_BUFFER_KEY)  # type: ignore[misc]
            if entry_raw is None:
                break

            entry = json.loads(entry_raw)
            try:
                async with self._session_factory() as session, session.begin():
                    await session.execute(text(entry["query"]), entry["params"])
                count += 1
            except Exception:
                # Put it back
                await self._redis.lpush(_REDIS_LOG_BUFFER_KEY, entry_raw)  # type: ignore[misc]
                break

        if count > 0:
            logger.info("Flushed %d buffered log entries to PostgreSQL", count)
        return count
