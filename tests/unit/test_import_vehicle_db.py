"""Unit tests for vehicle DB import script."""

from __future__ import annotations

from decimal import Decimal

from scripts.import_vehicle_db import (
    _clean_control_chars,
    _decimal_from_csv,
    _int_from_csv,
    _smallint_or_none,
)


class TestCleanControlChars:
    """Test control character cleanup."""

    def test_strips_0x11(self) -> None:
        assert _clean_control_chars("M12\x11x 1.5") == "M12x 1.5"

    def test_strips_0x13(self) -> None:
        assert _clean_control_chars("M12\x13x 1.5") == "M12x 1.5"

    def test_leaves_normal_text(self) -> None:
        assert _clean_control_chars("M12 x 1.5") == "M12 x 1.5"

    def test_strips_multiple_control_chars(self) -> None:
        assert _clean_control_chars("\x01hello\x1fworld") == "helloworld"

    def test_empty_string(self) -> None:
        assert _clean_control_chars("") == ""


class TestIntFromCsv:
    """Test CSV numeric conversion."""

    def test_integer_string(self) -> None:
        assert _int_from_csv("235") == 235

    def test_float_string(self) -> None:
        assert _int_from_csv("235.00") == 235

    def test_float_with_decimal(self) -> None:
        assert _int_from_csv("65.00") == 65


class TestDecimalFromCsv:
    """Test CSV to Decimal conversion."""

    def test_normal_value(self) -> None:
        assert _decimal_from_csv("114.30") == Decimal("114.30")

    def test_empty_string(self) -> None:
        assert _decimal_from_csv("") is None

    def test_null_string(self) -> None:
        assert _decimal_from_csv("NULL") is None


class TestSmallintOrNone:
    """Test CSV to smallint conversion."""

    def test_normal_value(self) -> None:
        assert _smallint_or_none("5") == 5

    def test_float_value(self) -> None:
        assert _smallint_or_none("5.00") == 5

    def test_empty(self) -> None:
        assert _smallint_or_none("") is None

    def test_null(self) -> None:
        assert _smallint_or_none("NULL") is None
