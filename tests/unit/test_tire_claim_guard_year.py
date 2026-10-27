"""The production year the model says about an offered tyre, judged against the data.

Wave 3-C (R07.1, golden set 2026-10-27): Твоя шина said «Ця шина поточного
року, 2026-го» over ``year=2023``; «Bridgestone Turanza T005 … актуальна на
2025 рік» had no year at all; Про Колесо said «Ця модель не вказує рік
виробництва». The shown tyres are built by `StoreClient._tire_item` over a
row shaped like the ``_query_tire_rows`` SELECT (``year_issue`` VARCHAR), so
the ``year`` key is the one the live path gives.

Every refusal is checked by *which* rule fired (the metric label): ``novelty``
sits right before ``year`` in the registry.
"""

from __future__ import annotations

import asyncio
import copy
from typing import Any

import pytest

from src.agent import tire_claim_guard as guard
from src.agent.tire_claim_guard import (
    RULE_NOVELTY,
    RULE_YEAR,
    RULES,
    TireClaimState,
    guard_tire_claims,
    guard_tire_claims_text,
)
from src.core.sentence_buffer import SentenceReady
from src.monitoring.metrics import tire_claim_refused_total
from src.store_client.client import StoreClient

#: R05, the owner's words — pinned literally, not through ``REPLACEMENTS``.
R05 = "Рік випуску цієї партії уточнить менеджер, коли передзвонить підтвердити заявку."


def _row(id_: str, brand: str, model: str, price: int, year: str | None) -> dict[str, Any]:
    """A ``_query_tire_rows`` row (``tire_stock`` columns included)."""
    return {
        "id": id_,
        "brand": brand,
        "model": model,
        "size": "205/55 R16",
        "season": "winter",
        "price": price,
        "stock_quantity": 4,
        "country": "Японія",
        "year_issue": year,
        "runflat": False,
        "eu_fuel": None,
        "eu_wet": None,
        "eu_noise_db": None,
        "eu_noise_class": None,
        "attr_studded": None,
        "attr_xl": None,
        "load_index": None,
        "speed_index": None,
        "desc_studded": False,
        "desc_studdable": False,
        "desc_xl": False,
    }


ITEMS = [
    StoreClient._tire_item(_row("1", "Bridgestone", "Blizzak 6", 8625, "2023")),
    StoreClient._tire_item(_row("2", "Bridgestone", "Turanza T005", 5100, None)),
    StoreClient._tire_item(_row("3", "Rydanz", "NORDICA NR01", 3882, "2026")),
]
ASKED = "зимові шини 205/55 R16"
TRUTH = "Bridgestone Blizzak 6 — 2023 року випуску."


def _state(*, sales: bool = True) -> TireClaimState:
    state = TireClaimState(sales_enabled=sales)
    state.note("search_tires", {"total": len(ITEMS), "items": copy.deepcopy(ITEMS)})
    return state


def _count(rule: str) -> float:
    return tire_claim_refused_total.labels(rule=rule)._value.get()


def _judge(text: str, state: TireClaimState | None = None) -> str:
    return guard_tire_claims_text(text, state or _state(), ASKED)


def _fired(text: str, state: TireClaimState | None = None) -> set[str]:
    before = {rule: _count(rule) for rule, _ in RULES}
    _judge(text, state)
    return {rule for rule, _ in RULES if _count(rule) > before[rule]}


@pytest.fixture(autouse=True)
def _year_2026(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(guard.ENV_FLAG, raising=False)
    monkeypatch.setattr(guard, "_this_year", lambda: 2026)


def test_the_item_carries_the_year_as_the_live_path_gives_it() -> None:
    assert ITEMS[0]["year"] == "2023"
    assert "year" not in ITEMS[1]


def test_rule_is_registered_after_novelty() -> None:
    names = [name for name, _ in RULES]
    assert names.index(RULE_YEAR) == names.index(RULE_NOVELTY) + 1


class TestGoldenSet:
    def test_tvoya_shina_current_year_over_2023_becomes_the_truth(self) -> None:
        text = "Раджу Bridgestone Blizzak 6. Ця шина поточного року, 2026-го. Ще щось цікавить?"
        assert _fired(text) == {RULE_YEAR}
        assert _judge(text) == f"Раджу Bridgestone Blizzak 6. {TRUTH} Ще щось цікавить?"

    def test_a_year_with_no_year_in_the_data_is_the_owner_sentence(self) -> None:
        text = "Bridgestone Turanza T005 — це сучасна модель, яка актуальна на 2025 рік."
        assert _fired(text) == {RULE_YEAR}
        assert _judge(text) == R05

    def test_prokoleso_no_year_said_while_the_data_has_it(self) -> None:
        text = "Раджу Bridgestone Blizzak 6. Ця модель не вказує рік виробництва."
        assert _fired(text) == {RULE_YEAR}
        assert _judge(text) == f"Раджу Bridgestone Blizzak 6. {TRUTH}"


def test_a_backed_year_does_not_carry_a_false_one_in_the_same_sentence() -> None:
    # «2023 року» is the data, «тобто поточного року» (2026) is not: every
    # year claim of the sentence must be backed, not just one (runner
    # mutation C3, all → any, survived without this test).
    text = "Bridgestone Blizzak 6 — 2023 року, тобто поточного року."
    assert _fired(text) == {RULE_YEAR}
    assert _judge(text) == TRUTH


class TestForms:
    @pytest.mark.parametrize(
        "sentence",
        [
            "Bridgestone Blizzak 6 — 2026 року випуску.",
            "Bridgestone Blizzak 6 2026 года.",
            "Bridgestone Blizzak 6 — партія 2025 р.",
            "Bridgestone Blizzak 6 — 26-го року.",
            "Bridgestone Blizzak 6 — 26 рік.",
            "Bridgestone Blizzak 6 двадцять шостого року.",
            "Bridgestone Blizzak 6 дві тисячі двадцять п'ятого року випуску.",
            "Bridgestone Blizzak 6 поточного року.",
            "Bridgestone Blizzak 6 цього року випуску.",
            "Bridgestone Blizzak 6 этого года.",
            "Bridgestone Blizzak 6 цьогорічна.",
            "Bridgestone Blizzak 6 минулого року.",
            "Bridgestone Blizzak 6 — свіжа партія.",
            "Bridgestone Blizzak 6 — свіжа партія 2026.",
            "Bridgestone Blizzak 6 вироблена у 2024 році.",
        ],
    )
    def test_a_year_the_data_does_not_back_is_replaced_by_the_truth(self, sentence: str) -> None:
        assert _fired(sentence) == {RULE_YEAR}
        assert _judge(sentence) == TRUTH

    @pytest.mark.parametrize(
        "sentence",
        [
            "Bridgestone Blizzak 6 — 2023 року випуску.",
            "Bridgestone Blizzak 6 двадцять третього року.",
            "Bridgestone Blizzak 6 — 23-го року.",
            "Rydanz NORDICA NR01 — свіжа партія, 2026 року.",
            "Rydanz NORDICA NR01 цьогорічна.",
        ],
    )
    def test_a_year_the_data_backs_passes(self, sentence: str) -> None:
        assert _fired(sentence) == set()
        assert _judge(sentence) == sentence

    def test_fresh_batch_with_its_year_is_not_novelty(self) -> None:
        """«свіжа партія, 2026 року» over ``year=2026``: neither novelty nor year."""
        assert _fired("Rydanz NORDICA NR01 — свіжа партія, 2026 року.") == set()


class TestNotTheTyresYear:
    @pytest.mark.parametrize(
        "sentence",
        [
            # The car's year.
            "Для Тойоти 2019 року підійде Bridgestone Blizzak 6.",
            "Bridgestone Blizzak 6 підійде на вашу Тойоту 2019 року.",
            "Bridgestone Blizzak 6 підійде для авто 2019 року.",
            # Warranty, storage, price.
            "Bridgestone Blizzak 6, гарантія 5 років, зберігання до 2027 року.",
            # A question, a negation, a model's history.
            "Bridgestone Blizzak 6 вам потрібна 2026 року?",
            "Bridgestone Blizzak 6 не 2026 року.",
            "Bridgestone Blizzak 6 випускається з 2019 року.",
            # No tyre named, two named.
            "Шини 2026 року є в наявності.",
            "Bridgestone Blizzak 6 і Rydanz NORDICA NR01 — 2026 року.",
        ],
    )
    def test_left_alone(self, sentence: str) -> None:
        assert _fired(sentence) == set()
        assert _judge(sentence) == sentence

    @pytest.mark.parametrize(
        "sentence", ["Bridgestone Blizzak 6 за 2025 гривень.", "Bridgestone Blizzak 6 — 2030 грн."]
    )
    def test_a_price_is_not_a_year(self, sentence: str) -> None:
        # The amount is not the shown price (8625): the price rule corrects
        # it — the year rule stays silent.
        assert _fired(sentence) == {"price"}
        assert "8625" in _judge(sentence)


class TestCheckAvailability:
    def test_a_checked_year_backs_a_tyre_without_one(self) -> None:
        state = _state()
        state.note(
            "check_availability",
            {"available": True, "quantity": 4, "price": 5100, "country": "Японія", "year": "2025"},
        )
        assert _fired("Bridgestone Turanza T005 — 2025 року.", state) == set()
        assert _fired("Bridgestone Turanza T005 — 2026 року.", state) == {RULE_YEAR}

    def test_an_availability_without_a_year_backs_nothing(self) -> None:
        state = _state()
        state.note("check_availability", {"available": False, "message": "Дані відсутні"})
        assert _judge("Bridgestone Turanza T005 — 2025 року.", state) == R05

    def test_sales_off_notes_nothing(self) -> None:
        state = TireClaimState(sales_enabled=False)
        state.note("check_availability", {"available": True, "year": "2025"})
        assert state.checked_years == frozenset()


class TestOnce:
    def test_the_owner_sentence_is_said_once(self) -> None:
        text = "Bridgestone Turanza T005 2025 року. Bridgestone Turanza T005 цьогорічна."
        assert _judge(text) == R05

    def test_the_stream_replaces_the_sentence(self) -> None:
        async def _gen():
            for part in ("Раджу Bridgestone Blizzak 6.", "Ця шина поточного року,", " 2026-го."):
                yield SentenceReady(text=part)

        async def _run() -> list[str]:
            return [e.text async for e in guard_tire_claims(_gen(), _state(), ASKED)]

        assert asyncio.run(_run()) == ["Раджу Bridgestone Blizzak 6.", TRUTH]
