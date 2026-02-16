"""PII sanitizer — masks personal data in log output.

Masks phone numbers, names, emails, card numbers, addresses, and IBANs
in stdout/file logs. Does NOT mask in PostgreSQL (needed for search/support).
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

# Email pattern
_EMAIL_RE = re.compile(
    r"\b([a-zA-Z0-9._%+-])([a-zA-Z0-9._%+-]*)@([a-zA-Z0-9.-]+)\.([a-zA-Z]{2,})\b"
)

# Card number patterns (with and without spaces/dashes, 13-19 digits)
_CARD_RE = re.compile(r"\b(\d{4})[\s-]?(\d{4})[\s-]?(\d{4})[\s-]?(\d{4})\b")

# Ukrainian address pattern (ул./вул./пр./просп./бульв./б-р/пров./наб. + name + house number)
_ADDRESS_RE = re.compile(
    r"((?:вул|ул|пр|просп|бульв|б-р|пров|наб)\.\s*)"  # street type abbreviation
    r"([А-ЯІЇЄҐа-яіїєґA-Za-z\s.-]+?)"  # street name
    r"(\s*,?\s*(?:д\.|буд\.?)?\s*\d+[а-яА-Яa-zA-Z]?(?:\s*/\s*\d+)?)",  # house number
    re.IGNORECASE,
)

# IBAN pattern (UA + 2 check digits + 25 alphanumeric, with optional spaces)
_IBAN_RE = re.compile(
    r"\b(UA\d{2})\s?(\d{4})\s?(\d{4})\s?(\d{4})\s?(\d{4})\s?(\d{4})\s?(\d{5})\s?(\d?)\b"
)


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


def sanitize_email(text: str) -> str:
    """Mask emails: user@example.com → u***@***.com."""

    def _mask(m: re.Match[str]) -> str:
        return f"{m.group(1)}***@***.{m.group(4)}"

    return _EMAIL_RE.sub(_mask, text)


def sanitize_card(text: str) -> str:
    """Mask card numbers: 4111 1111 1111 1111 → 4111 **** **** 1111."""

    def _mask(m: re.Match[str]) -> str:
        return f"{m.group(1)} **** **** {m.group(4)}"

    return _CARD_RE.sub(_mask, text)


def sanitize_address(text: str) -> str:
    """Mask addresses: вул. Хрещатик, 22 → вул. ***, ***."""

    def _mask(m: re.Match[str]) -> str:
        return f"{m.group(1)}***, ***"

    return _ADDRESS_RE.sub(_mask, text)


def sanitize_iban(text: str) -> str:
    """Mask IBANs: UA213223130000026007233566001 → UA21 **** **** 6001."""

    def _mask(m: re.Match[str]) -> str:
        # Last 4 digits from the tail of the IBAN
        full = m.group(0).replace(" ", "")
        return f"{full[:4]} **** **** {full[-4:]}"

    return _IBAN_RE.sub(_mask, text)


def sanitize_pii(text: str) -> str:
    """Sanitize all PII in text for logging."""
    text = sanitize_iban(text)
    text = sanitize_card(text)
    text = sanitize_phone(text)
    text = sanitize_email(text)
    text = sanitize_name(text)
    text = sanitize_address(text)
    return text
