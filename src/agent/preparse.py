"""Deterministic pre-parser for fitting utterances.

Extracts car brand and licence plate from a client's turn so that the
LLM progress-block flips «Марка авто» and «Держномер» to ✅ immediately.
Verbose callers who say everything at once («на завтра, лексус AA1234BB,
свої шини») skip 2-3 turns of follow-up questions.

Storage-choice detection is handled separately in pipeline.py — a
richer heuristic already exists there. We deliberately do NOT duplicate
it here to avoid two conflicting sources of truth for the same field.
"""

from __future__ import annotations

import re
from typing import Any

# Ukrainian licence-plate pattern (DSTU 4278-2004):
#   2 Cyrillic-look-alike letters + 4 digits + 2 letters.
# The 12 allowed Cyrillic letters (А В Е І К М Н О Р С Т Х) map 1:1
# to Latin (A B E I K M H O P C T X) — STT can output either script.
# Separators (space, dash, dot) are allowed between letter/digit blocks
# AND between individual digits (STT sometimes gives «12 34» for «1234»).
_PLATE_LETTER_CLASS = r"[АВЕІКМНОРСТХABEIKMHOPCTX]"
_PLATE_SEP = r"[\s\-.:_]*"
_PLATE_RE = re.compile(
    rf"(?<![A-Za-zА-Яа-яІіЇїЄєҐґ0-9])"
    rf"(?P<pfx>{_PLATE_LETTER_CLASS}{{2}}){_PLATE_SEP}"
    rf"(?P<digits>\d(?:{_PLATE_SEP}\d){{3}}){_PLATE_SEP}"
    rf"(?P<sfx>{_PLATE_LETTER_CLASS}{{2}})"
    rf"(?![A-Za-zА-Яа-яІіЇїЄєҐґ0-9])",
    re.IGNORECASE,
)
_PLATE_SEP_STRIP = re.compile(r"[\s\-.:_]")

# Car brands the caller might mention on turn 1. Keys are lowercase
# STT-friendly variants (Ukrainian, Russian, Latin); values are the
# canonical brand string we store in session.fitting_vehicle_brand.
# List is intentionally curated — random dictionary words like "opel"
# only match when they appear as whole words.
_CAR_BRANDS: dict[str, str] = {
    # Toyota family
    "toyota": "Toyota", "тойота": "Toyota", "тайота": "Toyota",
    "lexus": "Lexus", "лексус": "Lexus", "лєксус": "Lexus",
    # German
    "bmw": "BMW", "бмв": "BMW",
    "audi": "Audi", "ауді": "Audi", "ауди": "Audi",
    "mercedes": "Mercedes", "мерседес": "Mercedes", "мерс": "Mercedes",
    "volkswagen": "Volkswagen", "фольксваген": "Volkswagen", "фольцваген": "Volkswagen",
    "porsche": "Porsche", "порше": "Porsche",
    "opel": "Opel", "опель": "Opel",
    "smart": "Smart", "смарт": "Smart",
    # Japanese/Korean
    "honda": "Honda", "хонда": "Honda",
    "nissan": "Nissan", "ниссан": "Nissan", "нісан": "Nissan",
    "mazda": "Mazda", "мазда": "Mazda",
    "mitsubishi": "Mitsubishi", "міцубісі": "Mitsubishi", "мицубиси": "Mitsubishi",
    "subaru": "Subaru", "субару": "Subaru",
    "suzuki": "Suzuki", "сузукі": "Suzuki", "сузуки": "Suzuki",
    "hyundai": "Hyundai", "хюндай": "Hyundai", "хендай": "Hyundai", "хундай": "Hyundai",
    "kia": "Kia", "кіа": "Kia", "киа": "Kia",
    "infiniti": "Infiniti", "інфініті": "Infiniti", "инфинити": "Infiniti",
    # French / Italian
    "renault": "Renault", "рено": "Renault",
    "peugeot": "Peugeot", "пежо": "Peugeot",
    "citroen": "Citroën", "ситроен": "Citroën", "сітроен": "Citroën",
    "fiat": "Fiat", "фіат": "Fiat", "фиат": "Fiat",
    "alfa romeo": "Alfa Romeo", "альфа ромео": "Alfa Romeo",
    # American
    "ford": "Ford", "форд": "Ford",
    "chevrolet": "Chevrolet", "шевроле": "Chevrolet",
    "jeep": "Jeep", "джип": "Jeep",
    "dodge": "Dodge", "додж": "Dodge",
    "tesla": "Tesla", "тесла": "Tesla",
    # Swedish
    "volvo": "Volvo", "вольво": "Volvo",
    # Czech / Others
    "skoda": "Škoda", "škoda": "Škoda", "шкода": "Škoda",
    "seat": "Seat", "сеат": "Seat",
    # UK
    "mini": "Mini", "мінікупер": "Mini", "мини купер": "Mini", "міні купер": "Mini",
    "range rover": "Range Rover", "рендж ровер": "Range Rover", "рейнджровер": "Range Rover",
    "land rover": "Land Rover", "ленд ровер": "Land Rover",
    # Chinese / EV — STT-frequency aware
    "byd": "BYD", "бид": "BYD", "билл": "BYD", "біл": "BYD",
    "zeekr": "Zeekr", "зікер": "Zeekr", "зикер": "Zeekr", "сикер": "Zeekr", "лікер": "Zeekr",
    "nio": "NIO", "нео": "NIO", "ніо": "NIO",
    "xpeng": "Xpeng", "ксайпен": "Xpeng",
    "geely": "Geely", "джилі": "Geely", "джили": "Geely",
    "chery": "Chery", "чері": "Chery",
    "haval": "Haval", "хавал": "Haval",
    "great wall": "Great Wall", "грейт вол": "Great Wall",
    "lynk co": "Lynk & Co", "лисян": "Lynk & Co", "линк ко": "Lynk & Co",
    # CIS
    "lada": "Lada", "лада": "Lada",
    "vaz": "ВАЗ", "ваз": "ВАЗ",
    "gaz": "ГАЗ", "газ": "ГАЗ",
    # Wave 4B (2026-09-03) — Daewoo + friends. Common Ukrainian budget
    # brands that were missing. Preparser miss on «Daewoo Matiz» meant
    # session.fitting_vehicle_brand stayed None → book_fitting
    # auto-inject couldn't rescue LLM dropping vehicle_info (call
    # 1a799364 Wave 4B #1: false «У якому районі?» re-ask).
    "daewoo": "Daewoo", "деу": "Daewoo", "деву": "Daewoo",
    "даво": "Daewoo", "дауво": "Daewoo",
    # Nissan Juke STT variants — call 2026-09-03 Wave 4 #3: bot heard
    # «не стал жук» / «не сам жук» → guessed Škoda / VW Beetle. Longer
    # multi-word keys must come before their prefixes so `_CAR_BRAND_KEYS_
    # LONGEST_FIRST` picks the full model over the bare brand.
    "nissan juke": "Nissan Juke", "нісан джук": "Nissan Juke",
    "ниссан джук": "Nissan Juke", "жук нісан": "Nissan Juke",
    "juke": "Nissan Juke", "джук": "Nissan Juke",
    # Chevrolet family — Aveo/Lacetti often mis-STT'd; add model tokens
    # so «Шевроле авео» triggers Chevrolet regardless of the model word.
    "aveo": "Chevrolet", "лачетти": "Chevrolet", "лачетті": "Chevrolet",
    # Renault Logan / Duster common in UA
    "logan": "Renault", "логан": "Renault",
    "duster": "Renault", "дастер": "Renault",
    # Volkswagen models often heard as brand
    "polo": "Volkswagen", "поло": "Volkswagen",
    "passat": "Volkswagen", "пассат": "Volkswagen",
    "golf": "Volkswagen", "гольф": "Volkswagen",
    # Škoda Octavia / Fabia — common
    "octavia": "Škoda", "октавія": "Škoda", "октавия": "Škoda",
    "fabia": "Škoda", "фабія": "Škoda", "фабия": "Škoda",
}

# Sort keys by descending length so multi-word brands («range rover»,
# «mini cooper») match before their prefixes («mini», «rover»).
_CAR_BRAND_KEYS_LONGEST_FIRST = sorted(_CAR_BRANDS.keys(), key=len, reverse=True)


def _extract_plate(text: str) -> str | None:
    """Return normalised plate (upper, no separators) or None.

    Matches «AA1234BB», «АА-12-34-БВ», «AA 12 34 CD» — separators
    between letter/digit blocks are ignored, and the plate must be
    surrounded by non-word chars (or line edges) so we don't accidentally
    pick digits out of embedded numbers like a 7-digit phone number.
    """
    m = _PLATE_RE.search(text)
    if not m:
        return None
    digits = _PLATE_SEP_STRIP.sub("", m.group("digits"))
    return (m.group("pfx") + digits + m.group("sfx")).upper()


def _extract_brand(text: str) -> str | None:
    """Return canonical brand name or None. Matches whole words only."""
    lower = text.lower()
    for kw in _CAR_BRAND_KEYS_LONGEST_FIRST:
        if re.search(r"\b" + re.escape(kw) + r"\b", lower):
            return _CAR_BRANDS[kw]
    return None


def preparse_fitting(text: str) -> dict[str, Any]:
    """Extract fitting fields deterministically from a single utterance.

    Returns a dict containing ONLY the fields that could be detected.
    Callers should apply values conservatively — only overwrite session
    state if the field is not already set (to avoid trampling on richer
    LLM-driven values from a later turn).

    Currently detects:
        - plate  → Ukrainian DSTU 4278 plate, normalised uppercase
        - brand  → canonical brand name (Toyota, Lexus, BYD, Zeekr, …)

    Storage-choice detection lives in pipeline.py — do not duplicate here.
    """
    out: dict[str, Any] = {}
    plate = _extract_plate(text)
    if plate:
        out["plate"] = plate
    brand = _extract_brand(text)
    if brand:
        out["brand"] = brand
    return out
