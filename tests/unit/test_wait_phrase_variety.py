"""Wave 18 (2026-09-09) — the bot must stop saying «Секундочку» over and over.

Testers reported hearing it very often and sometimes several times in a row.
Two independent filler sources overlap in one turn: the thinking filler
(WAIT_THINKING_POOL, rotated by _thinking_counter) and the tool wait phrase
(_pick_tool_wait_phrase). «Секундочку» opened 12 of the 36 pool phrases, and
the tool phrase was drawn with random.choice, which on a 3-phrase pool repeats
itself one time in three.

Raising _FILLER_DELAY_SEC is NOT an option — streaming_loop.py:58-67 records
that the 0.3s cadence is an anti-drop measure (calls c11eae66, 8c85d7c5).
"""

from __future__ import annotations

import asyncio
import itertools
import time
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest

from src.agent import prompts
from src.agent.streaming_loop import (
    _FILLER_DELAY_SEC,
    _FILLER_ECHO_WINDOW_SEC,
    _TOOL_WAIT_POOLS,
    _filler_still_ringing,
    _opening_word,
    _pick_tool_wait_phrase,
)
from tests.unit.test_streaming_loop import _build_loop, _tool_stream

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from src.llm.models import StreamEvent


class _SlowRouter:
    """Wraps a mock router so the LLM stream starts after a real delay —
    without one, the filler's sleep is cancelled before it ever fires."""

    def __init__(self, inner: Any, delay_sec: float) -> None:
        self._inner = inner
        self._delay_sec = delay_sec

    async def complete_stream(self, *args: Any, **kwargs: Any) -> AsyncIterator[StreamEvent]:
        await asyncio.sleep(self._delay_sec)
        async for event in self._inner.complete_stream(*args, **kwargs):
            yield event

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

ALL_POOLS = {
    name: value
    for name, value in vars(prompts).items()
    if name.startswith("WAIT_") and name.endswith("_POOL") and isinstance(value, list)
}

TOOL_POOL_NAMES = {
    name for name, value in ALL_POOLS.items() if value is not prompts.WAIT_THINKING_POOL
}


class TestLexicon:
    def test_the_pools_were_actually_found(self) -> None:
        """Guard the reflection above: a rename must not silently empty this."""
        assert len(ALL_POOLS) >= 14

    @pytest.mark.parametrize("name", sorted(TOOL_POOL_NAMES))
    def test_sekundochku_is_reserved_for_the_thinking_pool(self, name: str) -> None:
        for phrase in ALL_POOLS[name]:
            assert "секундочку" not in phrase.lower(), f"{name}: {phrase!r}"

    def test_the_thinking_pool_still_has_it(self) -> None:
        assert any("секундочку" in p.lower() for p in prompts.WAIT_THINKING_POOL)

    @pytest.mark.parametrize("name", sorted(ALL_POOLS))
    def test_no_pool_repeats_an_opening_word(self, name: str) -> None:
        """Rotation only helps if consecutive entries actually sound different."""
        openers = [_opening_word(p) for p in ALL_POOLS[name]]
        assert len(openers) == len(set(openers)), f"{name}: {openers}"


class TestRotation:
    def test_consecutive_picks_never_repeat(self) -> None:
        pool = _TOOL_WAIT_POOLS["get_fitting_stations"]
        picks = [_pick_tool_wait_phrase(["get_fitting_stations"], index=i) for i in range(12)]
        assert all(a != b for a, b in itertools.pairwise(picks))
        assert set(picks) == set(pool)

    def test_an_unknown_tool_falls_back_to_the_default_pool(self) -> None:
        picks = [_pick_tool_wait_phrase(["no_such_tool"], index=i) for i in range(8)]
        assert set(picks) == set(prompts.WAIT_DEFAULT_POOL)

    def test_the_first_matching_tool_wins(self) -> None:
        phrase = _pick_tool_wait_phrase(["no_such_tool", "book_fitting"], index=0)
        assert phrase in prompts.WAIT_BOOKING_POOL


class TestTheLoopActuallyRotates:
    """The pure picker is useless if the caller never advances the index."""

    def test_consecutive_rounds_get_different_phrases(self) -> None:
        loop, _, _, _ = _build_loop([])
        picks = [loop._next_tool_wait_phrase(["get_fitting_stations"]) for _ in range(6)]
        assert all(a != b for a, b in itertools.pairwise(picks))
        assert set(picks) == set(_TOOL_WAIT_POOLS["get_fitting_stations"])

    def test_the_thinking_filler_is_avoided_across_rounds(self) -> None:
        loop, _, _, _ = _build_loop([])
        loop._last_thinking_filler = "Одну мить."
        for _ in range(6):
            phrase = loop._next_tool_wait_phrase(["get_fitting_stations"])
            assert _opening_word(phrase) != "одну"


class TestNoEchoOfTheThinkingFiller:
    @pytest.mark.parametrize("thinking", prompts.WAIT_THINKING_POOL)
    @pytest.mark.parametrize("tool", sorted(_TOOL_WAIT_POOLS))
    def test_tool_phrase_never_echoes_the_filler_just_queued(
        self, thinking: str, tool: str
    ) -> None:
        for index in range(len(_TOOL_WAIT_POOLS[tool])):
            phrase = _pick_tool_wait_phrase([tool], index=index, avoid=thinking)
            assert _opening_word(phrase) != _opening_word(thinking)

    def test_avoid_is_ignored_when_every_phrase_would_collide(self) -> None:
        """Never return empty: a collision-only pool still has to say something."""
        pool = _TOOL_WAIT_POOLS["get_fitting_stations"]
        phrase = _pick_tool_wait_phrase(
            ["get_fitting_stations"], index=1, avoid=pool[0]
        )
        assert phrase in pool


class TestTheThinkingPoolIsBigEnough:
    """Measured 2026-09-16: the thinking filler fires on ~every round, ~10 per
    call. Frequency is fixed by LLM latency (0% of calls answer under 800ms),
    so the pool size is what decides how often a caller hears the same word.
    """

    def test_the_pool_covers_a_whole_call(self) -> None:
        assert len(prompts.WAIT_THINKING_POOL) >= 10

    def test_one_call_worth_of_rounds_never_repeats(self) -> None:
        loop, _, _, _ = _build_loop([])
        picks = [loop._next_thinking_filler() for _ in range(len(prompts.WAIT_THINKING_POOL))]
        assert len(set(picks)) == len(picks)
        assert set(picks) == set(prompts.WAIT_THINKING_POOL)

    def test_the_pick_is_published_for_the_tool_phrase_to_avoid(self) -> None:
        """The avoid-echo machinery reads _last_thinking_filler — a pick that
        forgets to record itself would silently re-enable the echo."""
        loop, _, _, _ = _build_loop([])
        phrase = loop._next_thinking_filler()
        assert loop._last_thinking_filler == phrase


class TestTheRotationStartsSomewhereDifferentEachCall:
    """Rotation varies phrases inside one call, but a counter starting at 0
    made every call replay the same cycle from the same phrase.
    """

    def test_thinking_filler_does_not_always_open_with_the_same_phrase(self) -> None:
        firsts = {_build_loop([])[0]._next_thinking_filler() for _ in range(40)}
        assert len(firsts) > 1

    def test_tool_wait_phrase_does_not_always_open_with_the_same_phrase(self) -> None:
        firsts = {
            _build_loop([])[0]._next_tool_wait_phrase(["get_fitting_stations"]) for _ in range(40)
        }
        assert len(firsts) > 1


class TestTheWaitPhraseYieldsToAFillerStillPlaying:
    """A tool round used to speak twice: the thinking filler at +0.3s and then
    the tool wait phrase, back to back. Measured 2026-09-16, that was 58
    utterances across four calls.
    """

    def test_a_filler_that_never_played_leaves_the_wait_phrase_alone(self) -> None:
        """None is the rounds where nothing else covers the tool call."""
        assert _filler_still_ringing(None) is False

    def test_a_filler_that_just_ended_suppresses_it(self) -> None:
        now = time.monotonic()
        assert _filler_still_ringing(now - 0.2, now=now) is True

    def test_a_filler_from_long_ago_does_not(self) -> None:
        now = time.monotonic()
        assert _filler_still_ringing(now - 30.0, now=now) is False

    def test_the_window_has_both_edges(self) -> None:
        now = time.monotonic()
        inside = _FILLER_ECHO_WINDOW_SEC - 0.05
        outside = _FILLER_ECHO_WINDOW_SEC + 0.05
        assert _filler_still_ringing(now - inside, now=now) is True
        assert _filler_still_ringing(now - outside, now=now) is False

    @pytest.mark.asyncio
    async def test_a_slow_round_does_not_speak_twice(self) -> None:
        """Wiring: the predicate is useless if run_turn never consults it.

        The LLM here takes longer than _FILLER_DELAY_SEC, so the filler really
        plays before the tool call arrives — the ordinary case on prod.
        """
        loop, _, _, _ = _build_loop(
            [_tool_stream("", "t1", "get_fitting_stations", {"city": "Дніпро"})],
            tool_results={"get_fitting_stations": {"stations": []}},
        )
        loop._llm_router = _SlowRouter(loop._llm_router, delay_sec=_FILLER_DELAY_SEC + 0.25)
        with patch.object(
            loop, "_next_tool_wait_phrase", wraps=loop._next_tool_wait_phrase
        ) as picked:
            await loop.run_turn("Які у вас точки?", [])
        picked.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_fast_round_still_speaks_the_wait_phrase(self) -> None:
        """The counterpart: when no filler played, the wait phrase is the only
        thing covering the tool call and must survive."""
        loop, _, _, _ = _build_loop(
            [_tool_stream("", "t1", "get_fitting_stations", {"city": "Дніпро"})],
            tool_results={"get_fitting_stations": {"stations": []}},
        )
        with patch.object(
            loop, "_next_tool_wait_phrase", wraps=loop._next_tool_wait_phrase
        ) as picked:
            await loop.run_turn("Які у вас точки?", [])
        picked.assert_called_once()


class TestOpeningWord:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("Секундочку.", "секундочку"),
            ("Одну мить, дивлюся адреси.", "одну"),
            ("  Зараз!  ", "зараз"),
            ("", ""),
        ],
    )
    def test_first_word_is_lowercased_and_unpunctuated(
        self, text: str, expected: str
    ) -> None:
        assert _opening_word(text) == expected
