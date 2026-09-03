"""Tests for src.agent.ua_datetime."""

from src.agent.ua_datetime import date_to_words, time_to_words


class TestDateToWords:
    def test_common_dates(self) -> None:
        assert date_to_words("2026-09-07") == "сьоме вересня"
        assert date_to_words("2026-09-01") == "перше вересня"
        assert date_to_words("2026-09-15") == "пʼятнадцяте вересня"
        assert date_to_words("2026-09-21") == "двадцять перше вересня"
        assert date_to_words("2026-09-30") == "тридцяте вересня"
        assert date_to_words("2026-10-31") == "тридцять перше жовтня"

    def test_all_months(self) -> None:
        expected = [
            "січня", "лютого", "березня", "квітня", "травня", "червня",
            "липня", "серпня", "вересня", "жовтня", "листопада", "грудня",
        ]
        for m, word in enumerate(expected, start=1):
            iso = f"2026-{m:02d}-05"
            assert date_to_words(iso) == f"пʼяте {word}"

    def test_wave3_date_drift_scenario(self) -> None:
        # Wave 3 #5: client said «7 вересня» × 3, bot confabulated «десяте».
        # Server-side normalization now bakes the canonical form.
        assert date_to_words("2026-09-07") == "сьоме вересня"
        # Distinct enough that «десяте вересня» is impossible to confuse
        assert date_to_words("2026-09-10") == "десяте вересня"

    def test_bad_input(self) -> None:
        assert date_to_words(None) is None
        assert date_to_words("") is None
        assert date_to_words("07") is None  # not ISO
        assert date_to_words("07-09-2026") is None  # wrong order
        assert date_to_words("2026-13-01") is None  # bad month
        assert date_to_words("garbage") is None


class TestTimeToWords:
    def test_common_slots(self) -> None:
        assert time_to_words("10:30") == "десята тридцять"
        assert time_to_words("09:00") == "рівно девʼята"
        assert time_to_words("14:15") == "чотирнадцята пʼятнадцять"
        assert time_to_words("17:45") == "сімнадцята сорок пʼять"
        assert time_to_words("20:00") == "рівно двадцята"

    def test_all_fitting_hours(self) -> None:
        expected = {
            8: "восьма", 9: "девʼята", 10: "десята", 11: "одинадцята",
            12: "дванадцята", 13: "тринадцята", 14: "чотирнадцята",
            15: "пʼятнадцята", 16: "шістнадцята", 17: "сімнадцята",
            18: "вісімнадцята", 19: "девʼятнадцята", 20: "двадцята",
        }
        for h, word in expected.items():
            assert time_to_words(f"{h:02d}:00") == f"рівно {word}"

    def test_off_hours_return_none(self) -> None:
        assert time_to_words("07:00") is None
        assert time_to_words("21:00") is None
        assert time_to_words("00:00") is None

    def test_off_grid_minutes_return_none(self) -> None:
        assert time_to_words("10:07") is None
        assert time_to_words("10:23") is None

    def test_bad_input(self) -> None:
        assert time_to_words(None) is None
        assert time_to_words("") is None
        assert time_to_words("10") is None
        assert time_to_words("10:xx") is None
        assert time_to_words("abc:def") is None
