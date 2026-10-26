"""EU-label and stud claims about the offered tyres, judged against the result.

A port of the tshina web consultant's `Dialog/EuClassClaimGuard` and
`Dialog/StudClaimGuard` (bef4f5d66). The shown tyres are in the live shape of
`StoreClient._tire_item`: ``eu_label`` carries only the fields the label has,
``studded`` (``"studded" | "studdable" | "friction"``) only winter tyres.

Every refusal is checked by *which* rule fired (the metric label): the
cheapest / novelty / rating rules sit in the same registry.
"""

from __future__ import annotations

import asyncio
import copy
from typing import Any

import pytest

from src.agent import tire_claim_guard as guard
from src.agent.tire_claim_guard import (
    REPLACEMENTS,
    RULE_EU_CLASS,
    RULE_STUD,
    RULES,
    TireClaimState,
    guard_tire_claims,
    guard_tire_claims_text,
)
from src.core.sentence_buffer import SentenceReady
from src.monitoring.metrics import tire_claim_refused_total


def _item(id_: str, brand: str, model: str, price: int, **extra: Any) -> dict[str, Any]:
    """One `StoreClient._tire_item`."""
    return {
        "id": id_,
        "brand": brand,
        "model": model,
        "size": "215/55 R16",
        "season": "winter",
        "price": price,
        "in_stock": True,
        **extra,
    }


ITEMS = [
    _item(
        "1",
        "Bridgestone",
        "Blizzak 6",
        5200,
        studded="friction",
        eu_label={"fuel": "C", "wet": "B", "noise_db": 71, "noise_class": "B"},
    ),
    _item(
        "2",
        "Nokian",
        "Hakkapeliitta 10",
        6100,
        studded="studded",
        eu_label={"fuel": "D", "wet": "C", "noise_db": 72},
    ),
    _item("3", "Firestone", "WinterHawk 4", 4100, studded="studdable"),
    _item(
        "4",
        "Aplus",
        "A702",
        2950,
        studded="friction",
        eu_label={"fuel": "C", "wet": "C", "noise_db": 70, "noise_class": "A"},
    ),
    _item(
        "5",
        "Michelin",
        "Primacy 4",
        4800,
        season="summer",
        eu_label={"fuel": "B", "wet": "A", "noise_db": 69},
    ),
    # A standalone letter in the model name (a real Laufenn line).
    _item(
        "6",
        "Laufenn",
        "G Fit EQ",
        3100,
        season="summer",
        eu_label={"fuel": "C", "wet": "B", "noise_db": 70},
    ),
]
RESULT = {"total": len(ITEMS), "items": ITEMS}
ASKED = "зимові шини 215/55 R16"

EU_LIE = "Bridgestone Blizzak 6 має клас A по мокрій дорозі."
STUD_LIE = "Bridgestone Blizzak 6 — шипована модель для льоду."


def _state(*, sales: bool = True) -> TireClaimState:
    state = TireClaimState(sales_enabled=sales)
    state.note("search_tires", copy.deepcopy(RESULT))
    return state


def _count(rule: str) -> float:
    return tire_claim_refused_total.labels(rule=rule)._value.get()


def _judge(text: str, state: TireClaimState | None = None) -> str:
    return guard_tire_claims_text(text, state or _state(), ASKED)


def _fired(text: str, state: TireClaimState | None = None) -> set[str]:
    """Which rules refused ``text`` (by the metric, not by the wording)."""
    before = {rule: _count(rule) for rule, _ in RULES}
    _judge(text, state)
    return {rule for rule, _ in RULES if _count(rule) > before[rule]}


@pytest.fixture(autouse=True)
def _guard_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(guard.ENV_FLAG, raising=False)


# ── eu_class ─────────────────────────────────────────────────────────────


class TestEuClass:
    @pytest.mark.parametrize(
        "sentence",
        [
            EU_LIE,
            "У Bridgestone Blizzak 6 клас C за зчепленням на мокрій.",
            "Bridgestone Blizzak 6 — категорія A по мокрій дорозі.",
            "У Bridgestone Blizzak 6 класс сцепления на мокрой дороге A.",
            # Cyrillic twin after «клас»: «клас А».
            "Bridgestone Blizzak 6 має клас А по мокрій.",
            "Bridgestone Blizzak 6 має клас економії палива B.",
            "Nokian Hakkapeliitta 10 — економія пального клас C.",
        ],
    )
    def test_wrong_class_of_the_named_tyre_is_replaced(self, sentence: str) -> None:
        assert _fired(sentence) == {RULE_EU_CLASS}
        assert _judge(sentence) == REPLACEMENTS[RULE_EU_CLASS]

    @pytest.mark.parametrize(
        "sentence",
        [
            "Bridgestone Blizzak 6 має клас B по мокрій дорозі.",
            "Bridgestone Blizzak 6 — категорія C економії палива.",
            "У Bridgestone Blizzak 6 класс сцепления на мокрой дороге B.",
            "Nokian Hakkapeliitta 10 має клас D за економією пального.",
            # Two axes, one letter matching either — no guessing.
            "Bridgestone Blizzak 6 має клас B на мокрій та C за економією.",
            # A model letter is not a class letter: «A702», «G Fit EQ».
            "Aplus A702 має клас C по мокрій дорозі.",
            "Laufenn G Fit EQ має клас B по мокрій дорозі.",
        ],
    )
    def test_right_class_passes(self, sentence: str) -> None:
        assert _fired(sentence) == set()
        assert _judge(sentence) == sentence

    def test_wet_axis_is_checked_against_wet_not_fuel(self) -> None:
        # Blizzak: fuel C, wet B — «C по мокрій» is the fuel letter on the wet axis.
        sentence = "Bridgestone Blizzak 6 має клас C по мокрій дорозі."
        assert _fired(sentence) == {RULE_EU_CLASS}

    def test_letter_matching_neither_of_two_axes_is_replaced(self) -> None:
        sentence = "Bridgestone Blizzak 6 має клас A на мокрій та A за економією."
        assert _fired(sentence) == {RULE_EU_CLASS}

    @pytest.mark.parametrize("db", ["72 дБ", "70 дБ", "73дБ", "69 децибел"])
    def test_wrong_noise_db_is_replaced(self, db: str) -> None:
        sentence = f"Bridgestone Blizzak 6 тиха — всього {db}."
        assert _fired(sentence) == {RULE_EU_CLASS}
        assert _judge(sentence) == REPLACEMENTS[RULE_EU_CLASS]

    @pytest.mark.parametrize("db", ["71 дБ", "71дБ", "71 dB", "71,0 дБ"])
    def test_right_noise_db_passes(self, db: str) -> None:
        sentence = f"Bridgestone Blizzak 6 тиха — шум {db}."
        assert _judge(sentence) == sentence

    @pytest.mark.parametrize(
        "sentence",
        [
            "Firestone WinterHawk 4 має клас A по мокрій дорозі.",
            "Firestone WinterHawk 4 тиха — всього 68 дБ.",
        ],
    )
    def test_tyre_without_label_any_class_said_is_made_up(self, sentence: str) -> None:
        assert _fired(sentence) == {RULE_EU_CLASS}
        assert _judge(sentence) == REPLACEMENTS[RULE_EU_CLASS]

    # ── noise class (``eu_label.noise_class`` = tire_eu_labels.noise_class) ──

    @pytest.mark.parametrize(
        "sentence",
        [
            "Bridgestone Blizzak 6 має клас шуму B.",
            # Cyrillic twin after «класу».
            "Bridgestone Blizzak 6 має шум класу В.",
            "У Bridgestone Blizzak 6 класс шума B.",
            "Aplus A702 — клас шуму A, 70 дБ.",
            # Two axes, one letter matching either: wet B / noise B, fuel C / noise B.
            "Bridgestone Blizzak 6 має клас B по мокрій і за шумом.",
            "Bridgestone Blizzak 6 має клас C за економією та шумом.",
        ],
    )
    def test_right_noise_class_passes(self, sentence: str) -> None:
        assert _fired(sentence) == set()
        assert _judge(sentence) == sentence

    @pytest.mark.parametrize(
        "sentence",
        [
            "Bridgestone Blizzak 6 має клас шуму A.",
            # C is Blizzak's fuel letter, B its wet one — noise is checked
            # against ``noise_class`` alone.
            "Bridgestone Blizzak 6 має клас шуму C.",
            # Aplus: fuel C, wet C, noise A.
            "Aplus A702 — клас шуму C.",
            "Aplus A702 має клас шуму B.",
        ],
    )
    def test_wrong_noise_class_is_replaced(self, sentence: str) -> None:
        assert _fired(sentence) == {RULE_EU_CLASS}
        assert _judge(sentence) == REPLACEMENTS[RULE_EU_CLASS]

    @pytest.mark.parametrize(
        "sentence",
        [
            # Hakkapeliitta: fuel D, wet C, noise_db 72, no ``noise_class``.
            "Nokian Hakkapeliitta 10 має клас шуму C.",
            "Nokian Hakkapeliitta 10 має клас шуму D.",
            # No label at all.
            "Firestone WinterHawk 4 має клас шуму A.",
        ],
    )
    def test_noise_class_without_the_value_is_made_up(self, sentence: str) -> None:
        assert _fired(sentence) == {RULE_EU_CLASS}

    def test_right_noise_class_does_not_excuse_a_wrong_db(self) -> None:
        assert _fired("Bridgestone Blizzak 6 — клас шуму B, 72 дБ.") == {RULE_EU_CLASS}

    @pytest.mark.parametrize(
        "sentence",
        [
            "Чи має Bridgestone Blizzak 6 клас шуму A?",
            "Bridgestone Blizzak 6 не має класу шуму A.",
            "Bridgestone Blizzak 6 тихіша, ніж модель з класом шуму C.",
        ],
    )
    def test_ambiguous_noise_class_sentence_is_left_alone(self, sentence: str) -> None:
        assert _fired(sentence) == set()

    @pytest.mark.parametrize(
        ("column", "fired"),
        [("B", set()), (" b ", set()), ("A", {RULE_EU_CLASS}), (None, {RULE_EU_CLASS})],
    )
    def test_noise_class_from_a_catalogue_row(self, column: str | None, fired: set[str]) -> None:
        """The item is ``StoreClient._tire_item`` over a row shaped like the SELECT."""
        from src.store_client.client import StoreClient

        row = {
            "id": "1",
            "brand": "Bridgestone",
            "model": "Blizzak 6",
            "size": "215/55 R16",
            "season": "winter",
            "price": 5200,
            "stock_quantity": 4,
            "runflat": False,
            "eu_fuel": "C",
            "eu_wet": "B",
            "eu_noise_db": 71,
            "eu_noise_class": column,
            "attr_studded": "no_ship",
            "attr_xl": None,
            "load_index": None,
            "speed_index": None,
            "desc_studded": False,
            "desc_studdable": False,
            "desc_xl": False,
        }
        state = TireClaimState(sales_enabled=True)
        state.note("search_tires", {"total": 1, "items": [StoreClient._tire_item(row)]})
        assert _fired("Bridgestone Blizzak 6 має клас шуму B.", state) == fired

    def test_she_refers_to_the_tyre_named_before(self) -> None:
        text = "Раджу Bridgestone Blizzak 6. Вона має клас A по мокрій."
        assert _judge(text) == f"Раджу Bridgestone Blizzak 6. {REPLACEMENTS[RULE_EU_CLASS]}"

    @pytest.mark.parametrize(
        "sentence",
        [
            # No tyre named, two named, another tyre referred to.
            "Шини з класом A по мокрій дорозі гальмують коротше.",
            "Bridgestone Blizzak 6 і Nokian Hakkapeliitta 10 мають клас A по мокрій.",
            "Bridgestone Blizzak 6 гірша за мокрою, ніж модель з класом A.",
            "Bridgestone Blizzak 6 поступається першій по мокрій (клас A).",
            # Negation, question, condition.
            "Bridgestone Blizzak 6 не має класу A по мокрій.",
            "Чи має Bridgestone Blizzak 6 клас A по мокрій?",
            "Bridgestone Blizzak 6 має клас A по мокрій?",
            "Якщо треба клас A по мокрій, Bridgestone Blizzak 6 не підійде.",
            # A letter with no axis, a dB range.
            "Bridgestone Blizzak 6 має клас A.",
            "Bridgestone Blizzak 6: шум 70–72 дБ.",
            "Bridgestone Blizzak 6: шум 70 – 74 дБ.",
        ],
    )
    def test_ambiguous_sentence_is_left_alone(self, sentence: str) -> None:
        assert _fired(sentence) == set()
        assert _judge(sentence) == sentence

    def test_compare_phrase_of_the_code_passes(self) -> None:
        # `tyre_compare._item_clause` shape — if it ever reached the guard,
        # true values pass.
        phrase = "Bridgestone Blizzak 6: мокра дорога B, економія палива C, шум 71 дБ."
        assert _judge(phrase) == phrase


# ── stud ─────────────────────────────────────────────────────────────────


class TestStud:
    @pytest.mark.parametrize(
        "sentence",
        [
            # friction said studded.
            STUD_LIE,
            "Bridgestone Blizzak 6 має шипи і добре тримає лід.",
            "У Bridgestone Blizzak 6 есть шипы.",
            "Bridgestone Blizzak 6 з шипами впевнено тримає на льоду.",
            # studdable said studded.
            "Firestone WinterHawk 4 — шипована шина.",
            # studded said friction.
            "Nokian Hakkapeliitta 10 — це липучка для міста.",
            "Nokian Hakkapeliitta 10 продається без шипів.",
            "Nokian Hakkapeliitta 10 — фрикційна модель.",
            "Nokian Hakkapeliitta 10 — нешипована шина.",
            # «під шип» about a tyre that is not studdable.
            "Bridgestone Blizzak 6 іде під шип.",
            "Nokian Hakkapeliitta 10 — під шип.",
        ],
    )
    def test_wrong_stud_claim_is_replaced(self, sentence: str) -> None:
        assert _fired(sentence) == {RULE_STUD}
        assert _judge(sentence) == REPLACEMENTS[RULE_STUD]

    @pytest.mark.parametrize(
        "sentence",
        [
            "Nokian Hakkapeliitta 10 — шипована модель для льоду.",
            "Nokian Hakkapeliitta 10 має шипи.",
            "Bridgestone Blizzak 6 — липучка для міста.",
            "Bridgestone Blizzak 6 продається без шипів.",
            "Firestone WinterHawk 4 іде під шип.",
            # A studdable tyre is sold without studs.
            "Firestone WinterHawk 4 продається без шипів.",
        ],
    )
    def test_right_stud_claim_passes(self, sentence: str) -> None:
        assert _fired(sentence) == set()
        assert _judge(sentence) == sentence

    def test_studdable_is_not_studded(self) -> None:
        assert _fired("Firestone WinterHawk 4 має шипи.") == {RULE_STUD}

    def test_she_refers_to_the_tyre_named_before(self) -> None:
        text = "Раджу Bridgestone Blizzak 6. Вона шипована."
        assert _judge(text) == f"Раджу Bridgestone Blizzak 6. {REPLACEMENTS[RULE_STUD]}"

    @pytest.mark.parametrize(
        "sentence",
        [
            # Question, condition, negation not part of a form.
            "Чи вона шипована?",
            "Bridgestone Blizzak 6 шипована?",
            "Bridgestone Blizzak 6 шиповані чи липучка, уточніть.",
            "Уточню, чи Bridgestone Blizzak 6 шипована.",
            "Bridgestone Blizzak 6 не має шипів.",
            # No tyre, two tyres, another tyre.
            "Шиповані шини краще тримають на льоду.",
            "Bridgestone Blizzak 6 і Nokian Hakkapeliitta 10 мають шипи.",
            "Bridgestone Blizzak 6 — хороша альтернатива шипованим.",
            # «шини», «шиномонтаж» — not «шип».
            "Bridgestone Blizzak 6 — хороші шини для зими.",
        ],
    )
    def test_ambiguous_sentence_is_left_alone(self, sentence: str) -> None:
        assert _fired(sentence) == set()
        assert _judge(sentence) == sentence

    def test_summer_tyre_without_studded_is_not_judged(self) -> None:
        sentence = "Michelin Primacy 4 — шипована шина."
        assert _fired(sentence) == set()
        assert _judge(sentence) == sentence


# ── the switch, sales scope, the stream ──────────────────────────────────


@pytest.mark.parametrize("value", ["0", "off"])
def test_switch_off_silences_both_rules(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv(guard.ENV_FLAG, value)
    text = f"{EU_LIE} {STUD_LIE}"
    assert _fired(text) == set()
    assert _judge(text) == text


def test_sales_off_judges_nothing() -> None:
    text = f"{EU_LIE} {STUD_LIE}"
    assert _judge(text, _state(sales=False)) == text


def test_both_rules_in_one_reply_keep_the_rest() -> None:
    text = f"{EU_LIE} {STUD_LIE} Ціна 5200 гривень."
    assert _judge(text) == (
        f"{REPLACEMENTS[RULE_EU_CLASS]} {REPLACEMENTS[RULE_STUD]} Ціна 5200 гривень."
    )


def test_stream_replaces_the_sentence() -> None:
    async def _gen():
        for text in ["Bridgestone Blizzak 6 —", "шипована модель для льоду.", "Ціна 5200."]:
            yield SentenceReady(text=text)

    async def _run() -> list[str]:
        return [e.text async for e in guard_tire_claims(_gen(), _state(), ASKED)]

    assert asyncio.run(_run()) == [REPLACEMENTS[RULE_STUD], "Ціна 5200."]
