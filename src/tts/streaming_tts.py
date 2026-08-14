"""Streaming TTS synthesizer — converts SentenceReady events to PCM audio."""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from src.core.sentence_buffer import SentenceReady
from src.llm.models import StreamDone, ToolCallDelta, ToolCallEnd, ToolCallStart

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from src.core.sentence_buffer import BufferEvent
    from src.tts.base import TTSEngine

logger = logging.getLogger(__name__)


# ─── ISO date → Ukrainian "N місяць" normalization ─────────────────────────
# Testers 2026-07-27: bot uses YYYY-MM-DD in final confirmation
# ("перевіримо: 2026-07-31 о 17:00, ..."). Prompt tells the LLM to speak
# "N місяць" but a stubborn ISO string still leaks through. This is the
# last-mile safety net — a pure text substitution just before TTS.
_UKR_MONTHS_GEN: dict[int, str] = {
    1: "січня", 2: "лютого", 3: "березня", 4: "квітня", 5: "травня", 6: "червня",
    7: "липня", 8: "серпня", 9: "вересня", 10: "жовтня", 11: "листопада", 12: "грудня",
}
_ISO_DATE_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")


def _normalize_iso_dates(text: str) -> str:
    """Replace `YYYY-MM-DD` with `D місяця` (year dropped if it equals
    current year, kept otherwise). Handles malformed dates gracefully.
    """
    import datetime

    current_year = datetime.datetime.now().year

    def _sub(m: re.Match[str]) -> str:
        year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if month not in _UKR_MONTHS_GEN or not (1 <= day <= 31):
            return m.group(0)
        day_str = str(day)  # drop leading zero
        month_name = _UKR_MONTHS_GEN[month]
        # Include year only when it differs from the current year — tests
        # explicitly asked for year to be omitted by default.
        if year == current_year:
            return f"{day_str} {month_name}"
        return f"{day_str} {month_name} {year} року"

    return _ISO_DATE_RE.sub(_sub, text)


# ─── Tire size and house-number normalizers ────────────────────────────────
# Testers 2026-08-11: "225/50 R18" is read by TTS with the slash and Latin
# "R" pronounced literally («двісті двадцять п'ять слеш п'ятдесят ар»).
# House numbers like "72Б"/"1Д"/"55К" are read with English/mixed letter
# names ("сімдесят два бі"), not the Ukrainian conversational "бе/де/ка".
# These normalisers keep the digits intact (Google TTS says them fine
# in Ukrainian) and fix only the surrounding characters.

# Match: "225/50 R18", "225/50R18", "225/50/R18" (STT variants). Groups: W, H, D.
_TIRE_SIZE_RE = re.compile(
    r"\b(\d{3})\s*/\s*(\d{2})\s*[/]?\s*[Rr]\s*(\d{2})\b"
)

# Letter → Ukrainian conversational pronunciation. Uses only the letters that
# appear in Ukrainian house-number suffixes (DSTU vehicle-plate alphabet).
_HOUSE_LETTER_UK: dict[str, str] = {
    "А": "а", "Б": "бе", "В": "ве", "Г": "ге", "Д": "де",
    "Е": "е", "Ж": "же", "З": "зе", "И": "и", "І": "і",
    "К": "ка", "Л": "ел", "М": "ем", "Н": "ен", "О": "о",
    "П": "пе", "Р": "ер", "С": "ес", "Т": "те", "У": "у",
    "Ф": "еф", "Х": "ха", "Ц": "це", "Ч": "че", "Ш": "ша",
    "Щ": "ща", "Ю": "ю", "Я": "я",
}

# Match: "72Б", "1Д", "55К", "24А" — digits followed by ONE Cyrillic letter,
# as a whole token (surrounded by non-alphanumeric or line edges).
_HOUSE_NUM_RE = re.compile(
    r"(?<![A-Za-zА-Яа-яІіЇїЄєҐґ0-9])(\d{1,4})([А-ЯІЇЄҐ])(?![A-Za-zА-Яа-яІіЇїЄєҐґ0-9])"
)


def _normalize_tire_sizes(text: str) -> str:
    """Rewrite "225/50 R18" → "225 50 ер 18" so TTS pronounces each number
    naturally in Ukrainian and speaks "R" as «ер», not «ар»/«ес».
    """
    def _sub(m: re.Match[str]) -> str:
        return f"{m.group(1)} {m.group(2)} ер {m.group(3)}"
    return _TIRE_SIZE_RE.sub(_sub, text)


def _normalize_house_letter(text: str) -> str:
    """Rewrite "72Б" → "72 бе", "1Д" → "1 де" so TTS voices the letter
    the way Ukrainians actually say it in an address, not as its English
    or ISO name («бі» / «ди»).
    """
    def _sub(m: re.Match[str]) -> str:
        digits, letter = m.group(1), m.group(2)
        letter_upper = letter.upper()
        replacement = _HOUSE_LETTER_UK.get(letter_upper)
        if not replacement:
            return m.group(0)
        return f"{digits} {replacement}"
    return _HOUSE_NUM_RE.sub(_sub, text)


def _normalize_for_tts(text: str) -> str:
    """Compose all last-mile normalisers before handing text to TTS.

    Order matters: dates first (they contain only digits + dashes),
    then tire sizes (may share digits with house numbers otherwise),
    then house numbers (single-letter suffix on trailing digits).
    """
    text = _normalize_iso_dates(text)
    text = _normalize_tire_sizes(text)
    text = _normalize_house_letter(text)
    return text


@dataclass(frozen=True)
class AudioReady:
    """Raw PCM audio for one sentence, ready for AudioSocket delivery."""

    audio: bytes
    text: str  # original text (for logging/metrics)


TTSEvent = AudioReady | ToolCallStart | ToolCallDelta | ToolCallEnd | StreamDone


class StreamingTTSSynthesizer:
    """Converts SentenceReady → AudioReady via TTSEngine.synthesize().

    All other BufferEvents (tool calls, StreamDone) pass through unchanged.

    When prefetch=True (default), uses 1-slot lookahead: starts synthesizing
    the next sentence while the current one is being yielded/played.
    """

    def __init__(self, tts: TTSEngine, *, prefetch: bool = True) -> None:
        self._tts = tts
        self._prefetch = prefetch

    async def process(
        self,
        stream: AsyncIterator[BufferEvent],
    ) -> AsyncIterator[TTSEvent]:
        """Consume buffer events, synthesizing sentences into audio."""
        if not self._prefetch:
            async for event in self._process_sequential(stream):
                yield event
            return

        # Prefetch path: 1-slot lookahead
        pending_task: asyncio.Task[bytes] | None = None
        pending_text: str | None = None

        async for event in stream:  # type: ignore[assignment]
            if isinstance(event, SentenceReady):
                # Last-mile normalisation (ISO dates, tire sizes, house
                # numbers with letter suffix) so TTS speaks natural Ukrainian.
                normalized = _normalize_for_tts(event.text)
                # Launch new task FIRST so it runs while we await the old one
                new_task = asyncio.create_task(self._tts.synthesize(normalized))
                new_text = normalized

                # Now await previous task (new synthesis runs in parallel)
                if pending_task is not None:
                    try:
                        audio = await pending_task
                        yield AudioReady(audio=audio, text=pending_text)  # type: ignore[arg-type]
                    except Exception:
                        logger.warning("Prefetch TTS failed for '%s', skipping", pending_text)

                pending_task = new_task
                pending_text = new_text

            elif isinstance(event, (ToolCallStart, StreamDone)):
                # Flush pending synthesis before control events
                if pending_task is not None:
                    try:
                        audio = await pending_task
                        yield AudioReady(audio=audio, text=pending_text)  # type: ignore[arg-type]
                    except Exception:
                        logger.warning("Prefetch TTS failed for '%s', skipping", pending_text)
                    pending_task = None
                    pending_text = None
                yield event
            else:
                yield event

        # Flush any remaining pending task
        if pending_task is not None:
            try:
                audio = await pending_task
                yield AudioReady(audio=audio, text=pending_text)  # type: ignore[arg-type]
            except Exception:
                logger.warning("Prefetch TTS failed for '%s', skipping", pending_text)

    async def _process_sequential(
        self,
        stream: AsyncIterator[BufferEvent],
    ) -> AsyncIterator[TTSEvent]:
        """Sequential (no-prefetch) processing path."""
        async for event in stream:
            if isinstance(event, SentenceReady):
                normalized = _normalize_for_tts(event.text)
                audio = await self._tts.synthesize(normalized)
                yield AudioReady(audio=audio, text=normalized)
            else:
                yield event


async def synthesize_stream(
    stream: AsyncIterator[BufferEvent],
    tts: TTSEngine,
) -> AsyncIterator[TTSEvent]:
    """Convenience wrapper — create synthesizer and process stream."""
    synth = StreamingTTSSynthesizer(tts)
    async for event in synth.process(stream):
        yield event
