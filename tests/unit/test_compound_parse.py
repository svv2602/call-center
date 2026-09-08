"""Tests for `src.agent.compound_parse` (Wave 2-B of the FSM refactor).

The utterances in `TestSTTGarbage` are verbatim transcripts from production
calls, quoted in the anti-pattern blocks of `src/agent/prompts.py` (calls
c9ab41f8, 7afbc049, 1b6721a4, 18e96042 and the 2026-08-28 «Лукяненко» call).
They are the reason this module cannot rely on clean input.
"""

from __future__ import annotations

import time

import pytest

from src.agent.compound_parse import (
    APPLY_THRESHOLD,
    FIELD_KEYS,
    CompoundParseResult,
    compound_parse,
    compound_parse_with_intent,
)


def parse(text: str) -> CompoundParseResult:
    """Parse and assert the result never invents a field key."""
    result = compound_parse(text)
    unknown = set(result.fields) - set(FIELD_KEYS)
    assert not unknown, f"compound_parse returned off-contract keys: {unknown}"
    assert set(result.fields) == set(result.fields_confidence), (
        "every field must carry a confidence"
    )
    assert result.intent_hint is None, "intent is Wave 3-A, not compound_parse"
    return result


class TestSingleField:
    """One utterance, one field."""

    def test_diameter_only_with_r_prefix(self):
        result = parse("R18")
        assert result.fields == {"diameter": 18}

    def test_city_only(self):
        result = parse("Дніпро")
        assert result.fields == {"city": "Дніпро"}

    def test_color_only_inflected(self):
        # «Червоны» — the STT form that broke the substring matcher in call
        # dd3dd368 and that `color_detect` was rewritten to handle.
        result = parse("Червоны")
        assert result.fields == {"color": "червоний"}

    def test_brand_only(self):
        result = parse("Фольксваген")
        assert result.fields == {"brand": "Volkswagen"}

    def test_date_only_relative(self):
        result = parse("завтра")
        assert result.fields == {"date_hint": "завтра"}


class TestCompoundTwoFields:
    """Two fields in one breath — the case the FSM's auto-skip exists for."""

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("шиномонтаж R18 в Дніпрі", {"city": "Дніпро", "diameter": 18}),
            ("синій BMW", {"color": "синій", "brand": "BMW"}),
            ("на завтра в Києві", {"city": "Київ", "date_hint": "завтра"}),
            ("R16 у Харкові", {"city": "Харків", "diameter": 16}),
            ("білий Nissan", {"color": "білий", "brand": "Nissan"}),
            (
                "на п'ятницю о 14:00",
                {"date_hint": "п'ятниця", "time_hint": "14:00"},
            ),
            (
                "Дніпро, Донецьке шосе",
                {"city": "Дніпро", "station_hint": "Донецьке шосе"},
            ),
            (
                "запишіть на понеділок, Запоріжжя",
                {"city": "Запоріжжя", "date_hint": "понеділок"},
            ),
        ],
    )
    def test_two_fields(self, text, expected):
        assert parse(text).fields == expected


class TestCompoundManyFields:
    """Three or more fields — verbose callers."""

    def test_colour_brand_diameter_landmark_weekday(self):
        result = parse("мій білий Toyota R17 на Оболоні в понеділок")
        assert result.fields == {
            # Оболонь is a Kyiv district; the prompt's landmark rule says it
            # pins Київ even when the profile city says otherwise.
            "city": "Київ",
            "station_hint": "Оболонь",
            "date_hint": "понеділок",
            "diameter": 17,
            "color": "білий",
            "brand": "Toyota",
        }

    def test_all_seven_fields(self):
        result = parse("мене звати Юра, R17, Київ, завтра о 14:00, білий Toyota")
        assert result.fields == {
            "name": "Юра",
            "city": "Київ",
            "date_hint": "завтра",
            "time_hint": "14:00",
            "diameter": 17,
            "color": "білий",
            "brand": "Toyota",
        }

    def test_name_intro_stops_at_punctuation(self):
        # `detect_name` rejects anything longer than two words, so the intro
        # capture must stop at the comma instead of handing it the sentence.
        result = parse("мене звати Олена, білий Nissan, Запоріжжя")
        assert result.fields["name"] == "Олена"
        assert result.fields["city"] == "Запоріжжя"
        assert result.fields["brand"] == "Nissan"

    def test_storage_phrase_does_not_break_location_and_date(self):
        result = parse("шиномонтаж, шини свої, післязавтра, Дніпро, Караван")
        assert result.fields == {
            "city": "Дніпро",
            "station_hint": "Караван",
            "date_hint": "післязавтра",
        }

    def test_explicit_city_outranks_landmark_city(self):
        # «в Дніпрі на Оболоні» is contradictory; the city the caller actually
        # named wins, and the cross-city guard deals with the rest.
        result = parse("в Дніпрі на Оболоні")
        assert result.fields["city"] == "Дніпро"
        assert result.fields["station_hint"] == "Оболонь"
        assert result.fields_confidence["city"] == 1.0


class TestSTTGarbage:
    """Verbatim production transcripts. Every one of these mangles the city."""

    @pytest.mark.parametrize(
        ("text", "field", "expected"),
        [
            # call 7afbc049 2026-09-01 — «підкажіть ціну монтажу у Дніпрі»
            ("здравствуйте Добрый день подскажите артист монтажу в Дніпре", "city", "Дніпро"),
            # call c9ab41f8 2026-09-01 — «ми хотіли б записатись на монтаж у Києві»
            ("мы не трогать надпись Тину на монтаж в Києве", "city", "Київ"),
            # prompts.py Krok 1 — «запис на шиномонтаж, місто Черкаси»
            ("запах на шиномонтаж место Черкассы", "city", "Черкаси"),
            # prompts.py — «можна дізнатися, зі скількох працює монтаж у Києві»
            ("мне знать Со скольки кошка монтаж в Києве", "city", "Київ"),
            # call 1b6721a4 2026-08-28 — Харківське ШОСЕ is in Kyiv, not Kharkiv
            ("Запишите на монтаж на харьковскому", "city", "Київ"),
        ],
    )
    def test_city_survives_stt_noise(self, text, field, expected):
        result = parse(text)
        assert result.fields.get(field) == expected
        assert result.fields_confidence[field] >= APPLY_THRESHOLD

    def test_kharkiv_highway_is_a_kyiv_landmark_not_the_city_kharkiv(self):
        result = parse("Запишите на монтаж на харьковскому")
        assert result.fields["station_hint"] == "Харківське шосе"
        assert result.fields["city"] == "Київ"

    def test_landmark_survives_surrounding_noise(self):
        # call 2026-08-28 — «На Лук'яненка, база на Викторова».
        result = parse("На Лукьяненко база но у Викторов")
        assert result.fields["station_hint"] == "Лукʼяненка"
        assert result.fields["city"] == "Київ"

    def test_mangled_city_stays_below_apply_threshold(self):
        # «запорище» / «затурища» / «за Париже» — the distortions of Запоріжжя
        # that had the bot insisting on Дніпро four turns running (2026-07-31).
        result = parse("запорище")
        assert result.fields["city"] == "Запоріжжя"
        assert result.fields_confidence["city"] < APPLY_THRESHOLD


class TestConfidence:
    """Unambiguous detections are 1.0; ambiguous ones stay below the threshold."""

    @pytest.mark.parametrize(
        ("text", "field"),
        [
            ("R18", "diameter"),  # explicit «R» marker
            ("у Києві", "city"),  # explicit city name
            ("на 14:20", "time_hint"),  # literal HH:MM
            ("завтра", "date_hint"),  # unambiguous relative day
        ],
    )
    def test_unambiguous_is_one(self, text, field):
        assert parse(text).fields_confidence[field] == 1.0

    def test_bare_number_is_ambiguous(self):
        # «на 16» is a diameter, an hour and a day of the month at once. Wave 12
        # added the backend diameter guard because the bot quoted a price off
        # exactly this kind of number (calls f70deab5, ebe7dfcb).
        result = parse("шина з собою, монтажу на 16 яка вартість")
        assert result.fields["diameter"] == 16
        assert result.fields_confidence["diameter"] < APPLY_THRESHOLD
        assert "diameter" not in result.confident_fields()

    def test_diameter_marker_lifts_the_same_number_to_one(self):
        result = parse("діаметр шістнадцять")
        assert result.fields["diameter"] == 16
        assert result.fields_confidence["diameter"] == 1.0
        assert result.confident_fields()["diameter"] == 16

    def test_name_needs_an_explicit_introduction(self):
        # Ungated, `detect_name` accepts «Ммм» and «Що?» as names. compound_parse
        # has no bot-utterance context to gate on, so it only reports a name the
        # caller actually introduced; the bare Krok 0 reply stays with the
        # pipeline's `is_name_question` path.
        assert "name" not in parse("Юра").fields
        assert "name" not in parse("Ммм").fields

        intro = parse("мене звати Юра")
        assert intro.fields["name"] == "Юра"
        assert intro.fields_confidence["name"] >= APPLY_THRESHOLD


class TestNoFields:
    """Nothing to extract — and nothing invented."""

    @pytest.mark.parametrize("text", ["", "   ", "\n\t "])
    def test_empty_input(self, text):
        result = parse(text)
        assert result.fields == {}
        assert result.fields_confidence == {}

    @pytest.mark.parametrize("text", ["алло алло", "ммм", "угу", "що?"])
    def test_pure_noise(self, text):
        assert parse(text).fields == {}

    def test_non_fitting_text(self):
        assert parse("хочу піцу з ананасами").fields == {}


class TestDiameterTimeDateArbitration:
    """The digits 13-24 collide across three fields; masking keeps them apart."""

    def test_day_of_month_is_not_a_diameter(self):
        result = parse("запишіть на 18 вересня")
        assert result.fields == {"date_hint": "18 вересня"}
        assert "diameter" not in result.fields

    def test_hour_with_noun_is_not_a_diameter(self):
        result = parse("на 16 годин")
        assert result.fields == {"time_hint": "16:00"}

    def test_feminine_ordinal_hour_is_not_a_diameter(self):
        # «о чотирнадцятій» vs «чотирнадцять» (R14) — the ending is the only
        # discriminator, and `detect_diameter` matches on the stem.
        result = parse("о чотирнадцятій годині")
        assert result.fields == {"time_hint": "14:00"}

    def test_twelve_hour_afternoon_reading(self):
        # A shop open 08:00-20:00 — «о шостій» is 18:00, never 06:00.
        assert parse("о шостій").fields["time_hint"] == "18:00"

    def test_r_prefix_survives_a_time_in_the_same_utterance(self):
        result = parse("R16 на п'ятницю о 14:00 в Харкові")
        assert result.fields == {
            "city": "Харків",
            "date_hint": "п'ятниця",
            "time_hint": "14:00",
            "diameter": 16,
        }

    def test_part_of_day_is_a_preference_not_a_time(self):
        result = parse("на понеділок після обіду")
        assert result.fields["time_hint"] == "обід"
        assert result.fields_confidence["time_hint"] < APPLY_THRESHOLD


class TestResultHelpers:
    def test_confident_fields_filters_by_threshold(self):
        result = parse("запорище R18")
        assert result.confident_fields() == {"diameter": 18}
        assert result.confident_fields(threshold=0.5)["city"] == "Запоріжжя"

    def test_is_fast(self):
        text = "мене звати Юра, R17, Київ, завтра о 14:00, білий Toyota на Оболоні"
        started = time.perf_counter()
        for _ in range(100):
            compound_parse(text)
        per_call_ms = (time.perf_counter() - started) * 1000 / 100
        assert per_call_ms < 10.0, f"compound_parse took {per_call_ms:.2f} ms"


class TestCompoundParseWithIntent:
    """The async wrapper. Wave 3-A injects the real classifier."""

    async def test_without_classifier_returns_fields_only(self):
        result = await compound_parse_with_intent("R18 в Дніпрі")
        assert result.fields == {"city": "Дніпро", "diameter": 18}
        assert result.intent_hint is None

    async def test_injected_classifier_sets_the_hint(self):
        async def classifier(text: str, router):
            return "BOOK"

        result = await compound_parse_with_intent("R18 в Дніпрі", None, classifier=classifier)
        assert result.intent_hint == "BOOK"
        assert result.fields["diameter"] == 18

    async def test_classifier_failure_degrades_to_fields(self, caplog):
        async def classifier(text: str, router):
            raise RuntimeError("router down")

        result = await compound_parse_with_intent("R18 в Дніпрі", None, classifier=classifier)
        assert result.intent_hint is None
        assert result.fields["diameter"] == 18
        # A swallowed failure must still be visible — silent suppression of a
        # broken dependency is what lost three bookings in `37fb2d0`.
        assert any(rec.levelname == "WARNING" for rec in caplog.records)
