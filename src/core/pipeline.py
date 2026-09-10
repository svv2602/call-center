"""Pipeline Orchestrator: AudioSocket → STT → LLM → TTS → AudioSocket.

Coordinates real-time data flow between all components for a single call.
Supports barge-in (interrupting TTS when the caller speaks).
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime
import logging
import re
import time
import zoneinfo
from typing import TYPE_CHECKING, Any

from src.agent.confirm_detect import asked_for_confirmation, is_confirmation
from src.agent.parsers.storage_choice_parser import (
    _bot_is_asking_storage,
    detect_own_tires,
    detect_storage_choice,
)
from src.agent.prompts import (
    EMPTY_RESPONSE_SOFT_TEXT,
    ERROR_TEXT,
    FAREWELL_ORDER_TEXT,
    FAREWELL_TEXT,
    GREETING_TEXT,
    SILENCE_BRAND_REPROMPT_TEXT,
    SILENCE_COLOR_REPROMPT_TEXT,
    SILENCE_CONFIRM_REPROMPT_TEXT,
    SILENCE_PROMPT_TEXT,
    SILENCE_TIMEOUT_1_TEXT,
    SILENCE_TIMEOUT_2_TEXT,
    TRANSFER_TEXT,
    WAIT_ACK_POOL,
    WAIT_AVAILABILITY_POOL,
    WAIT_DEFAULT_POOL,
    WAIT_FITTING_POOL,
    WAIT_SEARCH_POOL,
    WAIT_STATUS_POOL,
    WAIT_TEXT,
    compute_order_stage,
    detect_scenario_from_text,
)
from src.core.audio_socket import AUDIO_FRAME_BYTES, AudioSocketConnection, PacketType
from src.core.call_session import SILENCE_TIMEOUT_SEC, CallSession, CallState
from src.monitoring.metrics import (
    audiosocket_to_stt_ms,
    barge_in_total,
    bot_filler_stripped_total,
    false_booking_claim_total,
    fsm_compound_preparse_fields_total,
    fsm_interrupt_total,
    fsm_voice_total,
    tts_delivery_ms,
)
from src.stt.base import STTConfig, STTEngine, Transcript

# Max time to wait for LLM agent to produce a response (seconds)
AGENT_PROCESSING_TIMEOUT_SEC = 45

# Barge-in suppression window after TTS ends (seconds).
# Prevents echo from speaker triggering false barge-in detection.
_BARGE_IN_SUPPRESSION_SEC = 0.3

# Default window to buffer multiple final transcripts (seconds).
# Google STT tends to finalize on ~500ms pauses, which fragments mid-utterance
# ("літніх" / "215" / "65" / "16" as 4 separate finals when the caller dictated
# a size with brief pauses). We buffer arrivals within this window and merge them.
# Actual value comes from STTConfig.transcript_buffer_sec; this is a fallback only.
_TRANSCRIPT_BUFFER_SEC_DEFAULT = 1.2

# Timeout for contextual farewell LLM call (seconds)
_FAREWELL_LLM_TIMEOUT_SEC = 3

# Keepalive silence — mobile carriers / SIP trunks drop the RTP session if
# audio flow to Asterisk becomes sparse for longer than a few frames.
# Asterisk app_audiosocket forwards our TCP audio to the peer as RTP at 50
# packets/sec (20 ms/frame); any gap in our TCP audio becomes a gap in the
# outbound RTP that the peer's mobile carrier can interpret as end-of-call.
# We fill the gap by streaming silence frames whenever `AudioSocketConnection.
# last_send_time` shows the flow has been quiet for `_KEEPALIVE_IDLE_MS`.
# Time-based (not `_speaking`-based) so it also covers in-turn gaps between
# filler audio, tool execution, and real LLM audio — the `_speaking` flag
# stays True for the whole turn but the actual audio flow can pause.
_KEEPALIVE_IDLE_MS = 40  # start silence stream after 40 ms without audio
_KEEPALIVE_POLL_SEC = 0.02  # check idle every 20 ms (one frame)

# Minimum dialog turns before using contextual farewell
_FAREWELL_MIN_TURNS = 3

# Grace window after the bot says goodbye. Without it the caller sits through
# the full 3×SILENCE_TIMEOUT_SEC ladder on a line nobody is going to use
# again — testers on 2026-09-09 had to hang up by hand every time.
_FAREWELL_HANGUP_GRACE_SEC = 2.0

# Closing markers, taken from what the LLM actually says. Over 14 days of prod
# these appeared in 20 bot turns and were the LAST turn in 18 of them, so they
# are a reliable end-of-call signal. Deliberately narrow: «дякую за звернення»
# alone is NOT here, because the bot also says it mid-call.
_FAREWELL_MARKERS: tuple[str, ...] = (
    "до побачення",
    "всього найкращого",
    "гарного дня",
    "гарного вам дня",
    "на все добре",
)


def _is_farewell(text: str) -> bool:
    """True when the bot's utterance is a closing line."""
    lowered = text.lower()
    return any(marker in lowered for marker in _FAREWELL_MARKERS)

# Default template dict (used if no PromptManager or DB unavailable)
_DEFAULT_TEMPLATES: dict[str, str] = {
    "greeting": GREETING_TEXT,
    "farewell": FAREWELL_TEXT,
    "silence_prompt": SILENCE_PROMPT_TEXT,
    "transfer": TRANSFER_TEXT,
    "error": ERROR_TEXT,
    "wait": WAIT_TEXT,
}

# --- Time-of-day greeting ---

_KYIV_TZ = zoneinfo.ZoneInfo("Europe/Kyiv")


def _time_of_day_greeting() -> str:
    """Return a Ukrainian greeting appropriate for the current Kyiv time."""
    hour = datetime.datetime.now(tz=_KYIV_TZ).hour
    if 5 <= hour < 12:
        return "Добрий ранок"
    if 12 <= hour < 18:
        return "Добрий день"
    if 18 <= hour < 23:
        return "Добрий вечір"
    return "Доброї ночі"


# --- Strip duplicate greeting from LLM response ---

_GREETING_PREFIXES = (
    "добрий ранок",
    "добрий день",
    "добрий вечір",
    "доброї ночі",
    "вітаю",
    "привіт",
)


def _strip_greeting(text: str) -> str:
    """Remove greeting prefix from LLM response to avoid double greeting."""
    lowered = text.lstrip()
    for prefix in _GREETING_PREFIXES:
        if lowered.lower().startswith(prefix):
            # Strip the greeting and any following punctuation/whitespace
            rest = lowered[len(prefix) :].lstrip(" !.,;:—–-")
            if rest:
                return rest[0].upper() + rest[1:] if rest else ""
            # If only the greeting and nothing else — return as-is
            return text
    return text


# --- Strip filler sentences before TTS ---
# Openers that indicate a filler sentence duplicating pipeline's wait-message
# or padding output before/after a tool result. Matched case-insensitively at
# the start of each sentence (after stripping punctuation/quotes).
_FILLER_SENTENCE_OPENERS = (
    "зараз перевірю",
    "зараз уточню",
    "тоді перевірю",
    "тоді уточню",
    "перевіряю",
    "уточнюю",
    "дивлюся",
    "секундочку",
    "хвилинку",
    "хвилиночку",
    "чекайте, будь ласка",
    "чекайте будь ласка",
    "почекайте, будь ласка",
    "почекайте будь ласка",
    "будь ласка, чекайте",
    "будь ласка чекайте",
    "будь ласка, почекайте",
    "хвилинку, будь ласка",
    "секундочку, будь ласка",
)

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_LEAD_STRIP = " —–-«\"'*_"

# --- Strip leaked system-prompt "Прогрес запису" block ---
# The fitting-module builds a "## 📋 Прогрес запису … НАСТУПНИЙ КРОК: Крок N" block
# in the system prompt to keep the LLM on track. Occasionally (observed in call
# d6e034f2 on 2026-08-05) the LLM echoes this internal checklist verbatim into
# its own reply, which would then be spoken by TTS as "решётка решётка Прогрес
# запису, галочка галочка…". Detect and strip it, keeping only any real dialog
# text that follows.
# Full block: from "Прогрес запису" header through the "НАСТУПНИЙ КРОК: …"
# closing line (with optional Markdown bold ** or leading ##). Non-greedy .*?
# to prefer the earliest closing marker; DOTALL so . matches newlines.
_LEAKED_PROGRESS_RE = re.compile(
    r"(?:^|\n)[#\s]{0,4}(?:📋\s*)?Прогрес\s+запису"
    r".*?"
    r"\*{0,2}НАСТУПНИЙ\s+КРОК[^\n]*",
    re.IGNORECASE | re.DOTALL,
)


def _strip_leaked_system_block(text: str) -> tuple[str, bool]:
    """Strip a leaked "## 📋 Прогрес запису … НАСТУПНИЙ КРОК" block from LLM output.

    Returns (cleaned_text, was_leaked). If the block is present but nothing
    meaningful follows it, returns ("", True) so the caller falls back to a
    soft re-ask instead of speaking the checklist.
    """
    if "Прогрес запису" not in text and "НАСТУПНИЙ КРОК" not in text:
        return text, False
    cleaned = _LEAKED_PROGRESS_RE.sub("\n", text, count=1)
    # Handle case where only the header leaked without the closing marker.
    if "Прогрес запису" in cleaned:
        cleaned = re.sub(
            r"(?:^|\n)[#\s]{0,4}(?:📋\s*)?Прогрес\s+запису[^\n]*",
            "\n",
            cleaned,
        )
    cleaned = cleaned.strip(" \n\t*—–-")
    return cleaned, True


# Wave 15 (2026-09-07) — phrasings the LLM only ever gets to use after
# book_fitting returns success (Krok 9 template, prompts.py). If one of these
# reaches the caller while nothing was booked, the caller hangs up believing a
# slot is held — call 7462c08b ended exactly this way.
_BOOKING_CLAIM_RE = re.compile(
    r"ви\s+записан|вас\s+записан|записала\s+вас|записала\s+на\s+"
    r"|запис\s+(?:створено|оформлено|підтверджено)"
    r"|смс\s+підтвердження",
    re.IGNORECASE,
)


def _claims_booking_done(text: str) -> bool:
    """True when the bot's reply tells the caller the fitting is booked."""
    return bool(text) and bool(_BOOKING_CLAIM_RE.search(text))


def _strip_filler(text: str) -> tuple[str, list[tuple[str, str]]]:
    """Strip filler sentences that pad LLM output before/after tool calls.

    Removes:
      - Sentences starting with filler openers ("Зараз перевірю…", "Чекайте,
        будь ласка", "Секундочку" тощо) — the pipeline speaks a wait phrase
        itself, so the LLM should not repeat it.
      - Double confirmation openers: if a sentence starts with "Зрозуміла,
        обираємо…" AND the next sentence starts with "Отже," — drop the
        first (they say the same thing).

    Returns (cleaned_text, stripped) where stripped is a list of
    (pattern_label, snippet) tuples for metrics/logging.
    """
    if not text or not text.strip():
        return text, []
    sentences = _SENTENCE_SPLIT_RE.split(text.strip())
    stripped: list[tuple[str, str]] = []
    kept: list[str] = []
    for sent in sentences:
        s = sent.strip()
        if not s:
            continue
        s_low = s.lower().lstrip(_LEAD_STRIP)
        opener_hit: str | None = None
        for opener in _FILLER_SENTENCE_OPENERS:
            if s_low.startswith(opener):
                opener_hit = opener
                break
        if opener_hit:
            stripped.append((f"opener:{opener_hit}", s))
            continue
        kept.append(s)

    # Second pass: collapse "Зрозуміла, обираємо X" + "Отже, X…"
    collapsed: list[str] = []
    i = 0
    while i < len(kept):
        cur = kept[i]
        cur_low = cur.lower().lstrip(_LEAD_STRIP)
        if i + 1 < len(kept):
            nxt_low = kept[i + 1].lower().lstrip(_LEAD_STRIP)
            if cur_low.startswith("зрозуміла") and nxt_low.startswith("отже"):
                stripped.append(("double_confirm", cur))
                i += 1
                continue
        collapsed.append(cur)
        i += 1

    cleaned = " ".join(collapsed).strip()
    return cleaned, stripped


# --- Contextual wait-phrase selection with rotation ---

_WAIT_CONTEXT_PATTERNS: list[tuple[list[str], list[str]]] = [
    # Only match explicit action requests — avoid false matches on car brands, names, etc.
    (["статус", "де замовлення", "де моє"], WAIT_STATUS_POOL),
    (["запис", "записати", "шиномонтаж", "монтаж"], WAIT_FITTING_POOL),
    (["наявність", "є в наявності"], WAIT_AVAILABILITY_POOL),
    (["підібрати", "підбери", "пошукай"], WAIT_SEARCH_POOL),
]

# Per-pool rotation counters (round-robin within a call and across calls)
_wait_counters: dict[int, int] = {}


# Keywords that indicate a real request (not a simple reply like a name or "yes")
_ACTION_KEYWORDS: list[str] = [
    # from _WAIT_CONTEXT_PATTERNS
    "статус",
    "де замовлення",
    "де моє",
    "запис",
    "записати",
    "шиномонтаж",
    "монтаж",
    "наявність",
    "є в наявності",
    "підібрати",
    "підбери",
    "пошукай",
    # additional action/topic keywords
    "замовлення",
    "замовити",
    "оформити",
    "заказ",
    "шини",
    "шину",
    "резину",
    "покришк",
    "доставк",
    "оплат",
    "гаранті",
    "повернен",
    "знижк",
    "акці",
    "промокод",
    "перевір",
    "дізнати",
    "розкажи",
    "підкажи",
    "порад",
    "порівня",
    "оператор",
    "менеджер",
    "переключи",
]


def _is_simple_reply(text: str) -> bool:
    """Detect short/simple replies that don't need a wait message.

    Examples: name, car brand, yes/no, license plate, single number.
    These process fast (no tool calls) so the wait filler is annoying.
    """
    words = text.split()
    if len(words) > 5:
        return False
    # Even short text with action keywords is NOT a simple reply
    lowered = text.lower()
    return not any(kw in lowered for kw in _ACTION_KEYWORDS)


def _select_wait_message(user_text: str, default: str) -> str:
    """Pick a contextual wait message, rotating through the pool.

    For simple replies (name, brand, yes/no) uses short acknowledgments
    ("Зрозуміла", "Добре") instead of full wait phrases.
    """
    if _is_simple_reply(user_text):
        return _rotate(WAIT_ACK_POOL)
    lowered = user_text.lower()
    for keywords, pool in _WAIT_CONTEXT_PATTERNS:
        if any(kw in lowered for kw in keywords):
            return _rotate(pool)
    return _rotate(WAIT_DEFAULT_POOL)


def _rotate(pool: list[str]) -> str:
    """Round-robin selection from a phrase pool."""
    pool_id = id(pool)
    idx = _wait_counters.get(pool_id, 0)
    phrase = pool[idx % len(pool)]
    _wait_counters[pool_id] = idx + 1
    return phrase


# Keywords that indicate the bot's last utterance was a yes/no confirmation
# question — used to switch the silence-timeout re-prompt to a targeted
# «say "так" or "ні"» message instead of the generic «Я на зв'язку», so
# a caller whose short «так» was dropped by STT can retry.
_CONFIRM_KEYWORDS: tuple[str, ...] = (
    "підтверджуєте",
    "підтвердіть",
    "підтверджуй",
    "вірно?",
    "так?",
    "правильно?",
    "згодні",
    "гаразд?",
    "все правильно",
    "все вірно",
    # Fitting-flow confirmations that don't contain the explicit words
    # above (call 78f185dc 2026-08-14: bot «Записуємо туди?» → generic
    # «Я на зв'язку» filler played instead of targeted «скажіть так
    # або ні», client complained «чем молчим?»).
    "записуємо туди",
    "туди?",
    "обираєте",
    "беремо?",
    "погоджуєтесь",
    "підходить?",
)


def _is_pending_confirmation(bot_utterance: str) -> bool:
    """True if bot's last utterance ended with a yes/no confirmation prompt."""
    if not bot_utterance:
        return False
    lowered = bot_utterance.lower()
    return any(kw in lowered for kw in _CONFIRM_KEYWORDS)


# --- STT correction context inference ---
# Map ``context_hint`` -> keywords that, when found in the bot's last
# utterance, signal that the client's reply should be interpreted in
# that context. Used to scope post-STT substitution rules like
# "N лет → N липня" so they only fire when the bot was asking for a date.
_CTX_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    # Weekdays included so that when the bot re-asks about a specific day
    # ("Ви маєте на увазі вівторок?"), the follow-up user utterance is
    # STT-corrected in the `date` context (fixes "28 лет"→"28 липня",
    # "викторах"→"вівторок" seen in call f14886d6 turns 5-7).
    ("date", (
        "дату", "дата", "число", "коли", "яку дату", "яке число", "на який день",
        "понеділок", "вівторок", "середу", "середа", "четвер",
        "п'ятницю", "пʼятницю", "пятницю", "суботу", "неділю",
    )),
    ("time", ("час", "часу", "о котрій", "який час", "вільний час", "слот")),
    ("plate", ("держномер", "номер авто", "номер автомобіля", "номер машини", "продиктуйте номер", "назвіть номер автомобіля")),
    ("city", ("місто", "місті", "у якому місті", "з якого міста")),
    ("station", ("район", "адрес", "адреса", "адресу", "вулиц", "орієнтир", "поблизу", "де зручніше")),
    ("phone", ("телефон", "номер телефон")),
)


def _infer_context_hint(bot_utterance: str) -> str | None:
    """Guess a ``context_hint`` from the bot's most recent utterance.

    Returns the first matching context, or None if nothing recognizable.
    """
    if not bot_utterance:
        return None
    lowered = bot_utterance.lower()
    for ctx, keywords in _CTX_KEYWORDS:
        if any(kw in lowered for kw in keywords):
            return ctx
    return None


# --- Contextual farewell prompt ---

# --- Scenario-specific greeting suffixes ---

_SCENARIO_GREETING_SUFFIX: dict[str, str] = {
    "tire_search": "Допоможу підібрати шини.",
    "order_status": "Перевірю статус вашого замовлення.",
    # NOTE 2026-08-14: fitting suffix removed. In Phase 2 we started
    # defaulting session.scenario = "fitting" for all no-IVR calls, which
    # made this suffix fire on every greeting — producing the double
    # message «…чи маєте інше питання? Запишу вас на шиномонтаж.» that
    # both contradicts the intent question and adds ~2s of dead air.
    # Was: "fitting": "Запишу вас на шиномонтаж.",
    "consultation": "Готова відповісти на ваші питання.",
}


_FAREWELL_SYSTEM_PROMPT = (
    "Ти — голосовий асистент інтернет-магазину шин Олена. "
    "Клієнт мовчить. Згенеруй коротке прощання (1 речення українською), "
    "підсумуй результат розмови. Подякуй за дзвінок."
)


# ---------------------------------------------------------------------------
# Wave 4-A — FSM wiring (feature-flagged)
# ---------------------------------------------------------------------------
#
# The previous attempt at this wire (commit 2fae3b6, reverted by c8c6601) went
# to production without a kill switch, short-circuited the turn unconditionally
# and copied arbitrary keys onto the session with setattr(). Everything below
# exists to make those four failure modes structurally impossible:
#
#   1. FSM_ENABLED=false  → nothing here is ever reached (rollback path).
#   2. Short-circuit requires *proven* progress, and a pipeline-side cap
#      bounds how many turns in a row the FSM may own.
#   3. session_updates pass through an explicit whitelist; unknown keys are
#      logged at ERROR, never silently applied and never silently skipped.
#   4. The FSM state fed to the classifier comes from session.fsm_state, which
#      the engine itself writes and which survives the Redis round-trip.

FSM_MODE_OFF = "off"
FSM_MODE_SHADOW = "shadow"
FSM_MODE_LIVE = "live"

# Below this classifier confidence we refuse to act on the intent and fall
# through to the normal streaming turn (the LLM sees the raw utterance).
FSM_INTERRUPT_CONFIDENCE_FLOOR = 0.5

# Main-flow states the FSM is allowed to ask in its own words (Wave 7-0). Every
# other state still gets its question from the LLM, so this set is the migration
# dial: a state joins it only when the prompt no longer asks the same thing, and
# leaving it is a one-line rollback that needs no deploy of the prompt.
#
# These three start it because their `question_template` is byte-identical to
# what the LLM already says in production (verified on calls 4e09dfab and
# c71ad0e5, 2026-09-10) — so the first cut changes *who* speaks, not *what* the
# caller hears, and any behaviour change is attributable to the seam itself.
FSM_VOICE_STATES: frozenset[str] = frozenset({"STORAGE", "COLOR", "BRAND"})

# Pipeline-side interrupt caps. These *duplicate* the handler-side caps in
# src/agent/interrupts.py on purpose: the revert cause was a handler that
# repeated the same sentence for 5 turns, so the pipeline must be able to stop
# it even if the handler's own bookkeeping is wrong or gets reset.
MAX_PIPELINE_INTERRUPT_TURNS = 5  # per call, total
MAX_CONSECUTIVE_PIPELINE_INTERRUPT_TURNS = 3  # in a row, without an LLM turn

# Counter keys live inside session.interrupt_counts so they survive the Redis
# round-trip (CallSession.from_dict sanitises values but keeps string keys) and
# survive the handler echoing interrupt_counts back via session_updates.
_PIPELINE_DISPATCH_TOTAL_KEY = "_pipeline_dispatch_total"
_PIPELINE_DISPATCH_STREAK_KEY = "_pipeline_dispatch_streak"

# The ONLY session attributes an interrupt handler is allowed to write.
# Derived by reading every assignment in src/agent/interrupts.py — if a handler
# starts writing a new field, this set must be extended deliberately.
FSM_SESSION_UPDATE_WHITELIST: frozenset[str] = frozenset(
    {
        "pending_price_interrupt_needs_diameter",
        "interrupt_counts",
        "fitting_diameter_client",
        "pending_cancel_action",
        "fitting_booked",
    }
)

# --- compound_parse → FSM mapping seam ---
#
# compound_parse emits *contract* keys; the FSM states own different names.
# The `_hint` suffix is meaningful upstream (it marks an unresolved, unverified
# extraction), so we translate here instead of renaming in compound_parse.
#
# Deliberately NOT mapped:
#   station_hint → station_id : a landmark string is not an ID. Filling
#       station_id from it would let the FSM skip the STATION state with a
#       value book_fitting cannot use. Needs get_fitting_stations resolution,
#       which is a network call — out of bounds for shadow mode. Dropped.
#   diameter                  : no state in MAIN_FLOW owns it (only the
#       PRICE_INTERRUPT side door does). Dropped.
#   name                      : no MAIN_FLOW state. Dropped.
#   date_hint → date          : Wave 6-B. `_detect_date_hint` returns a *label*
#       («завтра», «п'ятниця») at confidence 1.0, and this table used to copy
#       it straight into `fsm_filled_fields["date"]`, where DATE's
#       `auto_skip_if` reads it as a pinned date and skips the state on a value
#       nothing can book — the `c8c6601` defect class. The key is removed
#       rather than filtered so no future edit can reintroduce the raw label by
#       accident; `date` now comes from `date_parser`, which resolves against
#       an injected `now` and yields ISO or nothing. See
#       :func:`map_compound_fields_to_fsm`.
COMPOUND_TO_FSM_FIELD: dict[str, str] = {
    "city": "city",
    "time_hint": "time",
    "color": "color",
    "brand": "brand",
}

# Same floor compound_parse itself uses (APPLY_THRESHOLD). Kept local so the
# pipeline never silently inherits a loosened upstream threshold.
_FSM_APPLY_THRESHOLD = 0.7

# --- storage detection ---
#
# Wave 6-B: the five marker lists and the three functions used to live here as
# a byte-identical copy of `src/agent/parsers/storage_choice_parser`. Wave 6-A
# proved the two copies agreed on the whole corpus; this is the import that
# collapses them into one. Nothing is rewritten — the names are re-exported so
# the legacy nudge below and the FSM mapping layer call the same code.
#
# The three names come in with the module imports at the top of the file and
# stay part of `pipeline`'s surface on purpose: the nudge in
# `_transcript_processor_loop` calls `detect_own_tires` by that name, and so
# does the golden test that pins the FSM_ENABLED=false behaviour.


def broad_time_is_offered(offered: list[dict[str, Any]] | None, value: Any) -> bool:
    """Is this hour one the caller was actually offered?

    The targeted `time_parser` validates against `fitting_slots_offered` and by
    the Wave 14 contract can pin an existing slot but never invent one. The
    broad pass has no such check, and TIME carries `auto_skip_if=_filled`
    (`fitting_fsm.py:409`) — so a time written by the broad pass does not merely
    fill a field, it **cancels the only validation left** and carries the value
    into CONFIRM and BOOK. That is the `c8c6601` / Wave 15 defect class.

    Hence default-deny over the whole set, including the empty one: no slots
    offered yet means there is nothing to check against, not that anything goes.
    """
    return any(isinstance(slot, dict) and slot.get("time") == value for slot in (offered or ()))


def map_compound_fields_to_fsm(
    fields: dict[str, Any],
    fields_confidence: dict[str, float] | None = None,
    *,
    customer_text: str = "",
    min_confidence: float = _FSM_APPLY_THRESHOLD,
    now: datetime.datetime | None = None,
) -> dict[str, Any]:
    """Translate a compound_parse result into FSM field names.

    Pure function: no I/O, no session mutation, safe to call in shadow mode.

    Fields below ``min_confidence`` are dropped. Fields with no MAIN_FLOW state
    are dropped with a DEBUG log (never silently) so a future compound_parse key
    that nobody wired up is visible in the logs rather than invisible.

    ``storage_choice`` is not produced by compound_parse at all, so it is derived
    here from the raw utterance via :func:`detect_storage_choice`.

    ``date`` does not come from the table at all — see the comment above
    :data:`COMPOUND_TO_FSM_FIELD`. It is produced by `date_parser`, which is the
    one place allowed to turn a hint into a calendar date, and it needs a
    reference «today». ``now`` is injected rather than read from the process
    clock: a shadow-mode comparison that drifts with the wall clock cannot be
    replayed, and a test that cannot pin "today" cannot pin «завтра» either.
    Default-deny: ``now is None`` yields **no** ``date`` key, logged at DEBUG.
    """
    confidence = fields_confidence or {}
    mapped: dict[str, Any] = {}
    for key, value in (fields or {}).items():
        if value in (None, ""):
            continue
        if confidence.get(key, 1.0) < min_confidence:
            logger.debug(
                "FSM mapping: dropping %r (confidence %.2f < %.2f)",
                key,
                confidence.get(key, 1.0),
                min_confidence,
            )
            continue
        target = COMPOUND_TO_FSM_FIELD.get(key)
        if target is None:
            logger.debug(
                "FSM mapping: dropping %r — no MAIN_FLOW state owns it", key
            )
            continue
        mapped[target] = value

    if customer_text:
        if now is None:
            logger.debug(
                "FSM mapping: no reference date supplied — %r yields no `date`",
                customer_text,
            )
        else:
            from src.agent.parsers import date_parser
            from src.agent.parsers.base import ParseContext

            outcome = date_parser.PARSER.parse(
                ParseContext(customer_text=customer_text, now=now)
            )
            if outcome.status == "value" and outcome.value:
                mapped["date"] = outcome.value
            elif outcome.status == "unresolved":
                logger.debug(
                    "FSM mapping: date hint in %r did not resolve — no `date`",
                    customer_text,
                )

    storage = detect_storage_choice(customer_text)
    if storage is not None:
        mapped["storage_choice"] = storage
    return mapped


if TYPE_CHECKING:
    from src.agent.agent import LLMAgent
    from src.agent.parsers.base import ParseOutcome
    from src.agent.streaming_loop import StreamingAgentLoop
    from src.core.call_session import SessionStore
    from src.core.echo_canceller import EchoCanceller
    from src.monitoring.cost_tracker import CostBreakdown
    from src.sandbox.patterns import PatternSearch
    from src.tts.base import TTSEngine

logger = logging.getLogger(__name__)


class CallPipeline:
    """Orchestrates the STT → LLM → TTS pipeline for a single call.

    Lifecycle:
      1. Play greeting
      2. Listen (feed audio to STT)
      3. On final transcript → send to LLM
      4. LLM response → TTS → send audio back
      5. Repeat from step 2
      6. Handle barge-in, silence timeouts, transfer, hangup
    """

    def __init__(
        self,
        conn: AudioSocketConnection,
        stt: STTEngine,
        tts: TTSEngine,
        agent: LLMAgent,
        session: CallSession,
        stt_config: STTConfig | None = None,
        templates: dict[str, str] | None = None,
        pattern_search: PatternSearch | None = None,
        streaming_loop: StreamingAgentLoop | None = None,
        barge_in_event: asyncio.Event | None = None,
        agent_name: str | None = None,
        network_name: str | None = None,
        call_logger: Any = None,
        cost_breakdown: CostBreakdown | None = None,
        caller_history: str | None = None,
        storage_context: str | None = None,
        customer_profile: str | None = None,
        echo_canceller: EchoCanceller | None = None,
        session_store: SessionStore | None = None,
    ) -> None:
        self._conn = conn
        self._stt = stt
        self._tts_initial = tts
        self._agent = agent
        self._session = session
        self._stt_config = stt_config or STTConfig()
        self._templates = templates or _DEFAULT_TEMPLATES
        self._pattern_search = pattern_search
        self._streaming_loop = streaming_loop
        self._agent_name = agent_name
        self._network_name = network_name
        self._call_logger = call_logger
        self._cost = cost_breakdown
        self._caller_history = caller_history
        self._storage_context = storage_context
        self._customer_profile = customer_profile
        self._echo_canceller = echo_canceller
        self._session_store = session_store
        self._turn_counter = 0
        self._llm_history: list[dict[str, Any]] = []  # persistent LLM context for streaming path
        # Wave 4-A. Resolved once per call so the flag cannot flip mid-call.
        self._fsm_mode_cache: str | None = None
        # Shadow-mode artefact: the question the FSM *would* have asked. Written
        # in shadow mode, read by nothing on the customer path — neither TTS nor
        # the streaming loop ever sees it. Kept on the pipeline (not the session)
        # so it cannot leak into the Redis snapshot the LLM prompt is built from.
        self._fsm_shadow_reply: str | None = None
        self._speaking = False
        self._barge_in_event = barge_in_event or asyncio.Event()
        self._final_transcript_queue: asyncio.Queue[Transcript | None] = asyncio.Queue()
        # Set once the bot has said goodbye; shortens the next silence wait to
        # _FAREWELL_HANGUP_GRACE_SEC. Cleared as soon as the caller speaks.
        self._farewell_spoken = False

    @property
    def _tts(self) -> TTSEngine:
        """Return the current global TTS engine (picks up hot-reloaded config)."""
        from src.tts import get_engine

        return get_engine() or self._tts_initial

    def _flag_false_booking_claim(self, bot_text: str) -> None:
        """Record that the bot promised a booking that was never made.

        Detection only — the caller has already heard the sentence by the time
        we get here, and rewriting it mid-stream would put customer-facing text
        under a guard's control. The actual prevention is the Krok 8 marker
        upstream; this exists so a recurrence is visible instead of silent.
        """
        if not _claims_booking_done(bot_text) or self._session.fitting_booked:
            return
        false_booking_claim_total.inc()
        logger.error(
            "FALSE BOOKING CLAIM for call=%s — bot told the caller the fitting "
            "is booked but book_fitting never succeeded. Text: %r",
            self._session.channel_uuid,
            bot_text[:200],
        )

    async def _log_turn(
        self,
        speaker: str,
        content: str,
        stt_confidence: float | None = None,
        llm_latency_ms: int | None = None,
        language: str | None = None,
    ) -> None:
        """Log a turn to the database (fire-and-forget)."""
        if self._call_logger is None:
            return
        turn_number = self._turn_counter
        self._turn_counter += 1
        try:
            await self._call_logger.log_turn(
                call_id=self._session.channel_uuid,
                turn_number=turn_number,
                speaker=speaker,
                content=content,
                stt_confidence=stt_confidence,
                llm_latency_ms=llm_latency_ms,
                language=language,
            )
        except Exception:
            logger.warning("log_turn failed for call %s", self._session.channel_uuid)

    async def _persist_session(self) -> None:
        """Save session to Redis for mid-call crash recovery.

        Called after each assistant turn. Latency ~1ms (Redis SET),
        negligible vs LLM latency. Failures are logged but never
        propagated — session persistence is best-effort.
        """
        if self._session_store is None:
            return
        try:
            await self._session_store.save(self._session)
        except Exception:
            logger.warning("session persist failed for call %s", self._session.channel_uuid)

    # ------------------------------------------------------------------
    # Wave 4-A — FSM wiring
    # ------------------------------------------------------------------

    def _fsm_mode(self) -> str:
        """Resolve the rollout mode for this call: ``off`` / ``shadow`` / ``live``.

        Resolved once per pipeline instance and cached, so the flag cannot flip
        halfway through a call and leave the FSM half-applied.

        ``off`` is the rollback path and must stay absolutely inert: no engine,
        no classifier, no extra allocation on the hot path.
        """
        if self._fsm_mode_cache is not None:
            return self._fsm_mode_cache

        mode = FSM_MODE_OFF
        try:
            from src.config import get_settings

            fsm = get_settings().fsm
            if fsm.enabled:
                allowed = fsm.enabled_tenant_list
                tenant = str(self._session.tenant_id) if self._session.tenant_id else ""
                if allowed and tenant not in allowed:
                    logger.info(
                        "FSM disabled for tenant=%r (allow-list %s) call=%s",
                        tenant,
                        allowed,
                        self._session.channel_uuid,
                    )
                else:
                    mode = FSM_MODE_SHADOW if fsm.shadow_mode else FSM_MODE_LIVE
        except Exception:
            # A broken/absent config must degrade to the rollback path, never
            # to "live by accident". Logged at ERROR because a call running
            # with an unresolvable flag is a real defect.
            logger.error(
                "FSM flag resolution failed for call=%s — falling back to OFF",
                self._session.channel_uuid,
                exc_info=True,
            )
            mode = FSM_MODE_OFF

        self._fsm_mode_cache = mode
        if mode != FSM_MODE_OFF:
            logger.info("FSM mode=%s for call=%s", mode, self._session.channel_uuid)
        return mode

    def _get_llm_router(self) -> Any:
        """LLM router used by the intent classifier, or None if unavailable."""
        router = getattr(self._streaming_loop, "_llm_router", None)
        if router is None:
            router = getattr(self._agent, "_llm_router", None)
        return router

    def _get_tool_router(self) -> Any:
        """Tool router used by the interrupt handlers, or None if unavailable."""
        router = getattr(self._streaming_loop, "_tool_router", None)
        if router is None:
            router = getattr(self._agent, "tool_router", None)
        return router

    def _resolve_selected_station(self) -> dict[str, Any] | None:
        """The one fitting station this call is pinned to, if any.

        Priority: (1) the station the LLM last acted on via
        get_fitting_slots/book_fitting — that's the client's chosen one,
        regardless of how many were shown. (2) fallback: single-station case.
        (3) otherwise None so the LLM asks the client to pick.
        """
        selected: dict[str, Any] | None = None
        last_used = self._session.last_fitting_station_id
        if last_used:
            selected = next(
                (
                    s
                    for s in self._session.fitting_stations_seen
                    if s.get("id") == last_used
                ),
                None,
            )
        if selected is None and len(self._session.fitting_stations_seen) == 1:
            selected = self._session.fitting_stations_seen[0]
        return selected

    def _build_fitting_progress(
        self,
        selected_station: dict[str, Any] | None,
        *,
        krok8_confirmed: bool,
    ) -> dict[str, Any]:
        """Assemble the fitting progress block from the flat session fields.

        There is no ``session.fitting_progress`` — the block is derived from a
        dozen separate ``fitting_*`` fields. Extracted in Wave 4-A so the FSM
        snapshot and the LLM prompt block cannot drift apart.

        Pure read: the one-shot ``krok8_confabulation_pending`` flag is *read*
        here but deliberately reset by the caller, so calling this twice in a
        turn is safe.
        """
        return {
            "customer_name": self._session.fitting_customer_name,
            "city": (selected_station or {}).get("city"),
            "station_address": (selected_station or {}).get("address"),
            "storage_choice": self._session.fitting_storage_choice,
            "storage_contract": self._session.fitting_storage_contract,
            "date": self._session.selected_fitting_date,
            "time": self._session.selected_fitting_time,
            "plate": self._session.fitting_plate,
            "brand": self._session.fitting_vehicle_brand,
            "caller_phone": self._session.caller_phone,
            "booked": self._session.fitting_booked,
            "requested_weekday": self._session.fitting_requested_weekday,
            "krok8_confirmed": krok8_confirmed,
            "krok8_confabulation_pending": self._session.krok8_confabulation_pending,
        }

    def _fsm_filled_fields_snapshot(self) -> dict[str, Any]:
        """Compact truthy view of what the call has already collected."""
        progress = self._build_fitting_progress(
            self._resolve_selected_station(), krok8_confirmed=False
        )
        snapshot = {k: v for k, v in progress.items() if v not in (None, "", False)}
        snapshot.update(
            {
                k: v
                for k, v in self._session.fsm_filled_fields.items()
                if v not in (None, "")
            }
        )
        return snapshot

    def _dialog_history_tail(self, limit: int = 5) -> list[dict[str, str]]:
        """Last `limit` turns in the shape the intent classifier expects."""
        tail = []
        for turn in self._session.dialog_history[-limit:]:
            if not turn.content:
                continue
            tail.append({"role": turn.speaker, "content": turn.content})
        return tail

    def _fsm_shadow_divergence(self, mapped: dict[str, Any]) -> dict[str, str]:
        """Compare FSM-mapped values against the legacy session fields.

        This is the shadow-mode divergence signal: for every slot both sides
        claim, do they agree? Only compared where BOTH sides have a value —
        "the FSM extracted something the legacy path missed" is expected during
        shadow and is reported as ``new``, not as a divergence.
        """
        legacy: dict[str, Any] = {
            "city": (self._resolve_selected_station() or {}).get("city"),
            "storage_choice": self._session.fitting_storage_choice,
            "date": self._session.selected_fitting_date,
            "time": self._session.selected_fitting_time,
            "brand": self._session.fitting_vehicle_brand,
            "color": self._session.fitting_plate,
        }
        report: dict[str, str] = {}
        for name, value in mapped.items():
            if name not in legacy:
                report[name] = "unknown"
                continue
            other = legacy[name]
            if other in (None, ""):
                report[name] = "new"
            elif str(other).strip().lower() == str(value).strip().lower():
                report[name] = "match"
            else:
                report[name] = "diverge"
        return report

    def _last_bot_utterance(self) -> str:
        """The bot's most recent turn, or `""`.

        The targeted parsers gate on it (`is_diameter_question`,
        `is_name_question`, `_bot_is_asking_storage`). Handing them an empty
        string would silently disable every context gate in the package and
        make the targeted pass indistinguishable from the broad one.
        """
        for turn in reversed(self._session.dialog_history):
            if turn.speaker == "assistant" and turn.content:
                return turn.content
        return ""

    def _run_fsm_deterministic_step(self, transcript: Transcript) -> None:
        """Advance the FSM from deterministic evidence only. No I/O.

        Three passes, in this order (§3.1):

        1. **targeted** — the state knows which question it just asked, so its
           own parser earns the higher confidence. For STATION the parser only
           produces a landmark, so `resolve_station_from_session` finishes the
           job in the same step (see below).
        2. **passive** — `name` and `diameter` belong to no MAIN_FLOW state
           (`PASSIVE_PARSERS`). They run while their field is empty, write
           through `setdefault` and **never** call `apply_field`: that would
           advance the machine along the *passive* parser's step instead of the
           caller's, which is the `c8c6601` defect class.
        3. **broad** — the `compound_parse` sweep fills *other* fields via
           `setdefault` and is not allowed to overwrite anything — least of all
           the targeted parser's deliberate refusal on its own field.

        Every pass is pure regex over the utterance plus lookups in fields the
        session already holds, and the engine only touches session fields, so
        this is safe to run in shadow mode: zero LLM requests, zero Store API
        calls, and the method is synchronous — no await → no network.

        `FieldParser.aresolve` is never awaited and `ParseContext.conn` stays
        `None`, which is the gate that enforces it (§3.2 rule 3). Wave 6-C
        sharpens what that rule protects: it forbids **network**, not the word
        `aresolve`. The station resolver reads
        `session.fitting_stations_seen` — the snapshot `get_fitting_stations`
        already wrote — so it is called synchronously and runs in shadow too.
        `brand_parser`'s resolver needs `ctx.conn` for the alias table, is real
        I/O, and therefore stays live-only and uncalled from here.

        Exceptions are caught so a broken FSM never drops a live call, but they
        are logged at ERROR with a traceback. ``contextlib.suppress`` is
        deliberately NOT used here — a silently swallowed write on this path is
        exactly how 3/3 bookings were lost invisibly (`37fb2d0`).
        """
        try:
            from src.agent.compound_parse import compound_parse
            from src.agent.fitting_fsm import STATES, FsmEngine
            from src.agent.interrupts import classify_interrupt_text
            from src.agent.parsers.base import ParseContext
            from src.agent.parsers.registry import PASSIVE_PARSERS, get_parser
            from src.agent.parsers.station_parser import (
                resolve_proposed_station,
                resolve_station_from_session,
                station_city,
                unanimous_snapshot_city,
            )

            engine = FsmEngine(self._session)
            engine.start()
            state_before = engine.current_state()

            # DONE/TRANSFER absorb every event. Walking them again would keep
            # re-logging a call that has already ended and would let the shadow
            # reply overwrite itself with a terminal template on every
            # subsequent turn.
            if engine.is_terminal():
                logger.debug(
                    "fsm_shadow call=%s already terminal in %s — skipping",
                    self._session.channel_uuid,
                    state_before.value,
                )
                return

            # The FSM cannot leave WELCOME/INTENT without an `intent`, and the
            # only producer of `intent` is the LLM classifier — which shadow
            # mode is forbidden to call. Seed it from the same pure keyword
            # matcher the pipeline already runs a few lines below, so the FSM
            # actually walks during the shadow observation period instead of
            # sitting in WELCOME and reporting nothing.
            if not self._session.fsm_filled_fields.get("intent"):
                scenario = detect_scenario_from_text(transcript.text)
                if scenario == "fitting" or self._session.scenario == "fitting":
                    self._session.fsm_filled_fields["intent"] = "fitting"

            # STATION is the one state whose `auto_skip_if` reads data the tool
            # router writes (`session.fitting_station_ids`, filled by
            # `get_fitting_stations`) rather than data `apply_field` writes.
            # `_follow_auto_skips()` used to have a single call site, inside
            # `apply_field`, which fires on the turn the caller names the city —
            # strictly before the tool has run. The predicate was therefore
            # evaluated once, at the one moment it is guaranteed false, and
            # never re-checked: correct rule, dead in practice. Three of the
            # sixteen replayed calls ended in TRANSFER holding exactly one
            # station id and no `station_id`.
            #
            # Recomputed HERE, ahead of the targeted pass, so the pin lands
            # before this turn can be written off as a failed STATION answer
            # and charged to `max_parser_null`.
            #
            # Not gated on `advance`: `advance` gates the two things that move
            # the machine on *evidence the observer must not manufacture*
            # (`on_parser_null`, `on_interrupt_turn`). This reads the session
            # and writes a session field, awaits nothing and calls nothing, so
            # it is exactly as safe in shadow as the parsers below it — and
            # gating it would leave shadow measuring a machine the live path
            # does not have.
            #
            # `state_before` is reassigned on purpose: everything below parses
            # the turn *against the state the FSM is in*, and after the pin that
            # is no longer the state the turn opened in. The hop itself is not
            # lost — `_pin_single_station` logs `fsm_station_autopin` and the
            # skip logs its own `fsm_transition`.
            state_before = engine.refresh_auto_skips()

            cfg = STATES[state_before]
            own_field = cfg.field_name
            if own_field is None and cfg.next_state is not None:
                # WELCOME owns no field; the field that unblocks it belongs to
                # its successor (INTENT → "intent").
                own_field = STATES[cfg.next_state].field_name

            # --- targeted pass ---
            ctx = ParseContext(
                customer_text=transcript.text,
                last_bot_utterance=self._last_bot_utterance(),
                state=state_before,
                session=self._session,
                now=datetime.datetime.now(tz=_KYIV_TZ),
                conn=None,  # forbids aresolve — see the docstring.
            )
            parser = get_parser(cfg.parser)
            targeted: ParseOutcome | None = None
            if parser is None:
                # Not an error: INTENT names `intent_classifier`, which is an
                # async LLM call listed in NON_FIELD_PARSERS on purpose. Shadow
                # mode must never reach it, so the state simply has no
                # deterministic targeted pass and falls through to the broad one.
                logger.debug(
                    "fsm_shadow call=%s state=%s has no FieldParser (%s) — "
                    "broad pass only",
                    self._session.channel_uuid,
                    state_before.value,
                    cfg.parser,
                )
            else:
                targeted = parser.parse(ctx)
                # `station_parser.parse` cannot reach `status="value"` by
                # construction — it hands back the landmark («Оболонь»), and a
                # landmark is not a `station_id`. Only the resolver closes that
                # gap, and until Wave 6-C it had no call site anywhere in
                # `src/`: STATION was a dead end that 12 of 16 replayed calls
                # died in.
                #
                # Dispatched on `field_name`, not on `isinstance` and not on
                # the `cfg.parser` string: `field_name` is what the write two
                # lines below already keys on, so there is one identity for the
                # field in this block instead of two that can drift.
                #
                # No `await` and no `ctx.conn`: the resolver reads
                # `session.fitting_stations_seen`, the snapshot
                # `get_fitting_stations` wrote on entry. Network resolvers
                # (brand) stay shut behind `conn is None`.
                if (
                    parser.field_name == "station_id"
                    and targeted.status == "unresolved"
                    and targeted.value
                ):
                    targeted = resolve_station_from_session(ctx, targeted)
                # Same assignment as before, on purpose: this is the targeted
                # parser finishing its own field, not a find from the side, so
                # `claimed` still points at `station_id` and the broad pass is
                # still locked out of it.
                if targeted.status == "value" and parser.field_name:
                    self._session.fsm_filled_fields[parser.field_name] = targeted.value
                    # This pass already assigns rather than `setdefault`s, so it
                    # overwrites an inferred value on its own. Dropping the mark
                    # is what stops the broad pass from overwriting it *again*
                    # on some later turn — which would be the back door onto
                    # `claimed` the mark must not become.
                    if parser.field_name in self._session.fsm_inferred_fields:
                        self._session.fsm_inferred_fields.remove(parser.field_name)

            # The targeted parser *claims* its state's own field for this turn,
            # whatever it answered. A `unresolved`/`not_mentioned` on the field
            # the state is waiting for is a deliberate refusal — «they spoke
            # about a date but named none». Letting the broad sweep setdefault a
            # value over that refusal would pin exactly the value the targeted
            # parser declined to pin, which is the `c8c6601` defect class
            # arriving through the back door.
            claimed = (
                parser.field_name
                if parser is not None and parser.field_name and parser.field_name == own_field
                else None
            )

            # --- proposal pass (Wave 6-H) ---
            # Reads the *station snapshot* plus the bot's pending question
            # rather than the caller's words, which is why it is a pass of its
            # own and not part of either neighbour.
            #
            # Not restricted to STATION, and that is the whole point. Call
            # `011277ef` dies in CITY: the bot quoted a price for «у місті
            # Дніпро, провулок Добровольців», the caller said «так», and the FSM
            # was still in CITY because «на перемозі» is a landmark in two
            # cities and resolves to no city at all. A rule living inside
            # `StationParser.parse` never runs on that turn.
            #
            # Placed before the passive and broad passes because it is not
            # competing with them for evidence: it fires only when the bot's
            # pending question *is* the proposal and the caller agreed to it,
            # so the utterance is «так» and carries nothing either of those two
            # could read. The unanimity rule is the opposite case and sits
            # after the broad pass; see there.
            #
            # `apply_field` is deliberately not called here. The single sanctioned
            # hop happens below, once, for the current state's own field.
            proposed = resolve_proposed_station(ctx)
            if proposed.status == "value":
                self._session.fsm_filled_fields.setdefault("station_id", proposed.value)
                logger.info(
                    "fsm_station_proposal_confirmed call=%s state=%s station_id=%s offered=%d",
                    self._session.channel_uuid,
                    state_before.value,
                    proposed.value,
                    len(self._session.fitting_stations_seen or ()),
                    extra={"call_id": str(self._session.channel_uuid)},
                )
                # The city of the point the caller just agreed to. Without it
                # `011277ef` only moves from CITY to STATION, and STATION is
                # already the largest sink. The unanimity rule below cannot
                # cover this: `7462c08b` resolves a proposal out of a snapshot
                # holding nine stations across five cities.
                picked_city = station_city(self._session, proposed.value)
                if picked_city:
                    self._session.fsm_filled_fields.setdefault("city", picked_city)

            # --- passive pass ---
            # `name` and `diameter` belong to no MAIN_FLOW state, so nothing in
            # the seam ever filled them: `PASSIVE_PARSERS` had zero call sites
            # in `src/`, `fsm_filled_fields["name"]` stayed empty for the whole
            # call, and CONFIRM lists `name` in `required_context`. Measured
            # cost of that gap: the bot asks «Як до вас звертатися?» while the
            # FSM sits in CITY, and the correct answer is written off as a
            # failed city answer — 8 such charges in one day.
            #
            # These parsers write and never advance. `apply_field` moves the
            # machine to the CURRENT state's `next_state` regardless of which
            # step the field belongs to, so a passive parser calling it would
            # walk the FSM down a step the caller never answered — `c8c6601`
            # arriving through the back door (`registry.py:22-25`).
            passive_filled: dict[str, str] = {}
            for passive_name in PASSIVE_PARSERS:
                passive = get_parser(passive_name)
                if passive is None or not passive.field_name:
                    continue
                passive_field = passive.field_name
                # PRICE_INTERRUPT owns `diameter`: the targeted pass has
                # already run `diameter_parser` this turn and `claimed` now
                # protects the field whatever it answered. A second run would
                # reach around that guard — and `claimed` is only ever
                # `own_field`, so this one skip covers both.
                if passive_field == own_field:
                    continue
                # «Run on every turn *while the field is empty*»
                # (`registry.py:83`). Checked before `parse()`, not after, so a
                # filled field costs nothing per turn.
                if self._session.fsm_filled_fields.get(passive_field) not in (None, ""):
                    continue
                passive_outcome = passive.parse(ctx)
                if passive_outcome.status != "value":
                    continue
                # `setdefault`, never assignment: two passive parsers naming the
                # same field must not overwrite each other, and the first write
                # of a turn is the one that wins everywhere else in this seam.
                self._session.fsm_filled_fields.setdefault(passive_field, passive_outcome.value)
                passive_filled[passive_name] = passive_field
                # The value is PII (`name`). The neighbouring seam log prints
                # `mapped`, which can never carry a name — `name` is absent
                # from COMPOUND_TO_FSM_FIELD on purpose — so this line must not
                # be the one that starts printing it. Parser, field and state
                # only.
                logger.info(
                    "fsm_passive_fill call=%s parser=%s field=%s state=%s",
                    self._session.channel_uuid,
                    passive_name,
                    passive_field,
                    state_before.value,
                    extra={"call_id": str(self._session.channel_uuid)},
                )

            # --- broad pass ---
            parsed = compound_parse(transcript.text)
            mapped = map_compound_fields_to_fsm(
                parsed.fields,
                parsed.fields_confidence,
                customer_text=transcript.text,
                now=ctx.now,
            )
            # Which fields this turn moved from empty to filled. Emptiness is
            # read *before* the write, exactly as the passive pass does
            # (`registry.py:83`): `setdefault` returns the stored value either
            # way, so it cannot tell a first write from a no-op, and that
            # distinction is the whole loop-breaker below.
            broad_filled: list[str] = []
            for name, value in mapped.items():
                if name == claimed:
                    logger.debug(
                        "fsm_shadow call=%s broad pass not overriding claimed "
                        "field %s (targeted status=%s)",
                        self._session.channel_uuid,
                        name,
                        targeted.status if targeted else None,
                    )
                    continue
                # Before the emptiness read, not after: a rejected time must
                # neither be stored nor excuse this state's null, and putting
                # the check here makes the second half of that structurally
                # impossible rather than something a later edit must remember.
                if name == "time" and not broad_time_is_offered(
                    self._session.fitting_slots_offered, value
                ):
                    logger.info(
                        "fsm_time_not_offered call=%s state=%s value=%s offered=%d",
                        self._session.channel_uuid,
                        state_before.value,
                        value,
                        len(self._session.fitting_slots_offered or ()),
                        extra={"call_id": str(self._session.channel_uuid)},
                    )
                    continue
                if self._session.fsm_filled_fields.get(name) in (None, ""):
                    broad_filled.append(name)
                elif name in self._session.fsm_inferred_fields:
                    # The only place in this seam where the broad pass overwrites
                    # a filled slot, and it is not an exception to the rule above
                    # — it is the rule applied to a value the caller never said.
                    # `fsm_inferred_fields` is written by exactly one rule (the
                    # unanimous-snapshot city) and never by a parser, so a
                    # targeted refusal can never appear here and `claimed` stays
                    # the only thing standing between this pass and `c8c6601`.
                    #
                    # Without this, the snapshot's guess is permanent: it lands
                    # on the diameter answer, the machine leaves CITY, and the
                    # caller asking for Черкаси four times is never recorded
                    # because `setdefault` finds the slot taken and the targeted
                    # `city_parser` never runs again.
                    self._session.fsm_inferred_fields.remove(name)
                    if self._session.fsm_filled_fields.get(name) != value:
                        logger.info(
                            "fsm_inferred_field_revoked call=%s state=%s field=%s inferred=%s",
                            self._session.channel_uuid,
                            state_before.value,
                            name,
                            self._session.fsm_filled_fields.get(name),
                            extra={"call_id": str(self._session.channel_uuid)},
                        )
                    self._session.fsm_filled_fields[name] = value
                    continue
                self._session.fsm_filled_fields.setdefault(name, value)
            self._emit_preparse_metric(mapped)

            # --- unanimous-snapshot city (Wave 6-H) ---
            # When every point the bot has offered sits in one city, that city
            # came from `get_fitting_stations(city=...)` — the bot could not
            # have narrowed the catalog without it. `011277ef` is the case: the
            # snapshot holds one Дніпро point, and the FSM sits in CITY anyway
            # because «на перемозі» is a landmark in two cities.
            #
            # Last, on purpose. Everything above reads the *current utterance*;
            # this reads a snapshot built on earlier turns. Running it earlier
            # would let a stale snapshot take the slot before the broad pass
            # could write the city the caller just named, which is the
            # `bd95036c` hazard — a Дніпро point pinned on a caller asking for
            # Черкаси. Placed here, any reading of the live turn wins and the
            # snapshot only fills a hole nobody else could.
            #
            # The ordering is not enough on its own: a caller can speak about a
            # city the resolvers cannot pin, and then no one writes the field
            # and the stale snapshot wins by default. That is exactly what
            # `unresolved` means in the parser contract («they said something we
            # could not pin down», `base.py:88`) as opposed to `not_mentioned`.
            # So when the state's own parser is the one that refused, the
            # snapshot stands down for the turn — the snapshot is not going
            # anywhere, and the bot's re-ask is the cheaper way to learn.
            caller_spoke_of_a_city = (
                claimed == "city" and targeted is not None and targeted.status == "unresolved"
            )
            snapshot_city = (
                None if caller_spoke_of_a_city else unanimous_snapshot_city(self._session)
            )
            if snapshot_city and self._session.fsm_filled_fields.get("city") in (None, ""):
                # `setdefault` for the write and the emptiness read for the log,
                # the same split the broad pass uses: `setdefault` returns the
                # stored value either way and so cannot tell a first write from
                # a no-op.
                self._session.fsm_filled_fields.setdefault("city", snapshot_city)
                # Held revocably. Measured over the 42-call corpus this rule
                # fires on the *diameter* answer every single time — the first
                # turn after `get_fitting_stations` filled the catalog — so it
                # routinely takes the slot turns before the caller says anything
                # about a city. Marking it lets the broad pass hand the slot
                # back when they finally do.
                if "city" not in self._session.fsm_inferred_fields:
                    self._session.fsm_inferred_fields.append("city")
                logger.info(
                    "fsm_city_from_snapshot call=%s state=%s city=%s offered=%d",
                    self._session.channel_uuid,
                    state_before.value,
                    snapshot_city,
                    len(self._session.fitting_stations_seen or ()),
                    extra={"call_id": str(self._session.channel_uuid)},
                )

            # NEVER loop apply_field() over the mapped fields. apply_field
            # advances to the CURRENT state's next_state regardless of which
            # step the field actually belongs to, so N fields would mean N
            # blind hops down MAIN_FLOW. Instead: the fields are already
            # written above; apply_field is invoked at most ONCE, for the field
            # the current state itself is waiting on, and the engine then walks
            # its own auto_skip_if chain a single time.
            state_after = state_before
            if own_field:
                value = self._session.fsm_filled_fields.get(own_field)
                if value not in (None, ""):
                    state_after = engine.apply_field(own_field, value)
                elif targeted is not None and targeted.status in (
                    "unresolved",
                    "not_mentioned",
                ):
                    # Branch on `status`, never on a locally recomputed
                    # confidence — a second copy of the threshold is how a
                    # loosened upstream value gets silently inherited.
                    #
                    # `advance` is the mode, not the reason: only `live` is
                    # allowed to move the machine. Shadow still counts, logs
                    # and emits the metric — see `FsmEngine.on_parser_null`
                    # for why an advancing observer goes blind.
                    advance = self._fsm_mode_cache == FSM_MODE_LIVE
                    # A turn the caller spent opening an interrupt is not a
                    # failed answer. Charging it to `max_parser_null` handed
                    # the caller to an operator after three price questions
                    # the bot was about to answer. Detected by markers rather
                    # than by the classifier because this runs in shadow too,
                    # where a network call is forbidden — and because the seam
                    # and `_maybe_handle_intent` must agree on what an
                    # interrupt is instead of keeping two definitions.
                    interrupt_kind = classify_interrupt_text(transcript.text)
                    if interrupt_kind is not None:
                        state_after = engine.on_interrupt_turn(
                            interrupt_kind,
                            transcript.text,
                            advance=advance,
                        )
                    elif passive_filled:
                        # Same shape as the interrupt exemption above, same
                        # reason. The bot asked «Як до вас звертатися?» while
                        # the FSM sat in CITY; the caller answered it
                        # correctly. That turn is not a failed city answer, and
                        # charging it to CITY's `max_parser_null` is what put 8
                        # calls a day one step closer to an operator.
                        #
                        # This exemption cannot loop: a passive parser runs
                        # only while its field is empty, so each of the two
                        # passive fields can excuse at most one turn per call.
                        # That bound is structural, not a cap someone has to
                        # remember to lower (`feedback_guard_needs_loop_breaker`).
                        logger.info(
                            "fsm_parser_null_excused call=%s state=%s field=%s "
                            "passive=%s",
                            self._session.channel_uuid,
                            state_before.value,
                            own_field,
                            sorted(passive_filled),
                            extra={"call_id": str(self._session.channel_uuid)},
                        )
                    elif broad_filled:
                        # The caller answered a question — just not this state's
                        # one. Measured across all four class-D calls of the
                        # 16-call corpus, every such answer named a field
                        # *below* the current state in MAIN_FLOW: the bot had
                        # run ahead (on `b394f6c1` it merged the storage and
                        # colour questions into one utterance), the caller
                        # followed the bot, and the FSM charged the answer to
                        # the question it was still asking. Four calls reached
                        # an operator that way.
                        #
                        # The value itself was never lost — the broad pass above
                        # stored it and `auto_skip_if=_filled` skips that state
                        # later. Only the charge was wrong, which is why this is
                        # one branch and not a new state.
                        #
                        # Bounded structurally, like the passive exemption: only
                        # a field going empty → filled counts, and a field can
                        # do that once per call. So a caller repeating an answer
                        # the FSM already holds is charged from the second turn
                        # on — «белый», «oler белый», «колер белый» on
                        # `b394f6c1` excuses the first and charges the other
                        # two. A cap someone has to remember to lower would not
                        # survive (`feedback_guard_needs_loop_breaker`).
                        #
                        # Its own log line, not the passive one: the two
                        # exemptions answer different questions in prod, and
                        # merging them would hide whichever is regressing.
                        logger.info(
                            "fsm_parser_null_answered_elsewhere call=%s state=%s "
                            "field=%s answered=%s",
                            self._session.channel_uuid,
                            state_before.value,
                            own_field,
                            sorted(broad_filled),
                            extra={"call_id": str(self._session.channel_uuid)},
                        )
                    else:
                        state_after = engine.on_parser_null(
                            own_field,
                            transcript.text,
                            advance=advance,
                        )

            # Deliberately the *raw* template, not `engine.next_question()`:
            # `render()` logs a WARNING for every placeholder it cannot fill,
            # and in shadow the session is half-empty by construction, so
            # rendering here would fill the log with warnings about a reply
            # nobody speaks.
            self._fsm_shadow_reply = STATES[state_after].question_template or None

            logger.info(
                "fsm_shadow call=%s mode=%s state=%s→%s targeted=%s/%s mapped=%s "
                "divergence=%s",
                self._session.channel_uuid,
                self._fsm_mode_cache,
                state_before.value,
                state_after.value,
                cfg.parser,
                targeted.status if targeted is not None else "skipped",
                mapped,
                self._fsm_shadow_divergence(mapped),
                extra={"call_id": str(self._session.channel_uuid)},
            )
        except Exception:
            logger.error(
                "FSM deterministic step failed for call=%s — continuing on the "
                "legacy path",
                self._session.channel_uuid,
                exc_info=True,
            )

    @staticmethod
    def _emit_preparse_metric(mapped: dict[str, Any]) -> None:
        """Count what the broad pre-parse actually managed to extract.

        Declared at `metrics.py:501` since Wave 4 with no emitter — a counter
        nobody increments reads as «this never happens» on a dashboard, which
        is worse than no panel at all.
        """
        for name in mapped:
            try:
                fsm_compound_preparse_fields_total.labels(field=name).inc()
            except Exception:
                logger.warning(
                    "failed to emit fsm_compound_preparse_fields_total for %s",
                    name,
                    exc_info=True,
                )

    def _pipeline_interrupt_budget_left(self) -> bool:
        """True while the pipeline still allows the FSM to own a turn.

        Duplicates the handler-side caps in src/agent/interrupts.py on purpose.
        The reverted Wave 13 shipped a handler that answered the same PRICE
        question five turns in a row; the pipeline must be able to stop that
        even when the handler believes it is making progress.
        """
        counts = self._session.interrupt_counts
        total = counts.get(_PIPELINE_DISPATCH_TOTAL_KEY, 0)
        streak = counts.get(_PIPELINE_DISPATCH_STREAK_KEY, 0)
        if total >= MAX_PIPELINE_INTERRUPT_TURNS:
            logger.warning(
                "FSM interrupt cap: call=%s used %d/%d dispatches — handing the "
                "turn back to the LLM for the rest of the call",
                self._session.channel_uuid,
                total,
                MAX_PIPELINE_INTERRUPT_TURNS,
            )
            return False
        if streak >= MAX_CONSECUTIVE_PIPELINE_INTERRUPT_TURNS:
            logger.warning(
                "FSM interrupt cap: call=%s hit %d consecutive interrupt turns "
                "— forcing an LLM turn to break the loop",
                self._session.channel_uuid,
                streak,
            )
            return False
        return True

    def _note_pipeline_interrupt_dispatch(self, *, dispatched: bool) -> None:
        """Update the pipeline-side counters after a turn."""
        counts = self._session.interrupt_counts
        if dispatched:
            counts[_PIPELINE_DISPATCH_TOTAL_KEY] = (
                counts.get(_PIPELINE_DISPATCH_TOTAL_KEY, 0) + 1
            )
            counts[_PIPELINE_DISPATCH_STREAK_KEY] = (
                counts.get(_PIPELINE_DISPATCH_STREAK_KEY, 0) + 1
            )
        else:
            # Any turn that reaches the LLM breaks the streak.
            counts[_PIPELINE_DISPATCH_STREAK_KEY] = 0

    def _apply_interrupt_session_updates(self, updates: dict[str, Any] | None) -> int:
        """Copy handler-produced session updates through an explicit whitelist.

        The reverted implementation did ``setattr(session, k, v)`` over whatever
        the handler returned. Here an unknown key is refused AND logged at
        ERROR: a handler writing a field nobody wired up is a defect, not a
        thing to skip quietly.

        Returns the number of keys actually applied.
        """
        applied = 0
        for key, value in (updates or {}).items():
            if key not in FSM_SESSION_UPDATE_WHITELIST:
                logger.error(
                    "FSM session_updates: refusing unknown key %r for call=%s. "
                    "Add it to FSM_SESSION_UPDATE_WHITELIST deliberately or fix "
                    "the handler.",
                    key,
                    self._session.channel_uuid,
                )
                continue
            if not hasattr(self._session, key):
                logger.error(
                    "FSM session_updates: whitelisted key %r does not exist on "
                    "CallSession (call=%s) — whitelist is out of date",
                    key,
                    self._session.channel_uuid,
                )
                continue
            setattr(self._session, key, value)
            applied += 1
        return applied

    def _freeze_fsm_for_interrupt(self, primary_intent: str) -> tuple[Any, Any]:
        """Enter the interrupt side-state for `primary_intent` (Wave 4-B).

        Returns ``(engine, frozen_from)``. ``frozen_from`` is None whenever
        nothing was frozen, and the pipeline MUST then skip ``resume()``:
        ``FsmEngine.resume()`` with an empty ``fsm_prev_state`` deliberately
        goes to DONE, so calling it on a flow that was never frozen would end a
        live booking.

        Called only after the cap and the confidence floor have both passed —
        a turn that never reaches a handler must not move the FSM.
        """
        from src.agent.fitting_fsm import FROZEN_STATES, FsmEngine, FsmEvent, FsmState

        event = {
            "PRICE": FsmEvent.INTERRUPT_PRICE,
            "CANCEL": FsmEvent.INTERRUPT_CANCEL,
        }.get(primary_intent)
        if event is None:
            return None, None

        state = FsmState.coerce(self._session.fsm_state)
        if state is None or state not in FROZEN_STATES:
            # No booking in progress (or the flow is somewhere unresumable):
            # the handler still runs, it just has nothing to return the caller
            # to. This is also the FSM_ENABLED=false → live flip mid-call case.
            return None, None

        engine = FsmEngine(self._session)
        try:
            entered = engine.freeze_for_interrupt(event)
        except Exception:
            # Loud, never suppressed: an engine that cannot freeze is a defect,
            # but it must not cost the caller the turn.
            logger.error(
                "FSM live mode: freeze_for_interrupt failed for call=%s intent=%s",
                self._session.channel_uuid,
                primary_intent,
                exc_info=True,
            )
            return None, None
        if entered is state:
            return None, None
        return engine, state

    def _unfreeze_fsm(self, engine: Any, frozen_from: Any) -> None:
        """Leave the interrupt side-state, back where the caller was.

        Must be called on EVERY exit path once a freeze happened — dispatched
        reply, no proven progress, or a handler exception. Skipping it on the
        failure paths strands the FSM in PRICE_INTERRUPT for the rest of the
        call while the caller silently falls back to the LLM.

        The phrase ``resume()`` returns is discarded on purpose (seam decision A,
        README §4): the Wave 3-B handler already appended the same
        ``StateConfig.resume_phrase`` to ``reply_to_customer``, and it is the only
        component that knows whether the interrupt actually closed this turn.
        Speaking this one too would say it twice.
        """
        if engine is None or frozen_from is None:
            return
        try:
            engine.resume()
        except Exception:
            logger.error(
                "FSM live mode: resume() failed for call=%s — forcing the session back to %s",
                self._session.channel_uuid,
                frozen_from,
                exc_info=True,
            )
            self._session.fsm_state = getattr(frozen_from, "value", frozen_from)
            self._session.fsm_prev_state = None

    async def _dispatch_interrupt_reply(
        self, result: Any, *, kind: str
    ) -> None:
        """Speak an interrupt handler's reply and record the turn."""
        reply = result.reply_to_customer
        fsm_interrupt_total.labels(interrupt_type=kind).inc()
        self._session.reset_empty_response()
        self._session.add_assistant_turn(reply)
        await self._log_turn("bot", reply)
        await self._persist_session()
        await self._speak(reply)
        logger.info(
            "FSM interrupt dispatched: call=%s kind=%s advanced=%s resume=%s",
            self._session.channel_uuid,
            kind,
            result.advanced,
            result.resume_state,
        )

    async def _maybe_speak_fsm_question(self, transcript: Transcript) -> bool:
        """Live-mode main flow: let the FSM ask the next question itself.

        Runs after `_run_fsm_deterministic_step` has already consumed this
        transcript, so `session.fsm_state` is the state whose field we still
        need. Returns True only when the question was actually spoken, and the
        caller then skips the LLM turn entirely — that suppression is the point.
        Asking from both sides is what made the reverted build say «В якому
        місті?» twice in a row (`c8c6601`).

        Every refusal below falls through to the LLM, which is the behaviour
        that shipped before this method existed.
        """
        try:
            from src.agent.fitting_fsm import STATES, FsmEngine, FsmState

            state = FsmState(self._session.fsm_state)
            if state.value not in FSM_VOICE_STATES:
                return False

            cfg = STATES[state]
            field = cfg.field_name
            # The state's own field is already known — the FSM is about to move
            # on, and its question would ask for something we have.
            if not field or self._session.fsm_filled_fields.get(field):
                fsm_voice_total.labels(state=state.value, outcome="field_filled").inc()
                return False

            engine = FsmEngine(self._session)
            engine.start()
            question, unresolved = engine.next_question_checked(state)
            if unresolved:
                # `render` leaves these literal, so speaking now would put
                # «[districts]» in the caller's ear.
                fsm_voice_total.labels(state=state.value, outcome="unresolved").inc()
                return False
            if not question.strip():
                fsm_voice_total.labels(state=state.value, outcome="empty").inc()
                return False

            # Never say the same sentence twice in a row. On a parser_null the
            # FSM's own answer is to re-ask, and re-asking verbatim is the
            # symptom the first attempt was reverted for; the LLM rephrases.
            last_bot = next(
                (
                    t.content
                    for t in reversed(self._session.dialog_history)
                    if t.speaker == "assistant" and t.content
                ),
                "",
            )
            if question.strip() == last_bot.strip():
                fsm_voice_total.labels(state=state.value, outcome="repeat").inc()
                return False
        except Exception:
            logger.error(
                "FSM voice: refusing the turn for call=%s — falling through to "
                "the LLM",
                self._session.channel_uuid,
                exc_info=True,
            )
            return False

        self._session.add_user_turn(
            content=transcript.text,
            stt_confidence=transcript.confidence,
            detected_language=transcript.language,
        )
        await self._log_turn(
            "customer",
            transcript.text,
            stt_confidence=transcript.confidence,
            language=transcript.language,
        )
        self._session.reset_empty_response()
        self._session.add_assistant_turn(question)
        await self._log_turn("bot", question)
        await self._persist_session()
        await self._speak(question)
        fsm_voice_total.labels(state=state.value, outcome="spoken").inc()
        logger.info(
            "fsm_voice call=%s state=%s spoke=%r",
            self._session.channel_uuid,
            state.value,
            question,
            extra={"call_id": str(self._session.channel_uuid)},
        )
        return True

    async def _maybe_handle_intent(self, transcript: Transcript) -> bool:
        """Live-mode side door: let an interrupt handler own this turn.

        Returns True ONLY when the handler proved it did something:
        ``handled`` and (``advanced`` or a non-empty ``session_updates``).
        Anything else falls through to the normal streaming turn, so a handler
        that produces nothing can never take the caller hostage.
        """
        # Cap first — checked BEFORE the classifier so a blown budget costs no
        # latency at all, and unconditionally before any handler runs.
        if not self._pipeline_interrupt_budget_left():
            self._note_pipeline_interrupt_dispatch(dispatched=False)
            return False

        llm_router = self._get_llm_router()
        if llm_router is None:
            logger.error(
                "FSM live mode: no LLM router available for call=%s — intent "
                "classification skipped",
                self._session.channel_uuid,
            )
            self._note_pipeline_interrupt_dispatch(dispatched=False)
            return False

        try:
            from src.agent.intent_classifier import classify_intent

            result = await classify_intent(
                customer_text=transcript.text,
                session_context={
                    "fsm_state": self._session.fsm_state,
                    "current_step": self._session.fsm_state,
                    "filled_fields": self._fsm_filled_fields_snapshot(),
                    "dialog_history_tail": self._dialog_history_tail(),
                    "tenant": str(self._session.tenant_id or ""),
                },
                llm_router=llm_router,
            )
        except Exception:
            logger.error(
                "FSM live mode: intent classification failed for call=%s",
                self._session.channel_uuid,
                exc_info=True,
            )
            self._note_pipeline_interrupt_dispatch(dispatched=False)
            return False

        if result.confidence < FSM_INTERRUPT_CONFIDENCE_FLOOR:
            logger.info(
                "FSM live mode: intent=%s confidence=%.2f below floor %.2f for "
                "call=%s — using the normal LLM turn",
                result.primary_intent,
                result.confidence,
                FSM_INTERRUPT_CONFIDENCE_FLOOR,
                self._session.channel_uuid,
            )
            self._note_pipeline_interrupt_dispatch(dispatched=False)
            return False

        if result.primary_intent == "TRANSFER":
            self._session.add_user_turn(
                content=transcript.text,
                stt_confidence=transcript.confidence,
                detected_language=transcript.language,
            )
            await self._log_turn(
                "customer",
                transcript.text,
                stt_confidence=transcript.confidence,
                language=transcript.language,
            )
            self._session.mark_transfer(reason="intent_classifier_transfer")
            self._note_pipeline_interrupt_dispatch(dispatched=True)
            return True

        if result.primary_intent not in ("PRICE", "CANCEL"):
            # BOOK / RESCHEDULE are main flow — the LLM still owns them in 4-A.
            self._note_pipeline_interrupt_dispatch(dispatched=False)
            return False

        tool_router = self._get_tool_router()
        if tool_router is None:
            logger.error(
                "FSM live mode: no tool router available for call=%s — cannot "
                "run the %s interrupt handler",
                self._session.channel_uuid,
                result.primary_intent,
            )
            self._note_pipeline_interrupt_dispatch(dispatched=False)
            return False

        # Wave 4-B: park the main flow in the side-state for the duration of the
        # handler. Done here — after the cap and the confidence floor, before the
        # handler — so the handler observes PRICE_INTERRUPT/CANCEL_INTERRUPT and
        # resolves its resume phrase from the fsm_prev_state snapshot.
        engine, frozen_from = self._freeze_fsm_for_interrupt(result.primary_intent)

        try:
            from src.agent.interrupts import (
                handle_cancel_interrupt,
                handle_price_interrupt,
            )

            handler = (
                handle_price_interrupt
                if result.primary_intent == "PRICE"
                else handle_cancel_interrupt
            )
            interrupt = await handler(
                customer_text=transcript.text,
                session=self._session,
                tool_router=tool_router,
            )
        except Exception:
            logger.error(
                "FSM live mode: %s interrupt handler raised for call=%s",
                result.primary_intent,
                self._session.channel_uuid,
                exc_info=True,
            )
            self._unfreeze_fsm(engine, frozen_from)
            self._note_pipeline_interrupt_dispatch(dispatched=False)
            return False

        # Proof of progress. `handled` alone is not enough: the reverted build
        # short-circuited on `handled` and repeated one sentence for 5 turns.
        made_progress = bool(
            interrupt.handled
            and interrupt.reply_to_customer
            and (interrupt.advanced or interrupt.session_updates)
        )
        if not made_progress:
            logger.info(
                "FSM live mode: %s handler made no provable progress for "
                "call=%s (handled=%s advanced=%s updates=%s) — falling through "
                "to the LLM",
                result.primary_intent,
                self._session.channel_uuid,
                interrupt.handled,
                interrupt.advanced,
                bool(interrupt.session_updates),
            )
            self._unfreeze_fsm(engine, frozen_from)
            self._note_pipeline_interrupt_dispatch(dispatched=False)
            return False

        self._unfreeze_fsm(engine, frozen_from)
        self._apply_interrupt_session_updates(interrupt.session_updates)
        if interrupt.resume_state:
            self._session.fsm_state = interrupt.resume_state
        # Record the customer turn here rather than upstream: the FSM path skips
        # the streaming branch that normally does it, and a missing user turn
        # would silently corrupt both the transcript log and the next prompt.
        self._session.add_user_turn(
            content=transcript.text,
            stt_confidence=transcript.confidence,
            detected_language=transcript.language,
        )
        await self._log_turn(
            "customer",
            transcript.text,
            stt_confidence=transcript.confidence,
            language=transcript.language,
        )
        await self._dispatch_interrupt_reply(
            interrupt, kind=result.primary_intent.lower()
        )
        self._note_pipeline_interrupt_dispatch(dispatched=True)
        return True

    def _resolve_empty_response_fallback(self) -> str:
        """Pick a fallback phrase for an empty LLM response.

        Below the session's escalation threshold, use a gentle re-ask that
        does NOT mention an operator transfer — most empty responses are
        transient (network/model hiccup) and the caller shouldn't hear
        "connecting operator" for a one-off glitch. At or above the
        threshold, fall through to the standard error template (which does
        announce a transfer).
        """
        escalate = self._session.record_empty_response()
        logger.warning(
            "Empty LLM response: call=%s, consecutive=%d, escalate=%s",
            self._session.channel_uuid,
            self._session.empty_response_count,
            escalate,
        )
        if escalate:
            return self._templates.get("error", ERROR_TEXT)
        return EMPTY_RESPONSE_SOFT_TEXT

    async def run(self) -> None:
        """Run the full call pipeline until hangup or transfer."""
        keepalive_task: asyncio.Task[None] | None = None
        try:
            # Start STT and audio reader BEFORE greeting so that incoming
            # caller audio is fed to STT in real-time.  Without this, audio
            # accumulates in the TCP buffer during the ~8 s greeting and is
            # then flushed in a burst — which breaks latest_short model.
            await self._stt.start_stream(self._stt_config)
            audio_task = asyncio.create_task(self._audio_reader_loop())
            keepalive_task = asyncio.create_task(self._keepalive_loop())

            # Play greeting while STT is already consuming audio
            logger.info("Pipeline: starting greeting for %s", self._session.channel_uuid)
            await self._play_greeting()
            logger.info(
                "Pipeline: greeting done, entering LISTENING for %s", self._session.channel_uuid
            )

            # Main loop
            self._session.transition_to(CallState.LISTENING)

            # Start transcript reader and processor (audio reader already running)
            transcript_reader_task = asyncio.create_task(self._transcript_reader_loop())
            transcript_task = asyncio.create_task(self._transcript_processor_loop())

            _done, pending = await asyncio.wait(
                [audio_task, transcript_reader_task, transcript_task],
                return_when=asyncio.FIRST_COMPLETED,
            )

            # Cancel remaining tasks
            for task in pending:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

        except asyncio.CancelledError:
            logger.info("Pipeline cancelled: %s", self._session.channel_uuid)
        except Exception:
            logger.exception("Pipeline error: %s", self._session.channel_uuid)
            error_msg = self._templates.get("error", ERROR_TEXT)
            await self._log_turn("bot", error_msg)
            await self._speak(error_msg)
        finally:
            if keepalive_task is not None:
                keepalive_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await keepalive_task
            await self._stt.stop_stream()
            self._session.transition_to(CallState.ENDED)

    async def _keepalive_loop(self) -> None:
        """Fill any audio-flow gap longer than ``_KEEPALIVE_IDLE_MS`` with a
        silence frame, so the outbound RTP stream to the SIP peer never
        pauses. Time-based (uses ``conn.last_send_time``) so it covers both
        between-turn gaps and in-turn gaps that the ``_speaking`` flag hides.
        """
        silence_frame = b"\x00" * AUDIO_FRAME_BYTES
        idle_threshold = _KEEPALIVE_IDLE_MS / 1000
        first_send_logged = False
        keepalive_frames_sent = 0
        try:
            while not self._conn.is_closed:
                await asyncio.sleep(_KEEPALIVE_POLL_SEC)
                if self._conn.is_closed:
                    break
                idle = time.monotonic() - self._conn.last_send_time
                if idle < idle_threshold:
                    continue
                try:
                    await self._conn.send_audio(silence_frame)
                    keepalive_frames_sent += 1
                    if not first_send_logged:
                        logger.info(
                            "keepalive silence stream started for %s "
                            "(idle threshold %d ms)",
                            self._session.channel_uuid,
                            _KEEPALIVE_IDLE_MS,
                        )
                        first_send_logged = True
                except Exception:
                    logger.debug(
                        "keepalive silence send failed for %s",
                        self._session.channel_uuid,
                        exc_info=True,
                    )
                    await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            pass
        finally:
            if first_send_logged:
                logger.info(
                    "keepalive stats for %s: %d silence frames sent",
                    self._session.channel_uuid,
                    keepalive_frames_sent,
                )

    async def _play_greeting(self) -> None:
        """Play the greeting message, adapted to the time of day and agent name."""
        greeting = self._templates.get("greeting", GREETING_TEXT)
        greeting = greeting.replace("{time_greeting}", _time_of_day_greeting())
        greeting = greeting.replace("{agent_name}", self._agent_name or "Олена")
        if self._network_name:
            greeting = greeting.replace("{network_name}", self._network_name)
        else:
            # Remove placeholder with surrounding comma+space: "{network_name}, " → ""
            greeting = greeting.replace("{network_name}, ", "")
            greeting = greeting.replace("{network_name}", "")
        # Append scenario-specific suffix if IVR intent was resolved
        suffix = _SCENARIO_GREETING_SUFFIX.get(self._session.scenario or "")
        if suffix:
            greeting = greeting.rstrip() + " " + suffix
        self._session.transition_to(CallState.GREETING)
        _greet_t0 = time.monotonic()
        await self._speak(greeting)
        _greet_ms = int((time.monotonic() - _greet_t0) * 1000)
        # Total greeting duration — includes TTS synthesize + audio send.
        # Watch this on cold-start incidents: if it's much >2500ms the
        # HTTP/2 connection to Google Cloud TTS timed out during idle
        # and the periodic warmup task in main.py may have died.
        logger.info(
            "Greeting complete (%d ms total) for %s",
            _greet_ms,
            self._session.channel_uuid,
        )
        self._session.add_assistant_turn(greeting)
        await self._log_turn("bot", greeting)
        await self._persist_session()
        # Seed LLM history so the model knows the greeting was already spoken
        self._llm_history.append({"role": "assistant", "content": greeting})

    async def _audio_reader_loop(self) -> None:
        """Continuously read audio from AudioSocket and feed to STT."""
        while not self._conn.is_closed:
            packet = await self._conn.read_audio_packet()
            if packet is None:
                break

            if packet.type == PacketType.HANGUP:
                logger.info("Hangup: %s", self._session.channel_uuid)
                break

            if packet.type == PacketType.AUDIO:
                t0 = time.monotonic()
                audio = packet.payload
                if self._echo_canceller is not None:
                    audio = self._echo_canceller.process(audio, speaking=self._speaking)
                await self._stt.feed_audio(audio)
                audiosocket_to_stt_ms.observe((time.monotonic() - t0) * 1000)

            if packet.type == PacketType.ERROR:
                logger.warning("AudioSocket error: %s", self._session.channel_uuid)
                break

    async def _transcript_reader_loop(self) -> None:
        """Sole consumer of STT transcripts — fan-out to queue and barge-in.

        - Final transcripts with text → ``_final_transcript_queue``
        - Interim transcripts while ``_speaking`` → set ``_barge_in_event``
        - On STT stream end → put ``None`` sentinel to unblock queue reader
        """
        try:
            async for transcript in self._stt.get_transcripts():
                if transcript.is_final and transcript.text.strip():
                    await self._final_transcript_queue.put(transcript)
                elif not transcript.is_final and transcript.text.strip():
                    if self._speaking:
                        self._barge_in_event.set()
                        barge_in_total.inc()
                        logger.info(
                            "Barge-in signal: interim '%s' while speaking: %s",
                            transcript.text[:30],
                            self._session.channel_uuid,
                        )
                    else:
                        logger.info(
                            "STT interim (listening): '%s' for %s",
                            transcript.text[:50],
                            self._session.channel_uuid,
                        )
        finally:
            # Signal queue reader that STT stream has ended
            await self._final_transcript_queue.put(None)

    async def _drain_transcript_buffer(self, first: Transcript) -> Transcript:
        """Buffer multiple final transcripts arriving in quick succession.

        Google STT finalizes aggressively on ~500ms pauses, so a single spoken
        phrase often arrives as several finals. We wait ``transcript_buffer_sec``
        after each final and merge whatever arrives within that window into a
        single LLM turn.
        """
        buffer_sec = self._stt_config.transcript_buffer_sec or _TRANSCRIPT_BUFFER_SEC_DEFAULT
        texts = [first.text]
        confidences = [first.confidence]
        language = first.language
        start_ts = time.monotonic()

        try:
            while True:
                next_t = await asyncio.wait_for(
                    self._get_next_final_transcript(),
                    timeout=buffer_sec,
                )
                texts.append(next_t.text)
                confidences.append(next_t.confidence)
                if next_t.language:
                    language = next_t.language
        except (TimeoutError, asyncio.CancelledError):
            pass

        if len(texts) > 1:
            merged_text = " ".join(texts)
            avg_confidence = sum(confidences) / len(confidences)
            span_ms = (time.monotonic() - start_ts) * 1000
            logger.info(
                "STT buffer: merged=%d span_ms=%.0f window_sec=%.2f text='%s'",
                len(texts),
                span_ms,
                buffer_sec,
                merged_text[:80],
            )
            return Transcript(
                text=merged_text,
                is_final=True,
                confidence=avg_confidence,
                language=language,
            )
        return first

    async def _apply_stt_corrections(self, transcript: Transcript) -> Transcript:
        """Run regex substitutions from Redis on the transcript text.

        No-op if the corrections module fails to load — never let a bad
        rule break the pipeline. Any applied rules are logged (with the
        original text preserved in the log line for offline audit) and
        counted per rule_id for Prometheus.
        """
        if not transcript.text:
            return transcript
        try:
            from src.core.redis_client import get_redis
            from src.monitoring.metrics import stt_corrections_applied_total
            from src.stt.corrections import apply_corrections

            redis = await get_redis()
        except Exception:
            return transcript

        # Infer context_hint from the bot's last utterance (if any).
        context_hint: str | None = None
        for turn in reversed(self._session.dialog_history):
            if turn.speaker == "assistant" and turn.content:
                context_hint = _infer_context_hint(turn.content)
                break

        try:
            new_text, applied = await apply_corrections(
                redis, transcript.text, context_hint
            )
        except Exception:
            logger.warning(
                "stt_corrections: apply failed for call %s",
                self._session.channel_uuid,
                exc_info=True,
            )
            return transcript

        if not applied or new_text == transcript.text:
            return transcript

        logger.info(
            "stt_corrections: call=%s ctx=%s rules=%s original=%r → corrected=%r",
            self._session.channel_uuid,
            context_hint,
            applied,
            transcript.text[:120],
            new_text[:120],
        )
        for rule_id in applied:
            stt_corrections_applied_total.labels(rule_id=rule_id).inc()

        # Ukrainian numeral-word → digit conversion. Enabled for plate/phone
        # (spoken numbers like «два одинадцять» → "211") and time (spoken
        # slot picks like «одинадцята двадцять» → "1120", colon added below).
        # NOT enabled for other contexts to avoid mangling dates ("двадцять
        # восьме липня") or amounts ("сто гривень"). Runs AFTER Redis regex
        # rules so STT garbage tokens («начать»/«натязь») get dropped first.
        if context_hint in ("plate", "phone", "time"):
            try:
                from src.stt.numeral_parser import words_to_digits

                converted, n = words_to_digits(new_text)
                if n > 0 and converted != new_text:
                    logger.info(
                        "numeral_parser: call=%s ctx=%s runs=%d %r → %r",
                        self._session.channel_uuid,
                        context_hint,
                        n,
                        new_text[:120],
                        converted[:120],
                    )
                    new_text = converted
            except Exception:
                logger.warning(
                    "numeral_parser: failed for call %s",
                    self._session.channel_uuid,
                    exc_info=True,
                )

        # Time context: reinsert HH:MM colon on compact 3-4 digit times.
        # numeral_parser converts «одинадцята двадцять» → "1120"; here we
        # split it back into "11:20" so the LLM can match against the
        # offered_slots list directly. Also handles "820" → "8:20" for
        # single-digit hours. Skipped outside `time` ctx to keep plate
        # numbers («0211») intact.
        if context_hint == "time":
            colon_re = re.compile(
                r"(?<!\d)(0?[1-9]|1\d|2[0-3])([0-5]\d)(?!\d)"
            )
            new_text2 = colon_re.sub(r"\1:\2", new_text)
            if new_text2 != new_text:
                logger.info(
                    "stt_time_colon: call=%s %r → %r",
                    self._session.channel_uuid,
                    new_text[:120],
                    new_text2[:120],
                )
                new_text = new_text2

        return Transcript(
            text=new_text,
            is_final=transcript.is_final,
            confidence=transcript.confidence,
            language=transcript.language,
        )

    def _apply_entity_normalization(self, transcript: Transcript) -> Transcript:
        """Normalize domain entities: tire sizes and brand phonetic aliases.

        Runs unconditionally on every final transcript, independently of
        whether Redis corrections fired. No-op when nothing matches.
        """
        if not transcript.text:
            return transcript
        try:
            from src.stt.entity_normalizer import normalize_entities

            context_hint: str | None = None
            for turn in reversed(self._session.dialog_history):
                if turn.speaker == "assistant" and turn.content:
                    context_hint = _infer_context_hint(turn.content)
                    break

            new_text, n = normalize_entities(transcript.text, context_hint)
            if n > 0 and new_text != transcript.text:
                logger.info(
                    "entity_normalizer: call=%s ctx=%s n=%d %r → %r",
                    self._session.channel_uuid,
                    context_hint,
                    n,
                    transcript.text[:120],
                    new_text[:120],
                )
                return Transcript(
                    text=new_text,
                    is_final=transcript.is_final,
                    confidence=transcript.confidence,
                    language=transcript.language,
                )
        except Exception:
            logger.warning(
                "entity_normalizer: failed for call %s",
                self._session.channel_uuid,
                exc_info=True,
            )
        return transcript

    async def _transcript_processor_loop(self) -> None:
        """Process STT transcripts and drive the LLM → TTS flow."""
        logger.info(
            "Pipeline: transcript_processor_loop started for %s", self._session.channel_uuid
        )
        while not self._conn.is_closed:
            # Wait for final transcripts with silence timeout
            transcript = await self._wait_for_final_transcript()

            if transcript is None:
                if self._farewell_spoken:
                    # The bot has already said goodbye and the caller stayed
                    # quiet through the grace window — end the call instead of
                    # walking the silence ladder and saying goodbye a second
                    # time.
                    logger.info(
                        "Farewell spoken and caller silent for %.1fs — "
                        "hanging up call=%s",
                        _FAREWELL_HANGUP_GRACE_SEC,
                        self._session.channel_uuid,
                    )
                    break
                # Silence timeout
                should_end = self._session.record_timeout()
                if should_end:
                    farewell = await self._generate_contextual_farewell()
                    if farewell is None:
                        farewell = self._templates.get("farewell", FAREWELL_TEXT)
                    self._session.add_assistant_turn(farewell)
                    await self._log_turn("bot", farewell)
                    await self._persist_session()
                    await self._speak(farewell)
                    break
                else:
                    # Context-aware silence re-prompt: if the bot's last
                    # utterance was a yes/no confirmation question, prefer
                    # a targeted re-ask («say "так" or "ні"») instead of
                    # the generic «Я на зв'язку». Caller's short «так»
                    # may have been dropped by STT — this gives them an
                    # explicit second chance without breaking the flow.
                    last_bot_text = ""
                    for turn in reversed(self._session.dialog_history):
                        if turn.speaker == "assistant":
                            last_bot_text = turn.content or ""
                            break
                    pending_confirm = _is_pending_confirmation(last_bot_text)
                    # Wave 5 (2026-09-03) — targeted re-prompt when the
                    # bot's last question was Крок 5 (колір) or Крок 6
                    # (марка). Call dd3dd368 played the generic «Я на
                    # зв'язку» 3× while stuck on color; a direct nudge
                    # speeds recovery.
                    _last_bot_lc = last_bot_text.lower()
                    _asking_color_now = (
                        "колір" in _last_bot_lc
                        and "автомобіл" in _last_bot_lc
                    )
                    _asking_brand_now = (
                        "марка" in _last_bot_lc
                        and ("автомобіл" in _last_bot_lc or "авто" in _last_bot_lc)
                        and "колір" not in _last_bot_lc
                    )
                    if pending_confirm and self._session.timeout_count <= 1:
                        silence_msg = SILENCE_CONFIRM_REPROMPT_TEXT
                    elif _asking_color_now and self._session.timeout_count <= 1:
                        silence_msg = SILENCE_COLOR_REPROMPT_TEXT
                    elif _asking_brand_now and self._session.timeout_count <= 1:
                        silence_msg = SILENCE_BRAND_REPROMPT_TEXT
                    elif self._session.timeout_count <= 1:
                        silence_msg = SILENCE_TIMEOUT_1_TEXT
                    else:
                        silence_msg = SILENCE_TIMEOUT_2_TEXT
                    await self._log_turn("bot", silence_msg)
                    await self._speak(silence_msg)
                    continue

            logger.info(
                "Pipeline: got transcript '%s' for %s",
                transcript.text[:50],
                self._session.channel_uuid,
            )

            # Buffer multiple transcripts arriving in quick succession
            transcript = await self._drain_transcript_buffer(transcript)

            # Post-STT text corrections: apply deterministic regex fixes for
            # known STT quirks ("N лет"→"N липня", "викторог"→"вівторок", etc.)
            # BEFORE anything downstream sees the text. Scoped by context_hint
            # inferred from the bot's last utterance so rules like
            # "17:25 → 1725" only fire when we were asking for a plate.
            transcript = await self._apply_stt_corrections(transcript)
            transcript = self._apply_entity_normalization(transcript)

            # Got a final transcript — reset timeout and track language
            self._session.timeout_count = 0
            if transcript.language:
                self._session.detected_language = transcript.language

            # --- Wave 4-A: FSM injection point -------------------------------
            # Placed here on purpose: transcript.text has already been through
            # _apply_stt_corrections + _apply_entity_normalization, so the FSM
            # sees the same cleaned text the LLM will.
            #
            # FSM_ENABLED=false → the whole block is one boolean compare and the
            # turn continues exactly as it does today. That is the rollback path
            # and it must stay that cheap.
            fsm_mode = self._fsm_mode()
            fsm_took_turn = False
            if fsm_mode != FSM_MODE_OFF:
                # Deterministic, synchronous, zero I/O — legal in shadow mode.
                self._run_fsm_deterministic_step(transcript)
                if fsm_mode == FSM_MODE_LIVE:
                    # Only live mode is allowed to reach the customer.
                    fsm_took_turn = await self._maybe_handle_intent(transcript)
                    if not fsm_took_turn:
                        fsm_took_turn = await self._maybe_speak_fsm_question(transcript)
            if fsm_took_turn:
                if await self._close_turn():
                    break
                continue
            # -----------------------------------------------------------------

            # Auto-detect scenario from customer text (every turn).
            # First detection sets session.scenario; subsequent detections
            # accumulate in active_scenarios so modules are only added, never removed.
            detected = detect_scenario_from_text(transcript.text)
            if detected:
                if self._session.scenario is None:
                    self._session.scenario = detected
                    logger.info(
                        "Scenario auto-detected: %s for call %s",
                        detected,
                        self._session.channel_uuid,
                    )
                if detected not in self._session.active_scenarios:
                    self._session.active_scenarios.add(detected)
                    logger.info(
                        "Scenario added: %s (active: %s) for call %s",
                        detected,
                        self._session.active_scenarios,
                        self._session.channel_uuid,
                    )

            # Search for relevant conversation patterns
            pattern_context = None
            if self._pattern_search is not None:
                try:
                    tid = str(self._session.tenant_id) if self._session.tenant_id else None
                    patterns = await self._pattern_search.search(
                        query=transcript.text,
                        top_k=3,
                        min_similarity=0.72,
                        tenant_id=tid,
                    )
                    pattern_context = await self._pattern_search.format_for_prompt(patterns)
                    if patterns:
                        await self._pattern_search.increment_usage([p["id"] for p in patterns])
                        logger.info(
                            "Pattern injection: call=%s, patterns_found=%d, intents=%s",
                            self._session.channel_uuid,
                            len(patterns),
                            [p["intent_label"] for p in patterns],
                        )
                except Exception:
                    logger.warning("Pattern search failed, continuing without", exc_info=True)

            # Compute order stage for stage-aware prompt injection
            order_stage = compute_order_stage(self._session.order_draft, self._session.order_id)

            # If exactly one fitting station has been resolved for this call, inject
            # its full details into the LLM prompt so the model doesn't hallucinate
            # a different city/address (seen 2026-07-22 with Виктория/Запоріжжя
            # drifting to Київ during a silence gap). Multiple stations = customer
            # is still choosing, no injection.
            # Priority: (1) the station the LLM last acted on via
            # get_fitting_slots/book_fitting — that's the client's chosen one,
            # regardless of how many were shown. (2) fallback: single-station
            # case. (3) otherwise leave None so the LLM asks the client to pick.
            selected_station = self._resolve_selected_station()

            # Anti-hallucination: pin the slot the LLM must use. If the client
            # already picked a specific (date, time) — inject as "selected".
            # Otherwise inject the list of dates+times returned by the last
            # get_fitting_slots call so the LLM can't invent a fresh one.
            selected_slot: dict[str, str] | None = None
            offered_slots: list[dict[str, str]] | None = None
            if self._session.selected_fitting_date and self._session.selected_fitting_time:
                selected_slot = {
                    "date": self._session.selected_fitting_date,
                    "time": self._session.selected_fitting_time,
                }
            elif self._session.fitting_slots_offered:
                offered_slots = list(self._session.fitting_slots_offered)

            # Server-side extract requested weekday from user text. Client
            # often names weekday early («на пʼятницю»), then bot goes through
            # storage/city clarifications and forgets. Store in session so
            # progress block reminds LLM. Call 2026-08-03: bot re-asked date
            # after storage question, ignoring earlier «пʼятницю».
            if self._session.fitting_requested_weekday is None:
                # Wave 12 (2026-09-07) — extended with Russian day names.
                # Root case ebe7dfcb: turn 20 «запад на среду по 12» — «среду»
                # doesn't share the «серед» prefix, so guard missed →
                # session.fitting_requested_weekday stayed None → LLM freely
                # computed date_from=2026-09-08 (Tue) instead of 09-09 (Wed).
                _wd_kw = {
                    "понеділок": 0, "понеділка": 0, "понедельник": 0,
                    "вівторок": 1, "вівторка": 1, "вторник": 1,
                    "серед": 2, "среду": 2, "среды": 2, "среда": 2,
                    "четвер": 3, "четвр": 3, "четверг": 3,
                    "п'ятниц": 4, "пʼятниц": 4, "пятниц": 4,
                    "субот": 5, "суббот": 5,
                    "неділ": 6, "воскресен": 6,
                }
                _text_wd = transcript.text.lower()
                for _kw, _wd in _wd_kw.items():
                    if _kw in _text_wd:
                        self._session.fitting_requested_weekday = _wd
                        logger.info(
                            "Weekday auto-detected: %d (%s) from call=%s text=%r",
                            _wd, _kw, self._session.channel_uuid,
                            transcript.text[:60],
                        )
                        break

            # Server-side detect "own tires" phrases in user utterance.
            # Client often answers the storage question unprompted while
            # picking a station (call 2026-08-02). Pre-set choice="own" so
            # the progress block shows ✅ and LLM skips Krok 2.
            # Also flips from "contract" → "own" when find_storage auto-
            # locked the session to a contract but client actually said
            # «свої з собою» (call 2026-08-03 14:55: session stuck on
            # contract → 3-day guard blocked next-day booking forever).
            if self._session.fitting_storage_choice != "own":
                # Wave 4-A: the hint list moved to module scope
                # (_STORAGE_OWN_HINTS / _STORAGE_OWN_HINTS_WHEN_ASKED,
                # applied by detect_own_tires) so the FSM mapping layer shares
                # one source of truth with this legacy nudge. The list itself
                # is unchanged, verbatim, and so is the behaviour.
                _last_bot = ""
                for _t in reversed(self._session.dialog_history):
                    if _t.speaker == "assistant" and _t.content:
                        _last_bot = _t.content.lower()
                        break
                if detect_own_tires(
                    transcript.text,
                    asking_storage=_bot_is_asking_storage(_last_bot),
                ):
                    _was_contract = (
                        self._session.fitting_storage_choice == "contract"
                    )
                    self._session.fitting_storage_choice = "own"
                    # Flip cleanup: when find_storage previously auto-locked
                    # us to a contract, clear the contract state AND clear
                    # storage_contracts_found so book_fitting's "storage
                    # forgot to pass NumberContract" guard does not force a
                    # sentinel retry. The client explicitly said «свої з
                    # собою» — respect that.
                    if _was_contract:
                        self._session.fitting_storage_contract = None
                        self._session.storage_contracts_found = []
                        self._session.storage_contract_guard_triggered = False
                        logger.info(
                            "Storage FLIPPED contract→own for call=%s "
                            "(client said own-tires); cleared contract state",
                            self._session.channel_uuid,
                        )
                    else:
                        logger.info(
                            "Storage auto-detected 'own' from user text: call=%s",
                            self._session.channel_uuid,
                        )

            # Wave 4B (2026-09-03) — Color auto-detect. session.fitting_plate
            # (semantically now = color per 2026-08-18 refactor) stays None
            # unless preparser matches a DSTU plate. When the client answers
            # Krok 5 «Назвіть колір авто» with «червоний» / «сірий», preparser
            # sees nothing → checklist stays ⏳ → LLM sometimes drops
            # auto_number on book_fitting → server rejects → LLM invents
            # transfer or re-asks Krok 1 (call 1a799364 Wave 4B #1: ✅ was
            # missing so book_fitting got auto_number="" → «У якому районі?»
            # re-ask). Same defense pattern as storage own-detect below.
            #
            # Wave 5 (2026-09-03) — regex with word-boundary + Cyrillic tail
            # replaces the substring list. Call dd3dd368 showed «Червоны»
            # / «червона» / «біла» / «чорна» / «серая» were all missed
            # because the old hints stored only nominative masculine forms
            # («червоний», «білий», «чорний», «серый»). Now every root
            # matches all UA/RU gender/case inflections + STT mutations.
            if not self._session.fitting_plate:
                _text_lc = transcript.text.lower()
                # Scope to Krok 5: bot's last utterance mentions «колір» or
                # «марк». Avoids false-positive on «сірий» as unrelated
                # descriptor earlier in the call.
                _last_bot_color = ""
                for _t in reversed(self._session.dialog_history):
                    if _t.speaker == "assistant" and _t.content:
                        _last_bot_color = _t.content.lower()
                        break
                _asking_color = (
                    "колір" in _last_bot_color
                    or "цвет" in _last_bot_color
                    or "марк" in _last_bot_color
                )
                if _asking_color:
                    from src.agent.color_detect import detect_color

                    _matched = detect_color(_text_lc)
                    if _matched:
                        self._session.fitting_plate = _matched
                        logger.info(
                            "Color auto-detected %r for call=%s "
                            "(prevents LLM auto_number drop)",
                            _matched, self._session.channel_uuid,
                        )

            # Wave 6 (2026-09-03) — Name auto-persist. LLM sometimes
            # skips update_customer_profile after Krok 0 (call fcfb26a9
            # turn 1 «Юра» → tools=0). Without a persisted name, state
            # guard shows ⏳ Ім'я for the entire call, and LLM later
            # confabulates «Марина» (bot's name) or leaks example names
            # from prompt anti-patterns («Василь», «Наталя»).
            if not self._session.fitting_customer_name:
                from src.agent.name_detect import detect_name, is_name_question

                _last_bot_for_name = ""
                for _t in reversed(self._session.dialog_history):
                    if _t.speaker == "assistant" and _t.content:
                        _last_bot_for_name = _t.content
                        break
                if is_name_question(_last_bot_for_name):
                    _name = detect_name(transcript.text)
                    if _name:
                        self._session.fitting_customer_name = _name
                        # Mark as Krok-0-sourced so LLM overwrites via
                        # update_customer_profile(name=X) can be flagged.
                        self._session.name_from_krok0 = True
                        logger.info(
                            "Name auto-detected %r for call=%s "
                            "(prevents LLM name confabulation)",
                            _name, self._session.channel_uuid,
                        )

            # Wave 12 (2026-09-07) — Tire diameter auto-detect. Backend guard
            # for `_get_fitting_price` rejects LLM calls where the customer
            # never mentioned a diameter (root case ebe7dfcb: bot output R16
            # default price hallucination pre-question). We record the client
            # answer only when bot's last question was about diameter to avoid
            # false-positives on slot times («на 14:30» → 14 ≠ diameter).
            if self._session.fitting_diameter_client is None:
                from src.agent.diameter_detect import (
                    detect_diameter,
                    is_diameter_question,
                )

                _last_bot_dia = ""
                for _t in reversed(self._session.dialog_history):
                    if _t.speaker == "assistant" and _t.content:
                        _last_bot_dia = _t.content
                        break
                if is_diameter_question(_last_bot_dia):
                    _d = detect_diameter(transcript.text)
                    if _d is not None:
                        self._session.fitting_diameter_client = _d
                        logger.info(
                            "Diameter auto-detected R%d for call=%s "
                            "(unlocks get_fitting_price guard)",
                            _d, self._session.channel_uuid,
                        )

            # Wave 14 (2026-09-07) — Slot pin. Between get_fitting_slots and
            # book_fitting nothing recorded the client's verbal pick, so the
            # LLM could deny a time it had just offered (call 2026-09-07: bot
            # read a truncated list, client said «14 это 20», bot answered
            # «Слота на чотирнадцяту двадцять немає»). A pin can only ever
            # select a slot already present in fitting_slots_offered.
            if self._session.fitting_slots_offered:
                from src.agent.time_detect import bot_listed_slots, detect_time_choice

                _last_bot_slot = ""
                for _t in reversed(self._session.dialog_history):
                    if _t.speaker == "assistant" and _t.content:
                        _last_bot_slot = _t.content
                        break
                _offered_times = [
                    s["time"] for s in self._session.fitting_slots_offered
                ]
                # A bare hour is only unambiguous right after the bot read the
                # list out; later in the dialog «17» is far more likely a
                # diameter, so restrict widening to the unpinned case.
                _picked = detect_time_choice(
                    transcript.text,
                    _offered_times,
                    allow_hour_only=(
                        not self._session.selected_fitting_time
                        and bot_listed_slots(_last_bot_slot)
                    ),
                )
                if _picked and _picked != self._session.selected_fitting_time:
                    self._session.selected_fitting_time = _picked
                    logger.info(
                        "Slot auto-pinned %s for call=%s (client picked from "
                        "offered list; bot can no longer claim it is taken)",
                        _picked, self._session.channel_uuid,
                    )

            # Deterministic pre-parser (Phase 3 2026-08-14): pull car brand
            # and licence plate out of any user turn with regex/keyword match.
            # Only writes to session when the field is empty — never trample
            # an LLM-driven value from a later turn. Verbose callers who say
            # everything at once («на завтра, лексус AA1234BB, свої») now
            # skip 2-3 follow-up questions.
            if not self._session.fitting_plate or not self._session.fitting_vehicle_brand:
                try:
                    from src.agent.preparse import preparse_fitting

                    _extracted = preparse_fitting(transcript.text)
                    if _extracted:
                        if "plate" in _extracted and not self._session.fitting_plate:
                            self._session.fitting_plate = _extracted["plate"]
                        if "brand" in _extracted and not self._session.fitting_vehicle_brand:
                            self._session.fitting_vehicle_brand = _extracted["brand"]
                        logger.info(
                            "Fitting preparse for call %s extracted %s from %r",
                            self._session.channel_uuid,
                            _extracted,
                            transcript.text[:60],
                        )
                except Exception:
                    logger.debug(
                        "Fitting preparse failed for call %s",
                        self._session.channel_uuid,
                        exc_info=True,
                    )

            # Wave 5 (2026-09-03) — Krok 8 auto-book detection.
            # If the bot asked "Підтверджуєте?" and the customer answered
            # with an affirmation, set a marker so the state guard renders
            # an EMERGENCY banner forcing the LLM to call book_fitting on
            # this turn. Prompt-only rules regress at 70+ turns (call
            # dd3dd368 turn 77-78: «так» → «Перепрошую, не розчула»).
            # Wave 15 (2026-09-07) — both halves of the match were too
            # narrow and call 7462c08b slipped through, ending with the bot
            # telling the customer «ви записані» while book_fitting was
            # never called. Look back two assistant turns, because the
            # «Перепрошую, не розчула» re-ask sits between the question and
            # the answer without closing it.
            _krok8_confirmed = False
            _recent_bot_msgs: list[str] = []
            for _t in reversed(self._session.dialog_history):
                if _t.speaker == "assistant" and _t.content:
                    _recent_bot_msgs.append(_t.content)
                    if len(_recent_bot_msgs) == 2:
                        break
            if asked_for_confirmation(_recent_bot_msgs):
                _text_stripped = transcript.text.strip()
                if is_confirmation(_text_stripped):
                    # Wave 6 (2026-09-03) — sanity check: only fire the
                    # EMERGENCY book-fitting banner if ALL checklist
                    # fields are actually ✅. Wave 5 fired regardless of
                    # state, which contradicted the state guard when
                    # fields were ⏳ (call fcfb26a9 turn 78-80 loop).
                    _all_fields_ready = bool(
                        self._session.fitting_customer_name
                        and (selected_station or {}).get("city")
                        and (selected_station or {}).get("address")
                        and self._session.fitting_storage_choice is not None
                        and self._session.selected_fitting_date
                        and self._session.selected_fitting_time
                        and self._session.fitting_plate
                        and self._session.fitting_vehicle_brand
                        and self._session.caller_phone
                    )
                    if _all_fields_ready:
                        _krok8_confirmed = True
                        logger.info(
                            "Krok 8 auto-book marker set for call=%s "
                            "(bot: «...Підтверджуєте?», customer: %r, all ✅)",
                            self._session.channel_uuid, _text_stripped,
                        )
                    else:
                        # Wave 17 (2026-09-09) — refusing to force a doomed
                        # book_fitting is right, but staying silent let the
                        # bot carry on and announce a booking it never made
                        # (calls c1988daf, 57494646). Raise the Wave 6
                        # correction banner instead: it is rendered from this
                        # flag a few lines below, so it lands on this turn.
                        self._session.krok8_confabulation_pending = True
                        logger.warning(
                            "Krok 8 «так» arrived but fields still ⏳ for "
                            "call=%s (LLM hallucinated confirmation). "
                            "Suppressing emergency banner; raising the "
                            "checklist-correction banner instead.",
                            self._session.channel_uuid,
                        )

            # Fitting progress block: shows LLM what's already collected so it
            # doesn't loop back to Krok 2/3/4 after passing through them.
            fitting_progress = self._build_fitting_progress(
                selected_station, krok8_confirmed=_krok8_confirmed
            )
            # Consume the flag: reset after passing to guard (one-shot signal).
            self._session.krok8_confabulation_pending = False

            if self._streaming_loop is not None:
                # STREAMING PATH — add user turn to session (streaming loop uses separate _llm_history)
                self._session.add_user_turn(
                    content=transcript.text,
                    stt_confidence=transcript.confidence,
                    detected_language=transcript.language,
                )
                await self._log_turn(
                    "customer",
                    transcript.text,
                    stt_confidence=transcript.confidence,
                    language=transcript.language,
                )
                self._session.transition_to(CallState.SPEAKING)
                self._speaking = True
                self._barge_in_event.clear()
                start = time.monotonic()
                try:
                    result = await asyncio.wait_for(
                        self._streaming_loop.run_turn(
                            user_text=transcript.text,
                            conversation_history=self._llm_history,
                            caller_phone=self._session.caller_phone,
                            order_id=self._session.order_id,
                            pattern_context=pattern_context,
                            order_stage=order_stage,
                            caller_history=self._caller_history,
                            storage_context=self._storage_context,
                            customer_profile=self._customer_profile,
                            fitting_booked=self._session.fitting_booked,
                            tools_called=self._session.tools_called,
                            scenario=self._session.scenario,
                            active_scenarios=self._session.active_scenarios,
                            selected_station=selected_station,
                            selected_slot=selected_slot,
                            offered_slots=offered_slots,
                            fitting_progress=fitting_progress,
                        ),
                        timeout=AGENT_PROCESSING_TIMEOUT_SEC,
                    )
                except TimeoutError:
                    logger.error(
                        "Streaming agent timeout after %ds: call=%s",
                        AGENT_PROCESSING_TIMEOUT_SEC,
                        self._session.channel_uuid,
                    )
                    result = None
                finally:
                    self._speaking = False
                    if self._echo_canceller is not None:
                        self._echo_canceller.clear_far_end()

                llm_latency_ms = int((time.monotonic() - start) * 1000)

                # Track LLM token cost (streaming path)
                if self._cost is not None and result is not None:
                    self._cost.add_llm_usage(
                        result.total_usage.input_tokens,
                        result.total_usage.output_tokens,
                        provider_key=result.provider_key,
                        cached_input_tokens=result.total_usage.cached_input_tokens,
                    )

                if result is not None and result.spoken_text:
                    # Wave 6 (2026-09-03) — detect Krok 8 confabulation.
                    # Bot said «Перевіримо: …, Підтверджуєте?» while some
                    # checklist field was still ⏳. LLM invented values.
                    # Track as metric and set a session flag so the next
                    # state guard can render a correction banner.
                    _spoken_lower = result.spoken_text.lower()
                    # Wave 6 (2026-09-03) — CallerID phone regression metric.
                    # Bot should never ask for phone when caller_phone set.
                    if (
                        self._session.caller_phone
                        and (
                            "номер телефон" in _spoken_lower
                            or "продиктуйте номер" in _spoken_lower
                            or "назвіть номер" in _spoken_lower
                            or "назвіть телефон" in _spoken_lower
                            or "ваш телефон" in _spoken_lower
                            or "який ваш номер" in _spoken_lower
                        )
                        # Exclude confirmations like «Ваш номер X, вірно?»
                        and "вірно?" not in _spoken_lower
                    ):
                        from src.monitoring.metrics import (
                            phone_asked_despite_caller_id_total,
                        )
                        phone_asked_despite_caller_id_total.inc()
                        logger.warning(
                            "Bot asked for phone despite CallerID=%s for "
                            "call=%s (LLM ignored Крок 7 rule). Text: %r",
                            self._session.caller_phone,
                            self._session.channel_uuid,
                            result.spoken_text[:200],
                        )
                    if (
                        "перевіримо" in _spoken_lower
                        and "підтверджує" in _spoken_lower
                    ):
                        _missing: list[str] = []
                        if not self._session.selected_fitting_date:
                            _missing.append("date")
                        if not self._session.selected_fitting_time:
                            _missing.append("time")
                        if not self._session.fitting_plate:
                            _missing.append("color")
                        if not self._session.fitting_vehicle_brand:
                            _missing.append("brand")
                        if _missing:
                            from src.monitoring.metrics import (
                                krok8_confabulation_total,
                            )
                            for _f in _missing:
                                krok8_confabulation_total.labels(missing_field=_f).inc()
                            self._session.krok8_confabulation_pending = True
                            logger.warning(
                                "Krok 8 confabulation for call=%s — bot said "
                                "«Перевіримо: …, Підтверджуєте?» while ⏳ %s. "
                                "Text: %r",
                                self._session.channel_uuid,
                                _missing,
                                result.spoken_text[:200],
                            )
                    # Strip leaked Progress checklist from log / session for
                    # consistency (audio was already filtered at sentence-buffer
                    # level in streaming path).
                    log_text = result.spoken_text
                    log_text, was_leaked = _strip_leaked_system_block(log_text)
                    if was_leaked:
                        logger.warning(
                            "Stripped leaked Progress checklist from streaming "
                            "bot reply for %s (remainder %d chars)",
                            self._session.channel_uuid,
                            len(log_text),
                        )
                    # Strip filler for DB log / session history consistency.
                    # Audio was already streamed to caller, so this only
                    # cleans the recorded turn — LLM's own history (kept in
                    # streaming loop's _llm_history) is unaffected.
                    cleaned, stripped = _strip_filler(log_text or result.spoken_text)
                    if stripped:
                        logger.info(
                            "Stripped %d filler sentence(s) from streaming "
                            "bot reply for %s: %s",
                            len(stripped),
                            self._session.channel_uuid,
                            [pattern for pattern, _snippet in stripped],
                        )
                        for pattern, _snippet in stripped:
                            bot_filler_stripped_total.labels(pattern=pattern).inc()
                    logged_text = cleaned or result.spoken_text
                    self._flag_false_booking_claim(logged_text)
                    self._session.reset_empty_response()
                    self._session.add_assistant_turn(logged_text)
                    await self._log_turn("bot", logged_text, llm_latency_ms=llm_latency_ms)
                    await self._persist_session()
                    logger.info(
                        "Streaming turn completed: call=%s, user='%s', agent='%s', "
                        "tools=%d, llm=%dms%s",
                        self._session.channel_uuid,
                        transcript.text[:50],
                        logged_text[:50],
                        result.tool_calls_made,
                        llm_latency_ms,
                        f", wait_phrase='{result.wait_phrase}'" if result.wait_phrase else "",
                    )
                elif result is not None and (result.interrupted or result.disconnected):
                    # Barge-in or hangup before LLM produced any text — not an error
                    logger.info(
                        "Streaming turn interrupted before speech: call=%s, user='%s'",
                        self._session.channel_uuid,
                        transcript.text[:50],
                    )
                else:
                    fallback = self._resolve_empty_response_fallback()
                    self._session.add_assistant_turn(fallback)
                    await self._log_turn("bot", fallback)
                    await self._persist_session()
                    await self._speak(fallback)
            else:
                # BLOCKING PATH — delay add_user_turn so process_message
                # doesn't see it duplicated in messages_for_llm
                self._session.transition_to(CallState.PROCESSING)
                wait_default = self._templates.get("wait", WAIT_TEXT)
                wait_msg = _select_wait_message(transcript.text, wait_default)
                await self._log_turn("bot", wait_msg)
                await self._speak(wait_msg)  # contextual filler while processing

                start = time.monotonic()
                try:
                    response_text, _ = await asyncio.wait_for(
                        self._agent.process_message(
                            user_text=transcript.text,
                            conversation_history=self._session.messages_for_llm,
                            caller_phone=self._session.caller_phone,
                            order_id=self._session.order_id,
                            pattern_context=pattern_context,
                            order_stage=order_stage,
                            caller_history=self._caller_history,
                            storage_context=self._storage_context,
                            customer_profile=self._customer_profile,
                            fitting_booked=self._session.fitting_booked,
                            tools_called=self._session.tools_called,
                            scenario=self._session.scenario,
                            active_scenarios=self._session.active_scenarios,
                            selected_station=selected_station,
                            selected_slot=selected_slot,
                            offered_slots=offered_slots,
                            fitting_progress=fitting_progress,
                        ),
                        timeout=AGENT_PROCESSING_TIMEOUT_SEC,
                    )
                except TimeoutError:
                    logger.error(
                        "Agent timeout after %ds: call=%s",
                        AGENT_PROCESSING_TIMEOUT_SEC,
                        self._session.channel_uuid,
                    )
                    response_text = ""

                llm_latency_ms = int((time.monotonic() - start) * 1000)

                # Track LLM token cost (blocking path)
                if self._cost is not None:
                    self._cost.add_llm_usage(
                        self._agent.last_input_tokens,
                        self._agent.last_output_tokens,
                        provider_key=self._agent.last_provider_key,
                        cached_input_tokens=self._agent.last_cached_input_tokens,
                    )

                # Now record the user turn in session (for DB/analytics)
                self._session.add_user_turn(
                    content=transcript.text,
                    stt_confidence=transcript.confidence,
                    detected_language=transcript.language,
                )
                await self._log_turn(
                    "customer",
                    transcript.text,
                    stt_confidence=transcript.confidence,
                    language=transcript.language,
                )

                logger.info(
                    "Turn completed: call=%s, user='%s', agent='%s', llm=%dms",
                    self._session.channel_uuid,
                    transcript.text[:50],
                    response_text[:50] if response_text else "(empty)",
                    llm_latency_ms,
                )

                if response_text:
                    # Strip duplicate greeting that LLM may produce
                    response_text = _strip_greeting(response_text)
                    # Strip any leaked "## 📋 Прогрес запису … НАСТУПНИЙ КРОК"
                    # system-prompt checklist that the LLM sometimes echoes.
                    response_text, was_leaked = _strip_leaked_system_block(response_text)
                    if was_leaked:
                        logger.warning(
                            "Stripped leaked Progress checklist from bot reply "
                            "for %s (remainder %d chars)",
                            self._session.channel_uuid,
                            len(response_text),
                        )
                    # Strip filler sentences ("Зараз перевірю...", "Чекайте,
                    # будь ласка", "Зрозуміла... Отже...") that pad LLM output.
                    cleaned, stripped = _strip_filler(response_text)
                    if stripped:
                        logger.info(
                            "Stripped %d filler sentence(s) from bot reply "
                            "for %s: %s",
                            len(stripped),
                            self._session.channel_uuid,
                            [pattern for pattern, _snippet in stripped],
                        )
                        for pattern, _snippet in stripped:
                            bot_filler_stripped_total.labels(pattern=pattern).inc()
                        # Fall back to original if stripping emptied the text
                        # (defensive: never send empty text to TTS).
                        response_text = cleaned or response_text
                    self._flag_false_booking_claim(response_text)
                    self._session.reset_empty_response()
                    self._session.add_assistant_turn(response_text)
                    await self._log_turn("bot", response_text, llm_latency_ms=llm_latency_ms)
                    await self._persist_session()
                    await self._speak_streaming(response_text)
                else:
                    # Never leave the caller in silence — speak an error fallback
                    fallback = self._resolve_empty_response_fallback()
                    self._session.add_assistant_turn(fallback)
                    await self._log_turn("bot", fallback)
                    await self._persist_session()
                    await self._speak(fallback)

            # Check if transfer was triggered
            if await self._close_turn():
                break

    async def _close_turn(self) -> bool:
        """End-of-turn bookkeeping shared by the LLM path and the FSM path.

        Returns True when the call must stop looping (transfer announced).

        Extracted in Wave 4-A so the FSM short-circuit can reuse the exact same
        transfer handling instead of duplicating it — a divergence here is how
        an FSM-driven TRANSFER would end up silently never announced.
        """
        if self._session.transferred:
            transfer_msg = self._templates.get("transfer", TRANSFER_TEXT)
            await self._log_turn("bot", transfer_msg)
            await self._speak(transfer_msg)
            self._session.transition_to(CallState.TRANSFERRING)
            return True

        # Read the spoken text back off the session so both the LLM path and
        # the FSM short-circuit are covered by one check. Only the LAST turn
        # counts: on barge-in the bot adds no turn at all, and walking back past
        # the caller to an earlier goodbye would re-arm the hangup on someone
        # who just interrupted to ask something.
        history = self._session.dialog_history
        last_turn = history[-1] if history else None
        self._farewell_spoken = (
            last_turn is not None
            and last_turn.speaker == "assistant"
            and _is_farewell(last_turn.content or "")
        )

        self._session.transition_to(CallState.LISTENING)
        return False

    async def _generate_contextual_farewell(self) -> str | None:
        """Generate a contextual farewell based on conversation history.

        Returns None if the conversation is too short or LLM fails,
        so the caller can fall back to the standard template.

        Uses _llm_history (full context with tool_use/tool_result) instead
        of session.messages_for_llm (user/assistant text only) so the farewell
        can reference tool results like order confirmations, booking details, etc.
        """
        # Too short — use default template
        if len(self._session.dialog_history) < _FAREWELL_MIN_TURNS:
            return None

        # Rule: if an order was placed, use a specific farewell
        if self._session.order_id:
            return FAREWELL_ORDER_TEXT

        # If the caller never spoke (STT dropped all audio, or the caller
        # went silent from the start), a warm LLM-generated «Була рада
        # допомогти!» sounds sarcastic. Use a neutral hangup instead.
        # Anchor: call 2026-08-28 — Наташа silence throughout, bot
        # signed off with «Була рада допомогти! До побачення» after 3
        # silence timeouts. Right response: acknowledge the audio issue.
        user_turn_count = sum(
            1 for t in self._session.dialog_history if t.speaker == "user"
        )
        if user_turn_count == 0:
            return (
                "На жаль, не чую вас — можливо, проблеми зі зв'язком. "
                "Передзвоніть, будь ласка. До побачення."
            )

        # Use _llm_history (full context) for better farewell quality.
        # Falls back to session.messages_for_llm if _llm_history is empty
        # (shouldn't happen in practice, but safe).
        history = self._llm_history if self._llm_history else self._session.messages_for_llm
        try:
            response_text, _ = await asyncio.wait_for(
                self._agent.process_message(
                    user_text=_FAREWELL_SYSTEM_PROMPT,
                    conversation_history=history,
                ),
                timeout=_FAREWELL_LLM_TIMEOUT_SEC,
            )
            if response_text and response_text.strip():
                return response_text.strip()
        except TimeoutError:
            logger.warning("Contextual farewell LLM timed out: %s", self._session.channel_uuid)
        except Exception:
            logger.warning(
                "Contextual farewell LLM failed: %s", self._session.channel_uuid, exc_info=True
            )

        return None

    async def _wait_for_final_transcript(self) -> Transcript | None:
        """Wait for a final transcript from STT, with silence timeout.

        Returns None on silence timeout. Once the bot has said goodbye the
        wait shrinks to `_FAREWELL_HANGUP_GRACE_SEC` — just enough for a
        caller who still has a question to speak up.
        """
        timeout = (
            _FAREWELL_HANGUP_GRACE_SEC if self._farewell_spoken else SILENCE_TIMEOUT_SEC
        )
        try:
            return await asyncio.wait_for(
                self._get_next_final_transcript(),
                timeout=timeout,
            )
        except TimeoutError:
            return None

    async def _get_next_final_transcript(self) -> Transcript:
        """Block until a final transcript with non-empty text arrives."""
        transcript = await self._final_transcript_queue.get()
        if transcript is None:
            # STT stream ended — sentinel from _transcript_reader_loop
            raise asyncio.CancelledError
        return transcript

    async def _speak(self, text: str) -> None:
        """Synthesize text and send audio to AudioSocket.

        Supports barge-in: if the caller starts speaking during synthesis
        or playback, audio sending is interrupted early.
        """
        if self._conn.is_closed:
            return

        # Last-mile TTS normalisation (ISO dates, tire sizes, house-number
        # letter suffixes) — same rewriter the streaming path uses.
        from src.tts.streaming_tts import _normalize_for_tts

        text = _normalize_for_tts(text)

        # Track TTS character cost
        if self._cost is not None:
            self._cost.add_tts_usage(len(text))

        self._session.transition_to(CallState.SPEAKING)
        self._speaking = True
        self._barge_in_event.clear()

        try:
            logger.info(
                "TTS synthesize start (%d chars) for %s", len(text), self._session.channel_uuid
            )
            _synth_t0 = time.monotonic()
            audio = await self._tts.synthesize(text)
            _synth_ms = int((time.monotonic() - _synth_t0) * 1000)
            logger.info(
                "TTS synthesize done (%d bytes, %d ms) for %s",
                len(audio) if audio else 0,
                _synth_ms,
                self._session.channel_uuid,
            )

            # Check if barge-in was detected during TTS synthesis
            if self._barge_in_event.is_set():
                logger.info("Barge-in during TTS synthesis: %s", self._session.channel_uuid)
                return

            if self._echo_canceller is not None:
                self._echo_canceller.record_far_end(audio)

            t0 = time.monotonic()
            interrupted = await self._conn.send_audio(audio, cancel_event=self._barge_in_event)
            tts_delivery_ms.observe((time.monotonic() - t0) * 1000)

            if interrupted:
                logger.info("Barge-in during audio send: %s", self._session.channel_uuid)
        except Exception:
            logger.exception("TTS/send error: %s", self._session.channel_uuid)
        finally:
            self._speaking = False
            if self._echo_canceller is not None:
                self._echo_canceller.clear_far_end()
            # Suppress echo-triggered barge-in for 500ms after TTS ends
            self._barge_in_event.clear()
            await asyncio.sleep(_BARGE_IN_SUPPRESSION_SEC)

    async def _speak_streaming(self, text: str) -> None:
        """Synthesize and send audio sentence by sentence.

        Supports barge-in: stops sending if the caller starts speaking,
        both between sentences (event check) and mid-chunk (cancel_event).
        """
        if self._conn.is_closed:
            return

        # Track TTS character cost
        if self._cost is not None:
            self._cost.add_tts_usage(len(text))

        self._session.transition_to(CallState.SPEAKING)
        self._speaking = True
        self._barge_in_event.clear()

        try:
            async for audio_chunk in self._tts.synthesize_stream(text):
                # Check for barge-in between sentences
                if self._barge_in_event.is_set():
                    logger.info("Barge-in between sentences: %s", self._session.channel_uuid)
                    break

                if self._echo_canceller is not None:
                    self._echo_canceller.record_far_end(audio_chunk)

                t0 = time.monotonic()
                interrupted = await self._conn.send_audio(
                    audio_chunk, cancel_event=self._barge_in_event
                )
                tts_delivery_ms.observe((time.monotonic() - t0) * 1000)

                if interrupted:
                    logger.info("Barge-in during chunk send: %s", self._session.channel_uuid)
                    break
        except Exception:
            logger.exception("TTS streaming error: %s", self._session.channel_uuid)
        finally:
            self._speaking = False
            if self._echo_canceller is not None:
                self._echo_canceller.clear_far_end()
            # Suppress echo-triggered barge-in for 500ms after TTS ends
            self._barge_in_event.clear()
            await asyncio.sleep(_BARGE_IN_SUPPRESSION_SEC)
