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


#: The truth said in place of a refused sentence (wave 10-X), from ``studded``.
BLIZZAK_TRUTH = "Без шипів — Bridgestone Blizzak 6."
HAKKA_TRUTH = "Шиповані — Nokian Hakkapeliitta 10."
WINTERHAWK_TRUTH = "Під шип — Firestone WinterHawk 4."


class TestStud:
    @pytest.mark.parametrize(
        ("sentence", "truth"),
        [
            # friction said studded.
            (STUD_LIE, BLIZZAK_TRUTH),
            ("Bridgestone Blizzak 6 має шипи і добре тримає лід.", BLIZZAK_TRUTH),
            ("У Bridgestone Blizzak 6 есть шипы.", BLIZZAK_TRUTH),
            ("Bridgestone Blizzak 6 з шипами впевнено тримає на льоду.", BLIZZAK_TRUTH),
            # studdable said studded.
            ("Firestone WinterHawk 4 — шипована шина.", WINTERHAWK_TRUTH),
            # studded said friction.
            ("Nokian Hakkapeliitta 10 — це липучка для міста.", HAKKA_TRUTH),
            ("Nokian Hakkapeliitta 10 продається без шипів.", HAKKA_TRUTH),
            ("Nokian Hakkapeliitta 10 — фрикційна модель.", HAKKA_TRUTH),
            ("Nokian Hakkapeliitta 10 — нешипована шина.", HAKKA_TRUTH),
            # «під шип» about a tyre that is not studdable.
            ("Bridgestone Blizzak 6 іде під шип.", BLIZZAK_TRUTH),
            ("Nokian Hakkapeliitta 10 — під шип.", HAKKA_TRUTH),
            # Two tyres, one group (wave 9-W; was silent): Blizzak is friction.
            # The truth names both, studded first.
            (
                "Bridgestone Blizzak 6 і Nokian Hakkapeliitta 10 мають шипи.",
                f"{HAKKA_TRUTH} {BLIZZAK_TRUTH}",
            ),
        ],
    )
    def test_wrong_stud_claim_is_replaced_by_the_truth(self, sentence: str, truth: str) -> None:
        assert _fired(sentence) == {RULE_STUD}
        assert _judge(sentence) == truth

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
        assert _judge(text) == f"Раджу Bridgestone Blizzak 6. {BLIZZAK_TRUTH}"

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


# ── stud: a list of tyres (wave 9-W) ─────────────────────────────────────

GROUP_ITEMS = [
    _item("g1", "Bridgestone", "Blizzak 6", 5200, studded="friction"),
    _item("g2", "Nokian", "Nordman 8", 4300, studded="studded"),
    _item("g3", "Firestone", "WinterHawk 4", 4100, studded="studdable"),
    _item("g4", "Firestone", "Ice Cruiser 7", 4500, studded="studded"),
    _item("g5", "Michelin", "Primacy 4", 4800, season="summer"),
]

#: The goldset 3 reply (PK, 2026-10-26): WinterHawk 4 is studdable.
GROUP_LIE = (
    "Шиповані — Nokian Nordman 8 і Firestone WinterHawk 4 (шиповані можна додатково "
    "ставити), а Bridgestone Blizzak 6 — без шипів."
)


def _group_state() -> TireClaimState:
    state = TireClaimState(sales_enabled=True)
    state.note("search_tires", {"total": len(GROUP_ITEMS), "items": copy.deepcopy(GROUP_ITEMS)})
    return state


NORDMAN = "Nokian Nordman 8"
ICE = "Firestone Ice Cruiser 7"
WINTERHAWK = "Firestone WinterHawk 4"
BLIZZAK = "Bridgestone Blizzak 6"
#: The goldset 4 truth (10-26): every tyre GROUP_LIE names, studded first.
GROUP_TRUTH = f"Шиповані — {NORDMAN}. Під шип — {WINTERHAWK}. Без шипів — {BLIZZAK}."


class TestStudGroups:
    @pytest.mark.parametrize(
        ("sentence", "truth"),
        [
            (GROUP_LIE, GROUP_TRUTH),
            # The aside in brackets is dropped: alone it would make WinterHawk right.
            (
                "Шиповані — Firestone WinterHawk 4 і Nokian Nordman 8 (під шип можна ставити).",
                f"Шиповані — {NORDMAN}. Під шип — {WINTERHAWK}.",
            ),
            ("Nordman 8 і Blizzak 6 шиповані.", f"Шиповані — {NORDMAN}. Без шипів — {BLIZZAK}."),
            (
                "Nokian Nordman 8 та Bridgestone Blizzak 6 мають шипи.",
                f"Шиповані — {NORDMAN}. Без шипів — {BLIZZAK}.",
            ),
            (
                "Без шипів — Blizzak 6, а WinterHawk 4 шипована.",
                f"Під шип — {WINTERHAWK}. Без шипів — {BLIZZAK}.",
            ),
            # The wrong tyre is last of the list, not first; two of a kind are listed.
            (
                "Шиповані — Nordman 8, Ice Cruiser 7 та Blizzak 6.",
                f"Шиповані — {NORDMAN} і {ICE}. Без шипів — {BLIZZAK}.",
            ),
            # Primacy 4 has no ``studded``: not in the truth.
            (
                "Шиповані — Nordman 8 і Blizzak 6, а Primacy 4 — літня.",
                f"Шиповані — {NORDMAN}. Без шипів — {BLIZZAK}.",
            ),
            # The first group is right, the second (after «але» / «тоді як») is not.
            (
                "Nordman 8 і Ice Cruiser 7 шиповані, але Nordman 8 — липучка.",
                f"Шиповані — {NORDMAN} і {ICE}.",
            ),
            (
                "Blizzak 6 — липучка, тоді як WinterHawk 4 і Ice Cruiser 7 — під шип.",
                f"Шиповані — {ICE}. Під шип — {WINTERHAWK}. Без шипів — {BLIZZAK}.",
            ),
            (
                "Під шип — Nordman 8 і WinterHawk 4.",
                f"Шиповані — {NORDMAN}. Під шип — {WINTERHAWK}.",
            ),
            # «, а також» goes on with the list: Blizzak 6 is in it.
            (
                "Шиповані — Nordman 8, а також Blizzak 6.",
                f"Шиповані — {NORDMAN}. Без шипів — {BLIZZAK}.",
            ),
            ("Шиповані — Bridgestone Blizzak 6 і Michelin Primacy 4.", f"Без шипів — {BLIZZAK}."),
        ],
    )
    def test_wrong_kind_of_any_tyre_in_a_group_is_replaced_by_the_truth(
        self, sentence: str, truth: str
    ) -> None:
        state = _group_state()
        assert _fired(sentence, state) == {RULE_STUD}
        assert _judge(sentence, _group_state()) == truth

    @pytest.mark.parametrize(
        "sentence",
        [
            # The same reply, WinterHawk 4 put where it belongs.
            "Шиповані — Nokian Nordman 8, а Firestone WinterHawk 4 — під шип (шиповані "
            "можна додатково ставити), а Bridgestone Blizzak 6 — без шипів.",
            "Шиповані — Nokian Nordman 8 і Firestone Ice Cruiser 7.",
            "Без шипів — Blizzak 6, а Nordman 8 шипована.",
            "Nordman 8 і Ice Cruiser 7 шиповані, тоді як Blizzak 6 — липучка.",
            "Без шипів продаються Blizzak 6 і WinterHawk 4.",
            "Під шип — WinterHawk 4, а Nordman 8 і Ice Cruiser 7 вже з шипами.",
            # A tyre without ``studded`` (summer) in the group is not judged.
            "Шиповані — Nokian Nordman 8 і Michelin Primacy 4.",
            # «, а також» goes on with the list.
            "Шиповані — Nordman 8, а також Ice Cruiser 7.",
            # A tyre named only in the brackets is not judged.
            "Шиповані — Nordman 8 і Ice Cruiser 7 (Blizzak 6 — ні).",
        ],
    )
    def test_right_kind_of_every_tyre_passes(self, sentence: str) -> None:
        assert _fired(sentence, _group_state()) == set()
        assert _judge(sentence, _group_state()) == sentence

    @pytest.mark.parametrize(
        "sentence",
        [
            # Tyres split by other words — which ones are «шипована»?
            "Nordman 8 шипована, також є Blizzak 6 і WinterHawk 4.",
            "Nordman 8 і Ice Cruiser 7 мають шипи, на відміну від Blizzak 6.",
            # A kind with no tyres of its own.
            "Nordman 8 і Blizzak 6, а шиповані — ні.",
            # ... beside a group that would be judged on its own.
            "Без шипів — Blizzak 6 і Nordman 8, а шиповані — ні.",
            # Two kinds in one group.
            "Nordman 8 і Blizzak 6 шиповані чи липучки.",
            "Nordman 8 і Blizzak 6 — шиповані і липучки.",
            # A comparison.
            "Шиповані шини краще за Nordman 8 і Blizzak 6.",
            # A question, a negation not part of a form, a condition.
            "Nordman 8 і Blizzak 6 шиповані?",
            "Nordman 8 і Blizzak 6 не мають шипів.",
            "Якщо треба шиповані — Nordman 8 і Blizzak 6.",
            # Brands alone: which models are meant is a guess.
            "Шиповані — Nokian і Bridgestone.",
            # «обидві» — `_OTHER_REFERENT`, as for one tyre.
            "Nordman 8 і Blizzak 6 — обидві шиповані.",
        ],
    )
    def test_sentence_that_does_not_cut_cleanly_is_left_alone(self, sentence: str) -> None:
        assert _fired(sentence, _group_state()) == set()
        assert _judge(sentence, _group_state()) == sentence

    def test_one_named_tyre_keeps_the_old_path(self) -> None:
        state = _group_state()
        assert _fired("Bridgestone Blizzak 6 — шипована модель.", state) == {RULE_STUD}
        assert _fired("Nokian Nordman 8 — шипована модель.", _group_state()) == set()
        # One tyre is never handed to the group branch (`_rule_stud_groups` needs two).
        assert _fired("Firestone WinterHawk 4 — під шип.", _group_state()) == set()

    def test_rest_of_the_reply_is_kept(self) -> None:
        text = f"{GROUP_LIE} Ціна від 4100 гривень."
        assert _judge(text, _group_state()) == f"{GROUP_TRUTH} Ціна від 4100 гривень."

    def test_sales_off_judges_no_group(self) -> None:
        state = TireClaimState(sales_enabled=False)
        state.note("search_tires", {"items": copy.deepcopy(GROUP_ITEMS)})
        assert _fired(GROUP_LIE, state) == set()


# ── stud: a list told tyre by tyre, at its commas (wave 11-Y) ────────────

#: The goldset 5 replies (10-26, both networks): WinterHawk 4 is studdable.
COMMA_LIE = (
    "Є Bridgestone Blizzak 6 без шипів за 4150 гривень, Firestone WinterHawk 4 шиповані "
    "за 3300 гривень, Nokian Nordman 8 шиповані за 3600 гривень."
)
COMMA_HEDGED_LIE = (
    "Шипована Nokian Nordman 8 за 3600 гривень, Firestone WinterHawk 4 — шипована можливо, "
    "Bridgestone Blizzak 6 — без шипів."
)


class TestStudCommaList:
    @pytest.mark.parametrize(
        ("sentence", "truth"),
        [
            (COMMA_LIE, GROUP_TRUTH),
            # «можливо» does not excuse the part: the truth comes from the result.
            (COMMA_HEDGED_LIE, GROUP_TRUTH),
            (
                "Nordman 8 шипована, Blizzak 6 шипована.",
                f"Шиповані — {NORDMAN}. Без шипів — {BLIZZAK}.",
            ),
            (
                "Blizzak 6 — мабуть шипована, WinterHawk 4 під шип.",
                f"Під шип — {WINTERHAWK}. Без шипів — {BLIZZAK}.",
            ),
            # Primacy 4 has no ``studded``: its part is not judged, Blizzak 6's is.
            ("Blizzak 6 шипована, Primacy 4 шипована.", f"Без шипів — {BLIZZAK}."),
        ],
    )
    def test_wrong_kind_of_any_tyre_is_replaced_by_the_truth(
        self, sentence: str, truth: str
    ) -> None:
        assert _fired(sentence, _group_state()) == {RULE_STUD}
        assert _judge(sentence, _group_state()) == truth

    @pytest.mark.parametrize(
        "sentence",
        [
            # The goldset form, every kind right.
            "Є Bridgestone Blizzak 6 без шипів за 4150 гривень, Firestone WinterHawk 4 під шип "
            "за 3300 гривень, Nokian Nordman 8 шиповані за 3600 гривень.",
            "Шипована Nokian Nordman 8 за 3600 гривень, Firestone WinterHawk 4 — під шип "
            "можливо, Bridgestone Blizzak 6 — без шипів.",
            # «Без шипів» is true of a studdable tyre: it is sold without studs.
            "WinterHawk 4 без шипів, Nordman 8 шиповані.",
            "Nordman 8 шипована, Primacy 4 шипована.",
        ],
    )
    def test_right_kind_of_every_tyre_passes(self, sentence: str) -> None:
        assert _fired(sentence, _group_state()) == set()
        assert _judge(sentence, _group_state()) == sentence

    @pytest.mark.parametrize(
        "sentence",
        [
            # Every sentence lies about Blizzak 6; one unclear part cancels the whole cut.
            # A part with no tyre.
            "Blizzak 6 шипована, за 4150 гривень, Nordman 8 шипована.",
            "Blizzak 6 шипована, а ще, Nordman 8 шипована.",
            # A part with two tyres.
            "Blizzak 6 шипована, Nordman 8 і Ice Cruiser 7 шиповані.",
            # A part with no kind.
            "Blizzak 6 шипована, Nordman 8 за 3600 гривень, Ice Cruiser 7 шипована.",
            # A part with two kinds.
            "Blizzak 6 шипована, Nordman 8 шипована або без шипів.",
            # A brand alone: which model is meant is a guess.
            "Blizzak 6 шипована, Nokian шипована.",
            # A condition, a comparison in a part.
            "Blizzak 6 шипована, якщо Nordman 8 шипована.",
            "Blizzak 6 шипована, Nordman 8 краще шипована.",
            # A question.
            "Blizzak 6 шипована, Nordman 8 шипована?",
        ],
    )
    def test_part_that_is_not_clean_cancels_the_cut(self, sentence: str) -> None:
        assert _fired(sentence, _group_state()) == set()
        assert _judge(sentence, _group_state()) == sentence

    def test_w_chain_keeps_the_group_path(self) -> None:
        # «X, Y та Z шиповані» is one group of one kind (W), not three parts.
        sentence = "Nordman 8, Ice Cruiser 7 та Blizzak 6 шиповані."
        assert _fired(sentence, _group_state()) == {RULE_STUD}
        assert _judge(sentence, _group_state()) == (
            f"Шиповані — {NORDMAN} і {ICE}. Без шипів — {BLIZZAK}."
        )
        assert _fired("Nordman 8, Ice Cruiser 7 та WinterHawk 4 з шипами.", _group_state()) == {
            RULE_STUD
        }

    def test_one_tyre_keeps_the_old_path(self) -> None:
        sentence = "Bridgestone Blizzak 6 шипована, за 4150 гривень."
        assert _fired(sentence, _group_state()) == {RULE_STUD}
        assert _judge(sentence, _group_state()) == f"Без шипів — {BLIZZAK}."

    def test_rest_of_the_reply_is_kept(self) -> None:
        text = f"Ось що є. {COMMA_LIE} Ціна від 3300 гривень."
        assert _judge(text, _group_state()) == f"Ось що є. {GROUP_TRUTH} Ціна від 3300 гривень."

    def test_sales_off_judges_no_list(self) -> None:
        state = TireClaimState(sales_enabled=False)
        state.note("search_tires", {"items": copy.deepcopy(GROUP_ITEMS)})
        for sentence in (COMMA_LIE, COMMA_HEDGED_LIE):
            assert _fired(sentence, state) == set()
            assert _judge(sentence, state) == sentence


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
    assert _judge(text) == f"{REPLACEMENTS[RULE_EU_CLASS]} {BLIZZAK_TRUTH} Ціна 5200 гривень."


def test_stream_replaces_the_sentence() -> None:
    async def _gen():
        for text in ["Bridgestone Blizzak 6 —", "шипована модель для льоду.", "Ціна 5200."]:
            yield SentenceReady(text=text)

    async def _run() -> list[str]:
        return [e.text async for e in guard_tire_claims(_gen(), _state(), ASKED)]

    assert asyncio.run(_run()) == [BLIZZAK_TRUTH, "Ціна 5200."]


def test_stream_says_the_same_truth_once() -> None:
    async def _gen():
        for text in [STUD_LIE, "Ціна 5200.", "Bridgestone Blizzak 6 має шипи."]:
            yield SentenceReady(text=text)

    async def _run() -> list[str]:
        return [e.text async for e in guard_tire_claims(_gen(), _state(), ASKED)]

    before = _count(RULE_STUD)
    assert asyncio.run(_run()) == [BLIZZAK_TRUTH, "Ціна 5200."]
    assert _count(RULE_STUD) == before + 2


# ── stud: the truth in place of the lie (wave 10-X) ──────────────────────


class TestStudTruth:
    def test_goldset_reply_gets_the_truth_about_all_three(self) -> None:
        assert _fired(GROUP_LIE, _group_state()) == {RULE_STUD}
        truth = _judge(GROUP_LIE, _group_state())
        assert truth == GROUP_TRUTH
        # Only names and kinds: no price, no advice, nothing in Russian.
        assert REPLACEMENTS[RULE_STUD] not in truth
        assert not any(w in truth for w in ("грн", "раджу", "рекоменд", "шипы", "без шипов"))

    def test_one_tyre_lie_gets_its_kind(self) -> None:
        sentence = "Firestone WinterHawk 4 шипована."
        assert _fired(sentence, _group_state()) == {RULE_STUD}
        assert _judge(sentence, _group_state()) == WINTERHAWK_TRUTH

    def test_one_name_with_two_kinds_falls_back_to_the_neutral_sentence(self) -> None:
        # Two sizes of one model, the result says studded for one, friction for
        # the other: which is true of «Nordman 8» is a guess.
        items = [*copy.deepcopy(GROUP_ITEMS), _item("g6", "Nokian", "Nordman 8", 4400)]
        items[-1]["size"] = "205/55 R16"
        items[-1]["studded"] = "friction"
        state = TireClaimState(sales_enabled=True)
        state.note("search_tires", {"items": items})
        sentence = "Nokian Nordman 8 — липучка."
        assert _judge(sentence, state) == REPLACEMENTS[RULE_STUD]

    def test_neutral_sentence_is_said_once(self) -> None:
        items = [*copy.deepcopy(GROUP_ITEMS), _item("g6", "Nokian", "Nordman 8", 4400)]
        items[-1]["studded"] = "friction"
        state = TireClaimState(sales_enabled=True)
        state.note("search_tires", {"items": items})
        text = "Nokian Nordman 8 — липучка. Nokian Nordman 8 продається без шипів."
        assert _judge(text, state) == REPLACEMENTS[RULE_STUD]

    def test_same_truth_is_said_once_in_the_text_path(self) -> None:
        # The text path has no `drop_repeated_sentences`: the rule itself
        # drops the second lie, the truth was already said.
        text = f"{STUD_LIE} Ціна 5200 гривень. Bridgestone Blizzak 6 має шипи."
        before = _count(RULE_STUD)
        assert _judge(text) == f"{BLIZZAK_TRUTH} Ціна 5200 гривень."
        assert _count(RULE_STUD) == before + 2

    def test_different_truths_are_both_said(self) -> None:
        text = f"{STUD_LIE} Firestone WinterHawk 4 — шипована шина."
        assert _judge(text) == f"{BLIZZAK_TRUTH} {WINTERHAWK_TRUTH}"

    @pytest.mark.parametrize(
        "sentence",
        [
            # The aside is the only stud word: nothing claimed (was refused).
            "Firestone WinterHawk 4 (шиповані можна додатково ставити).",
            "Firestone WinterHawk 4 — під шип (шиповані можна додатково ставити).",
            # A negation inside the aside — unclear, silent.
            "Firestone WinterHawk 4 шипована (не всі розміри).",
            "Firestone WinterHawk 4 шипована (без шипів теж є).",
        ],
    )
    def test_one_tyre_aside_is_not_the_claim(self, sentence: str) -> None:
        assert _fired(sentence, _group_state()) == set()
        assert _judge(sentence, _group_state()) == sentence

    def test_one_tyre_claim_outside_the_aside_is_judged(self) -> None:
        sentence = "Firestone WinterHawk 4 шипована (під шип)."
        assert _fired(sentence, _group_state()) == {RULE_STUD}
        assert _judge(sentence, _group_state()) == WINTERHAWK_TRUTH

    def test_sales_off_says_no_truth(self) -> None:
        state = TireClaimState(sales_enabled=False)
        state.note("search_tires", {"items": copy.deepcopy(GROUP_ITEMS)})
        sentence = "Firestone WinterHawk 4 шипована (під шип)."
        assert _fired(sentence, state) == set()
        assert _judge(sentence, state) == sentence
