"""PII sanitizer — masks personal data in log output.

Masks phone numbers and names in stdout/file logs.
Does NOT mask in PostgreSQL (needed for search/support).
"""

from __future__ import annotations

import re

# Ukrainian/international phone patterns
_PHONE_RE = re.compile(
    r"(\+?3?8?0)"  # country code
    r"(\d{2})"  # operator code
    r"(\d{3})"  # middle digits
    r"(\d{2})"  # last digits
    r"(\d{2})"  # last digits
)

# Simple name pattern (capitalized words, 2+ chars)
_NAME_RE = re.compile(r"\b([А-ЯІЇЄҐA-Z][а-яіїєґa-z]{2,})\s+([А-ЯІЇЄҐA-Z][а-яіїєґa-z]{2,})\b")


def sanitize_phone(text: str) -> str:
    """Mask phone numbers: +380XXXXXXXXX → +380***XXX."""

    def _mask(m: re.Match[str]) -> str:
        return f"{m.group(1)}{m.group(2)}***{m.group(5)}"

    return _PHONE_RE.sub(_mask, text)


def sanitize_name(text: str) -> str:
    """Mask names: Іван Петренко → І*** П***."""

    def _mask(m: re.Match[str]) -> str:
        return f"{m.group(1)[0]}*** {m.group(2)[0]}***"

    return _NAME_RE.sub(_mask, text)


def sanitize_pii(text: str) -> str:
    """Sanitize all PII in text for logging."""
    text = sanitize_phone(text)
    text = sanitize_name(text)
    return text
