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

import itertools

import pytest

from src.agent import prompts
from src.agent.streaming_loop import (
    _TOOL_WAIT_POOLS,
    _opening_word,
    _pick_tool_wait_phrase,
)
from tests.unit.test_streaming_loop import _build_loop

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
