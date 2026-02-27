"""Tests for phone number normalization utility."""

from __future__ import annotations

import pytest

from src.utils.phone import normalize_phone_ua


@pytest.mark.parametrize(
    ("input_phone", "expected"),
    [
        # +380 format (international)
        ("+380504874375", "0504874375"),
        ("+380 50 487 43 75", "0504874375"),
        ("+380(50)487-43-75", "0504874375"),
        # 380 format (no plus)
        ("380504874375", "0504874375"),
        # 80 format (trunk prefix)
        ("80504874375", "0504874375"),
        # 0 format (local)
        ("0504874375", "0504874375"),
        ("050-487-43-75", "0504874375"),
        ("(050) 487-43-75", "0504874375"),
        # Short / unknown — returned as digits
        ("12345", "12345"),
        ("", ""),
        # Already normalized
        ("0671234567", "0671234567"),
        # Non-digit characters stripped
        ("+38 (067) 123-45-67", "0671234567"),
    ],
)
def test_normalize_phone_ua(input_phone: str, expected: str) -> None:
    assert normalize_phone_ua(input_phone) == expected


def test_normalize_preserves_unknown_format() -> None:
    """Unknown formats return stripped digits without transformation."""
    result = normalize_phone_ua("123456789012345")
    assert result == "123456789012345"
