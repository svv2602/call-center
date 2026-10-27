"""Spoken tyre price vs the search result — call 51f0652f (2026-10-27):
3118 грн said as «три тисячі», two tyres as «разом шість тисяч»."""

from __future__ import annotations

import pytest

from src.agent.tire_claim_guard import RULE_PRICE, TireClaimState, guard_tire_claims_text
from src.agent.tire_price_claim import find_amounts, in_delivery_clause, price_corrections

ITEMS = [
    {"brand": "Sportrak", "model": "Snowtrek SP709", "price": 3118},
    {"brand": "Wanli", "model": "SW312", "price": 3630},
]
SIZE = "двісті тридцять п'ять п'ятдесят п'ять ер вісімнадцять"


def _state() -> TireClaimState:
    st = TireClaimState(sales_enabled=True)
    st.note("search_tires", {"items": ITEMS})
    return st


class TestAmounts:
    @pytest.mark.parametrize(
        ("text", "values"),
        [
            ("за три тисячі сто вісімнадцять гривень", [3118]),
            ("за сім тисяч дев'ятсот сімдесят сім гривень", [7977]),
            ("за 3 118 грн", [3118]),
            ("за 3118 гривень, разом 6236 гривень", [3118, 6236]),
            ("за тисячу сто вісімнадцять гривень", [1118]),
            (f"{SIZE} за три тисячі гривень", [3000]),
            ("вісімнадцять три тисячі гривень", [3000]),
            ("за пять тысяч триста гривен", [5300]),
        ],
    )
    def test_amount_before_currency(self, text: str, values: list[int]) -> None:
        assert [a.value for a in find_amounts(text)] == values

    def test_total_markers(self) -> None:
        amounts = find_amounts("3118 гривень за шину, разом 6236 гривень, за дві шини 6236 гривень")
        assert [a.total for a in amounts] == [False, True, True]

    def test_total_without_a_count_is_not_judged(self) -> None:
        assert price_corrections("Sportrak, разом шість тисяч гривень.", 3118, None) is None


class TestRule:
    def test_the_order_question_of_the_call(self) -> None:
        text = (
            f"Отже, обираємо Sportrak Snowtrek SP709 {SIZE} за три тисячі гривень за шину, вірно?"
        )
        assert guard_tire_claims_text(text, _state(), "") == (
            f"Отже, обираємо Sportrak Snowtrek SP709 {SIZE} за 3118 гривень за шину, вірно?"
        )

    def test_the_recap_with_a_total(self) -> None:
        text = (
            f"Записала: дві шини Sportrak Snowtrek SP709 {SIZE}, три тисячі гривень за шину, "
            "разом шість тисяч гривень. Де бажаєте отримати шини?"
        )
        assert guard_tire_claims_text(text, _state(), "") == (
            f"Записала: дві шини Sportrak Snowtrek SP709 {SIZE}, 3118 гривень за шину, "
            "разом 6236 гривень. Де бажаєте отримати шини?"
        )

    @pytest.mark.parametrize(
        "text",
        [
            "Є Sportrak Snowtrek SP709 за три тисячі сто вісімнадцять гривень.",
            "Ціна за одну шину Sportrak Snowtrek SP709 — 3118 гривень.",
            "Дві шини Sportrak Snowtrek SP709 — разом 6236 гривень.",
            # two tyres named — not judged here
            "Є Sportrak Snowtrek SP709 за три тисячі і Wanli SW312 за чотири тисячі гривень.",
            # no tyre named
            "Доставка коштує сто гривень.",
        ],
    )
    def test_right_or_unjudged_prices_pass(self, text: str) -> None:
        assert guard_tire_claims_text(text, _state(), "") == text

    def test_counted_under_its_own_rule(self, monkeypatch) -> None:
        seen: list[str] = []

        class _M:
            def __init__(self, rule: str) -> None:
                seen.append(rule)

            def inc(self) -> None:
                pass

        monkeypatch.setattr(
            "src.agent.tire_claim_guard.tire_claim_refused_total",
            type("C", (), {"labels": staticmethod(lambda rule: _M(rule))})(),
        )
        guard_tire_claims_text("Sportrak Snowtrek SP709 за три тисячі гривень.", _state(), "")
        assert seen == [RULE_PRICE]


class TestDeliverySumIsNotATyrePrice:
    """A sum in a delivery / courier / COD-fee clause is not the tyre's price —
    the rule rewrote «орієнтовно 810 грн» to «орієнтовно 3118 грн»."""

    @pytest.mark.parametrize(
        "text",
        [
            "Sportrak Snowtrek SP709 — 3118 грн, доставка Новою поштою — орієнтовно 810 грн.",
            "Sportrak Snowtrek SP709 — 3118 грн, кур'єром — ще 60 грн.",
            "Sportrak Snowtrek SP709 — 3118 грн, комісія за накладений платіж — 260 грн.",
            "Sportrak Snowtrek SP709 — 3118 грн, комісія — 260 грн.",
            "Sportrak Snowtrek SP709 — 3118 грн; разом із доставкою 3928 грн.",
            "Sportrak Snowtrek SP709 — 3118 грн, а відправка коштуватиме 120 гривень.",
        ],
    )
    def test_delivery_sum_survives_the_guard(self, text: str) -> None:
        assert guard_tire_claims_text(text, _state(), "") == text

    def test_tyre_price_in_the_same_sentence_is_still_fixed(self) -> None:
        text = "Sportrak Snowtrek SP709 — 3000 грн, доставка Новою поштою — орієнтовно 810 грн."
        assert guard_tire_claims_text(text, _state(), "") == (
            "Sportrak Snowtrek SP709 — 3118 грн, доставка Новою поштою — орієнтовно 810 грн."
        )

    def test_a_dash_does_not_end_the_delivery_clause(self) -> None:
        text = "доставка Новою поштою — орієнтовно 810 грн"
        assert in_delivery_clause(text, text.index("810"))

    def test_a_comma_ends_the_delivery_clause(self) -> None:
        text = "доставка безкоштовна, Sportrak за 3000 грн"
        assert not in_delivery_clause(text, text.index("3000"))
        assert price_corrections(text, 3118, None) == "доставка безкоштовна, Sportrak за 3118 грн"

    def test_a_delivery_word_after_the_sum_does_not_count(self) -> None:
        text = "Sportrak Snowtrek SP709 за 3000 грн з доставкою."
        assert guard_tire_claims_text(text, _state(), "") == (
            "Sportrak Snowtrek SP709 за 3118 грн з доставкою."
        )
