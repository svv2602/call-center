"""A price range is the budget (tshina `4b9579299`, ported 2026-10-30)."""

from __future__ import annotations

import pytest

from src.agent.parsers.tire_query import Budget, drops_chosen_brand, parse_budget
from src.agent.prompts import _render_tire_progress
from src.core.pipeline import merge_tire_query


@pytest.mark.parametrize(
    ("text", "floor", "amount"),
    [
        ("від 3500 до 4500 гривень", 3500, 4500),
        ("от 4000 до 5000", 4000, 5000),
        ("ціна 4000-5000 грн", 4000, 5000),
        ("бюджет 4-5 тисяч", 4000, 5000),
    ],
)
def test_range(text: str, floor: int, amount: int) -> None:
    assert parse_budget(text) == Budget(amount, None, True, floor)


@pytest.mark.parametrize("text", ["2019-2020", "4000-5000", "шини 4-5 штук"])
def test_not_a_range(text: str) -> None:
    budget = parse_budget(text)
    assert budget is None or budget.floor is None


def test_two_years_are_not_a_range() -> None:
    assert parse_budget("з 2019 до 2021").floor is None


def test_cap_has_no_floor() -> None:
    assert parse_budget("до 4000") == Budget(4000, None, True, None)


def test_range_reaches_the_session_and_the_block() -> None:
    query = merge_tire_query({}, "літні 205/55 R16 від 3500 до 4500 гривень", consult_started=True)
    assert query["budget"] == {"amount": 4500, "scope": None, "is_cap": True, "floor": 3500}
    assert "від 3500 до 4500 грн" in _render_tire_progress(query)


@pytest.mark.parametrize(
    ("text", "drops"),
    [
        ("не, это дорого, давайте подешевле", True),
        ("дороговато", True),
        ("а дешевше є?", True),
        ("не дорого, беру", False),
        ("а дорожче є?", False),
        ("Michelin", False),
    ],
)
def test_drops_chosen_brand(text: str, drops: bool) -> None:
    assert drops_chosen_brand(text) is drops
