"""Deterministic pre-parser for fitting utterances.

Extracts the car brand from a client's turn so that the LLM progress-block
flips «Марка авто» to ✅ immediately. Verbose callers who say everything at
once («на завтра, лексус, свої шини») skip a turn of follow-up questions.

Plate extraction was removed on 2026-09-16. Krok 5 stopped asking for the
plate on 2026-08-18 — it asks for the *colour*, and `session.fitting_plate`
kept its old name — so a plate written here filled the colour slot. Over 21
days and 264 calls the pattern fired twice and was wrong both times: «на
16.09 на 10:00» reads as prefix «на» + four digits + suffix «на», because
«на» is a preposition built from two letters the plate alphabet allows.
Both callers lost their booking — the fabricated plate marked the colour ✅,
so the bot never asked for it, and the brand guard then refused the booking
(`2489d6bd`, `60ae3fdd`). Zero correct extractions in the same window.

Real plates still reach the session from 1C: `update_customer_profile` and
`get_customer_bookings` both write one when the caller has a profile.

Storage-choice detection is handled separately in pipeline.py — a
richer heuristic already exists there. We deliberately do NOT duplicate
it here to avoid two conflicting sources of truth for the same field.
"""

from __future__ import annotations

import re
from typing import Any

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
        - brand  → canonical brand name (Toyota, Lexus, BYD, Zeekr, …)

    Storage-choice detection lives in pipeline.py — do not duplicate here.
    """
    out: dict[str, Any] = {}
    brand = _extract_brand(text)
    if brand:
        out["brand"] = brand
    return out
