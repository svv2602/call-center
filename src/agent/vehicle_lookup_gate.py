"""The car's factory sizes, looked up by the code; studs asked only for winter.

Probe 2026-09-29 (`camry_compare_taurus_bridgestone`: «шины на камри» →
«шины R17 на Toyota Camry» → «летние»), ProKoleso 0/2: once the model never
called `get_vehicle_tire_sizes` and asked the year and the profile for two
turns; once it had the sizes but on «летние» asked «Шиповані чи без шипів?»
and searched nothing. Three roots, three parts here:

1. **Forced lookup.** A caller's utterance about tyres that names a car
   (`disk_intent.vehicle_words` is the cheap pre-filter) gets
   `get_vehicle_tire_sizes` run by the code before the first LLM round, with
   the internal argument ``vehicle_text`` — the utterance itself; the handler
   (`main._build_tool_router`) reads the car out of it with
   `StoreClient.resolve_vehicle_text` and needs a brand AND a model, never a
   guess. The call goes through ``ToolRouter.execute`` (the audit row); the
   history gets the pair only for a car found and not yet looked up in this
   call, and its ``tool_use`` carries the car the catalogue read
   (``brand`` / ``model`` / ``year``) — the arguments the model itself would
   pass. ``vehicle_text`` is never in the history, as with `search_disks`, so
   the model never sees it as an argument of its own.
2. **The factory size → the request.** A `get_vehicle_tire_sizes` result
   (forced or the model's) is kept per call; when the caller named a
   diameter and exactly one factory size has it — or the car has exactly one
   factory size — that size goes into the loop's request (``sizes``) as if the
   caller had said it, so the search gate G (`tire_search_gate`) searches on
   «летние» by itself. Never over a size the caller named.
3. **Studs only in winter.** A sentence of the model asking about studs /
   «липучка» is dropped when the request's season is known and is not winter
   (summer / all_season / any). Season unknown or winter — untouched.

Loop-breakers: one forced lookup per turn (one `plan` per turn); the same car
words are not looked up twice in a call; a car already in the history (found
by the model or the code) does not get a second pair.

Sales scope only: with ``sales_enabled`` off nothing is armed, the request is
not touched, no sentence is dropped.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

from src.agent.disk_intent import has_disk_intent, vehicle_words
from src.agent.parsers.tire_query import parse_tire_size
from src.core.sentence_buffer import SentenceReady

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterable

    from src.core.sentence_buffer import BufferEvent

logger = logging.getLogger(__name__)

LOOKUP_TOOL = "get_vehicle_tire_sizes"

_DIAMETER_MIN = 10
_DIAMETER_MAX = 30

#: The utterance is about tyres: a tyre noun (UA + RU).
_TYRE_NOUN = re.compile(
    r"(?<!\w)(?:шин\w*|резин\w*|гум[аиуі]|покришк\w*|покрышк\w*|колес\w*|коліс\w*|скат\w*)(?!\w)",
    re.IGNORECASE,
)
#: The bot's last question was about the car («Яке у вас авто?»).
_BOT_ASKED_CAR = re.compile(r"(?<!\w)(?:авто\w*|машин\w*|марк[аиуі]|модел\w*)(?!\w)", re.IGNORECASE)
_SENTENCE = re.compile(r"[^.!?]*[.!?]?")
#: Words of a tyre request that `vehicle_words` keeps but that name no car
#: («шини на зиму», «купити гуму на літо», «чотири шини», «доставка»).
_NOT_A_CAR = re.compile(
    r"\d+|зим\w*|літ\w*|лет\w*|весн\w*|осін\w*|осен\w*|сезон\w*|куп\w*|замов\w*|заказ\w*"
    r"|доставк\w*|оплат\w*|одн[аіуе]|одна|дв[аіе]|три|чотир\w*|четыр\w*|пар[аиуі]|штук\w*"
)

#: Seasons with no studs: studs exist only on winter tyres.
_STUDLESS_SEASONS = frozenset({"summer", "all_season", "any"})
_STUD_WORD = re.compile(
    r"(?<!\w)(?:шип\w*|нешип\w*|липуч\w*|фрикцій\w*|фрикцион\w*)", re.IGNORECASE
)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _bot_asked_car(last_bot_text: str) -> bool:
    return any(
        s.strip().endswith("?") and _BOT_ASKED_CAR.search(s)
        for s in _SENTENCE.findall(last_bot_text or "")
    )


def turn_is_about_tyres(user_text: str, last_bot_text: str = "") -> bool:
    """A tyre noun, a size, a season or studs — or the answer to «яке авто?»."""
    from src.agent.parsers.tire_query import parse_nail_type
    from src.core.pipeline import parse_tire_season

    if _TYRE_NOUN.search(user_text):
        return True
    if parse_tire_size(user_text) or parse_tire_season(user_text) or parse_nail_type(user_text):
        return True
    return _bot_asked_car(last_bot_text)


def car_words(user_text: str) -> tuple[list[str], int | None]:
    """`vehicle_words` without the words a tyre request itself is made of."""
    from src.agent.parsers.tire_query import extract_tire_brands, parse_nail_type
    from src.core.pipeline import parse_tire_season

    words, year = vehicle_words(user_text)
    kept = [
        w
        for w in words
        if not _NOT_A_CAR.fullmatch(w)
        and not extract_tire_brands(w)
        and parse_tire_season(w) is None
        and parse_nail_type(w) is None
    ]
    return kept, year


#: Where a tyre request names the car: «шини НА камрі», «ДЛЯ Октавії»,
#: «ПІД Гольф», «у МЕНЕ Тойота». The car is in the words right after it.
_CAR_MARKER = re.compile(r"(?<!\w)(?:на|для|під|под|мене|меня)\s+([^,.!?;:]+)", re.IGNORECASE)
_MARKER_SPAN_WORDS = 3


def named_car_words(user_text: str, last_bot_text: str = "") -> tuple[list[str], int | None]:
    """The words that may name a car in this utterance, and the model year.

    The answer to the bot's «Яке у вас авто?» is all car; otherwise only the
    words right after «на / для / під / мене» count — a filler word elsewhere
    («бренд будь-який, без побажань») is no car and must not cost a lookup.
    """
    _, year = vehicle_words(user_text)
    if _bot_asked_car(last_bot_text):
        return car_words(user_text)[0], year
    words: list[str] = []
    for match in _CAR_MARKER.finditer(user_text or ""):
        span = " ".join(match.group(1).split()[:_MARKER_SPAN_WORDS])
        for word in car_words(span)[0]:
            if word not in words:
                words.append(word)
    return words, year


def said_diameter(user_text: str) -> int | None:
    """The diameter the caller said without a full size («R17», «сімнадцятий радіус»)."""
    sizes = parse_tire_size(user_text or "") or []
    if not sizes or any(s.is_full for s in sizes):
        return None
    found = {
        s.diameter for s in sizes if s.diameter and _DIAMETER_MIN <= s.diameter <= _DIAMETER_MAX
    }
    return found.pop() if len(found) == 1 else None


def _size_diameter(size: str) -> int | None:
    parsed = [s for s in parse_tire_size(size) or [] if s.is_full]
    return parsed[0].diameter if len(parsed) == 1 else None


def factory_size(stock_sizes: list[str], diameter: int | None) -> str | None:
    """The one factory size the request can take, or ``None``.

    A named diameter with exactly one factory size of it; no diameter and a
    single factory size. Anything else is the caller's choice to make.
    """
    if diameter is not None:
        matching = [s for s in stock_sizes if _size_diameter(s) == diameter]
        return matching[0] if len(matching) == 1 else None
    return stock_sizes[0] if len(stock_sizes) == 1 else None


def _int_or_none(value: Any) -> int | None:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


class VehicleLookupGate:
    """Per call: the code's own car lookups and the factory size they give."""

    def __init__(self, *, sales_enabled: bool) -> None:
        self._sales = sales_enabled
        self._asked_words: set[tuple[tuple[str, ...], int | None]] = set()
        self._cars: set[tuple[str, str, int | None]] = set()
        self._stock_sizes: list[str] = []
        self._diameter: int | None = None
        self._written: str | None = None
        self._year: int | None = None

    # ── 1. The forced lookup ────────────────────────────────────────────

    def plan(
        self, user_text: str, last_bot_text: str, tools: Iterable[dict[str, Any]]
    ) -> dict[str, Any] | None:
        """``get_vehicle_tire_sizes`` arguments to run now, or ``None``. Once per turn."""
        if not self._sales or not any(t.get("name") == LOOKUP_TOOL for t in tools):
            return None
        from src.core.pipeline import _FITTING_REQUEST_RE

        text = (user_text or "").strip()
        if _FITTING_REQUEST_RE.search(text.lower()) or has_disk_intent(text):
            return None
        words, year = named_car_words(text, last_bot_text)
        if not words:
            return None
        if not turn_is_about_tyres(text, last_bot_text):
            return None
        key = (tuple(words), year)
        if key in self._asked_words:
            return None
        self._asked_words.add(key)
        self._year = year
        logger.info("Forced vehicle lookup words=%s year=%s text=%r", words, year, text[:120])
        return {"vehicle_text": text}

    def settle(self, raw: Any) -> dict[str, Any] | None:
        """After a forced lookup: the car for the history's ``tool_use``, or ``None``.

        ``None`` — nothing goes into the history: the car was not read (default
        deny), or this call already has its sizes.
        """
        if not self._note(raw, self._year):
            logger.info("Forced vehicle lookup: nothing for the history (%r)", _brief(raw))
            return None
        args: dict[str, Any] = {"brand": raw["brand"], "model": raw["model"]}
        if self._year is not None:
            args["year"] = self._year
        return args

    def note_model_call(self, args: dict[str, Any], raw: Any) -> None:
        """A `get_vehicle_tire_sizes` the model made itself: keep its sizes."""
        self._note(raw, _int_or_none((args or {}).get("year")))

    def _note(self, raw: Any, year: int | None) -> bool:
        """Keep a found result's sizes; True when the car is new to this call."""
        if not isinstance(raw, dict) or raw.get("found") is not True:
            return False
        brand, model = str(raw.get("brand") or ""), str(raw.get("model") or "")
        stock = [str(s) for s in raw.get("stock_sizes") or [] if s]
        if not brand or not model or not stock:
            return False
        # A car with front/rear pairs has no one size to take.
        self._stock_sizes = [] if raw.get("staggered_pairs") else stock
        key = (brand.lower(), model.lower(), year)
        if key in self._cars:
            return False
        self._cars.add(key)
        return True

    # ── 2. The factory size in the request ──────────────────────────────

    def note_turn(self, user_text: str, query: dict[str, Any] | None) -> None:
        """Remember the diameter the caller said this turn (not about wheels)."""
        if not self._sales or has_disk_intent(user_text or ""):
            return
        diameter = said_diameter(user_text) or _int_or_none((query or {}).get("diameter"))
        if diameter is not None:
            self._diameter = diameter

    def apply(self, query: dict[str, Any] | None) -> dict[str, Any] | None:
        """``query`` with the one factory size in ``sizes`` — never over the caller's own."""
        if not self._sales:
            return query
        size = factory_size(self._stock_sizes, self._diameter)
        if size is None:
            return query
        current = (query or {}).get("sizes")
        if current and current != [self._written]:
            return query
        out = dict(query or {})
        out["sizes"] = [size]
        out.pop("diameter", None)
        if self._written != size:
            logger.info("Factory size into the request: %s", size)
        self._written = size
        return out


def _brief(raw: Any) -> Any:
    if isinstance(raw, dict):
        return {k: raw.get(k) for k in ("found", "brand", "model", "vehicle_resolved") if k in raw}
    return type(raw).__name__


# ── 3. Studs only in winter ─────────────────────────────────────────────


def studs_out_of_season(query: dict[str, Any] | None) -> bool:
    """True when the request's season is known and has no studs."""
    return str((query or {}).get("season") or "") in _STUDLESS_SEASONS


def asks_about_studs(sentence: str) -> bool:
    """Is ``sentence`` a question about studs / «липучка»?"""
    text = sentence.strip()
    return text.endswith("?") and _STUD_WORD.search(text) is not None


def _log(call_id: str, site: str, text: str) -> None:
    logger.warning("stud_question_dropped: call=%s, site=%s, text=%r", call_id, site, text[:200])


def drop_stud_questions_text(
    text: str, query: dict[str, Any] | None, call_id: str = "unknown", site: str = "text_path"
) -> str:
    """The text path: drop the stud questions of a whole reply out of season."""
    if not studs_out_of_season(query) or not text or not text.strip():
        return text
    kept: list[str] = []
    for sentence in _SENTENCE_SPLIT.split(text.strip()):
        if asks_about_studs(sentence):
            _log(call_id, site, sentence)
            continue
        kept.append(sentence)
    return " ".join(kept)


async def drop_stud_questions(
    stream: AsyncIterator[BufferEvent],
    query: dict[str, Any] | None,
    call_id: str = "unknown",
) -> AsyncIterator[BufferEvent]:
    """The spoken path: drop stud questions before they reach TTS.

    Fragments are held to the end of the sentence (as `drop_fit_claims`):
    «Вам шиповані» and «чи без шипів?» may arrive apart.
    """
    active = studs_out_of_season(query)
    held: list[SentenceReady] = []

    def settle() -> list[SentenceReady]:
        nonlocal held
        queued = held
        held = []
        if not queued or not active:
            return queued
        text = " ".join(e.text for e in queued).strip()
        if asks_about_studs(text):
            _log(call_id, "stream", text)
            return []
        return queued

    async for event in stream:
        if isinstance(event, SentenceReady):
            held.append(event)
            if event.text.rstrip().endswith((".", "!", "?")):
                for queued in settle():
                    yield queued
            continue
        for queued in settle():
            yield queued
        yield event

    for queued in settle():
        yield queued
