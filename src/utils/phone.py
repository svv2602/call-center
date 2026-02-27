"""Phone number normalization utilities for Ukrainian numbers."""

from __future__ import annotations


def normalize_phone_ua(phone: str) -> str:
    """Normalize Ukrainian phone to format 0XXXXXXXXX (10 digits).

    Handles: +380XXXXXXXXX, 380XXXXXXXXX, 80XXXXXXXXX, 0XXXXXXXXX.
    Returns digits as-is for unrecognized formats.
    """
    digits = "".join(c for c in phone if c.isdigit())
    if digits.startswith("380") and len(digits) == 12:
        return "0" + digits[3:]
    if digits.startswith("80") and len(digits) == 11:
        return "0" + digits[2:]
    if digits.startswith("0") and len(digits) == 10:
        return digits
    return digits  # return as-is if unknown format
