"""`proposed_station` — was the bot's *pending* question a station choice?

The module answers half of «the caller said „так“ to a station». The other
half — which station — lives in `station_parser`, and this file deliberately
does not touch it.

Two properties are pinned here, and only the second one is interesting.

The easy one: a marker in the bot's latest turn means a proposal, and the two
lookalikes do not — the Krok 8 summary («перевіримо: …») names a station that
was already chosen, and a bare «записуємо» also opens a city question and a
date question.

The one that cost a rewrite: **the window is not flat**. Call `bd95036c` has a
real proposal marker at t7 («Продовжимо запис на цю точку?»), then a city-change
prompt at t9, then «підтверджую» at t10. A window that simply reads the last two
bot turns fires on t7 and pins the Дніпро station to a caller who is asking to
move to Черкаси. It refused on the corpus only because `compound_parse` happened
to read «Черкасах» out of an earlier turn and the city narrowing in
`station_parser` caught it — luck, not construction. So the tests below assert
the refusal *here*, in the detector, where no city is in scope at all.

The strings are transcript lines from the 2026-09-07..09 corpus, not invented
phrasings.
"""

from __future__ import annotations

import pytest

from src.agent.station_proposal_detect import (
    _PROPOSE_MARKERS,
    _REASK_MARKERS,
    proposed_station,
)

# --- real bot turns -------------------------------------------------------

PROPOSAL = "Знайшла точку біля Оболоні, на вулиці Маршала Тимошенка, 7. Записуємо туди?"
PRICE_THEN_PROPOSAL = (
    "Комплексний шиномонтаж R17 у місті Дніпро, провулок Добровольців, один де: "
    "легкові — триста дев'яносто шість гривень за колесо, позашляховики — "
    "чотириста тридцять вісім гривень за колесо. Записуємо на монтаж?"
)
CITY_CHANGE_PROMPT = (
    "Для зміни міста потрібне підтвердження. Підтвердіть, будь ласка, "
    "що замінюємо місто на Черкаси."
)
KEEP_THIS_STATION = (
    "Ваше місто зараз Дніпро. Перейдімо до Черкас, зараз знайду точки шиномонтажу "
    "в Черкасах. У вас у записі обрана точка шиномонтажу в Дніпрі, провулок "
    "Добровольців, 1де. Продовжимо запис на цю точку?"
)
KROK_8_SUMMARY = (
    "Наталя, перевіримо: дев'ятого вересня о 11:00, м. Дніпро, провулок "
    "Добровольців, один де, зелений Жигулі. Підтверджуєте?"
)
CITY_QUESTION = "У якому місті вам зручніше записатися на шиномонтаж?"
DATE_QUESTION = "На яку дату записуємо?"
NO_STATION_FOUND = 'За орієнтиром "на природе" точки не знайшла. Назвіть вулицю або район.'

REASK_YES_NO = (
    'Перепрошую, не розчула. Скажіть, будь ласка, "так" щоб підтвердити або "ні" щоб змінити.'
)
REASK_SILENCE = "Я на зв'язку. Якщо маєте запитання — я слухаю."
REASK_ALIVE = "Ви ще на лінії?"


class TestTheProposalIsRecognised:
    def test_marker_in_the_newest_turn(self) -> None:
        assert proposed_station([PROPOSAL]) is True

    def test_a_price_quote_that_ends_in_a_proposal_counts(self) -> None:
        """Call `011277ef` — the whole reason the wave is not STATION-only.

        The turn reads like a price answer and ends «Записуємо на монтаж?». It
        was written off as «just a price quote» once already, from output
        truncated before the last sentence.
        """
        assert proposed_station([PRICE_THEN_PROPOSAL]) is True

    @pytest.mark.parametrize("marker", _PROPOSE_MARKERS)
    def test_every_marker_is_reachable(self, marker: str) -> None:
        """No marker may be dead: a dead one is a claim nothing supports."""
        assert proposed_station([f"Отже, {marker}?"]) is True


class TestTheLookalikesAreRefused:
    """These must fail on the markers, not on a string exception."""

    def test_krok_8_summary_is_not_a_proposal(self) -> None:
        assert proposed_station([KROK_8_SUMMARY]) is False

    @pytest.mark.parametrize("turn", [CITY_QUESTION, DATE_QUESTION])
    def test_a_bare_zapysuyemo_question_is_not_a_proposal(self, turn: str) -> None:
        assert proposed_station([turn]) is False

    def test_the_failure_line_is_not_a_proposal(self) -> None:
        """«точки не знайшла» — genitive after a negation, so «знайшла точку»
        simply does not occur in it. No negation rule needed."""
        assert proposed_station([NO_STATION_FOUND]) is False

    @pytest.mark.parametrize("turns", [[], [""], ["", ""], [None]])
    def test_nothing_to_read_is_not_a_proposal(self, turns: list) -> None:
        assert proposed_station(turns) is False


class TestTheWindowStopsAtThePendingQuestion:
    """The property that makes the rule safe without help from the city."""

    def test_a_reask_is_crossed(self) -> None:
        """Call `2536a21d`: proposal, silence, re-ask, «так»."""
        assert proposed_station([REASK_YES_NO, PROPOSAL]) is True

    @pytest.mark.parametrize("reask", _REASK_MARKERS)
    def test_every_reask_shape_is_crossed(self, reask: str) -> None:
        assert proposed_station([reask, PROPOSAL]) is True

    def test_a_city_change_prompt_ends_the_search(self) -> None:
        """Call `bd95036c` t10 — the control the wave is built around.

        t7 really does carry a marker. The refusal must come from «t9 asked
        something of its own», which holds for any intervening question, not
        from recognising this particular sentence.
        """
        assert proposed_station([CITY_CHANGE_PROMPT, KEEP_THIS_STATION]) is False

    @pytest.mark.parametrize("intervening", [CITY_QUESTION, DATE_QUESTION, KROK_8_SUMMARY])
    def test_any_other_question_ends_the_search(self, intervening: str) -> None:
        assert proposed_station([intervening, PROPOSAL]) is False

    def test_the_stale_proposal_is_refused_even_though_the_marker_is_there(self) -> None:
        """States the failure of the flat window directly.

        A flat two-turn scan sees the marker in slot 2 and returns True; this
        must not.
        """
        turns = [CITY_CHANGE_PROMPT, KEEP_THIS_STATION]
        assert any(m in KEEP_THIS_STATION.lower() for m in _PROPOSE_MARKERS)
        assert proposed_station(turns) is False
