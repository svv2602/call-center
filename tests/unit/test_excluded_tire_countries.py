"""«Китай сразу отпадает» — a refused country of origin, read from the caller's words.

R06 (sales operator gaps 2026-10-27): the caller refused Chinese tyres and the
bot offered Sailun — the stock rows carry the country, nothing read it.
`excluded_tire_countries` reads the refusal (UA / RU, oblique cases, the
adjective); `merge_tire_query` keeps it in ``tire_query.excluded_countries``
for the rest of the call, and the ``search_tires`` handler passes it on
(`tests/unit/test_search_tires_ladder.py::TestRefusedCountryHandler`). Every
assertion goes through the functions imported from `src/`.
"""

from __future__ import annotations

import pytest

from src.agent.parsers.tire_query import excluded_tire_countries
from src.core.pipeline import merge_tire_query

_REFUSALS = [
    "Китай сразу отпадает. Что есть на подобие в этой ценовой политике?",  # op_no_chinese
    "не китай",
    "без китайських",
    "без китайской резины",
    "китайщину не хочу",
    "тільки не Китай",
    "только не Китай",
    "крім Китаю",
    "кроме Китая",
    "Китай не розглядаю",
    "китайські не підходять",
    "китайские не нужны",
    "никаких китайских",
    "ні, Китай не треба",
    "китайські — ні",
    "Китай одразу відпадає",
]

_NOT_REFUSALS = [
    "а китайські є?",
    "а китайские есть?",
    "це китайська?",
    "а що з Китаю є?",
    "хочу китайську",
    "Сайлун китайский?",
    "Китай подходит",
    "зимові 235/60 R16",
    "",
]


@pytest.mark.parametrize("text", _REFUSALS)
def test_refusal_names_the_country(text: str) -> None:
    assert excluded_tire_countries(text) == ["Китай"]


@pytest.mark.parametrize("text", _NOT_REFUSALS)
def test_question_or_choice_is_not_a_refusal(text: str) -> None:
    assert excluded_tire_countries(text) is None


def _dialog(*lines: str) -> dict:
    state: dict = {}
    for line in lines:
        state = merge_tire_query(state, line)
    return state


def test_merge_writes_the_refused_country() -> None:
    state = _dialog("зимова резина 235/60 R16, бренд будь-який", "Китай сразу отпадает")
    assert state["excluded_countries"] == ["Китай"]


def test_refusal_holds_on_the_next_turns() -> None:
    """«А дешевше?» after «Китай отпадает» still searches without China."""
    state = _dialog("зимова резина 235/60 R16", "без китайських", "а дешевше?", "а ще варіанти?")
    assert state["excluded_countries"] == ["Китай"]


def test_repeated_refusal_is_stored_once() -> None:
    state = _dialog("зимова резина 235/60 R16", "не китай", "кажу ж, тільки не Китай")
    assert state["excluded_countries"] == ["Китай"]


def test_question_writes_no_key() -> None:
    """An empty list must not reach the state (it passes the progress filter)."""
    state = _dialog("зимова резина 235/60 R16", "а китайські є?")
    assert "excluded_countries" not in state


def test_refusal_before_the_request_starts_is_not_collected() -> None:
    """Collection starts on a tyre request; a lone line is about something else."""
    assert merge_tire_query({}, "не китай") == {}
