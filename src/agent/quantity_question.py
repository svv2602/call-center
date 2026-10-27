"""«Скільки шин потрібно?» is not asked again once the caller said how many.

Call 51f0652f (Про Колесо, 2026-10-27): the caller said «Мені потрібно дві
шини», the parser read 2 into the request, and the bot asked «Скільки шин
потрібно?» once more. A sentence of the bot asking the tyre count while the
request (``quantity`` of the «Підбір шин» progress) already has it is
replaced by the count itself — «Записала: дві шини.» — not dropped: the
question may be the whole reply, and an emptied turn is silence.

`order_quantity_refusal` is the same fact at the order: the bot wrote «одну
шину» after the caller said «більше не потрібно» — a count nobody named. A
one-item `submit_order_request` goes through only with the count the caller
said; once per call (loop-breaker), as the season guard.

Sales scope only: the loops wrap the stream only under ``sales_enabled``.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

from src.core.sentence_buffer import SentenceReady

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from src.core.sentence_buffer import BufferEvent

logger = logging.getLogger(__name__)

#: The bot asking the tyre count — also read by `merge_tire_query` to take a
#: bare «дві» as the answer.
ASKS_QUANTITY_RE = re.compile(
    r"скільки\s+(?:шин|штук|коліс|покришок)|яка\s+кількість|кількість\s+шин"
    r"|сколько\s+(?:шин|штук|колес)",
    re.IGNORECASE,
)

_COUNT_WORDS = {
    1: "одну шину",
    2: "дві шини",
    3: "три шини",
    4: "чотири шини",
    5: "п'ять шин",
    6: "шість шин",
    7: "сім шин",
    8: "вісім шин",
}


def asks_quantity(sentence: str) -> bool:
    text = sentence.strip()
    return text.endswith("?") and ASKS_QUANTITY_RE.search(text) is not None


def _known(query: dict[str, Any] | None) -> int | None:
    value = (query or {}).get("quantity")
    return value if isinstance(value, int) and value in _COUNT_WORDS else None


def _replacement(count: int) -> str:
    return f"Записала: {_COUNT_WORDS[count]}."


def _log(call_id: str, site: str, text: str, count: int) -> None:
    logger.info(
        "quantity_question_replaced: call=%s, site=%s, count=%d, text=%r",
        call_id,
        site,
        count,
        text[:200],
    )


_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def replace_quantity_question_text(
    text: str, query: dict[str, Any] | None, call_id: str = "unknown"
) -> str:
    """The text path: the count question of a whole reply → the count."""
    count = _known(query)
    if count is None or not text or not text.strip():
        return text
    out: list[str] = []
    changed = False
    for sentence in _SENTENCE_SPLIT.split(text.strip()):
        if asks_quantity(sentence):
            _log(call_id, "text_path", sentence, count)
            changed = True
            if _replacement(count) not in out:
                out.append(_replacement(count))
            continue
        out.append(sentence)
    return " ".join(out) if changed else text


async def replace_quantity_question(
    stream: AsyncIterator[BufferEvent],
    query: dict[str, Any] | None,
    call_id: str = "unknown",
) -> AsyncIterator[BufferEvent]:
    """The spoken path: fragments held to the end of the sentence."""
    count = _known(query)
    held: list[SentenceReady] = []
    said = False

    def settle() -> list[SentenceReady]:
        nonlocal held, said
        queued = held
        held = []
        if not queued or count is None:
            return queued
        text = " ".join(e.text for e in queued).strip()
        if not asks_quantity(text):
            return queued
        _log(call_id, "stream", text, count)
        if said:
            return []
        said = True
        return [SentenceReady(text=_replacement(count))]

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


def order_quantity_refusal(
    items: list[dict[str, Any]], stated: Any, *, guard_fired: bool
) -> dict[str, Any] | None:
    """The quantity check of a one-item order, or ``None`` to allow."""
    if guard_fired or len(items) != 1:
        return None
    asked = items[0].get("quantity")
    if not isinstance(stated, int) or stated not in _COUNT_WORDS:
        return {
            "status": "missing_field",
            "field": "quantity",
            "message": (
                "Заявку ще НЕ створено: кількість шин клієнт не назвав. Запитай "
                "«Скільки шин потрібно?» і після відповіді виклич submit_order_request "
                "ще раз з цією кількістю."
            ),
        }
    if asked == stated:
        return None
    return {
        "status": "missing_field",
        "field": "quantity",
        "message": (
            f"Заявку ще НЕ створено: клієнт назвав {_COUNT_WORDS[stated]}, а в заявці "
            f"{asked}. Виклич submit_order_request ще раз з quantity={stated}; якщо "
            f"клієнтові вже назвав іншу кількість — виправ її вголос."
        ),
    }
