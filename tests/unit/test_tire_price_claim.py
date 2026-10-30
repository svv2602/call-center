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


MICHELIN = [
    {"brand": "Michelin", "model": "X-ICE SNOW+", "price": 8952},
    {"brand": "Michelin", "model": "X-ICE SNOW", "price": 9280},
    {"brand": "Michelin", "model": "Pilot Alpin 5", "price": 10434},
]
LADDER = [
    {"brand": "Bridgestone", "model": "Blizzak 6", "price": 7977},
    {"brand": "Firestone", "model": "WinterHawk 4", "price": 6057},
    {"brand": "Laufenn", "model": "I FIT+ LW31", "price": 5346},
]


def _shown(items: list[dict]) -> TireClaimState:
    st = TireClaimState(sales_enabled=True)
    st.note("search_tires", {"items": items})
    return st


class TestListOfTyres:
    """Call 06311ba8 (2026-10-29): three Michelins, one price said for all."""

    def test_one_price_said_for_three_tyres(self) -> None:
        text = (
            "Є Мішлен Ікс-аїс сно плюс за 10434 гривні, Ікс-аїс сно за 10434 гривень "
            "і Пілот Алпін п'ять за десять тисяч чотириста тридцять чотири гривні."
        )
        assert guard_tire_claims_text(text, _shown(MICHELIN), "") == (
            "Є Мішлен Ікс-аїс сно плюс за 8952 гривні, Ікс-аїс сно за 9280 гривень "
            "і Пілот Алпін п'ять за десять тисяч чотириста тридцять чотири гривні."
        )

    def test_prices_swapped_between_named_tyres(self) -> None:
        text = "Є Файрстоун Вінтерхок чотири за 7977 гривень і Бріджстоун Блідзак шосте за 6057 гривень."
        assert guard_tire_claims_text(text, _shown(LADDER), "") == (
            "Є Файрстоун Вінтерхок чотири за 6057 гривень і Бріджстоун Блідзак шосте за 7977 гривень."
        )

    @pytest.mark.parametrize(
        "text",
        [
            "Є Бріджстоун Блідзак шосте за сім тисяч дев’ятсот сімдесят сім гривень, Файрстоун "
            "Вінтерхок чотири за шість тисяч п’ятдесят сім гривень і Лауффенн ай фіт плюс ел діб "
            "тридцять один за п’ять тисяч триста сорок шість гривень.",
            "Бріджстоун Блідзак шосте — 7977 гривень, Лауффенн — 5346 гривень.",
            # a brand only, the right prices in another order
            "Мішлен є за 10434 гривні і за 8952 гривні.",
            # no tyre named
            "Є варіанти за 5000 гривень і за 6000 гривень.",
        ],
    )
    def test_right_or_unjudged_lists_pass(self, text: str) -> None:
        items = MICHELIN if "Мішлен" in text else LADDER
        assert guard_tire_claims_text(text, _shown(items), "") == text


class TestNumberTail:
    """Call f06909f9: «двісті п'ятьдесят дві гривні» — «дві» was read as 2 UAH."""

    RECAP = (
        "Записала: чотири шини Бріджстоун Бліцзак 6, шість тисяч вісімсот тринадцять гривень "
        "за шину, разом двадцять сім тисяч двісті п'ятьдесят дві гривні."
    )

    def test_misspelt_tens_read_whole(self) -> None:
        assert find_amounts(self.RECAP)[-1].value == 27252

    def test_right_total_with_misspelt_tens_passes(self) -> None:
        items = [{"brand": "Bridgestone", "model": "Blizzak 6", "price": 6813}]
        assert guard_tire_claims_text(self.RECAP, _shown(items), "") == self.RECAP

    def test_unreadable_tens_are_not_cut_to_their_tail(self) -> None:
        # «сорокдесят» reads as nothing: «дві» after it is not 2 UAH.
        assert [a.value for a in find_amounts("разом двісті сорокдесят дві гривні")] == []
        assert [a.value for a in find_amounts("разом шістьдесят дві гривні")] == [62]

    def test_a_diameter_before_the_price_is_not_part_of_it(self) -> None:
        assert [a.value for a in find_amounts("ер вісімнадцять три тисячі гривень")] == [3000]
