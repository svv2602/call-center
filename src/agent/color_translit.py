"""Cyrillic → Latin char-by-char transliteration for 1C AutoNumber field.

Wave 11 (2026-09-04): the fitting flow's Krok 5 collects a color (Wave 6
pattern, 2026-08-18) and stuffs it into `auto_number` — but 1C's
`AutoNumber` field rejects Cyrillic free-text ("білий" → «empty JSON»).
This module converts Cyrillic characters to Latin phonetic equivalents
so 1C accepts the booking. The station operator recognises the tag
("bilyy", "purpurniy") visually at reception.

Not a translation. «пурпурный» → «purpurniy» (not "purple"). Preserves
the exact word the customer said in a Latin-safe form.

Char map follows the convention:
    и → i    ы → i    й → y
    і → i    ї → yi   є → ye
    ё → yo   х → kh   ц → ts
    ч → ch   ш → sh   щ → shch
    ю → yu   я → ya   ъ → -   ь → -

Digits, ASCII letters, spaces and hyphens are preserved unchanged.
Unknown symbols (accents, punctuation like «») are silently dropped.
"""

from __future__ import annotations

_CHAR_MAP: dict[str, str] = {
    "а": "a", "б": "b", "в": "v", "г": "g", "ґ": "g",
    "д": "d", "е": "e", "ё": "yo", "є": "ye",
    "ж": "zh", "з": "z",
    "и": "i", "і": "i", "ї": "yi", "й": "y",
    "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
    "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "kh", "ц": "ts", "ч": "ch",
    "ш": "sh", "щ": "shch",
    "ъ": "", "ы": "i", "ь": "",
    "э": "e", "ю": "yu", "я": "ya",
}


def translit_color_to_latin(color: str) -> str:
    """Transliterate a Cyrillic color name to Latin phonetic form.

    Returns empty string for empty input. Latin input is passed through
    (lowercased + trimmed) so ``translit_color_to_latin("white")`` still
    yields ``"white"``.

    Examples:
        "білий"          → "bilyy"
        "чорний"         → "chorniy"
        "пурпурный"      → "purpurniy"
        "мокрий асфальт" → "mokriy asfalt"
        "graphite"       → "graphite"
    """
    if not color:
        return ""
    s = color.strip().lower()
    if not s:
        return ""
    out: list[str] = []
    for ch in s:
        if ch in _CHAR_MAP:
            out.append(_CHAR_MAP[ch])
        elif ch.isascii() and (ch.isalnum() or ch in " -"):
            out.append(ch)
        # else: drop (accents, punctuation)
    return "".join(out)
