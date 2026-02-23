"""Tests for pipeline template improvements.

Covers:
1. Time-of-day greeting
2. Stress marks in template constants
3. Contextual wait-phrase selection
4. Contextual farewell (rule-based + LLM fallback)
"""

from __future__ import annotations

import asyncio
import datetime
import uuid
from unittest.mock import AsyncMock, patch

import pytest

from src.agent.prompts import (
    ERROR_TEXT,
    FAREWELL_ORDER_TEXT,
    FAREWELL_TEXT,
    GREETING_TEXT,
    ORDER_CANCELLED_TEXT,
    SILENCE_PROMPT_TEXT,
    TRANSFER_TEXT,
    WAIT_AVAILABILITY_POOL,
    WAIT_AVAILABILITY_TEXT,
    WAIT_DEFAULT_POOL,
    WAIT_FITTING_POOL,
    WAIT_FITTING_TEXT,
    WAIT_KNOWLEDGE_POOL,
    WAIT_KNOWLEDGE_TEXT,
    WAIT_ORDER_POOL,
    WAIT_ORDER_TEXT,
    WAIT_SEARCH_POOL,
    WAIT_SEARCH_TEXT,
    WAIT_STATUS_POOL,
    WAIT_STATUS_TEXT,
    WAIT_TEXT,
)
from src.core.call_session import CallSession
from src.core.pipeline import (
    _FAREWELL_MIN_TURNS,
    CallPipeline,
    _select_wait_message,
    _time_of_day_greeting,
    _wait_counters,
)

# ---------------------------------------------------------------------------
# 1. Time-of-day greeting
# ---------------------------------------------------------------------------


class TestTimeOfDayGreeting:
    """Test _time_of_day_greeting() for different hours."""

    @pytest.mark.parametrize(
        ("hour", "expected"),
        [
            (6, "До́брий ра́нок"),
            (7, "До́брий ра́нок"),
            (11, "До́брий ра́нок"),
            (12, "До́брий день"),
            (15, "До́брий день"),
            (17, "До́брий день"),
            (18, "До́брий ве́чір"),
            (22, "До́брий ве́чір"),
            (0, "До́брий ве́чір"),
            (5, "До́брий ве́чір"),
        ],
    )
    def test_greeting_by_hour(self, hour: int, expected: str) -> None:
        fake_dt = datetime.datetime(2026, 2, 20, hour, 0, tzinfo=datetime.UTC)
        with patch("src.core.pipeline.datetime") as mock_dt:
            mock_dt.datetime.now.return_value = fake_dt
            result = _time_of_day_greeting()
        assert result == expected

    def test_greeting_replaces_placeholder_in_template(self) -> None:
        """Greeting template with '{time_greeting}' placeholder gets replaced."""
        assert "{time_greeting}" in GREETING_TEXT
        morning = "До́брий ра́нок"
        replaced = GREETING_TEXT.replace("{time_greeting}", morning)
        assert morning in replaced
        assert "{time_greeting}" not in replaced


# ---------------------------------------------------------------------------
# 2. Stress marks in template constants
# ---------------------------------------------------------------------------


class TestStressMarks:
    """Verify U+0301 combining acute accent is present in templates."""

    ACCENT = "\u0301"  # combining acute accent

    @pytest.mark.parametrize(
        "text",
        [
            GREETING_TEXT,
            FAREWELL_TEXT,
            WAIT_TEXT,
            SILENCE_PROMPT_TEXT,
            TRANSFER_TEXT,
            ERROR_TEXT,
            ORDER_CANCELLED_TEXT,
            WAIT_SEARCH_TEXT,
            WAIT_AVAILABILITY_TEXT,
            WAIT_ORDER_TEXT,
            WAIT_FITTING_TEXT,
            WAIT_STATUS_TEXT,
            WAIT_KNOWLEDGE_TEXT,
            FAREWELL_ORDER_TEXT,
        ],
    )
    def test_template_has_stress_marks(self, text: str) -> None:
        assert self.ACCENT in text, f"Missing stress marks in: {text!r}"

    def test_greeting_specific_accents(self) -> None:
        # Key words must have accents (note: capital І in Інтерне́т)
        assert "Інтерне́т-магази́н" in GREETING_TEXT
        assert "Оле́на" in GREETING_TEXT
        assert "автомати́чною" in GREETING_TEXT

    def test_farewell_specific_accents(self) -> None:
        assert "Дя́кую" in FAREWELL_TEXT
        assert "дзвіно́к" in FAREWELL_TEXT

    def test_wait_specific_accents(self) -> None:
        assert "Зачека́йте" in WAIT_TEXT
        assert "ла́ска" in WAIT_TEXT
        assert "дивлю́ся" in WAIT_TEXT


# ---------------------------------------------------------------------------
# 3. Contextual wait-phrase selection
# ---------------------------------------------------------------------------


class TestContextualWaitPhrase:
    """Test _select_wait_message() keyword matching with pool rotation."""

    DEFAULT = "default wait"

    @pytest.fixture(autouse=True)
    def _reset_counters(self) -> None:
        """Reset rotation counters so each test starts from pool[0]."""
        _wait_counters.clear()

    def test_order_keywords(self) -> None:
        assert _select_wait_message("Хочу оформити замовлення", self.DEFAULT) in WAIT_ORDER_POOL

    def test_status_keywords(self) -> None:
        assert _select_wait_message("Де моє замовлення?", self.DEFAULT) in WAIT_STATUS_POOL
        assert _select_wait_message("Перевірте статус", self.DEFAULT) in WAIT_STATUS_POOL

    def test_fitting_keywords(self) -> None:
        assert _select_wait_message("Запис на шиномонтаж", self.DEFAULT) in WAIT_FITTING_POOL

    def test_availability_keywords(self) -> None:
        assert _select_wait_message("Перевірте наявність", self.DEFAULT) in WAIT_AVAILABILITY_POOL

    def test_search_keywords(self) -> None:
        assert _select_wait_message("Підібрати зимові шини", self.DEFAULT) in WAIT_SEARCH_POOL

    def test_knowledge_keywords(self) -> None:
        assert _select_wait_message("Порівняти бренди", self.DEFAULT) in WAIT_KNOWLEDGE_POOL

    def test_no_match_returns_default_pool(self) -> None:
        assert _select_wait_message("Привіт", self.DEFAULT) in WAIT_DEFAULT_POOL

    def test_case_insensitive(self) -> None:
        assert _select_wait_message("ЗАМОВЛЕННЯ", self.DEFAULT) in WAIT_ORDER_POOL

    def test_first_match_wins(self) -> None:
        # "статус" matches status pattern (first in list) before order's "замовлення"
        result = _select_wait_message("замовлення статус", self.DEFAULT)
        assert result in WAIT_STATUS_POOL

    def test_partial_keyword_match(self) -> None:
        # "зимов" should match as a substring
        assert _select_wait_message("зимові шини 205/55", self.DEFAULT) in WAIT_SEARCH_POOL

    def test_rotation_cycles_through_pool(self) -> None:
        """Consecutive calls rotate through pool variants."""
        results = [
            _select_wait_message("Підібрати шини", self.DEFAULT)
            for _ in range(len(WAIT_SEARCH_POOL) + 1)
        ]
        # First len(pool) calls should cover all variants
        assert set(results[: len(WAIT_SEARCH_POOL)]) == set(WAIT_SEARCH_POOL)
        # After cycling, it wraps around
        assert results[len(WAIT_SEARCH_POOL)] == WAIT_SEARCH_POOL[0]


# ---------------------------------------------------------------------------
# 4. Contextual farewell
# ---------------------------------------------------------------------------


class TestContextualFarewell:
    """Test _generate_contextual_farewell() method."""

    def _make_pipeline(self, session: CallSession) -> CallPipeline:
        """Create a minimal CallPipeline with mocked dependencies."""
        conn = AsyncMock()
        conn.is_closed = False
        stt = AsyncMock()
        tts = AsyncMock()
        agent = AsyncMock()
        return CallPipeline(
            conn=conn,
            stt=stt,
            tts=tts,
            agent=agent,
            session=session,
        )

    @pytest.mark.asyncio
    async def test_short_conversation_returns_none(self) -> None:
        """Less than _FAREWELL_MIN_TURNS → None (use default template)."""
        session = CallSession(uuid.uuid4())
        session.add_user_turn("Привіт")
        session.add_assistant_turn("Добрий день!")
        assert len(session.dialog_history) < _FAREWELL_MIN_TURNS

        pipeline = self._make_pipeline(session)
        result = await pipeline._generate_contextual_farewell()
        assert result is None

    @pytest.mark.asyncio
    async def test_order_id_returns_order_farewell(self) -> None:
        """If session has order_id → return FAREWELL_ORDER_TEXT."""
        session = CallSession(uuid.uuid4())
        for i in range(4):
            session.add_user_turn(f"Turn {i}")
            session.add_assistant_turn(f"Response {i}")
        session.order_id = "ORD-12345"

        pipeline = self._make_pipeline(session)
        result = await pipeline._generate_contextual_farewell()
        assert result == FAREWELL_ORDER_TEXT

    @pytest.mark.asyncio
    async def test_llm_farewell_called(self) -> None:
        """Long conversation without order → calls LLM."""
        session = CallSession(uuid.uuid4())
        for i in range(4):
            session.add_user_turn(f"Turn {i}")
            session.add_assistant_turn(f"Response {i}")

        pipeline = self._make_pipeline(session)
        pipeline._agent.process_message = AsyncMock(
            return_value=("Дякую за консультацію! До побачення!", [])
        )

        result = await pipeline._generate_contextual_farewell()
        assert result == "Дякую за консультацію! До побачення!"
        pipeline._agent.process_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_llm_timeout_returns_none(self) -> None:
        """LLM timeout → returns None for default fallback."""
        session = CallSession(uuid.uuid4())
        for i in range(4):
            session.add_user_turn(f"Turn {i}")
            session.add_assistant_turn(f"Response {i}")

        pipeline = self._make_pipeline(session)

        async def slow_response(*args, **kwargs):
            await asyncio.sleep(10)
            return ("late", [])

        pipeline._agent.process_message = slow_response

        result = await pipeline._generate_contextual_farewell()
        assert result is None

    @pytest.mark.asyncio
    async def test_llm_error_returns_none(self) -> None:
        """LLM exception → returns None for default fallback."""
        session = CallSession(uuid.uuid4())
        for i in range(4):
            session.add_user_turn(f"Turn {i}")
            session.add_assistant_turn(f"Response {i}")

        pipeline = self._make_pipeline(session)
        pipeline._agent.process_message = AsyncMock(side_effect=RuntimeError("LLM down"))

        result = await pipeline._generate_contextual_farewell()
        assert result is None

    @pytest.mark.asyncio
    async def test_llm_empty_response_returns_none(self) -> None:
        """LLM returns empty string → returns None."""
        session = CallSession(uuid.uuid4())
        for i in range(4):
            session.add_user_turn(f"Turn {i}")
            session.add_assistant_turn(f"Response {i}")

        pipeline = self._make_pipeline(session)
        pipeline._agent.process_message = AsyncMock(return_value=("", []))

        result = await pipeline._generate_contextual_farewell()
        assert result is None
