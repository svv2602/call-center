"""Color auto-detection from customer transcripts.

Wave 5 (2026-09-03): replaces the substring-based hint list in pipeline.py
that missed inflected forms ('Червоны', 'біла', 'чорна', 'серая', ...).
Uses regex with word boundaries + Cyrillic tails so every UA/RU
gender/number/case form of each color root matches.

Anti-pattern (call dd3dd368 2026-09-03):
- Customer said «Червоны» → substring 'червоний' did not match → bot
  said «не розчула колір» twice, then triggered the «колір не назвали»
  escape hatch inappropriately.

The mapping is: matched-root → canonical color word passed to
book_fitting.auto_number. Downstream 1C accepts freeform UA text.
"""

from __future__ import annotations

import re

# Multi-word compound colors — checked FIRST (longest wins).
# Each entry: (regex, canonical form to persist).
_COMPOUND_COLORS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"мокр[а-яёіїєґ]*\s+асфальт[а-яё]*"), "мокрий асфальт"),
    (re.compile(r"мокрий\s+асфальт"), "мокрий асфальт"),
    (re.compile(r"мокрый\s+асфальт"), "мокрий асфальт"),
    (re.compile(r"темно[-\s]?[а-яёіїєґ]+"), "темний"),
    (re.compile(r"світло[-\s]?[а-яёіїєґ]+"), "світлий"),
    (re.compile(r"светло[-\s]?[а-яё]+"), "світлий"),
    (re.compile(r"ярко[-\s]?[а-яё]+"), "яскравий"),
    (re.compile(r"яскраво[-\s]?[а-яёіїєґ]+"), "яскравий"),
)

# Single-root colors — regex captures every UA/RU inflection.
# Order matters only for logging (first match wins); no overlap concerns
# because each root is distinctive enough that ambiguity is rare.
# Cyrillic tail class covers UA (іїєґ) + RU (ёы) + all shared letters.
_TAIL = r"[а-яёіїєґ]*"


def _root(root: str, canonical: str) -> tuple[re.Pattern[str], str]:
    """Build a word-boundary-anchored regex for a color root + all endings."""
    return (re.compile(rf"\b{root}{_TAIL}\b"), canonical)


_COLOR_ROOTS: tuple[tuple[re.Pattern[str], str], ...] = (
    # UA + RU base colors (root covers all genders/numbers/cases).
    # UA nominative forms are canonical.
    _root("червон", "червоний"),   # UA червоний/червона/червоне/червоні + STT «червоны»
    _root("красн", "червоний"),    # RU красный/красная/красное/красну
    _root("чорн", "чорний"),       # UA чорний/чорна/чорне
    _root("черн", "чорний"),       # RU чёрный (STT drops ё → черн)
    _root("сір", "сірий"),         # UA сірий/сіра/сіре
    _root("серебр", "срібний"),    # RU серебряный/серебристый — check BEFORE «сер»
    _root("серебрист", "срібний"),
    _root("сребр", "срібний"),
    _root("сріб", "срібний"),      # UA срібний/срібляст
    _root("сер", "сірий"),         # RU серый — after «серебр» so it doesn't false-match
    _root("біл", "білий"),         # UA білий/біла/біле
    _root("бел", "білий"),         # RU белый/белая/белое
    _root("син", "синій"),         # UA синій, RU синий/синяя/синее
    _root("зелен", "зелений"),     # UA/RU зелений/зелёный
    _root("жовт", "жовтий"),       # UA
    _root("желт", "жовтий"),       # RU (STT often ё→е)
    _root("помаранч", "помаранчевий"),   # UA
    _root("оранж", "помаранчевий"),      # RU
    _root("коричнев", "коричневий"),
    _root("беж", "бежевий"),
    _root("бордов", "бордовий"),
    _root("фіолетов", "фіолетовий"),
    _root("фиолетов", "фіолетовий"),
    _root("рожев", "рожевий"),
    _root("розов", "рожевий"),
    _root("бирюз", "бирюзовий"),
    _root("бірюз", "бирюзовий"),
    _root("голуб", "голубий"),
    _root("блакитн", "блакитний"),
    # Fancy — one-word.
    _root("перламутр", "перламутр"),
    _root("антрацит", "антрацит"),
    _root("маренго", "маренго"),
    _root("шампан", "шампань"),
    _root("шампань", "шампань"),
    _root("піщан", "пісочний"),
    _root("песочн", "пісочний"),
    _root("пісочн", "пісочний"),
    _root("шоколад", "шоколадний"),
    _root("графіт", "графіт"),
    _root("графит", "графіт"),
    _root("олив", "оливковий"),
    _root("титан", "титан"),
    _root("платин", "платина"),
    _root("металі", "металік"),
    _root("металли", "металік"),
    _root("гранат", "гранатовий"),
    _root("вишн", "вишневий"),
    _root("попіл", "попелястий"),
    _root("пепел", "попелястий"),
    _root("сталев", "сталевий"),
    _root("сталь", "сталевий"),
)

# Filter out common false-positives — words that share a root but are not
# colors, given only when they appear standalone (with word boundaries).
_FALSE_POSITIVE_WORDS: frozenset[str] = frozenset(
    {
        "серпень", "серпня",             # August — shares "сер"
        "серпанок",                       # haze
        "сергій", "сергей", "сергеевич",  # name — shares "сер"
        "серветк",                        # napkin
        "сербія",                         # country
        "серга", "серьги",                # earring — shares "сер"
        "белка", "белок",                 # squirrel / egg white — shares "бел"
        "белград", "белорус",             # cities/nationality
        "біла церква",                    # city
        "біль",                           # pain
        "красноярськ", "красноярск",      # city
        "краснодар",                      # city
        "краснодон",                      # city
        "чернігів", "чернигов",           # city
        "чорнобиль",                      # city
        "черкас",                         # city
        "оранж",                          # brand name Orange (mobile op) — allow root anyway
    }
)


def detect_color(text: str) -> str | None:
    """Detect a color mention in the given text.

    Returns the canonical color form (Ukrainian nominative) or None
    if no color root matched.

    The text is expected to be already lowercased. False-positive city
    names (Черкаси, Чернігів, Краснодар, etc.) are filtered before
    checking roots so «поїхав у Красноярськ» does NOT match «красн».
    """
    if not text:
        return None

    # Normalize «ё» → «е» so «чёрный» / «зелёный» / «жёлтый» match the
    # roots «черн» / «зелен» / «желт». STT frequently drops the diaeresis
    # anyway, but normalizing here removes the coupling.
    text = text.replace("ё", "е").replace("Ё", "Е")

    # Strip false-positive substrings first so their roots don't mismatch.
    stripped = text
    for fp in _FALSE_POSITIVE_WORDS:
        stripped = stripped.replace(fp, " ")

    # Compound colors first (longest wins).
    for pattern, canonical in _COMPOUND_COLORS:
        if pattern.search(stripped):
            return canonical

    # Single-root colors.
    for pattern, canonical in _COLOR_ROOTS:
        if pattern.search(stripped):
            return canonical

    return None
