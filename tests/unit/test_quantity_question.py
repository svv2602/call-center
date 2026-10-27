"""Tyre count — call 51f0652f (2026-10-27): «Мені потрібно дві шини» was
followed by «Скільки шин потрібно?», and a bare «дві» was not read."""

from __future__ import annotations

import pytest

from src.agent.parsers.tire_query import parse_answer_quantity
from src.agent.quantity_question import (
    replace_quantity_question,
    replace_quantity_question_text,
)
from src.core.pipeline import merge_tire_query
from src.core.sentence_buffer import SentenceReady

STARTED = {"season": "winter", "sizes": ["235/55 R18"]}
ASKED = "Записала: одну шину. Скільки шин потрібно?"


class TestAnswer:
    @pytest.mark.parametrize(
        ("text", "count"), [("дві", 2), ("4", 4), ("одну", 1), ("чотири штуки", 4), ("мені дві", 2)]
    )
    def test_bare_count(self, text: str, count: int) -> None:
        q = parse_answer_quantity(text)
        assert q is not None and q.count == count

    @pytest.mark.parametrize("text", ["більше не потрібно", "дві або чотири", "235", "дев'ять"])
    def test_no_count(self, text: str) -> None:
        q = parse_answer_quantity(text)
        assert q is None or q.count is None

    def test_read_only_after_the_count_question(self) -> None:
        assert merge_tire_query(STARTED, "дві", last_bot_text=ASKED)["quantity"] == 2
        assert "quantity" not in merge_tire_query(
            STARTED, "дві", last_bot_text="Яка вас зацікавила?"
        )


class TestReplace:
    def test_text_path(self) -> None:
        out = replace_quantity_question_text("Скільки шин потрібно?", {"quantity": 2})
        assert out == "Записала: дві шини."

    def test_unknown_count_keeps_the_question(self) -> None:
        text = "Добре. Скільки шин потрібно?"
        assert replace_quantity_question_text(text, {"season": "winter"}) == text

    def test_other_sentences_stay(self) -> None:
        out = replace_quantity_question_text(
            "Добре. Скільки шин вам потрібно? Де бажаєте отримати?", {"quantity": 4}
        )
        assert out == "Добре. Записала: чотири шини. Де бажаєте отримати?"

    @pytest.mark.asyncio
    async def test_stream_holds_fragments(self) -> None:
        async def _events():
            for t in ["Скільки шин", "потрібно?", "Де бажаєте отримати?"]:
                yield SentenceReady(text=t)

        out = [e.text async for e in replace_quantity_question(_events(), {"quantity": 2})]
        assert out == ["Записала: дві шини.", "Де бажаєте отримати?"]


# ── wiring: both loops speak the count, not the question ─────────────────

from tests.unit.test_vehicle_lookup_gate import LOOPS  # noqa: E402


@LOOPS
def test_both_loops_replace_the_question(run) -> None:
    _audited, _history, replies, _seen = run(
        [("мені потрібні зимові шини 205/55 R16, дві шини", [], "Добре. Скільки шин потрібно?")]
    )
    assert replies[-1] == "Добре. Записала: дві шини."


@LOOPS
def test_no_filter_while_sales_are_off(run) -> None:
    _audited, _history, replies, _seen = run(
        [("мені потрібні зимові шини 205/55 R16, дві шини", [], "Добре. Скільки шин потрібно?")],
        sales=False,
    )
    assert replies[-1] == "Добре. Скільки шин потрібно?"
