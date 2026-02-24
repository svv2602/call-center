"""STT phrase hints — boost recognition of tire-specific terminology.

Three-level system:
  1. Base dictionary — hardcoded brand pronunciations + tire terms (Ukrainian)
  2. Auto-extracted — manufacturer names + model names (in stock) from tire catalog (DB)
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
    # Key = canonical brand name (for LLM mapping), values = Cyrillic STT hints only.
    # STT outputs Cyrillic for uk-UA/ru-RU — Latin hints are useless.
    "Bridgestone": ["Бріджстоун", "Бріджстон", "Бриджстоун", "Бриджстон"],
    "Michelin": ["Мішлен", "Мішелін", "Мишлен", "Мишелін"],
    "Continental": ["Континенталь", "Контінентал", "Контінентал", "Континентал"],
    "Goodyear": ["Гудір", "Гудієр", "Гудіяр", "Гудьір", "Гудиир"],
    "Pirelli": ["Піреллі", "Пірелі", "Пирелли", "Пірелі"],
    "Nokian": ["Нокіан", "Нокіен", "Нокіян", "Нокиан"],
    "Hankook": ["Ханкук", "Ганкук", "Ханкук", "Хенкук"],
    "Yokohama": ["Йокогама", "Йокохама", "Йокогама"],
    "Dunlop": ["Данлоп", "Данлап", "Данлоп"],
    "Toyo": ["Тойо", "Тойо"],
    "Kumho": ["Кумхо", "Кумго"],
    "Nexen": ["Нексен", "Нексін", "Нексен"],
    "Firestone": ["Файрстоун", "Файрстон", "Файєрстоун"],
    "BFGoodrich": ["Бі Еф Гудріч", "БФ Гудріч", "Бі Еф Гудрич", "Гудріч"],
    "Falken": ["Фалкен", "Фалькен"],
    "Maxxis": ["Максіс", "Макксіс", "Максис"],
    "Cooper": ["Купер"],
    "Lassa": ["Ласса"],
    "Matador": ["Матадор"],
    "Barum": ["Барум"],
    "Semperit": ["Семперіт", "Семперит"],
    "Sava": ["Сава"],
    "Kleber": ["Клебер"],
    "Vredestein": ["Вредештайн", "Вредестайн", "Вредештейн", "Вредестін", "Вредештаін"],
    "General Tire": ["Дженерал Тайр", "Дженерал Тайєр"],
    "Nitto": ["Нітто", "Нитто"],
    "Triangle": ["Тріангл", "Трайенгл", "Триангл"],
    "Sailun": ["Сайлун"],
    "Taurus": ["Таурус"],
    "Росава": ["Росава"],
    "Белшина": ["Белшина", "Білшина"],
    "Кама": ["Кама"],
}

# Common Ukrainian/Russian tire terms that STT may misrecognize.
# Cyrillic only — STT for uk-UA/ru-RU never outputs Latin.
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
    "ранфлет",
    "ранфлєт",
]

BASE_STORE_NAMES: list[str] = [
    "ПроКолесо",
    "Твоя Шина",
]


def get_base_phrases() -> list[str]:
    """Return all base phrases (Cyrillic pronunciations + terms + store names).

    Latin brand keys are NOT included — STT for uk-UA/ru-RU outputs Cyrillic only.
    """
    phrases: list[str] = []
    for pronunciations in BRAND_PRONUNCIATIONS.values():
        phrases.extend(pronunciations)
    phrases.extend(BASE_TERMS_UK)
    phrases.extend(BASE_STORE_NAMES)
    return phrases


# ═══════════════════════════════════════════════════════════
#  2. Transliteration Latin → Cyrillic
# ═══════════════════════════════════════════════════════════

# Trigraphs (3 chars) are checked before digraphs (2 chars).
_TRIGRAPHS: dict[str, str] = {
    "ice": "айс",  # Ice, IceZero, IceMaster → "Айс..."
    "igh": "ай",  # High → "Хай"
}

_DIGRAPHS: dict[str, str] = {
    "sh": "ш",
    "ch": "ч",
    "th": "т",
    "ph": "ф",
    "ck": "к",
    "ee": "і",
    "oo": "у",
    "ce": "се",  # soft C: Ice→handled by trigraph, Pace→Пасе, Race→Расе
    "ci": "сі",  # soft C: Cinturato→Сінтурато, CityRover→Сітіровер
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
# Model name must contain a "real word" (4+ consecutive letters) to be a hint.
# This filters out internal codes like A502, ADR26, FM601 while keeping
# real names like Blizzak, Pilot Sport, ContiWinterContact.
_WORD_4PLUS_LETTERS = re.compile(r"[a-zA-Zа-яА-ЯіїєґІЇЄҐ]{4,}")


def _transliterate_word(word: str) -> str:
    """Transliterate a single Latin word to Cyrillic.

    Handles trigraphs (ice→айс), digraphs (sh→ш, ce→се), soft C rule,
    and standalone I before hyphen (I-Power → Ай-Повер).
    """
    if not _HAS_LATIN.search(word):
        return word

    result: list[str] = []
    lower = word.lower()
    i = 0
    while i < len(lower):
        # 1. Try trigraphs (3 chars)
        if i + 2 < len(lower):
            tri = lower[i : i + 3]
            if tri in _TRIGRAPHS:
                cyr = _TRIGRAPHS[tri]
                if i == 0 and word[0].isupper():
                    cyr = cyr[0].upper() + cyr[1:]
                result.append(cyr)
                i += 3
                continue

        # 2. Try digraphs (2 chars)
        if i + 1 < len(lower):
            pair = lower[i : i + 2]
            if pair in _DIGRAPHS:
                cyr = _DIGRAPHS[pair]
                if i == 0 and word[0].isupper():
                    cyr = cyr[0].upper() + cyr[1:]
                result.append(cyr)
                i += 2
                continue

        # 3. Standalone I before hyphen or at end → "ай" (I-Power, iON)
        ch = lower[i]
        if ch == "i" and i == 0 and (i + 1 >= len(lower) or lower[i + 1] == "-"):
            cyr = "ай"
            if word[0].isupper():
                cyr = "Ай"
            result.append(cyr)
            i += 1
            continue

        # 4. Single character
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


def _add_cyrillic_phrase(phrases: set[str], name: str) -> None:
    """Add a phrase as Cyrillic: transliterate Latin or keep Cyrillic as-is."""
    cyr = transliterate_to_cyrillic(name)
    if cyr:
        phrases.add(cyr)
    else:
        phrases.add(name)


# type_id for discs (wheels/rims) — excluded from STT hints
_DISC_TYPE_ID = "566"

_SQL_MANUFACTURERS = (
    "SELECT DISTINCT manufacturer FROM tire_models WHERE manufacturer IS NOT NULL"
)

# Model names with stock: only tires (not discs), only models that have
# products currently in stock — filters out discontinued/unavailable models.
_SQL_MODEL_NAMES = """
    SELECT DISTINCT tm.name
    FROM tire_models tm
    JOIN tire_products tp ON tp.model_id = tm.id
    JOIN tire_stock ts ON ts.sku = tp.sku
    WHERE ts.stock_quantity > 0
      AND tm.type_id != :disc_type_id
"""


async def extract_catalog_phrases(db_engine: Any) -> list[str]:
    """Extract manufacturer + model names from tire catalog.

    Manufacturers: all distinct names from tire_models.
    Models: only tire models (not discs) that have products currently in stock.
    Model names are filtered to contain a real word (4+ letters) — this drops
    internal codes like A502, ADR26 while keeping Blizzak, Pilot Sport, etc.

    All Latin names are transliterated to Cyrillic (STT outputs Cyrillic only).
    """
    from sqlalchemy import text

    phrases: set[str] = set()

    try:
        async with db_engine.begin() as conn:
            # 1. Manufacturer names
            result = await conn.execute(text(_SQL_MANUFACTURERS))
            for row in result:
                name = row[0].strip()
                if not name:
                    continue
                _add_cyrillic_phrase(phrases, name)

            # 2. Model names (tires with stock only)
            result = await conn.execute(
                text(_SQL_MODEL_NAMES),
                {"disc_type_id": _DISC_TYPE_ID},
            )
            for row in result:
                raw = row[0].strip().rstrip(".")
                if not raw:
                    continue
                # Skip codes without real words (A502, FM601, etc.)
                if not _WORD_4PLUS_LETTERS.search(raw):
                    continue
                _add_cyrillic_phrase(phrases, raw)

    except Exception:
        logger.exception("Failed to extract catalog phrases")

    return sorted(phrases)


# ═══════════════════════════════════════════════════════════
#  4. Redis persistence and cache
# ═══════════════════════════════════════════════════════════


async def refresh_phrase_hints(db_engine: Any, redis: Redis) -> dict[str, Any]:
    """Rebuild phrase hints: base + auto (from catalog) + custom (preserved).

    If base_customized=True, preserves the user-modified base list.
    Returns stats dict.
    """
    global _cache, _cache_ts

    auto = await extract_catalog_phrases(db_engine)

    # Preserve existing custom phrases and check base_customized flag
    custom: list[str] = []
    base_customized = False
    base = get_base_phrases()
    try:
        raw = await redis.get(REDIS_KEY)
        if raw:
            data = json.loads(raw if isinstance(raw, str) else raw.decode())
            custom = data.get("custom", [])
            base_customized = data.get("base_customized", False)
            if base_customized:
                base = data.get("base", base)
    except Exception:
        logger.debug("Failed to read existing phrase hints", exc_info=True)

    payload = {
        "base": base,
        "auto": auto,
        "custom": custom,
        "base_customized": base_customized,
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
                "base_customized": data.get("base_customized", False),
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
        "base_customized": False,
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


async def update_base_phrases(redis: Redis, phrases: list[str]) -> dict[str, Any]:
    """Replace base phrase list in Redis and mark as customized."""
    global _cache, _cache_ts

    try:
        raw = await redis.get(REDIS_KEY)
        if raw:
            data = json.loads(raw if isinstance(raw, str) else raw.decode())
        else:
            data = {"base": get_base_phrases(), "auto": [], "custom": []}
    except Exception:
        data = {"base": get_base_phrases(), "auto": [], "custom": []}

    data["base"] = phrases
    data["base_customized"] = True
    data["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    await redis.set(REDIS_KEY, json.dumps(data, ensure_ascii=False))

    _cache = ()
    _cache_ts = 0.0

    return {
        "base_count": len(phrases),
        "auto_count": len(data.get("auto", [])),
        "custom_count": len(data.get("custom", [])),
        "total": len(phrases) + len(data.get("auto", [])) + len(data.get("custom", [])),
        "google_limit": _GOOGLE_PHRASE_LIMIT,
        "base_customized": True,
    }


async def reset_base_to_defaults(redis: Redis) -> dict[str, Any]:
    """Reset base phrases to hardcoded defaults and clear customized flag."""
    global _cache, _cache_ts

    base = get_base_phrases()

    try:
        raw = await redis.get(REDIS_KEY)
        if raw:
            data = json.loads(raw if isinstance(raw, str) else raw.decode())
        else:
            data = {"base": base, "auto": [], "custom": []}
    except Exception:
        data = {"base": base, "auto": [], "custom": []}

    data["base"] = base
    data["base_customized"] = False
    data["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    await redis.set(REDIS_KEY, json.dumps(data, ensure_ascii=False))

    _cache = ()
    _cache_ts = 0.0

    return {
        "base_count": len(base),
        "auto_count": len(data.get("auto", [])),
        "custom_count": len(data.get("custom", [])),
        "total": len(base) + len(data.get("auto", [])) + len(data.get("custom", [])),
        "google_limit": _GOOGLE_PHRASE_LIMIT,
        "base_customized": False,
    }


def invalidate_cache() -> None:
    """Invalidate in-process cache (for testing)."""
    global _cache, _cache_ts
    _cache = ()
    _cache_ts = 0.0
