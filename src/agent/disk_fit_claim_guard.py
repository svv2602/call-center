"""No «підходять» from the LLM when the code could not confirm the fit.

Goldset №4 (2026-09-28, `disk_no_car_data_cannot_confirm`): for a car the
wheel catalogue has no data on, every offered wheel carries
``fit.status == cannot_confirm`` — and the model still told the caller the
wheels fit the Tavria. The verdict is spoken by the loop itself
(`disk_caveat_phrase`); this guard removes the model's own sentences that
affirm compatibility in a turn where every verdict was ``cannot_confirm``.

A sentence is a claim when it names «підходить / підійде / подходит / …»
with no negation or question word («не», «чи», «ли», …) before it and is not
a question. «Не можу підтвердити, що підходять» and «не підходить» stay.
The code's own caveats never pass through here.

Sales scope only: the loops record ``search_disks`` results under
``sales_enabled`` and wrap the stream only then.
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

_CANNOT_CONFIRM = "cannot_confirm"

_CLAIM = re.compile(r"(?<!\w)(?:підход\w*|підійд\w*|подход\w*|подойд\w*)", re.IGNORECASE)
#: A negation or a question word before the claim word turns it into a
#: disclaimer or a question («не можу підтвердити, що підходять», «чи підійдуть»).
_NOT_A_CLAIM = re.compile(
    r"(?<!\w)(?:не|ні|немає|нема|нет|ни|чи|ли|якщо|если)(?!\w)", re.IGNORECASE
)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


class DiskFitClaimState:
    """Per turn: the fit verdicts of every ``search_disks`` result seen so far."""

    def __init__(self) -> None:
        self._statuses: list[str] = []

    def note(self, result: Any) -> None:
        """Record the verdicts of a ``search_disks`` result (anything else is ignored)."""
        if not isinstance(result, dict):
            return
        for item in result.get("items") or []:
            fit = item.get("fit") if isinstance(item, dict) else None
            if isinstance(fit, dict):
                self._statuses.append(str(fit.get("status") or _CANNOT_CONFIRM))

    @property
    def active(self) -> bool:
        """True when this turn's wheels were offered and none could be confirmed."""
        return bool(self._statuses) and all(s == _CANNOT_CONFIRM for s in self._statuses)


def collect_disk_verdict(raw: Any, state: DiskFitClaimState, caveats: list[str]) -> None:
    """After a ``search_disks`` call: record its verdicts, queue the verdict phrase.

    One call site per road (the model's own call and the substitution, in
    each loop) — the twin of the ``tire_caveat_phrase`` collection.
    """
    from src.agent.tool_result_compressor import disk_caveat_phrase

    state.note(raw)
    phrase = disk_caveat_phrase(raw)
    if phrase and phrase not in caveats:
        caveats.append(phrase)


def is_fit_claim(sentence: str) -> bool:
    """Does ``sentence`` affirm that the wheels fit the car?"""
    text = sentence.strip()
    if not text or text.endswith("?"):
        return False
    match = _CLAIM.search(text)
    if match is None:
        return False
    return _NOT_A_CLAIM.search(text[: match.start()]) is None


def _log(call_id: str, site: str, text: str) -> None:
    logger.warning("disk_fit_claim_dropped: call=%s, site=%s, text=%r", call_id, site, text[:200])


def drop_fit_claims_text(
    text: str, state: DiskFitClaimState, call_id: str = "unknown", site: str = "text_path"
) -> str:
    """The text path: drop the claim sentences of a whole reply."""
    if not state.active or not text or not text.strip():
        return text
    kept: list[str] = []
    for sentence in _SENTENCE_SPLIT.split(text.strip()):
        if is_fit_claim(sentence):
            _log(call_id, site, sentence)
            continue
        kept.append(sentence)
    return " ".join(kept)


async def drop_fit_claims(
    stream: AsyncIterator[BufferEvent],
    state: DiskFitClaimState,
    call_id: str = "unknown",
) -> AsyncIterator[BufferEvent]:
    """The spoken path: drop claim sentences before they reach TTS.

    Fragments are held to the end of the sentence, as in
    `guard_network_claims`: «Не можу підтвердити,» and «що підходять.» may
    arrive as two fragments, and the second judged alone would read as a claim.
    ``state`` is read per sentence — the round's tools are back by then.
    """
    held: list[SentenceReady] = []

    def settle() -> list[SentenceReady]:
        nonlocal held
        queued = held
        held = []
        if not queued or not state.active:
            return queued
        text = " ".join(e.text for e in queued).strip()
        if is_fit_claim(text):
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
