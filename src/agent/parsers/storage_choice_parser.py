"""`storage_choice_parser` — STORAGE. Two lists of markers, not one.

Moved out of `src/core/pipeline.py`, where the detector settled during Wave 4-A
because half of it depends on whether the bot had just asked the Krok 2
question — and `compound_parse` has no «last bot utterance» parameter to hang
that on. `ParseContext` does, so this is where it belongs.

Both lists are copied **verbatim**: same order, same wording, same STT
mangles. They were collected off live calls (2026-08-03 14:55/14:56,
2026-09-03 10:37/10:38 with Kusaeva) and «tidying» them is how a caller who
said «за собою» stops being understood.

The two lists are not duplication — they carry different costs:

* the wide legacy list («тобою», «за собою», «не маю») feeds an LLM nudge. A
  false positive is cheap: the LLM asks anyway;
* the narrow self-evident list is grounds to **skip a state**. A false
  positive there means the question was silently never asked.

Merging them would bring back «STORAGE skipped because the caller said
„тобою“». The separation is expressed through confidence against the single
threshold (§3.6):

======================================  ===========  ==========================
signal                                  confidence   consequence
======================================  ===========  ==========================
self-evident own / contract             `1.0`        may skip STORAGE
wide list, bot did ask Krok 2           `0.9`        applies, but only *inside*
                                                     STORAGE, where there is
                                                     nothing left to skip
wide list, bot did not ask              `0.5`        `unresolved`; feeds the
                                                     legacy nudge, skips
                                                     nothing
======================================  ===========  ==========================

The `0.9` level is unreachable in the broad pass, which has no bot utterance —
one field, two confidences, which is the whole argument for variant A (§3.1).

**Ambiguity is not a coin flip.** An utterance carrying both readings («свої
привезу, чи те що у вас на зберіганні?») yields no value. `detect_storage_choice`
already does this for the narrow lists; §3.6 extends it to the wide one, so a
wide «own» hit next to a self-evident «contract» hit resolves to nothing and
the question gets asked.

Wave 6-B debt (§Scope forbids touching `src/core/pipeline.py` in this wave):
`pipeline.detect_own_tires` / `pipeline.detect_storage_choice` still exist and
are still what the legacy nudge calls. Wave 6-B removes them, points the
pipeline at this module (or at a `src/agent/storage_detect.py` re-export, as
§3.6 step 1 describes), and proves the nudge stayed bit-for-bit identical with
`FSM_ENABLED=false` on the corpus quoted in the comments below.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from src.agent.parsers.base import NOT_MENTIONED, ParseOutcome, graded, unresolved

if TYPE_CHECKING:
    from src.agent.parsers.base import ParseContext

logger = logging.getLogger(__name__)

#: Self-evident phrasing — no context needed, high enough to skip the state.
_SELF_EVIDENT_CONFIDENCE = 1.0
#: Wide list, and the bot had just asked the storage question.
_ASKED_CONFIDENCE = 0.9
#: Wide list without that context: a nudge for the LLM, never a state skip.
_UNASKED_CONFIDENCE = 0.5


_STORAGE_OWN_HINTS: tuple[str, ...] = (
    "привезу з собою",
    "привезли з собою",
    "привозим з собою",
    "привозимо з собою",
    "привожу з собою",
    "привожу с собой",
    "привозим с собой",
    "везу з собою",
    "везу свої",
    "свої привезу",
    "свій комплект",
    "шини в мене",
    "шини мої",
    "мої шини",
    "з собою везу",
    # Wave 4 (2026-09-03) — STT mangles «з собою» → «за собою»
    # (calls 10:37, 10:38 with Kusaeva). Also drops the «з»
    # entirely leaving «собою». And Russian preposition drift
    # «с собой» → «за собой». All → own tires.
    "за собою",
    "за собой",
    "с собою",
    # Wave 4 STT: «привезу» → «приложу»/«приложишь»/«прикладу»
    # (rare word-level mangle; call 10:38 «приложишь с собой»).
    "приложу з собою",
    "приложу с собой",
    "приложишь з собою",
    "приложишь с собой",
    "прикладу з собою",
    "прикладу с собой",
    # Short affirmations to the storage question — STT often
    # cuts «з собою» down to «тобою» / «з тобою» (call 2026-08-03
    # 14:56). And the rus/ukr mix «Шины будут любую» is real STT
    # output for «шини будуть з собою».
    "тобою",
    "з тобою",
    "шини будуть з собою",
    "шини будуть с собой",
    "шины будут с собой",
    "шины будут з собою",
    "шины будут любую",
    "будуть з собою",
    "будут с собой",
    # Explicit "no storage" phrasings — client denies having a
    # storage contract, implicitly = own tires. Call 2026-08-03:
    # STT «в мене нема сина зберігає» (mangled) → repeat loop.
    "нема зберігання",
    "немає зберігання",
    "нема ніякого зберігання",
    "немає ніякого зберігання",
    "не здавав на зберігання",
    "не здавали на зберігання",
    "ніколи не здавав",
    "нема сина зберіга",  # STT mangle of «немає жодного зберігання»/«немає нашого»
    "немає сина зберіга",
)

# Context-scoped: «в мене нема»/«у мене немає» = storage denial ONLY if the bot
# just asked the storage question (Krok 2).
_STORAGE_OWN_HINTS_WHEN_ASKED: tuple[str, ...] = (
    "в мене нема",
    "у мене немає",
    "в мене немає",
    "у мене нема",
    "не маю",
    "немає у мене",
    "нема у мене",
)

_STORAGE_ASKING_MARKERS: tuple[str, ...] = (
    "зберіган",
    "привозите свої",
    "з собою чи",
)

# FSM-only, deliberately narrower than the legacy list above: phrases that are
# self-evident *without* the bot having asked anything. The legacy list is full
# of context-dependent STT mangles («тобою») that are safe as a nudge to the
# LLM but not safe as an FSM state skip.
_STORAGE_SELF_EVIDENT_OWN: tuple[str, ...] = (
    "з собою",
    "с собой",
    "свої шини",
    "свои шины",
    "власні шини",
    "свій комплект",
)
_STORAGE_SELF_EVIDENT_CONTRACT: tuple[str, ...] = (
    "зі зберігання",
    "з зберігання",
    "на зберіганні",
    "у вас на зберіганні",
    "зберігання у вас",
    "зі складу",
)


def _bot_is_asking_storage(last_bot_utterance: str) -> bool:
    """True if the bot's last utterance was the Krok 2 storage question."""
    lowered = (last_bot_utterance or "").lower()
    return any(marker in lowered for marker in _STORAGE_ASKING_MARKERS)


def detect_own_tires(text: str, *, asking_storage: bool) -> bool:
    """Legacy 'client brought their own tires' detector.

    Extracted verbatim from _transcript_processor_loop so the FSM mapping layer
    and the legacy nudge share one list instead of drifting apart. Semantics are
    unchanged: the context-scoped extras only apply right after the bot asked
    the storage question.
    """
    lowered = (text or "").lower()
    hints = _STORAGE_OWN_HINTS
    if asking_storage:
        hints = (*hints, *_STORAGE_OWN_HINTS_WHEN_ASKED)
    return any(h in lowered for h in hints)


def detect_storage_choice(text: str) -> str | None:
    """Self-evident storage choice for the FSM mapping layer.

    Returns "own", "contract" or None. Unlike :func:`detect_own_tires` this is
    context-free — it must be safe to run on any utterance without knowing what
    the bot just said, because it is allowed to *skip an FSM state*.

    An utterance mentioning both is ambiguous and yields None rather than a
    coin flip.
    """
    lowered = (text or "").lower()
    own = any(h in lowered for h in _STORAGE_SELF_EVIDENT_OWN)
    contract = any(h in lowered for h in _STORAGE_SELF_EVIDENT_CONTRACT)
    if own and contract:
        return None
    if own:
        return "own"
    if contract:
        return "contract"
    return None


class StorageChoiceParser:
    """STORAGE."""

    name = "storage_choice_parser"
    field_name = "storage_choice"
    aresolve = None

    def parse(self, ctx: ParseContext) -> ParseOutcome:
        text = (ctx.customer_text or "").strip()
        if not text:
            return NOT_MENTIONED

        lowered = text.lower()
        self_evident_own = any(h in lowered for h in _STORAGE_SELF_EVIDENT_OWN)
        self_evident_contract = any(h in lowered for h in _STORAGE_SELF_EVIDENT_CONTRACT)

        asking = _bot_is_asking_storage(ctx.last_bot_utterance)
        wide_own = detect_own_tires(text, asking_storage=asking)

        if (self_evident_own or wide_own) and self_evident_contract:
            logger.debug("storage_choice_parser: both readings present — no value")
            return unresolved()

        if self_evident_own:
            return graded("own", _SELF_EVIDENT_CONFIDENCE)
        if self_evident_contract:
            return graded("contract", _SELF_EVIDENT_CONFIDENCE)
        if wide_own:
            return graded("own", _ASKED_CONFIDENCE if asking else _UNASKED_CONFIDENCE)
        return NOT_MENTIONED


PARSER = StorageChoiceParser()
