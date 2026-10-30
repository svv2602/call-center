"""Three parser defects found in the tshina consultant (ported 2026-10-30).

- tshina `61e1b14d3`: «не літні» set the summer season;
- tshina `131dad99f`: «китайські шини мене не цікавлять» did not refuse China;
- tshina `31516cd04`: «БМВ х3 22 року» read 22 as the rim diameter.
"""

from __future__ import annotations

import pytest

from src.agent.parsers.tire_query import excluded_tire_countries, parse_tire_size
from src.core.pipeline import merge_tire_query, parse_tire_season


class TestSeasonRefusal:
    @pytest.mark.parametrize(
        "text", ["не літні", "летние не надо", "літні не потрібні", "не зимові", "зимние — нет"]
    )
    def test_a_refused_season_is_not_named(self, text: str) -> None:
        assert parse_tire_season(text) is None

    @pytest.mark.parametrize(
        ("text", "season"),
        [
            ("мне не летние, а зимние", "winter"),
            ("зимові ні, літні", "summer"),
            ("літні, не дорогі", "summer"),
            ("не знаю, зимові", "winter"),
            ("зимние", "winter"),
        ],
    )
    def test_the_other_season_is_named(self, text: str, season: str) -> None:
        assert parse_tire_season(text) == season

    def test_a_refusal_does_not_overwrite_the_season(self) -> None:
        query = merge_tire_query({}, "зимові 205/55 R16", consult_started=True)
        query = merge_tire_query(query, "літні не потрібні", consult_started=True)
        assert query["season"] == "winter"


class TestCountryRefusalAfter:
    @pytest.mark.parametrize(
        "text",
        [
            "китайські шини мене не цікавлять",
            "китайцы меня особо не интересуют",
            "Китай не хочу",
            "китайские совсем не рассматриваю",
            "китайські не дуже цікавлять",
        ],
    )
    def test_refused(self, text: str) -> None:
        assert excluded_tire_countries(text) == ["Китай"]

    @pytest.mark.parametrize(
        "text", ["китайські не гірші?", "китайские не дорогие?", "китайські є?"]
    )
    def test_not_a_refusal(self, text: str) -> None:
        assert excluded_tire_countries(text) is None


class TestShortYear:
    @pytest.mark.parametrize(
        "text", ["БМВ х3 22 року", "Tiguan 18 року", "x5 22года", "18 г. выпуска"]
    )
    def test_a_two_digit_year_is_not_a_diameter(self, text: str) -> None:
        assert parse_tire_size(text) is None

    @pytest.mark.parametrize(
        ("text", "diameter"), [("диск 18", 18), ("на 17 радиус", 17), ("р18", 18)]
    )
    def test_a_diameter_stays(self, text: str, diameter: int) -> None:
        sizes = parse_tire_size(text)
        assert sizes is not None
        assert sizes[0].diameter == diameter
