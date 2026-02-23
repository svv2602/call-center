"""Unit tests for sandbox import-call endpoint."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.api.sandbox import ImportCallRequest

# Helpers

CALL_ID = uuid4()
TENANT_ID = uuid4()


def _make_call_row(
    *,
    caller_id: str = "+380991234567",
    started_at: datetime | None = None,
    scenario: str | None = "tire_search",
    tenant_id: Any = None,
    quality_score: float | None = 4.2,
    duration_seconds: int | None = 45,
    prompt_version: str | None = "v3.5-natural",
) -> MagicMock:
    row = MagicMock()
    row.id = str(CALL_ID)
    row.caller_id = caller_id
    row.started_at = started_at or datetime(2026, 2, 20, 14, 30, 0)
    row.scenario = scenario
    row.tenant_id = tenant_id
    row.quality_score = quality_score
    row.duration_seconds = duration_seconds
    row.prompt_version = prompt_version
    return row


def _make_turn_rows(turns: list[dict[str, Any]]) -> list[MagicMock]:
    rows = []
    for t in turns:
        row = MagicMock()
        row._mapping = t
        rows.append(row)
    return rows


def _make_result(rows: list[MagicMock] | None = None, first: Any = "USE_DEFAULT") -> MagicMock:
    result = MagicMock()
    if rows is not None:
        result.__iter__ = MagicMock(return_value=iter(rows))
    if first != "USE_DEFAULT":
        result.first.return_value = first
    elif rows:
        result.first.return_value = rows[0]
    else:
        result.first.return_value = None
    return result


def _sandbox_turn_row(turn_number: int, speaker: str) -> MagicMock:
    row = MagicMock()
    row.id = uuid4()
    row.turn_number = turn_number
    row.speaker = speaker
    row.content = f"content for turn {turn_number}"
    row.llm_latency_ms = None
    row.created_at = datetime(2026, 2, 20, 14, 31, 0)
    row._mapping = {
        "id": row.id,
        "turn_number": row.turn_number,
        "speaker": row.speaker,
        "content": row.content,
        "llm_latency_ms": row.llm_latency_ms,
        "created_at": row.created_at,
    }
    return row


def _conv_row() -> MagicMock:
    row = MagicMock()
    row.id = uuid4()
    row._mapping = {
        "id": row.id,
        "title": "Импорт: +380991234567 2026-02-20 14:30",
        "tool_mode": "mock",
        "tags": ["imported", f"call:{str(CALL_ID)[:8]}"],
        "scenario_type": "tire_search",
        "status": "active",
        "is_baseline": False,
        "metadata": {},
        "tenant_id": None,
        "created_at": datetime(2026, 2, 20, 14, 31, 0),
        "updated_at": datetime(2026, 2, 20, 14, 31, 0),
    }
    return row


# ── Model validation ─────────────────────────────────────────


class TestImportCallRequestModel:
    def test_valid_request(self) -> None:
        req = ImportCallRequest(call_id=CALL_ID)
        assert req.call_id == CALL_ID
        assert req.title is None

    def test_valid_with_title(self) -> None:
        req = ImportCallRequest(call_id=CALL_ID, title="My custom title")
        assert req.title == "My custom title"

    def test_title_too_long(self) -> None:
        with pytest.raises(ValidationError):
            ImportCallRequest(call_id=CALL_ID, title="x" * 301)

    def test_missing_call_id_fails(self) -> None:
        with pytest.raises(ValidationError):
            ImportCallRequest()


# ── Endpoint tests ───────────────────────────────────────────


class TestImportCallEndpoint:
    @pytest.fixture
    def _patch_engine(self):
        """Context manager to patch sandbox module engine."""
        import src.api.sandbox as sandbox_module

        original = sandbox_module._engine

        def _set(engine):
            sandbox_module._engine = engine
            return original

        yield _set
        sandbox_module._engine = original

    @pytest.mark.asyncio
    async def test_call_not_found_404(self, _patch_engine) -> None:
        from fastapi import HTTPException

        from src.api.sandbox import import_call

        mock_conn = AsyncMock()
        # Call query returns nothing
        call_not_found = MagicMock()
        call_not_found.first.return_value = None
        mock_conn.execute.return_value = call_not_found

        @asynccontextmanager
        async def mock_begin():
            yield mock_conn

        mock_engine = MagicMock()
        mock_engine.begin = mock_begin
        _patch_engine(mock_engine)

        req = ImportCallRequest(call_id=uuid4())
        with pytest.raises(HTTPException) as exc_info:
            await import_call(req, {"user_id": "test"})
        assert exc_info.value.status_code == 404
        assert "not found" in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    async def test_no_turns_400(self, _patch_engine) -> None:
        from fastapi import HTTPException

        from src.api.sandbox import import_call

        mock_conn = AsyncMock()
        # Call found, but turns empty
        call_found = _make_result(first=_make_call_row())
        turns_empty = MagicMock()
        turns_empty.__iter__ = MagicMock(return_value=iter([]))
        turns_empty.first.return_value = None
        mock_conn.execute.side_effect = [call_found, turns_empty]

        @asynccontextmanager
        async def mock_begin():
            yield mock_conn

        mock_engine = MagicMock()
        mock_engine.begin = mock_begin
        _patch_engine(mock_engine)

        req = ImportCallRequest(call_id=CALL_ID)
        with pytest.raises(HTTPException) as exc_info:
            await import_call(req, {"user_id": "test"})
        assert exc_info.value.status_code == 400
        assert "no turns" in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    async def test_import_success(self, _patch_engine) -> None:
        from src.api.sandbox import import_call

        mock_conn = AsyncMock()

        # Prepare sequential responses
        call_row = _make_call_row()
        call_result = _make_result(first=call_row)

        turns_data = [
            {"turn_number": 1, "speaker": "customer", "content": "Привіт", "llm_latency_ms": None},
            {"turn_number": 2, "speaker": "bot", "content": "Вітаю!", "llm_latency_ms": 350},
            {
                "turn_number": 3,
                "speaker": "customer",
                "content": "Шини 205/55 R16",
                "llm_latency_ms": None,
            },
            {
                "turn_number": 4,
                "speaker": "bot",
                "content": "Шукаю для вас...",
                "llm_latency_ms": 420,
            },
        ]
        turns_rows = _make_turn_rows(turns_data)
        turns_result = MagicMock()
        turns_result.__iter__ = MagicMock(return_value=iter(turns_rows))

        tc_data = [
            {
                "turn_number": 2,
                "tool_name": "search_tires",
                "tool_args": {"width": 205},
                "tool_result": {"items": []},
                "duration_ms": 150,
            },
            {
                "turn_number": 4,
                "tool_name": "check_availability",
                "tool_args": {"sku": "ABC"},
                "tool_result": {"available": True},
                "duration_ms": 80,
            },
        ]
        tc_rows = _make_turn_rows(tc_data)
        tc_result = MagicMock()
        tc_result.__iter__ = MagicMock(return_value=iter(tc_rows))

        conv = _conv_row()
        conv_insert = _make_result(first=conv)

        # Build sandbox turn insert results (one per turn)
        sandbox_turns = []
        for td in turns_data:
            speaker = "agent" if td["speaker"] == "bot" else "customer"
            st = _sandbox_turn_row(td["turn_number"], speaker)
            sandbox_turns.append(_make_result(first=st))

        # Order: call_select, turns_select, tc_select, conv_insert, 4x turn_insert, 2x tc_insert
        mock_conn.execute.side_effect = [
            call_result,
            turns_result,
            tc_result,
            conv_insert,
            *sandbox_turns,
            MagicMock(),  # tool call insert 1
            MagicMock(),  # tool call insert 2
        ]

        @asynccontextmanager
        async def mock_begin():
            yield mock_conn

        mock_engine = MagicMock()
        mock_engine.begin = mock_begin
        _patch_engine(mock_engine)

        req = ImportCallRequest(call_id=CALL_ID)
        result = await import_call(req, {"user_id": "admin"})

        assert "item" in result
        assert "turns" in result
        assert len(result["turns"]) == 4
        assert "message" in result
        assert "4 turns" in result["message"]

    @pytest.mark.asyncio
    async def test_speaker_mapping(self, _patch_engine) -> None:
        """bot→agent, customer→customer."""
        from src.api.sandbox import import_call

        mock_conn = AsyncMock()

        call_result = _make_result(first=_make_call_row())
        turns_data = [
            {"turn_number": 1, "speaker": "customer", "content": "Hi", "llm_latency_ms": None},
            {"turn_number": 2, "speaker": "bot", "content": "Hello", "llm_latency_ms": 200},
        ]
        turns_result = MagicMock()
        turns_result.__iter__ = MagicMock(return_value=iter(_make_turn_rows(turns_data)))

        tc_result = MagicMock()
        tc_result.__iter__ = MagicMock(return_value=iter([]))

        conv = _conv_row()

        st_customer = _sandbox_turn_row(1, "customer")
        st_agent = _sandbox_turn_row(2, "agent")

        mock_conn.execute.side_effect = [
            call_result,
            turns_result,
            tc_result,
            _make_result(first=conv),
            _make_result(first=st_customer),
            _make_result(first=st_agent),
        ]

        @asynccontextmanager
        async def mock_begin():
            yield mock_conn

        mock_engine = MagicMock()
        mock_engine.begin = mock_begin
        _patch_engine(mock_engine)

        req = ImportCallRequest(call_id=CALL_ID)
        result = await import_call(req, {"user_id": "test"})

        assert result["turns"][0]["speaker"] == "customer"
        assert result["turns"][1]["speaker"] == "agent"

    @pytest.mark.asyncio
    async def test_conversation_history_incremental(self, _patch_engine) -> None:
        """Agent turns should receive conversation_history snapshots."""
        from src.api.sandbox import import_call

        mock_conn = AsyncMock()
        executed_params = []

        call_result = _make_result(first=_make_call_row())
        turns_data = [
            {"turn_number": 1, "speaker": "customer", "content": "Привіт", "llm_latency_ms": None},
            {"turn_number": 2, "speaker": "bot", "content": "Вітаю!", "llm_latency_ms": 200},
            {"turn_number": 3, "speaker": "customer", "content": "Шини", "llm_latency_ms": None},
            {"turn_number": 4, "speaker": "bot", "content": "Шукаю", "llm_latency_ms": 300},
        ]
        turns_result = MagicMock()
        turns_result.__iter__ = MagicMock(return_value=iter(_make_turn_rows(turns_data)))

        tc_result = MagicMock()
        tc_result.__iter__ = MagicMock(return_value=iter([]))

        conv = _conv_row()

        sandbox_turn_results = []
        for td in turns_data:
            speaker = "agent" if td["speaker"] == "bot" else "customer"
            sandbox_turn_results.append(
                _make_result(first=_sandbox_turn_row(td["turn_number"], speaker))
            )

        all_results = [
            call_result,
            turns_result,
            tc_result,
            _make_result(first=conv),
            *sandbox_turn_results,
        ]
        result_iter = iter(all_results)

        async def capture_execute(query, params=None):
            if params:
                executed_params.append(dict(params))
            return next(result_iter)

        mock_conn.execute = capture_execute

        @asynccontextmanager
        async def mock_begin():
            yield mock_conn

        mock_engine = MagicMock()
        mock_engine.begin = mock_begin
        _patch_engine(mock_engine)

        req = ImportCallRequest(call_id=CALL_ID)
        await import_call(req, {"user_id": "test"})

        # Find turn inserts with history (agent turns)
        history_params = [
            p for p in executed_params if "history" in p and p.get("history") is not None
        ]
        assert len(history_params) == 2  # 2 agent turns

        # First agent turn (turn 2): history should have customer message only
        h1 = json.loads(history_params[0]["history"])
        assert len(h1) == 1
        assert h1[0]["role"] == "user"
        assert h1[0]["content"] == "Привіт"

        # Second agent turn (turn 4): should have 3 messages
        h2 = json.loads(history_params[1]["history"])
        assert len(h2) == 3
        assert h2[0] == {"role": "user", "content": "Привіт"}
        assert h2[1] == {"role": "assistant", "content": "Вітаю!"}
        assert h2[2] == {"role": "user", "content": "Шини"}

    @pytest.mark.asyncio
    async def test_tool_calls_linked_by_turn_number(self, _patch_engine) -> None:
        """Tool calls should be linked to the correct sandbox turn by turn_number."""
        from src.api.sandbox import import_call

        mock_conn = AsyncMock()
        executed_params = []

        call_result = _make_result(first=_make_call_row())
        turns_data = [
            {"turn_number": 1, "speaker": "customer", "content": "Hi", "llm_latency_ms": None},
            {"turn_number": 2, "speaker": "bot", "content": "Hello", "llm_latency_ms": 200},
        ]
        turns_result = MagicMock()
        turns_result.__iter__ = MagicMock(return_value=iter(_make_turn_rows(turns_data)))

        tc_data = [
            {
                "turn_number": 2,
                "tool_name": "search_tires",
                "tool_args": {},
                "tool_result": {},
                "duration_ms": 100,
            },
        ]
        tc_result = MagicMock()
        tc_result.__iter__ = MagicMock(return_value=iter(_make_turn_rows(tc_data)))

        conv = _conv_row()
        agent_turn = _sandbox_turn_row(2, "agent")
        agent_turn_id = agent_turn.id
        customer_turn = _sandbox_turn_row(1, "customer")

        all_results = [
            call_result,
            turns_result,
            tc_result,
            _make_result(first=conv),
            _make_result(first=customer_turn),
            _make_result(first=agent_turn),
            MagicMock(),  # tool call insert
        ]
        result_iter = iter(all_results)

        async def capture_execute(query, params=None):
            if params:
                executed_params.append(dict(params))
            return next(result_iter)

        mock_conn.execute = capture_execute

        @asynccontextmanager
        async def mock_begin():
            yield mock_conn

        mock_engine = MagicMock()
        mock_engine.begin = mock_begin
        _patch_engine(mock_engine)

        req = ImportCallRequest(call_id=CALL_ID)
        await import_call(req, {"user_id": "test"})

        # Find tool call insert params
        tc_params = [p for p in executed_params if "tool_name" in p]
        assert len(tc_params) == 1
        assert tc_params[0]["turn_id"] == str(agent_turn_id)
        assert tc_params[0]["tool_name"] == "search_tires"

    @pytest.mark.asyncio
    async def test_is_mock_false(self, _patch_engine) -> None:
        """Imported tool calls should have is_mock=false."""
        from src.api.sandbox import import_call

        mock_conn = AsyncMock()
        executed_queries = []

        call_result = _make_result(first=_make_call_row())
        turns_data = [
            {"turn_number": 1, "speaker": "customer", "content": "Hi", "llm_latency_ms": None},
            {"turn_number": 2, "speaker": "bot", "content": "Hello", "llm_latency_ms": 200},
        ]
        turns_result = MagicMock()
        turns_result.__iter__ = MagicMock(return_value=iter(_make_turn_rows(turns_data)))

        tc_data = [
            {
                "turn_number": 2,
                "tool_name": "search_tires",
                "tool_args": {},
                "tool_result": {},
                "duration_ms": 100,
            },
        ]
        tc_result = MagicMock()
        tc_result.__iter__ = MagicMock(return_value=iter(_make_turn_rows(tc_data)))

        conv = _conv_row()
        all_results = [
            call_result,
            turns_result,
            tc_result,
            _make_result(first=conv),
            _make_result(first=_sandbox_turn_row(1, "customer")),
            _make_result(first=_sandbox_turn_row(2, "agent")),
            MagicMock(),
        ]
        result_iter = iter(all_results)

        async def capture_execute(query, params=None):
            executed_queries.append((str(query), dict(params) if params else {}))
            return next(result_iter)

        mock_conn.execute = capture_execute

        @asynccontextmanager
        async def mock_begin():
            yield mock_conn

        mock_engine = MagicMock()
        mock_engine.begin = mock_begin
        _patch_engine(mock_engine)

        req = ImportCallRequest(call_id=CALL_ID)
        await import_call(req, {"user_id": "test"})

        # The tool call INSERT should contain 'false' for is_mock
        tc_queries = [
            (q, p) for q, p in executed_queries if "sandbox_tool_calls" in q and "INSERT" in q
        ]
        assert len(tc_queries) == 1
        assert "false" in tc_queries[0][0].lower()

    @pytest.mark.asyncio
    async def test_tags_and_metadata(self, _patch_engine) -> None:
        """Conversation should have correct tags and metadata."""
        from src.api.sandbox import import_call

        mock_conn = AsyncMock()
        executed_params = []

        call_result = _make_result(first=_make_call_row(quality_score=3.8, duration_seconds=120))
        turns_data = [
            {"turn_number": 1, "speaker": "customer", "content": "Hi", "llm_latency_ms": None},
            {"turn_number": 2, "speaker": "bot", "content": "Hello", "llm_latency_ms": 200},
        ]
        turns_result = MagicMock()
        turns_result.__iter__ = MagicMock(return_value=iter(_make_turn_rows(turns_data)))

        tc_result = MagicMock()
        tc_result.__iter__ = MagicMock(return_value=iter([]))

        conv = _conv_row()
        all_results = [
            call_result,
            turns_result,
            tc_result,
            _make_result(first=conv),
            _make_result(first=_sandbox_turn_row(1, "customer")),
            _make_result(first=_sandbox_turn_row(2, "agent")),
        ]
        result_iter = iter(all_results)

        async def capture_execute(query, params=None):
            if params:
                executed_params.append(dict(params))
            return next(result_iter)

        mock_conn.execute = capture_execute

        @asynccontextmanager
        async def mock_begin():
            yield mock_conn

        mock_engine = MagicMock()
        mock_engine.begin = mock_begin
        _patch_engine(mock_engine)

        req = ImportCallRequest(call_id=CALL_ID)
        await import_call(req, {"user_id": "test"})

        # Find conversation insert params (has 'tags' key)
        conv_params = [p for p in executed_params if "tags" in p and "scenario_type" in p]
        assert len(conv_params) == 1
        tags = conv_params[0]["tags"]
        assert "imported" in tags
        assert any(t.startswith("call:") for t in tags)

        metadata = json.loads(conv_params[0]["metadata"])
        assert metadata["source_call_id"] == str(CALL_ID)
        assert metadata["original_quality_score"] == 3.8
        assert metadata["original_duration_seconds"] == 120
        assert "imported_at" in metadata

    @pytest.mark.asyncio
    async def test_title_auto_generated(self, _patch_engine) -> None:
        """Default title should be 'Импорт: {caller_id} {date}'."""
        from src.api.sandbox import import_call

        mock_conn = AsyncMock()
        executed_params = []

        call_row = _make_call_row(
            caller_id="+380501112233",
            started_at=datetime(2026, 1, 15, 10, 0, 0),
        )
        call_result = _make_result(first=call_row)
        turns_data = [
            {"turn_number": 1, "speaker": "customer", "content": "Hi", "llm_latency_ms": None},
        ]
        turns_result = MagicMock()
        turns_result.__iter__ = MagicMock(return_value=iter(_make_turn_rows(turns_data)))

        tc_result = MagicMock()
        tc_result.__iter__ = MagicMock(return_value=iter([]))

        conv = _conv_row()
        all_results = [
            call_result,
            turns_result,
            tc_result,
            _make_result(first=conv),
            _make_result(first=_sandbox_turn_row(1, "customer")),
        ]
        result_iter = iter(all_results)

        async def capture_execute(query, params=None):
            if params:
                executed_params.append(dict(params))
            return next(result_iter)

        mock_conn.execute = capture_execute

        @asynccontextmanager
        async def mock_begin():
            yield mock_conn

        mock_engine = MagicMock()
        mock_engine.begin = mock_begin
        _patch_engine(mock_engine)

        req = ImportCallRequest(call_id=CALL_ID)
        await import_call(req, {"user_id": "test"})

        conv_params = [p for p in executed_params if "title" in p and "tags" in p]
        assert len(conv_params) == 1
        assert conv_params[0]["title"] == "Импорт: +380501112233 2026-01-15 10:00"

    @pytest.mark.asyncio
    async def test_title_override(self, _patch_engine) -> None:
        """Custom title from request should be used instead of auto-generated."""
        from src.api.sandbox import import_call

        mock_conn = AsyncMock()
        executed_params = []

        call_result = _make_result(first=_make_call_row())
        turns_data = [
            {"turn_number": 1, "speaker": "customer", "content": "Hi", "llm_latency_ms": None},
        ]
        turns_result = MagicMock()
        turns_result.__iter__ = MagicMock(return_value=iter(_make_turn_rows(turns_data)))

        tc_result = MagicMock()
        tc_result.__iter__ = MagicMock(return_value=iter([]))

        conv = _conv_row()
        all_results = [
            call_result,
            turns_result,
            tc_result,
            _make_result(first=conv),
            _make_result(first=_sandbox_turn_row(1, "customer")),
        ]
        result_iter = iter(all_results)

        async def capture_execute(query, params=None):
            if params:
                executed_params.append(dict(params))
            return next(result_iter)

        mock_conn.execute = capture_execute

        @asynccontextmanager
        async def mock_begin():
            yield mock_conn

        mock_engine = MagicMock()
        mock_engine.begin = mock_begin
        _patch_engine(mock_engine)

        req = ImportCallRequest(call_id=CALL_ID, title="Мой звонок для анализа")
        await import_call(req, {"user_id": "test"})

        conv_params = [p for p in executed_params if "title" in p and "tags" in p]
        assert conv_params[0]["title"] == "Мой звонок для анализа"

    @pytest.mark.asyncio
    async def test_tool_calls_skipped_if_no_matching_turn(self, _patch_engine) -> None:
        """Tool calls with turn_number not matching any turn should be skipped."""
        from src.api.sandbox import import_call

        mock_conn = AsyncMock()
        executed_params = []

        call_result = _make_result(first=_make_call_row())
        turns_data = [
            {"turn_number": 1, "speaker": "customer", "content": "Hi", "llm_latency_ms": None},
        ]
        turns_result = MagicMock()
        turns_result.__iter__ = MagicMock(return_value=iter(_make_turn_rows(turns_data)))

        # Tool call references turn 99 which doesn't exist
        tc_data = [
            {
                "turn_number": 99,
                "tool_name": "search_tires",
                "tool_args": {},
                "tool_result": {},
                "duration_ms": 100,
            },
        ]
        tc_result = MagicMock()
        tc_result.__iter__ = MagicMock(return_value=iter(_make_turn_rows(tc_data)))

        conv = _conv_row()
        all_results = [
            call_result,
            turns_result,
            tc_result,
            _make_result(first=conv),
            _make_result(first=_sandbox_turn_row(1, "customer")),
            # No tool call insert should happen
        ]
        result_iter = iter(all_results)

        async def capture_execute(query, params=None):
            if params:
                executed_params.append(dict(params))
            return next(result_iter)

        mock_conn.execute = capture_execute

        @asynccontextmanager
        async def mock_begin():
            yield mock_conn

        mock_engine = MagicMock()
        mock_engine.begin = mock_begin
        _patch_engine(mock_engine)

        req = ImportCallRequest(call_id=CALL_ID)
        result = await import_call(req, {"user_id": "test"})

        # Should succeed with no tool calls inserted
        assert len(result["turns"]) == 1
        tc_insert_params = [p for p in executed_params if "tool_name" in p]
        assert len(tc_insert_params) == 0
