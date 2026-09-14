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


# Wave 1-A (2026-09-14) — the reverse direction, for reschedules.
#
# 1C stores the colour as `UPPERCASE_LATIN_TRANSLIT(колір)` inside AutoNumber,
# so `get_customer_bookings` hands back «SIRIY Tiguan» for a car the caller
# described as «сірий». `translit_color_to_latin` is one-way — `и`/`і`/`ы` all
# collapse to `i`, `ь`/`ъ` vanish, case is lost — so «siriy» decodes equally to
# «сірий», «сирий» and «сирый». A general inverse does not exist.
#
# What does exist is the set of colour words this system has actually produced.
# Measured 2026-09-14 over 30 days of `call_tool_calls.tool_args->>'auto_number'`:
# 26 distinct values, of which these are the colour forms. Anything outside the
# list decodes to None and the caller is asked — never guessed.
#
# Second element is the canonical Ukrainian form: the value travels back into 1C
# and is read out to the caller at Krok 8, so a Russian spelling the LLM sent
# verbatim must not come back out of here.
_KNOWN_COLOR_FORMS: tuple[tuple[str, str], ...] = (
    ("чорний", "чорний"),
    ("сірий", "сірий"),
    ("білий", "білий"),
    ("червоний", "червоний"),
    ("жовтий", "жовтий"),
    ("синій", "синій"),
    ("сріблястий", "сріблястий"),
    ("зелений", "зелений"),
    ("блакитний", "блакитний"),
    ("рожевий", "рожевий"),
    ("срібний", "срібний"),
    ("помаранчевий", "помаранчевий"),
    ("бежевий", "бежевий"),
    ("фіолетовий", "фіолетовий"),
    # Russian spellings seen in the same 30 days, answered in Ukrainian.
    ("червонный", "червоний"),
    ("пурпурный", "пурпурний"),
    ("помаранчивий", "помаранчевий"),
    ("помаранчивый", "помаранчевий"),
)


def _build_latin_color_index() -> tuple[dict[str, str], dict[str, set[str]]]:
    """Index `_KNOWN_COLOR_FORMS` by the Latin form the forward function emits.

    Keys are produced by running `translit_color_to_latin` rather than typed by
    hand, so the table cannot drift away from the function it inverts when the
    char map is edited.

    Returns the index and, separately, the keys two different canonical colours
    both claim. A plain dict comprehension would drop one of them in silence;
    the collision map is returned so a test can assert it stays empty.
    """
    table: dict[str, str] = {}
    collisions: dict[str, set[str]] = {}
    for form, canonical in _KNOWN_COLOR_FORMS:
        key = translit_color_to_latin(form).upper()
        if not key:
            continue
        previous = table.get(key)
        if previous is not None and previous != canonical:
            collisions.setdefault(key, {previous}).add(canonical)
        table[key] = canonical
    return table, collisions


_LATIN_TO_COLOR, _LATIN_COLOR_COLLISIONS = _build_latin_color_index()


def latin_prefix_to_color(prefix: str) -> str | None:
    """Decode one Latin AutoNumber token back to a Ukrainian colour name.

    Exact lookup only — no fuzzy matching, no prefix matching. An unrecognised
    token returns None and the caller is expected to ask rather than to guess:
    the plate debris the pre-2026-08-18 schema left in this field («4448ка»,
    «1873») and the «колір не назвали» escape hatch both land here.

    Examples:
        "SIRIY"   → "сірий"
        "siriy"   → "сірий"
        "CHORNIY" → "чорний"
        "4448KA"  → None
    """
    if not prefix:
        return None
    key = prefix.strip().upper()
    if not key:
        return None
    return _LATIN_TO_COLOR.get(key)
