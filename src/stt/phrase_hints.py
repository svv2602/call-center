"""STT phrase hints — boost recognition of tire-specific terminology.

Three-level system:
  1. Base dictionary — hardcoded brand pronunciations + tire terms (Ukrainian)
  2. Auto-extracted — DISTINCT manufacturer names from tire catalog (DB) + transliteration
  3. Custom — user-managed list via Admin UI

All levels are merged and stored in Redis as a single JSON structure.
Google Cloud STT v2 SpeechAdaptation uses these as PhraseSet hints (boost=10).
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from redis.asyncio import Redis

logger = logging.getLogger(__name__)

REDIS_KEY = "stt:phrase_hints"
_GOOGLE_PHRASE_LIMIT = 5000

# ─── In-process cache ─────────────────────────────────────
_cache: tuple[str, ...] = ()
_cache_ts: float = 0.0
_CACHE_TTL = 60.0  # seconds

# ═══════════════════════════════════════════════════════════
#  1. Base dictionary — brand pronunciations
# ═══════════════════════════════════════════════════════════

BRAND_PRONUNCIATIONS: dict[str, list[str]] = {
    "Bridgestone": ["Бріджстоун", "Бріджстон"],
    "Michelin": ["Мішлен", "Мішелін"],
    "Continental": ["Континенталь", "Контінентал"],
    "Goodyear": ["Гудір", "Гудієр"],
    "Pirelli": ["Піреллі", "Пірелі"],
    "Nokian": ["Нокіан", "Нокіен"],
    "Hankook": ["Ханкук", "Ганкук"],
    "Yokohama": ["Йокогама", "Йокохама"],
    "Dunlop": ["Данлоп", "Данлап"],
    "Toyo": ["Тойо"],
    "Kumho": ["Кумхо"],
    "Nexen": ["Нексен", "Нексін"],
    "Firestone": ["Файрстоун", "Файрстон"],
    "BFGoodrich": ["Бі Еф Гудріч", "БФ Гудріч"],
    "Falken": ["Фалкен", "Фалькен"],
    "Maxxis": ["Максіс", "Макксіс"],
    "Cooper": ["Купер"],
    "Lassa": ["Ласса"],
    "Matador": ["Матадор"],
    "Barum": ["Барум"],
    "Semperit": ["Семперіт"],
    "Sava": ["Сава"],
    "Kleber": ["Клебер"],
    "Vredestein": ["Вредештайн", "Вредестайн"],
    "General Tire": ["Дженерал Тайр"],
    "Nitto": ["Нітто"],
    "Triangle": ["Тріангл", "Трайенгл"],
    "Sailun": ["Сайлун"],
    "Taurus": ["Таурус"],
    "Росава": [],
    "Белшина": [],
    "Кама": [],
}

# Common Ukrainian tire terms that STT may misrecognize
BASE_TERMS_UK: list[str] = [
    "шиномонтаж",
    "шини",
    "шина",
    "літні",
    "зимові",
    "всесезонні",
    "протектор",
    "диски",
    "балансування",
    "вулканізація",
    "камера",
    "безкамерна",
    "радіальна",
    "діагональна",
    "ширина профілю",
    "висота профілю",
    "посадковий діаметр",
    "індекс навантаження",
    "індекс швидкості",
    "RunFlat",
    "Ранфлет",
    "XL",
    "SUV",
]

BASE_STORE_NAMES: list[str] = [
    "ПроКолесо",
    "ProKoleso",
    "Твоя Шина",
]


def get_base_phrases() -> list[str]:
    """Return all base phrases (brands + pronunciations + terms + store names)."""
    phrases: list[str] = []
    for brand, pronunciations in BRAND_PRONUNCIATIONS.items():
        phrases.append(brand)
        phrases.extend(pronunciations)
    phrases.extend(BASE_TERMS_UK)
    phrases.extend(BASE_STORE_NAMES)
    return phrases


# ═══════════════════════════════════════════════════════════
#  2. Transliteration Latin → Cyrillic
# ═══════════════════════════════════════════════════════════

_DIGRAPHS: dict[str, str] = {
    "sh": "ш",
    "ch": "ч",
    "th": "т",
    "ph": "ф",
    "ck": "к",
    "ee": "і",
    "oo": "у",
}

_LATIN_TO_CYRILLIC: dict[str, str] = {
    "a": "а",
    "b": "б",
    "c": "к",
    "d": "д",
    "e": "е",
    "f": "ф",
    "g": "г",
    "h": "х",
    "i": "і",
    "j": "дж",
    "k": "к",
    "l": "л",
    "m": "м",
    "n": "н",
    "o": "о",
    "p": "п",
    "q": "к",
    "r": "р",
    "s": "с",
    "t": "т",
    "u": "у",
    "v": "в",
    "w": "в",
    "x": "кс",
    "y": "і",
    "z": "з",
}

_HAS_LATIN = re.compile(r"[a-zA-Z]")
_HAS_CYRILLIC = re.compile(r"[а-яА-ЯіїєґІЇЄҐ]")


def _transliterate_word(word: str) -> str:
    """Transliterate a single Latin word to Cyrillic."""
    if not _HAS_LATIN.search(word):
        return word

    result: list[str] = []
    lower = word.lower()
    i = 0
    while i < len(lower):
        # Try digraphs first
        if i + 1 < len(lower):
            pair = lower[i : i + 2]
            if pair in _DIGRAPHS:
                cyr = _DIGRAPHS[pair]
                # Capitalize if first char of original is upper
                if i == 0 and word[0].isupper():
                    cyr = cyr[0].upper() + cyr[1:]
                result.append(cyr)
                i += 2
                continue

        ch = lower[i]
        if ch in _LATIN_TO_CYRILLIC:
            cyr = _LATIN_TO_CYRILLIC[ch]
            if i == 0 and word[0].isupper():
                cyr = cyr[0].upper() + cyr[1:]
            result.append(cyr)
        else:
            # Keep digits, hyphens, etc. as-is
            result.append(word[i])
        i += 1

    return "".join(result)


def transliterate_to_cyrillic(text: str) -> str | None:
    """Transliterate Latin text to Cyrillic. Returns None if already Cyrillic."""
    if not _HAS_LATIN.search(text):
        return None

    words = text.split()
    result_words: list[str] = []
    for word in words:
        if _HAS_LATIN.search(word) and not _HAS_CYRILLIC.search(word):
            result_words.append(_transliterate_word(word))
        else:
            result_words.append(word)
    return " ".join(result_words)


# ═══════════════════════════════════════════════════════════
#  3. Catalog extraction
# ═══════════════════════════════════════════════════════════


async def extract_catalog_phrases(db_engine: Any) -> list[str]:
    """Extract DISTINCT manufacturer names from tire catalog.

    For each name: add original + transliterated Cyrillic variant.
    Model names excluded — too numerous (5000+) and mostly noise (codes like '005 RST').
    """
    from sqlalchemy import text

    phrases: set[str] = set()

    try:
        async with db_engine.begin() as conn:
            result = await conn.execute(
                text("SELECT DISTINCT manufacturer FROM tire_models WHERE manufacturer IS NOT NULL")
            )
            for row in result:
                name = row[0].strip()
                if name:
                    phrases.add(name)
                    cyr = transliterate_to_cyrillic(name)
                    if cyr:
                        phrases.add(cyr)

    except Exception:
        logger.exception("Failed to extract catalog phrases")

    return sorted(phrases)


# ═══════════════════════════════════════════════════════════
#  4. Redis persistence and cache
# ═══════════════════════════════════════════════════════════


async def refresh_phrase_hints(db_engine: Any, redis: Redis) -> dict[str, Any]:
    """Rebuild phrase hints: base + auto (from catalog) + custom (preserved).

    Returns stats dict.
    """
    global _cache, _cache_ts

    base = get_base_phrases()
    auto = await extract_catalog_phrases(db_engine)

    # Preserve existing custom phrases
    custom: list[str] = []
    try:
        raw = await redis.get(REDIS_KEY)
        if raw:
            data = json.loads(raw if isinstance(raw, str) else raw.decode())
            custom = data.get("custom", [])
    except Exception:
        logger.debug("Failed to read existing custom phrases", exc_info=True)

    payload = {
        "base": base,
        "auto": auto,
        "custom": custom,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    await redis.set(REDIS_KEY, json.dumps(payload, ensure_ascii=False))

    # Invalidate in-process cache
    _cache = ()
    _cache_ts = 0.0

    stats = {
        "base_count": len(base),
        "auto_count": len(auto),
        "custom_count": len(custom),
        "total": len(base) + len(auto) + len(custom),
        "google_limit": _GOOGLE_PHRASE_LIMIT,
        "updated_at": payload["updated_at"],
    }
    logger.info(
        "Phrase hints refreshed: base=%d, auto=%d, custom=%d, total=%d",
        stats["base_count"],
        stats["auto_count"],
        stats["custom_count"],
        stats["total"],
    )
    return stats


async def get_phrase_hints(redis: Redis) -> dict[str, Any]:
    """Get phrase hints data from Redis (with stats)."""
    try:
        raw = await redis.get(REDIS_KEY)
        if raw:
            data = json.loads(raw if isinstance(raw, str) else raw.decode())
            base = data.get("base", [])
            auto = data.get("auto", [])
            custom = data.get("custom", [])
            return {
                "base": base,
                "auto": auto,
                "custom": custom,
                "base_count": len(base),
                "auto_count": len(auto),
                "custom_count": len(custom),
                "total": len(base) + len(auto) + len(custom),
                "google_limit": _GOOGLE_PHRASE_LIMIT,
                "updated_at": data.get("updated_at"),
            }
    except Exception:
        logger.debug("Failed to read phrase hints from Redis", exc_info=True)

    # Fallback: base only
    base = get_base_phrases()
    return {
        "base": base,
        "auto": [],
        "custom": [],
        "base_count": len(base),
        "auto_count": 0,
        "custom_count": 0,
        "total": len(base),
        "google_limit": _GOOGLE_PHRASE_LIMIT,
        "updated_at": None,
    }


async def get_all_phrases_flat(redis: Redis) -> tuple[str, ...]:
    """Get merged phrase list for STTConfig (with in-process cache)."""
    global _cache, _cache_ts

    now = time.monotonic()
    if _cache and (now - _cache_ts) < _CACHE_TTL:
        return _cache

    data = await get_phrase_hints(redis)
    merged: list[str] = []
    seen: set[str] = set()
    for phrase in data["base"] + data["auto"] + data["custom"]:
        lower = phrase.lower()
        if lower not in seen:
            seen.add(lower)
            merged.append(phrase)

    # Respect Google API limit
    if len(merged) > _GOOGLE_PHRASE_LIMIT:
        merged = merged[:_GOOGLE_PHRASE_LIMIT]

    _cache = tuple(merged)
    _cache_ts = now
    return _cache


async def update_custom_phrases(redis: Redis, phrases: list[str]) -> dict[str, Any]:
    """Replace custom phrase list in Redis."""
    global _cache, _cache_ts

    try:
        raw = await redis.get(REDIS_KEY)
        if raw:
            data = json.loads(raw if isinstance(raw, str) else raw.decode())
        else:
            data = {"base": get_base_phrases(), "auto": [], "custom": []}
    except Exception:
        data = {"base": get_base_phrases(), "auto": [], "custom": []}

    data["custom"] = phrases
    data["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    await redis.set(REDIS_KEY, json.dumps(data, ensure_ascii=False))

    # Invalidate cache
    _cache = ()
    _cache_ts = 0.0

    return {
        "custom_count": len(phrases),
        "total": len(data.get("base", [])) + len(data.get("auto", [])) + len(phrases),
        "google_limit": _GOOGLE_PHRASE_LIMIT,
    }


def invalidate_cache() -> None:
    """Invalidate in-process cache (for testing)."""
    global _cache, _cache_ts
    _cache = ()
    _cache_ts = 0.0
