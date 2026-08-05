"""Deterministic entity normalization for STT transcripts.

Runs after Redis regex corrections and numeral parsing. Fixes structural
formatting issues that STT consistently gets wrong for domain entities:
- Tire sizes: "205 55 R16" / "205 55 16" → "205/55 R16"
- Brand names: "Мішлен" → "Michelin", "Бріджстоун" → "Bridgestone", etc.

Safe to run on every transcript: returns the original text if nothing matched.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Tire size normalization
# ---------------------------------------------------------------------------
# STT commonly drops the slash and/or "R" prefix:
#   "205 55 R16"  → "205/55 R16"   (space separator)
#   "205 55 16"   → "205/55 R16"   (space + no R)
#   "205/55R16"   → "205/55 R16"   (slash but no space before R)
#   "205/55 r16"  → "205/55 R16"   (lowercase r)
#   "205/55 R16"  → "205/55 R16"   (already canonical — no-op)
#
# Width range 100–339, aspect ratio 2 digits, rim diameter 13–24.
# Already-canonical form matches and produces identical output.
_TIRE_SIZE_RE = re.compile(
    r"\b([12]\d\d|3[0-3]\d)"  # width: 100–339 (typical: 135–335)
    r"[\s/]+"                   # separator: / or whitespace
    r"(\d{2})"                  # aspect ratio (e.g. 55)
    r"[\s/]*[rR]?\s*"           # optional separator + R
    r"(1[3-9]|2[0-4])"         # rim diameter: 13–24
    r"\b",
)


def normalize_tire_sizes(text: str) -> tuple[str, int]:
    """Rewrite tire size tokens to canonical 'WWW/AA RDD' form."""
    result, n = _TIRE_SIZE_RE.subn(
        lambda m: f"{m.group(1)}/{m.group(2)} R{m.group(3)}", text
    )
    return result, n


# ---------------------------------------------------------------------------
# Brand name phonetic aliases
# ---------------------------------------------------------------------------
# Ukrainian and Russian phonetic forms that STT outputs instead of the
# Latin brand name. Pairs are (compiled_pattern, replacement).
_BRAND_ALIASES: list[tuple[re.Pattern[str], str]] = [
    # Michelin
    (re.compile(r"\bмішлен\b", re.I), "Michelin"),
    (re.compile(r"\bмишлен\b", re.I), "Michelin"),
    (re.compile(r"\bміченн?ін\b", re.I), "Michelin"),
    (re.compile(r"\bмічелін\b", re.I), "Michelin"),
    # Bridgestone
    (re.compile(r"\bбрідж?стоун\b", re.I), "Bridgestone"),
    (re.compile(r"\bбридж?стоун\b", re.I), "Bridgestone"),
    (re.compile(r"\bбрідстоун\b", re.I), "Bridgestone"),
    # Continental
    (re.compile(r"\bконт[иі]ненталь\b", re.I), "Continental"),
    (re.compile(r"\bконтиненталь\b", re.I), "Continental"),
    # Pirelli
    (re.compile(r"\bп[иі]релл?[иі]\b", re.I), "Pirelli"),
    # Hankook
    (re.compile(r"\bханкук\b", re.I), "Hankook"),
    (re.compile(r"\bганкук\b", re.I), "Hankook"),
    # Nokian
    (re.compile(r"\bнокіан\b", re.I), "Nokian"),
    (re.compile(r"\bнокіян\b", re.I), "Nokian"),
    # Goodyear
    (re.compile(r"\bгуд[ьй][єе]р\b", re.I), "Goodyear"),
    (re.compile(r"\bгудйір\b", re.I), "Goodyear"),
    (re.compile(r"\bгудиєр\b", re.I), "Goodyear"),
    # Dunlop
    (re.compile(r"\bданлоп\b", re.I), "Dunlop"),
    (re.compile(r"\bдунлоп\b", re.I), "Dunlop"),
    # Falken
    (re.compile(r"\bфалькен\b", re.I), "Falken"),
    # Toyo
    (re.compile(r"\bтойо\b", re.I), "Toyo"),
    # Yokohama
    (re.compile(r"\bйокогама\b", re.I), "Yokohama"),
    (re.compile(r"\bйокохама\b", re.I), "Yokohama"),
    # Kumho
    (re.compile(r"\bкумхо\b", re.I), "Kumho"),
    (re.compile(r"\bкамхо\b", re.I), "Kumho"),
    # Nexen
    (re.compile(r"\bнексен\b", re.I), "Nexen"),
    # Maxxis
    (re.compile(r"\bмакс[иі]с\b", re.I), "Maxxis"),
    # Sailun
    (re.compile(r"\bсейлан\b", re.I), "Sailun"),
    (re.compile(r"\bсайлун\b", re.I), "Sailun"),
    # Vredestein
    (re.compile(r"\bвредестейн\b", re.I), "Vredestein"),
    (re.compile(r"\bвредштейн\b", re.I), "Vredestein"),
]


def normalize_brand_names(text: str) -> tuple[str, int]:
    """Replace phonetic brand aliases with canonical Latin brand names."""
    total = 0
    for pattern, replacement in _BRAND_ALIASES:
        text, n = pattern.subn(replacement, text)
        total += n
    return text, total


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def normalize_entities(text: str, context_hint: str | None) -> tuple[str, int]:
    """Run all deterministic entity normalizations.

    Tire sizes are skipped in date/time contexts to avoid mangling timestamps.
    Brand names are always safe to normalize.

    Returns (normalized_text, total_replacements_made).
    """
    if not text:
        return text, 0

    total = 0

    if context_hint not in ("date", "time"):
        text, n = normalize_tire_sizes(text)
        total += n

    text, n = normalize_brand_names(text)
    total += n

    return text, total
