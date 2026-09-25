"""Streaming agent loop — LLM ↔ tool execution with real-time audio.

Each LLM invocation streams through Layers 2→3→4 (sentence buffer →
TTS → audio sender). If the result contains tool_calls, execute them
and loop back to the LLM with results.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from src.agent.agent import MAX_HISTORY_MESSAGES, MAX_TOOL_CALLS_PER_TURN
from src.agent.booking_consent import (
    BOOKING_DECLINED_FAREWELL,
    BOOKING_OFFER,
    GATE_FAREWELL,
)
from src.agent.history_compressor import summarize_old_messages
from src.agent.prompts import (
    SYSTEM_PROMPT,
    WAIT_AVAILABILITY_POOL,
    WAIT_BOOKING_LOOKUP_POOL,
    WAIT_BOOKING_POOL,
    WAIT_CANCEL_POOL,
    WAIT_DEFAULT_POOL,
    WAIT_FITTING_POOL,
    WAIT_FITTING_PRICE_POOL,
    WAIT_KNOWLEDGE_POOL,
    WAIT_SEARCH_POOL,
    WAIT_STATIONS_POOL,
    WAIT_STATUS_POOL,
    WAIT_STORAGE_POOL,
    WAIT_THINKING_POOL,
    build_system_prompt_with_context,
    fitting_confirmation_sentence,
    fitting_steps_collected,
    next_fitting_question,
)
from src.agent.time_detect import (
    asks_which_time,
    denies_a_slot,
    detect_time_choice,
    hour_only_allowed,
    lists_alternative_times,
    reslices_the_pinned_hour,
)
from src.agent.tool_result_compressor import compress_tool_result
from src.agent.tools import ALL_TOOLS, filter_tools_by_state
from src.core.audio_sender import send_audio_stream
from src.core.sentence_buffer import BufferEvent, SentenceReady, buffer_sentences
from src.llm.models import (
    LLMTask,
    ToolCallDelta,
    ToolCallEnd,
    ToolCallStart,
    Usage,
)
from src.monitoring.metrics import (
    booking_offer_redirected_total,
    control_plane_prose_dropped_total,
    false_transfer_blocked_total,
    guard_refusal_repeated_total,
    history_compression_mode,
    history_messages_count,
    llm_stop_reason_total,
    reopened_time_choice_total,
    settled_question_redirect_skipped_total,
    settled_question_redirected_total,
    system_prompt_chars,
    tool_call_errors_total,
    tool_rounds_exhausted_total,
    tool_rounds_per_turn,
    transfer_promise_suppressed_total,
    transfer_promise_unbacked_total,
)
from src.tts.streaming_tts import synthesize_stream

# Per-tool execution timeout (seconds). Prevents a single slow 1C/API call
# from blocking the entire agent turn. On timeout, tool returns an error
# message so the LLM can respond gracefully.
_TOOL_TIMEOUT_SEC = 15

# Delay (seconds) before playing a filler phrase when LLM is slow to respond.
# Tuned 2026-08-14 (calls c11eae66, 8c85d7c5): mobile carriers / SIP trunk
# were killing AudioSocket connections when the server went silent even ~2s
# during LLM inference. The 1.0s value still lost calls because LLM emits
# tool_call in <1s → stream ends → filler_task cancelled BEFORE its sleep
# completed → 0 audio played. 0.3s is aggressive but survives the 1-3 sec
# «client speech → first bot audio» window that carriers seem to enforce.
# Cost: on every turn caller hears ~200-500ms of «Секундочку…» before
# real audio. Acceptable price for calls not dropping.
_FILLER_DELAY_SEC = 0.3

# Hard cap on TTS pre-synthesis for the filler phrase. If TTS API is hung,
# never block run_turn on it — proceed without filler.
_FILLER_PRESYNTH_TIMEOUT_SEC = 1.5

# Max retries when LLM returns empty (0 text, 0 tools). On the last retry,
# automatically switch to a fallback provider.
_MAX_EMPTY_RETRIES = 2

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from src.agent.agent import ToolRouter
    from src.core.audio_socket import AudioSocketConnection
    from src.core.echo_canceller import EchoCanceller
    from src.llm.router import LLMRouter
    from src.logging.pii_vault import PIIVault
    from src.tts.base import TTSEngine

logger = logging.getLogger(__name__)

# Map tool names to contextual wait-phrase pools.
# When the LLM emits a tool call without preceding text, the streaming
# loop speaks one of these phrases while the tool executes.
_TOOL_WAIT_POOLS: dict[str, list[str]] = {
    "search_tires": WAIT_SEARCH_POOL,
    "check_availability": WAIT_AVAILABILITY_POOL,
    "get_order_status": WAIT_STATUS_POOL,
    "get_fitting_stations": WAIT_STATIONS_POOL,  # search points/addresses
    "get_fitting_slots": WAIT_FITTING_POOL,       # check schedule/slots
    "book_fitting": WAIT_BOOKING_POOL,
    "cancel_fitting": WAIT_CANCEL_POOL,
    "get_fitting_price": WAIT_FITTING_PRICE_POOL,
    "get_customer_bookings": WAIT_BOOKING_LOOKUP_POOL,  # find an existing booking
    "search_knowledge_base": WAIT_KNOWLEDGE_POOL,
    "find_storage": WAIT_STORAGE_POOL,
}


# Upper bound for the per-call random start offset of the tool wait rotation.
# `_pick_tool_wait_phrase` takes the index modulo its pool, so any non-negative
# value is valid; the longest pool is the only one that can use the full range.
_MAX_TOOL_WAIT_POOL_LEN = max(len(pool) for pool in _TOOL_WAIT_POOLS.values())


# How long after a thinking filler stops playing a tool wait phrase still reads
# as the same breath. The filler ends roughly when the LLM stream is about to
# yield its tool call (filler starts 0.3s in, LLM p50 is ~1.5-2s), so the two
# land within a second of each other in the ordinary case.
_FILLER_ECHO_WINDOW_SEC = 2.0


def _filler_still_ringing(filler_finished_at: float | None, now: float | None = None) -> bool:
    """True if a thinking filler finished recently enough that a wait phrase
    now would sound like a second half of it.

    `None` means the caller never heard a filler this round — real audio beat it
    or pre-synthesis failed — so the wait phrase is the only thing covering the
    tool call and must still be spoken.
    """
    if filler_finished_at is None:
        return False
    return (now if now is not None else time.monotonic()) - filler_finished_at < (
        _FILLER_ECHO_WINDOW_SEC
    )


def _opening_word(text: str) -> str:
    return re.split(r"[\s,.!?]+", text.strip().lower(), maxsplit=1)[0]


def _pick_tool_wait_phrase(tool_names: list[str], index: int = 0, avoid: str = "") -> str:
    """Choose a contextual wait phrase based on the tool(s) being called.

    Rotates by `index` rather than picking at random: on a three-phrase pool
    `random.choice` repeated the same filler back-to-back one time in three.
    `avoid` is the thinking filler queued for this round — a candidate opening
    with the same word is skipped so the caller doesn't hear «Одну мить.»
    immediately followed by «Одну мить, дивлюся адреси.».
    """
    pool = WAIT_DEFAULT_POOL
    for name in tool_names:
        candidate = _TOOL_WAIT_POOLS.get(name)
        if candidate:
            pool = candidate
            break
    avoid_word = _opening_word(avoid) if avoid else ""
    for offset in range(len(pool)):
        phrase = pool[(index + offset) % len(pool)]
        if not avoid_word or _opening_word(phrase) != avoid_word:
            return phrase
    return pool[index % len(pool)]


# Backend guard against LLM hallucinating a customer-request/cannot-help
# transfer when the customer has said nothing that could justify it.
# See prompts.py lines 108-109 (calls 21f61d17, Wave 4 #7): LLM invents
# reason="customer_request" after customer only says their name.
# Prompt-level rules keep regressing; a hard backend gate is the only fix.

# Substrings (case-insensitive, UA/RU/EN) that signal a real request for
# a human. If ANY user text turn contains one, customer_request is allowed.
_OPERATOR_KEYWORDS = (
    "оператор", "менеджер", "консультант",
    "жива людина", "живою людиною", "живий",
    "живой", "живого", "живому",
    "человек", "людин",
    "manager", "operator",
)

# Substrings that signal frustration / can't-help legitimacy.
_ESCALATION_KEYWORDS = (
    "не працює", "не работает", "не могу", "не можу",
    "погано", "плохо", "жах", "ужас", "скарг", "жалоб",
    "не хочу з тобою", "не хочу с тобой", "переключи", "перекл",
    "живого", "живую", "живий", "жива",
)

# Topics the fitting-only bot genuinely cannot serve. Deliberately excludes
# «зберігання» — «шини на зберіганні» is a question the checklist itself asks —
# and bare «шин», which every «свої шини» answer would carry.
#
# Payment, invoices, goods and stock were missing until 2026-09-24. «оплата за
# шини» (a7477e1b) and «уточнить выписку по шинам» (450edbcf) had the LLM reach
# for a transfer, get blocked with «продовжуй чекліст», and ask a caller who
# wanted the accounts desk for their name and city. Over 40 days these stems
# occur in 3 customer turns, all three off-topic, none in a booking call.
_OUT_OF_SCOPE_KEYWORDS = (
    "кредит", "розстрочк", "рассрочк",
    "купити", "купить", "куплю", "придбат",
    "замовл", "заказ", "доставк",
    "гаранті", "гаранти", "поверн", "возврат", "рекламац",
    "оплат", "сплат", "платіж", "платеж", "рахун",
    "виписк", "выписк", "товар", "наявн", "наличи",
)

_GUARD_MARKER = "⛔ HALLUCINATION_GUARD"

# After this many blocks in one call, let the transfer through. Blocking is a
# nudge back into the checklist, not a cage: an LLM that keeps insisting has
# either found a real dead end or is stuck in a loop, and both are better
# resolved by a human than by an endless bot.
_MAX_BLOCKS_PER_CALL = 2


def _count_prior_blocks(history: list[dict[str, Any]]) -> int:
    """Count guard rejections already present in this call's history."""
    count = 0
    for msg in history:
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and _GUARD_MARKER in str(block.get("content", "")):
                count += 1
    return count


def _extract_user_text_turns(history: list[dict[str, Any]]) -> list[str]:
    """Return only free-text customer turns (excludes tool_result content)."""
    turns: list[str] = []
    for msg in history:
        if msg.get("role") != "user":
            continue
        content = msg.get("content")
        if isinstance(content, str) and content.strip():
            turns.append(content.strip())
    return turns


def _should_block_false_transfer(
    tool_args: dict[str, Any], history: list[dict[str, Any]]
) -> str | None:
    """If the transfer_to_operator call looks like a hallucination, return
    a synthetic tool_result message telling the LLM to continue. Otherwise
    return None (transfer is allowed).

    Wave 16 (2026-09-08) — inverted from deny-list to default-deny. The old
    version named only customer_request and cannot_help, so a cornered LLM
    walked the rest of the enum until something passed: call 347317e4 tried
    customer_request → cannot_help → complex_question in 10 seconds and
    escaped through the third, abandoning a fully collected booking. Two of
    the six reasons seen in production (non_fitting_scope,
    fitting_service_unavailable) are not even in the tool schema.

    Every reason now needs positive evidence in the customer's own words:
    - customer_request — an operator keyword anywhere in the call.
    - cannot_help / negative_emotion — an escalation keyword in the last 3
      customer turns.
    - anything else (complex_question, invented reasons) — an out-of-scope
      topic or an escalation keyword in the last 3 customer turns.
    """
    reason = str(tool_args.get("reason", "")).strip().lower()

    if _count_prior_blocks(history) >= _MAX_BLOCKS_PER_CALL:
        logger.warning(
            "Transfer guard exhausted (%d prior blocks) — letting reason=%s through",
            _MAX_BLOCKS_PER_CALL,
            reason,
        )
        return None

    user_turns = _extract_user_text_turns(history)
    joined = " ".join(user_turns).lower()
    recent_3 = " ".join(user_turns[-3:]).lower()

    if reason not in ("customer_request", "cannot_help", "negative_emotion"):
        if any(kw in recent_3 for kw in _OUT_OF_SCOPE_KEYWORDS):
            return None
        if any(kw in recent_3 for kw in _ESCALATION_KEYWORDS):
            return None
        last = user_turns[-1] if user_turns else ""
        return (
            f'{_GUARD_MARKER}: transfer_to_operator(reason="{reason}") '
            "заблокований бекендом. У ОСТАННІХ 3 репліках клієнта немає ні "
            "питання поза шиномонтажем, ні скарги — отже це не привід "
            f"передавати оператору. Остання репліка клієнта: {last!r}. "
            "Якщо book_fitting повернув помилку — прочитай, яких саме полів "
            "бракує, і запитай їх у клієнта. Якщо щось не розчула — перепитай "
            "коротко. Продовжуй чекліст запису."
        )

    if reason == "customer_request":
        # Only allow if the customer actually asked for a human somewhere.
        if any(kw in joined for kw in _OPERATOR_KEYWORDS):
            return None
        last = user_turns[-1] if user_turns else ""
        return (
            f"{_GUARD_MARKER}: transfer_to_operator(reason=\"customer_request\") "
            "заблокований бекендом. Клієнт НЕ просив оператора. "
            f"Останнє повідомлення клієнта: {last!r} (усього реплік клієнта: {len(user_turns)}). "
            "Якщо клієнт назвав ім'я — виклич update_customer_profile(name=...) і продовжи fitting-чекліст "
            "(Крок 1: запитай місто). Не виклик transfer_to_operator знову з цією ж причиною."
        )

    # reason == "cannot_help" | "negative_emotion" — same evidence test: both
    # claim the customer is unhappy, so the customer must sound unhappy.
    # Wave 5 (2026-09-03) — tightened: require escalation keyword in the
    # LAST 3 customer turns (not any turn in history). Call dd3dd368
    # turn 62: 16 turns in, LLM invoked cannot_help after customer said
    # «не feat Fiat» (pronunciation clarification). Old logic passed
    # because len>=5; new logic checks that a REAL escalation happened
    # recently. Escalation from turn 3 doesn't justify a transfer 40
    # turns later — that context was resolved long ago.
    if any(kw in recent_3 for kw in _ESCALATION_KEYWORDS):
        return None
    last = user_turns[-1] if user_turns else ""
    return (
        f'{_GUARD_MARKER}: transfer_to_operator(reason="{reason}") '
        "заблокований бекендом — немає escalation-сигналу від клієнта "
        f"в ОСТАННІХ 3 репліках. Реплік клієнта усього: {len(user_turns)}, "
        f"остання: {last!r}. "
        f"{reason} дозволено ТІЛЬКИ якщо клієнт у останніх 3 репліках "
        "сказав щось на кшталт «не працює», «не розумію», «переключи», "
        "«не хочу з тобою», «погано». Просте уточнення марки/номера/дати "
        "НЕ є escalation'ом. Продовжи чекліст, перепитай коротко якщо "
        "щось не зрозумів."
    )


#: The apostrophe glyphs that reach us in «з'єдную» — the LLM is not consistent
#: about which one it emits, and the sentence never matches if we only know one.
_APOSTROPHES = ("ʼ", "’", "`", "‘", "´")

#: First-person verbs that announce the connection *as it happens*. The tense is
#: the whole discriminator, and a 30-day corpus of every operator-mentioning bot
#: sentence is what drew the line: «переключаю на оператора» is a promise, while
#: «можу переключити вас на оператора» (infinitive offer), «краще поговорити з
#: оператором» (recommendation), «зверніться до оператора» (imperative) and
#: «оператори недоступні» (unavailability) are not, and none of them carries a
#: first-person ending. Perfective futures («переключу», «перемкну») are here
#: because they promise just as hard as the imperfective present.
_CONNECTING_VERBS = (
    "з'єдную",
    "з'єднаю",
    "перекладаю",
    "перемикаю",
    "перемкну",
    "переводжу",
    "переключаю",
    "переключу",
    "переключую",
)

#: Who the caller is being handed to. Required alongside the verb: «переключаю»
#: on its own could one day mean switching a station or a city, and this filter
#: deletes audio, so it refuses to guess.
_HANDOFF_TARGETS = ("оператор", "спеціаліст", "менеджер")


def is_transfer_promise(text: str) -> bool:
    """True when this sentence tells the caller they are being connected now.

    Deliberately narrower than «mentions an operator». The sentence buffer splits
    on clauses past 25 characters, so what arrives here can be a fragment — but
    every promise shape seen in 30 days keeps its verb and its noun in the same
    fragment («з'єдную вас з оператором» and «переключаю на оператора» contain no
    comma), which is why a per-fragment test is enough.
    """
    low = text.lower()
    for glyph in _APOSTROPHES:
        low = low.replace(glyph, "'")
    if not any(verb in low for verb in _CONNECTING_VERBS):
        return False
    return any(target in low for target in _HANDOFF_TARGETS)


async def hold_unconfirmed_transfer_promise(
    stream: AsyncIterator[BufferEvent],
    history: list[dict[str, Any]],
) -> AsyncIterator[BufferEvent]:
    """Withhold «I'm connecting you» until the transfer behind it is allowed.

    The prompt instructs the LLM to say one short phrase *and* call
    `transfer_to_operator` in the same breath (`prompts.py:62`), and the phrase
    is genuinely load-bearing: a successful AMI redirect tears the channel down
    before `_close_turn` can speak `TRANSFER_TEXT`, so in 30 days of production
    that template was never once heard and this sentence was the only thing a
    transferred caller got. It therefore has to be spoken *before* the tool runs
    and cannot simply be moved after it.

    What it must not outrun is the guard's verdict. `_should_block_false_transfer`
    runs where the tool executes, long after `send_audio_stream` has finished, so
    on 11 of the 12 calls in 30 days where the bot promised an operator and none
    arrived, the guard did its job and the caller had already heard the promise.
    Replaying those histories through the real guard is what established that —
    every one of the five reasons was blocked for all 11.

    So the sentence is held exactly as long as it takes the arguments to stream,
    and released the moment the verdict is knowable but before anything is done.

    A held promise followed by no transfer at all is prose, and prose is what it
    usually is: over 21 days 13 of 318 calls heard one, and on 12 of the 14 turns
    the promise is an *opener* the LLM glues onto an ordinary question before
    carrying on — «Одну секунду, з'єдную вас з оператором. Як до вас
    звертатися?» (53761bd9), and two of those calls went on to book. It surfaces
    when the previous turn was unintelligible, which is why the model reaches for
    the one script the prompt gives it (`prompts.py:830`).

    So the unbacked promise is dropped when there is anything behind it. That
    used to be rejected as trading an untrue turn for a silent one; it is not,
    because the buffer splits «Одну секунду,» off as its own fragment ahead of
    the promise and the question survives underneath. When the promise really is
    the tail of the turn — 2 of the 14 — dropping it *would* leave silence, so
    there it is still spoken. Both outcomes are counted.
    """
    held: list[BufferEvent] | None = None
    names: dict[str, str] = {}
    arguments: dict[str, str] = {}

    def resolve(*, drop_promise: bool) -> list[BufferEvent]:
        nonlocal held
        pending = held or []
        held = None
        if drop_promise:
            return [
                e
                for e in pending
                if not (isinstance(e, SentenceReady) and is_transfer_promise(e.text))
            ]
        return pending

    async for event in stream:
        if isinstance(event, ToolCallStart):
            names[event.id] = event.name
        elif isinstance(event, ToolCallDelta):
            arguments[event.id] = arguments.get(event.id, "") + event.arguments_chunk

        if held is None and isinstance(event, SentenceReady) and is_transfer_promise(event.text):
            # Everything after the promise queues behind it so that releasing
            # later cannot reorder the turn.
            held = [event]
            continue

        if held is not None:
            held.append(event)

            if isinstance(event, ToolCallEnd) and names.get(event.id) == "transfer_to_operator":
                try:
                    args = json.loads(arguments.get(event.id) or "{}")
                except json.JSONDecodeError:
                    args = {}
                if not isinstance(args, dict):
                    args = {}
                blocked = _should_block_false_transfer(args, history)
                if blocked is not None:
                    reason_label = str(args.get("reason", "unknown"))
                    transfer_promise_suppressed_total.labels(reason=reason_label).inc()
                    logger.warning(
                        "Withholding transfer promise — guard blocks reason=%s",
                        reason_label,
                    )
                for queued in resolve(drop_promise=blocked is not None):
                    yield queued
                continue

            continue

        yield event

    drop_unbacked = False
    if held is not None:
        # Still holding once the stream is exhausted: nothing behind the promise
        # ever called transfer_to_operator, so it was prose.
        drop_unbacked = any(
            isinstance(e, SentenceReady) and not is_transfer_promise(e.text)
            for e in held
        )
        transfer_promise_unbacked_total.labels(
            outcome="dropped" if drop_unbacked else "spoken"
        ).inc()
        logger.warning(
            "Transfer promise with no transfer_to_operator behind it — %s",
            "dropped, the rest of the turn stands"
            if drop_unbacked
            else "spoken, it is the whole turn",
        )
    for queued in resolve(drop_promise=drop_unbacked):
        yield queued


#: Which checklist row a sentence is asking the caller about.
#:
#: Matched against the whole checklist rather than the two rows seen failing, so
#: a row that starts regressing tomorrow is covered without another edit here.
_FIELD_QUESTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("name", re.compile(r"як (?:до вас |вас )?(?:звертатися|звати|називати)|ваше ім'я")),
    ("city", re.compile(r"[ву] якому місті|яке місто|[ву] якому городі")),
    ("storage", re.compile(r"привозите свої|на зберіганні|свої з собою|зі зберігання")),
    ("date", re.compile(r"на яку дату|на яке число|яка дата")),
    ("time", re.compile(r"о котрій|котрій годині|на який час|який час зручн")),
    ("color", re.compile(r"колір|кольор")),
    ("brand", re.compile(r"марк[аиуо]")),
    ("phone", re.compile(r"номер телефону|ваш номер|продиктуйте (?:телефон|номер)")),
)

#: Imperatives that make a sentence a request even without a question mark —
#: «Назвіть, будь ласка, колір автомобіля.» ends in a full stop.
_REQUEST_VERBS = ("назвіть", "скажіть", "уточніть", "продиктуйте", "підкажіть", "нагадайте")

#: A question asking for a *different* value than the settled one is not a
#: re-ask, so the gate keeps its hands off it. `get_fitting_slots` writes
#: `selected_fitting_date` on lookup, before the caller has picked anything
#: (`main.py:2940`), which means the date row reads ✅ at exactly the moment the
#: bot has to say «Записати на 9 жовтня не можу… На якій іншій даті зручніше?» —
#: a real sentence from 2026-09-11 that the pattern table would otherwise catch.
_ASKS_FOR_AN_ALTERNATIVE = re.compile(r"інш|перенос|перенес")

#: How many times the bot may be steered to the same question before the gate
#: gives up and lets the LLM speak. The replacement is only useful while the
#: caller can still answer it; `e4fa7fc1` shows the other case, where
#: `storage_choice_parser` returned not_mentioned on eight turns in a row, and
#: forcing that question forever would talk past the caller instead of the LLM.
_MAX_SAME_REDIRECTS = 2


def _sentence_is_a_request(text: str) -> bool:
    """True when the sentence asks the caller for something.

    Without this, «Отже, ви привозите шини свої з собою.» — the bot reading an
    answer back — matches the storage pattern and would be rewritten into a
    question the caller has already answered.
    """
    low = text.lower()
    return "?" in low or any(verb in low for verb in _REQUEST_VERBS)


def settled_field_asked(text: str, collected: dict[str, bool]) -> str | None:
    """The checklist row this sentence asks for, if that row is already filled.

    None covers both «asks for nothing» and «asks for something still missing»,
    because the gate treats them identically: the LLM speaks.
    """
    if not _sentence_is_a_request(text):
        return None
    low = text.lower().replace("ʼ", "'").replace("’", "'")
    if _ASKS_FOR_AN_ALTERNATIVE.search(low):
        return None
    for field_key, pattern in _FIELD_QUESTION_PATTERNS:
        if collected.get(field_key) and pattern.search(low):
            return field_key
    return None


def _assistant_texts(history: list[dict[str, Any]]) -> list[str]:
    """Every line the bot has spoken, flattened out of the content blocks."""
    spoken: list[str] = []
    for msg in history:
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content")
        if isinstance(content, str):
            spoken.append(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    spoken.append(str(block.get("text", "")))
    return spoken


#: Checklist rows that belong to a booking only. Name, city and phone are
#: collected for a price quote or a callback too, so asking them is no sign the
#: bot has started booking.
_BOOKING_ONLY_FIELDS = frozenset({"storage", "date", "time", "color", "brand"})


def booking_field_asked(text: str) -> str | None:
    """The booking-only checklist row this sentence asks for, if any."""
    if not _sentence_is_a_request(text):
        return None
    low = text.lower().replace("ʼ", "'").replace("’", "'")
    for field_key, pattern in _FIELD_QUESTION_PATTERNS:
        if field_key in _BOOKING_ONLY_FIELDS and pattern.search(low):
            return field_key
    return None


class BookingOfferGate:
    """One turn's worth of `offer_booking_before_checklist` state.

    Shared across the LLM rounds of a turn: the filter is rebuilt per round,
    and a round after a tool call must neither offer a second time nor resume
    the booking script the first round was stopped in.
    """

    __slots__ = ("mode", "offered")

    def __init__(self, mode: str | None) -> None:
        #: `GATE_OFFER`, `GATE_FAREWELL`, or None for off.
        self.mode = mode
        self.offered = False

    @property
    def replacement(self) -> str:
        return BOOKING_DECLINED_FAREWELL if self.mode == GATE_FAREWELL else BOOKING_OFFER


async def offer_booking_before_checklist(
    stream: AsyncIterator[BufferEvent],
    gate: BookingOfferGate,
) -> AsyncIterator[BufferEvent]:
    """Offer to book instead of starting a booking the caller never agreed to.

    `gate.mode` is decided by the pipeline (`booking_consent.gate_mode`): off
    once the caller has agreed; a farewell once they have said no to an offer
    (354ff3b5, 2026-09-25: «Ні дякую» → «Шини привозите свої…»); otherwise the
    offer, at most twice. On 2026-09-24 the quote ended
    «У вас легковий чи позашляховик?», the caller answered «позашляховик» and
    the bot went on to «Шини привозите свої з собою…».

    The first sentence asking a booking-only row becomes the replacement, and
    the rest of the turn is dropped: whatever followed was the booking script.
    Fragments are held until the sentence ends, as in
    `redirect_settled_question`, because «Назвіть,» alone asks nothing.
    """
    if gate.mode is None:
        async for event in stream:
            yield event
        return

    held: list[SentenceReady] = []
    pending = ""

    async for event in stream:
        if not isinstance(event, SentenceReady):
            if not gate.offered:
                for queued in held:
                    yield queued
                held, pending = [], ""
            yield event
            continue
        if gate.offered:
            continue

        held.append(event)
        pending = f"{pending} {event.text}".strip()
        field_key = booking_field_asked(pending)
        if field_key is not None:
            booking_offer_redirected_total.labels(field=field_key).inc()
            logger.warning(
                "Price consult: bot asked %s before the caller agreed to book — "
                "saying %r instead: %r",
                field_key,
                gate.replacement,
                pending[:120],
            )
            gate.offered = True
            held, pending = [], ""
            yield SentenceReady(text=gate.replacement)
            continue
        if pending.rstrip().endswith((".", "!", "?")):
            for queued in held:
                yield queued
            held, pending = [], ""

    if not gate.offered:
        for queued in held:
            yield queued


async def redirect_settled_question(
    stream: AsyncIterator[BufferEvent],
    progress: dict[str, Any] | None,
    history: list[dict[str, Any]],
) -> AsyncIterator[BufferEvent]:
    """Speak the step still waiting instead of one the caller already answered.

    On 2026-09-11 three of eight booking calls re-asked a field that was ✅ in
    the progress block at that very moment, seven times between them, and every
    single one fired on the turn right after the time slot was accepted:
    «15:20 прийнято. Назвіть, будь ласка, колір автомобіля.» The LLM walks the
    Krok numbers in order, so once Krok 4 lands it resumes at Krok 5 — whether
    or not Kroks 5 and 6 were filled out of order earlier in the call. Both
    existing defences are prompt text (the ✅/⏳ block itself, added for this bug
    in July, and the explicit ban in `prompts.py`), and both were in the context
    window for all seven.

    The unit of work is a sentence, not a fragment. `buffer_sentences` splits on
    clauses past 25 characters, which cuts «Назвіть,» away from «колір
    автомобіля.» — neither half is recognisable alone, so fragments are held
    until the sentence ends. On the 199 bot turns of that day this costs nothing
    at all for 109 of them and 143 ms at p90, because only the first sentence of
    a turn can delay audio: after that, generation is running far ahead of
    playback.

    The replacement is the first unanswered question, which is also what the
    prompt block points at, so the gate cannot steer somewhere the prompt
    disagrees with. When nothing is unanswered there is no replacement to make
    and the LLM speaks — that is deliberate, and it is what keeps a caller
    correcting a booked-up checklist («ні, на іншу годину») from being talked
    over.
    """
    progress = progress or {}
    collected = fitting_steps_collected(progress)
    replacement = next_fitting_question(progress) or fitting_confirmation_sentence(progress)
    if not replacement or bool(progress.get("booked")):
        async for event in stream:
            yield event
        return

    already_spoken = sum(1 for text in _assistant_texts(history) if replacement in text)
    held: list[SentenceReady] = []
    pending = ""
    # Set once the verdict for the current sentence is in: the rest of it is
    # either spoken as the LLM wrote it or dropped, but never re-judged, so a
    # trailing «будь ласка, ще раз» cannot survive its own question.
    tail: str | None = None
    redirected = False

    def ends_sentence(text: str) -> bool:
        return text.rstrip().endswith((".", "!", "?"))

    async for event in stream:
        if not isinstance(event, SentenceReady):
            for queued in held:
                yield queued
            held, pending, tail = [], "", None
            yield event
            continue

        if tail is not None:
            if tail == "speak":
                yield event
            if ends_sentence(event.text):
                tail = None
            continue

        held.append(event)
        pending = f"{pending} {event.text}".strip()
        field_key = settled_field_asked(pending, collected)

        if field_key is None:
            if ends_sentence(pending):
                for queued in held:
                    yield queued
                held, pending = [], ""
            continue

        if already_spoken >= _MAX_SAME_REDIRECTS:
            settled_question_redirect_skipped_total.labels(reason="repeat").inc()
            logger.warning(
                "Not redirecting %s — already steered to %r %d times",
                field_key,
                replacement,
                already_spoken,
            )
            for queued in held:
                yield queued
            tail = None if ends_sentence(pending) else "speak"
        else:
            settled_question_redirected_total.labels(field=field_key).inc()
            logger.warning(
                "Redirecting a settled question: asked %s, speaking %r instead",
                field_key,
                replacement,
            )
            if not redirected:
                redirected = True
                already_spoken += 1
                yield SentenceReady(text=replacement)
            tail = None if ends_sentence(pending) else "drop"
        held, pending = [], ""

    for queued in held:
        yield queued


def _last_user_text(history: list[dict[str, Any]]) -> str:
    """The caller's most recent words, flattened out of the content blocks."""
    for msg in reversed(history):
        if msg.get("role") != "user":
            continue
        content = msg.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = [
                str(block.get("text", ""))
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            ]
            if any(parts):
                return " ".join(parts)
    return ""


async def confirm_settled_time(
    stream: AsyncIterator[BufferEvent],
    offered_slots: list[dict[str, str]] | None,
    history: list[dict[str, Any]],
) -> AsyncIterator[BufferEvent]:
    """Accept the slot the caller named instead of asking for it again.

    Fires only when the caller's own last turn picked a slot off the offered
    list. The membership test is the whole safety argument: a time the bot never
    offered can never arm the gate. The 12-hour reading still applies inside it,
    so «5:00» matches an offered 17:00 (call `74c61ff8`).

    A bare hour arms a second, narrower reading. `hour_only_allowed` reproduces
    the widening the Wave-14 pin in `pipeline.py` already applied, so «на 12»
    against a 40-minute grid resolves to the 12:20 the machine is holding. That
    reading is *not* trusted against the three triggers above — «На 9:00 чи
    9:40?» is the bot doing its job when both are real — only against a sentence
    that contradicts the catalogue outright. Call `e31ae29f` (2026-09-14): no
    12:00 and no 12:30 anywhere on the list, yet the bot asked «дванадцята рівно
    чи дванадцята тридцять?» and then accepted «дванадцята рівно» — twenty
    minutes from the 12:20 it went on to book. Naming a minute of the caller's
    own hour that does not exist is a contradiction no judgement is needed to
    see; a denial is excluded because «На 10:00 слоту немає» names one for
    exactly that reason.

    Replayed over the 2484 bot turns — 4775 sentences — of the 189 calls that
    reached `get_fitting_slots` in 45 days: twelve firing turns across twelve
    calls, no other sentence touched. Ten of those are the exact-time arm and
    were already firing before the bare hour was let in; the two the widening
    adds are `e31ae29f` and `23a189af`, and nothing else in the corpus changed.
    An exact pick arms the gate on 130 bot turns and the widened read on 34 more,
    so the triggers do most of their work by *not* firing: 253 further sentences
    match one of the three on turns armed by neither — the bot legitimately
    asking which hour, declining a time it had not offered, or reading the list
    out for the first time — and the pick precondition is what keeps the gate
    off every one of them.

    Two of the twelve are anti-patterns `prompts.py` already names by call id
    (lines 588 and 593). They were in the context window when they fired, which
    is the argument for spending a gate here rather than a seventh prompt line.
    Three calls fire twice, and they are why the gate drops *every* match in the
    turn rather than only the first: `add8354b`, `74c61ff8` and `9c82ce3d` each
    denied the slot in one sentence and re-read the list in the next, so a gate
    that stopped after the denial would put the acceptance and the contradiction
    into the same breath.
    """
    offered_times = [s["time"] for s in (offered_slots or []) if s.get("time")]
    said_by_caller = _last_user_text(history)
    picked = (
        detect_time_choice(said_by_caller, offered_times, allow_hour_only=False)
        if offered_times
        else None
    )
    spoken = _assistant_texts(history)
    widened = (
        detect_time_choice(said_by_caller, offered_times, allow_hour_only=True)
        if offered_times and hour_only_allowed(spoken[-1] if spoken else "")
        else None
    )
    settled = picked or widened
    if not settled:
        async for event in stream:
            yield event
        return

    def reopens(sentence: str) -> bool:
        if picked and (
            asks_which_time(sentence)
            or denies_a_slot(sentence)
            or lists_alternative_times(sentence)
        ):
            return True
        return reslices_the_pinned_hour(sentence, settled, offered_times)

    replacement = f"Добре, {settled} прийнято."
    held: list[SentenceReady] = []
    pending = ""
    tail: str | None = None
    fired = False

    def ends_sentence(text: str) -> bool:
        return text.rstrip().endswith((".", "!", "?"))

    async for event in stream:
        if not isinstance(event, SentenceReady):
            for queued in held:
                yield queued
            held, pending, tail = [], "", None
            yield event
            continue

        if tail is not None:
            if tail == "speak":
                yield event
            if ends_sentence(event.text):
                tail = None
            continue

        held.append(event)
        pending = f"{pending} {event.text}".strip()

        if not reopens(pending):
            if ends_sentence(pending):
                for queued in held:
                    yield queued
                held, pending = [], ""
            continue

        # Every re-opening in the turn is dropped, but the acceptance is spoken
        # once. A turn that argues with itself twice — `add8354b` denied the
        # slot and then re-read the whole list — would otherwise have the second
        # half reach the caller behind an acceptance of the first.
        reopened_time_choice_total.inc()
        logger.warning(
            "Caller already picked %s off the offered list — speaking %r "
            "instead of re-opening the choice with %r",
            settled,
            replacement if not fired else "nothing",
            pending[:120],
        )
        if not fired:
            fired = True
            yield SentenceReady(text=replacement)
        tail = None if ends_sentence(pending) else "drop"
        held, pending = [], ""

    for queued in held:
        yield queued


#: Canonical tool names, read off the registry rather than copied out, so that
#: tool 21 is covered on the day it is added and not on the day someone
#: remembers this list exists.
_TOOL_NAME_ALTERNATION = "|".join(
    sorted((str(t["name"]) for t in ALL_TOOLS), key=len, reverse=True)
)

#: The shapes a model produces when it writes *about* the machinery instead of
#: talking to the caller, each paired with the label the metric reports.
#:
#: Described by form, not by inventory. The six calls behind this filter include
#: `[!IMPORTANT]` (call fa2a523d) — a GitHub-alert marker that occurs nowhere in
#: `prompts.py` and nowhere else in the repository, so the model did not copy it,
#: it invented it. A table assembled by listing the prompt's own markup would
#: therefore have missed the very call that motivated the rule. What is matched
#: here is machinery *syntax*: an identifier carrying an argument list, a JSON
#: object, an admonition marker, a bracketed aside, a fence, a tag. None of it
#: occurs in Ukrainian speech, which is what makes refusing all of it cheap.
#:
#: Ordered most specific first — the first match names the shape in the metric.
_CONTROL_PLANE_FORMS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # `functions.get_customer_bookings({"phone":…` — a namespaced call. Both
    # halves must be Latin, so «м. Харків (центр)» is not a call.
    (
        "namespaced_call",
        re.compile(r"[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*\s*\(", re.IGNORECASE),
    ),
    # `get_fitting_stations(city="Дніпро"…` — snake_case identifier + arguments.
    # The underscore is load-bearing: without it «білий Hyundai (седан)» matches.
    ("call_syntax", re.compile(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)+\s*\(", re.IGNORECASE)),
    # `{"reason":"non_fitting_scope"…}` — a JSON literal. Over 2669 bot turns in
    # 30 days a brace appears twice, and both times it is this defect.
    ("json_args", re.compile(r"[{}]")),
    # `[!IMPORTANT]` — an admonition marker. Carries no whitespace, so the
    # bracketed-aside rule below cannot see it and it needs its own line.
    ("admonition", re.compile(r"\[\s*!")),
    # `[book_fitting виклик…]`, `[Профіль оновлено: …]` — a bracketed aside,
    # multi-word by construction. `[PHONE_1]`, the PII vault's own placeholder,
    # has no whitespace inside and is deliberately left alone: it reaches TTS on
    # real booking confirmations (calls 57494646, 159e7b49) and refusing those
    # would trade a mispronounced word for a caller who is told nothing.
    ("bracket_aside", re.compile(r"\[[^\]]*\s")),
    # A tool name read out as a bare word, with no argument list to give it away
    # — `[Інструмент book_fitting успішно виконав бронювання]` survives having
    # its brackets stripped by a future edit only if this line exists.
    ("tool_name", re.compile(rf"\b(?:{_TOOL_NAME_ALTERNATION})\b")),
    # `(Крок 8)` — the prompt's step numbering, read aloud. Parenthesised *and*
    # numbered, so «наступний крок — підтвердження» stays speech.
    ("step_marker", re.compile(r"\(\s*крок\s*\d", re.IGNORECASE)),
    # ``` and `<tool_use>` — the other two ways a model writes machinery down.
    # `tool_code` pseudo-Python has already been spoken to a caller once
    # (call 43a4b637, recorded in `pii_vault.py`).
    ("markup_fence", re.compile(r"```|~~~|</?[a-z][a-z0-9_:-]*\s*/?>", re.IGNORECASE)),
)


def _current_call_id() -> str:
    """The call this turn belongs to, for the log line.

    Taken from the context variable `main.py` already sets from
    `conn.channel_uuid` before the pipeline starts, rather than threaded through
    `StreamingAgentLoop.__init__`: that constructor lives in `main.py`, and the
    log line is not worth a new argument there.
    """
    from src.llm.router import llm_call_id_var

    return str(llm_call_id_var.get(None) or "unknown")


def control_plane_syntax(text: str) -> str | None:
    """Name the machinery shape this text carries, or None when it is speech.

    None means «no shape matched», which is the only way through: the rules are
    forms rather than an allowlist of permitted phrasings, so a seventh shape is
    refused the moment it looks like syntax and not like Ukrainian.
    """
    for label, pattern in _CONTROL_PLANE_FORMS:
        if pattern.search(text):
            return label
    return None


async def drop_control_plane_prose(
    stream: AsyncIterator[BufferEvent],
    call_id: str = "unknown",
) -> AsyncIterator[BufferEvent]:
    """Refuse to speak a sentence that turned out to be machinery.

    In 30 days the model wrote a tool call, or a note to itself about one, into
    the text of a reply eleven times across ten calls, and every one of them was
    synthesised and played down the line. Nothing else catches this: the guards
    all sit on the tool *execution* path, so prose reaches neither them nor
    `call_tool_calls`, and on d343c327 the caller heard
    `functions.transfer_to_operator ({"reason":…})` while no transfer happened
    at all. On e209d7fb the JSON that was read out contained the caller's own
    phone number.

    The unit of work is a whole sentence, not a fragment, because the shape is
    routinely cut in half: `buffer_sentences` splits on clauses past 25
    characters, and `get_fitting_stations(city="Дніпро", for_price=true) Який
    діаметр…` breaks at the comma *inside the argument list*, leaving
    `for_price=true)` glued to a perfectly good question. Judging fragments
    would drop the half that reads as syntax and speak the half that reads as
    Ukrainian, which is the worst of both. Fragments are therefore held until
    punctuation ends the sentence, or until a tool call or the end of the stream
    says no more of it is coming.

    Holding costs nothing on the common path: `redirect_settled_question`
    downstream already withholds fragments to the same boundary, and measured
    143 ms at p90 when it is the one doing the holding, since only the first
    sentence of a turn can delay audio at all.

    The whole sentence is dropped rather than cleaned up. Excising the syntax
    and speaking the remainder is tempting — four of the six calls leave a real
    question behind — but a call written as prose did not run, so anything in
    the same breath about its result is unbacked, and `146788f4` is exactly
    that: «Інструмент book_fitting успішно виконав бронювання» over a
    `book_fitting` that had returned an error. A turn emptied by this filter is
    not silence either; `pipeline.py` answers an empty turn with «Перепрошую, не
    почула. Скажіть, будь ласка, ще раз.»
    """
    held: list[BufferEvent] = []
    pending = ""

    def settle() -> list[BufferEvent]:
        nonlocal held, pending
        queued, text = held, pending
        held, pending = [], ""
        if not text:
            return queued
        form = control_plane_syntax(text)
        if form is None:
            return queued
        control_plane_prose_dropped_total.labels(form=form, site="stream").inc()
        logger.warning(
            "Dropping machinery written as speech: call=%s, form=%s, text=%r",
            call_id,
            form,
            text[:200],
        )
        return []

    async for event in stream:
        if isinstance(event, SentenceReady):
            held.append(event)
            pending = f"{pending} {event.text}".strip()
            if pending.rstrip().endswith((".", "!", "?")):
                for queued in settle():
                    yield queued
            continue

        # A tool call or the end of the stream ends the sentence whether or not
        # punctuation did: `buffer_sentences` flushes partial text ahead of
        # both, so nothing more will arrive to complete it. Judging here rather
        # than flushing blind is what stops half a call syntax from slipping out
        # under an unterminated fragment.
        for queued in settle():
            yield queued
        yield event

    for queued in settle():
        yield queued


@dataclass(frozen=True)
class TurnResult:
    """Result of one complete conversation turn (possibly multi-round)."""

    spoken_text: str
    tool_calls_made: int
    stop_reason: str
    total_usage: Usage
    provider_key: str = ""
    interrupted: bool = False
    disconnected: bool = False
    wait_phrase: str = ""


class StreamingAgentLoop:
    """Streaming agent loop — LLM ↔ tool execution with real-time audio.

    Each LLM invocation streams through Layers 2→3→4 (sentence buffer →
    TTS → audio sender). If the result contains tool_calls, execute them
    and loop back to the LLM with results.
    """

    def __init__(
        self,
        llm_router: LLMRouter,
        tool_router: ToolRouter,
        tts: TTSEngine,
        conn: AudioSocketConnection,
        barge_in_event: asyncio.Event,
        *,
        tools: list[dict[str, Any]] | None = None,
        system_prompt: str | None = None,
        pii_vault: PIIVault | None = None,
        provider_override: str | None = None,
        max_tool_rounds: int = MAX_TOOL_CALLS_PER_TURN,
        few_shot_context: str | None = None,
        safety_context: str | None = None,
        promotions_context: str | None = None,
        is_modular: bool = False,
        agent_name: str | None = None,
        echo_canceller: EchoCanceller | None = None,
    ) -> None:
        self._llm_router = llm_router
        self._tool_router = tool_router
        self._tts_initial = tts
        self._conn = conn
        self._barge_in = barge_in_event
        self._tools = tools
        self._system_prompt = system_prompt or SYSTEM_PROMPT
        self._pii_vault = pii_vault
        self._provider_override = provider_override
        self._max_tool_rounds = max_tool_rounds
        self._few_shot_context = few_shot_context
        self._safety_context = safety_context
        self._promotions_context = promotions_context
        self._is_modular = is_modular
        self._agent_name = agent_name
        self._echo_canceller = echo_canceller
        # Both counters start at a random offset rather than 0. Rotation alone
        # varies the phrases *within* a call, but with a fixed start every call
        # replayed the same cycle from the same first phrase — a caller who
        # phones twice hears a metronome. The offset is per-call, so the
        # rotation guarantees the tests pin still hold inside one call.
        self._thinking_counter = random.randrange(len(WAIT_THINKING_POOL))
        # Rotates the tool wait phrase; paired with _last_thinking_filler so the
        # two filler sources never open with the same word back-to-back.
        self._tool_wait_counter = random.randrange(_MAX_TOOL_WAIT_POOL_LEN)
        self._last_thinking_filler = ""

    @property
    def _tts(self) -> TTSEngine:
        """Return the current global TTS engine (picks up hot-reloaded config)."""
        from src.tts import get_engine

        return get_engine() or self._tts_initial

    def _get_fallback_provider(self) -> str | None:
        """Return the first fallback provider key for the agent task, or None."""
        try:
            chain = self._llm_router._resolve_chain(LLMTask.AGENT, None)
            # chain[0] is primary, chain[1:] are fallbacks
            if len(chain) > 1:
                return chain[1]
        except Exception:
            logger.debug("Failed to resolve fallback provider chain", exc_info=True)
        return None

    async def _request_summary_fallback(
        self,
        system: str,
        conversation_history: list[dict[str, Any]],
    ) -> str:
        """Ask LLM to summarize tool results when max tool rounds exhausted.

        Returns a short customer-facing summary or a static fallback.
        """
        _summary_timeout_sec = 5
        _summary_prompt = (
            "Ти вичерпав ліміт викликів інструментів. "
            "Підсумуй для клієнта те, що вдалося дізнатися, "
            "в 1-2 реченнях українською. Не використовуй інструменти."
        )
        _fallback_text = (
            "Перепрошую, мені потрібно трохи більше часу. "
            "Спробуйте, будь ласка, уточнити ваше питання."
        )

        summary_history = [*conversation_history, {"role": "user", "content": _summary_prompt}]

        try:
            llm_resp = await asyncio.wait_for(
                self._llm_router.complete(
                    LLMTask.AGENT,
                    summary_history,
                    system=system,
                    tools=[],
                    max_tokens=256,
                ),
                timeout=_summary_timeout_sec,
            )
            if llm_resp.text and llm_resp.text.strip():
                summary = llm_resp.text.strip()
                # The second road from LLM text to the speaker. This summary is
                # synthesised directly (`tts.synthesize(summary)` below in
                # `run_turn`), so the four-filter chain never sees it — and it
                # lands in `call_turns` all the same. Asking with `tools=[]`
                # makes call syntax unlikely but rules out none of the markup
                # shapes, and the shape that opened this wave was one the model
                # invented rather than copied. Judged as one block rather than
                # per sentence, because there is no sentence buffer on this
                # road; the price of that coarseness is the static fallback,
                # which is a sentence the caller can act on.
                form = control_plane_syntax(summary)
                if form is not None:
                    control_plane_prose_dropped_total.labels(
                        form=form, site="summary_fallback"
                    ).inc()
                    logger.warning(
                        "Summary fallback carried machinery (form=%s) — speaking the "
                        "static fallback instead: %r",
                        form,
                        summary[:200],
                    )
                    return _fallback_text
                logger.info("Streaming summary fallback produced text")
                return summary
        except TimeoutError:
            logger.warning("Streaming summary fallback timed out (%ds)", _summary_timeout_sec)
        except Exception:
            logger.warning("Streaming summary fallback failed", exc_info=True)

        return _fallback_text

    def _next_thinking_filler(self) -> str:
        """Pick this round's thinking filler and advance the rotation.

        Records the pick in `_last_thinking_filler` so the tool wait phrase
        chosen later in the same round can avoid echoing its opening word.
        """
        phrase = WAIT_THINKING_POOL[self._thinking_counter % len(WAIT_THINKING_POOL)]
        self._thinking_counter += 1
        self._last_thinking_filler = phrase
        return phrase

    def _next_tool_wait_phrase(self, tool_names: list[str]) -> str:
        """Pick this round's wait phrase and advance the rotation.

        Selection and advance live together so a caller cannot pick without
        advancing — that would put the caller back on repeat-the-same-filler.
        """
        phrase = _pick_tool_wait_phrase(
            tool_names,
            index=self._tool_wait_counter,
            avoid=self._last_thinking_filler,
        )
        self._tool_wait_counter += 1
        return phrase

    async def run_turn(
        self,
        user_text: str,
        conversation_history: list[dict[str, Any]],
        caller_phone: str | None = None,
        order_id: str | None = None,
        pattern_context: str | None = None,
        order_stage: str | None = None,
        caller_history: str | None = None,
        storage_context: str | None = None,
        customer_profile: str | None = None,
        fitting_booked: bool = False,
        tools_called: set[str] | None = None,
        scenario: str | None = None,
        active_scenarios: set[str] | None = None,
        selected_station: dict[str, Any] | None = None,
        selected_slot: dict[str, str] | None = None,
        offered_slots: list[dict[str, str]] | None = None,
        fitting_progress: dict[str, Any] | None = None,
        booking_gate_mode: str | None = None,
    ) -> TurnResult:
        """Run a full conversation turn with streaming audio output.

        May loop multiple times if the LLM returns tool calls.
        Mutates conversation_history in place.
        """
        booking_offer_gate = BookingOfferGate(booking_gate_mode)

        # Mask PII before sending to LLM
        if self._pii_vault is not None:
            user_text = self._pii_vault.mask(user_text)

        # Add user message
        conversation_history.append({"role": "user", "content": user_text})

        # Compress/summarize old messages to save tokens (BEFORE trim so
        # early context like customer name / topic is captured in the summary).
        # Tuned 2026-08-14: summary_threshold=9 + keep_recent=7 (was 10/10)
        # to cut ~1-2k tok on fitting calls that hit 10+ turns (call 98ee0296
        # had 34 turns / 28k input tokens). The last 7 messages still cover
        # the current Krok context, so the state machine stays coherent.
        pre_len = len(conversation_history)
        conversation_history[:] = summarize_old_messages(
            conversation_history,
            summary_threshold=9,
            keep_recent=7,
        )
        post_len = len(conversation_history)

        # Record compression mode metric
        if post_len < pre_len and post_len > 0 and conversation_history[0].get("content", "").startswith("(Резюме"):
            history_compression_mode.labels(mode="summarize").inc()
        elif post_len < pre_len:
            history_compression_mode.labels(mode="compress").inc()
        else:
            history_compression_mode.labels(mode="none").inc()

        # Safety-net trim: if history is still too long after summarization
        if len(conversation_history) > MAX_HISTORY_MESSAGES:
            conversation_history[:] = (
                conversation_history[:1] + conversation_history[-(MAX_HISTORY_MESSAGES - 1) :]
            )

        # Build system prompt with caller context (mask caller phone)
        masked_phone = caller_phone
        if self._pii_vault is not None and caller_phone:
            masked_phone = self._pii_vault.mask(caller_phone)
        system = build_system_prompt_with_context(
            self._system_prompt,
            is_modular=self._is_modular,
            order_stage=order_stage,
            safety_context=self._safety_context,
            few_shot_context=self._few_shot_context,
            promotions_context=self._promotions_context,
            caller_phone=masked_phone,
            order_id=order_id,
            pattern_context=pattern_context,
            agent_name=self._agent_name,
            customer_profile=customer_profile,
            caller_history=caller_history,
            storage_context=storage_context,
            tools_called=tools_called,
            scenario=scenario,
            active_scenarios=active_scenarios,
            selected_station=selected_station,
            selected_slot=selected_slot,
            offered_slots=offered_slots,
            fitting_progress=fitting_progress,
            enabled_tools={t["name"] for t in (self._tools or [])},
        )

        # Record prompt and history metrics
        system_prompt_chars.observe(len(system))
        history_messages_count.observe(len(conversation_history))

        # Filter tools by conversation state (remove irrelevant tools)
        tools = filter_tools_by_state(
            self._tools or [], order_stage=order_stage, fitting_booked=fitting_booked
        )

        spoken_parts: list[str] = []
        has_llm_text = False  # True when LLM produced real text (not just wait-phrases)
        wait_phrase_spoken = ""  # Tracked separately from spoken_parts for quality scoring
        total_input_tokens = 0
        total_output_tokens = 0
        total_cached_input_tokens = 0
        tool_calls_made = 0
        stop_reason = "end_turn"
        provider_key = ""
        interrupted = False
        disconnected = False
        empty_retries = 0
        current_provider_override = self._provider_override

        turn_start = time.monotonic()

        # (tool, canonical args) pairs a guard has already refused this turn.
        # A guard verdict is a pure function of session state, so re-asking the
        # identical question inside one turn cannot get a different answer — and
        # the model does re-ask: over 21 days 47 of the refusals in 18 calls were
        # verbatim repeats, 29 of them `past_krok_2`. Repeats are what made that
        # one guard pathological (3.7 refusals per call against ~1.0 for every
        # other guard, which the model obeys first time). Five rounds of the same
        # refusal leave the turn with nothing to say, so the caller hears the
        # exhaustion fallback — «На жаль, я вичерпала…» in `8e5fe347`, «Здається,
        # виникла невелика технічна…» in `fb854e33` — instead of an answer.
        # Only `error is True` counts. A transient failure is a string («Сервіс
        # тимчасово не відповідає»), and retrying one of those is legitimate.
        refused_this_turn: dict[str, str] = {}
        ended_on_refusal_loop = False

        tool_round = 0
        while tool_round < self._max_tool_rounds:
            # Stream LLM → sentence buffer → TTS → audio sender
            try:
                stream = self._llm_router.complete_stream(
                    LLMTask.AGENT,
                    conversation_history,
                    system=system,
                    tools=tools,
                    max_tokens=1024,
                    provider_override=current_provider_override,
                )
                # `drop_control_plane_prose` sits innermost, directly on the
                # sentence buffer, for two reasons. The other two filters reason
                # about what the bot is *saying* to the caller, and a sentence
                # that is really a tool call makes them reason about nothing:
                # «[!IMPORTANT] Клієнт назвав марку авто» reads to
                # `settled_field_asked` as a question about the car brand. And
                # production names only the first filter that fires, so a
                # machinery sentence swallowed by a neighbour would leave this
                # defect invisible in exactly the way it stayed invisible for
                # 30 days.
                # `confirm_settled_time` sits inside `redirect_settled_question`
                # so that it judges the LLM's own words and never a replacement
                # the checklist filter substituted — `next_fitting_question` can
                # itself return «О котрій зручніше?», and a gate that rewrote
                # that would be answering its neighbour rather than the caller.
                # The reverse order is safe in the other direction: this gate's
                # replacement is an acceptance, not a request, so
                # `settled_field_asked` never looks at it.
                # `offer_booking_before_checklist` sits outside the checklist
                # redirect: a question the redirect substituted is still a
                # booking question, and still not the caller's to answer yet.
                buffered = hold_unconfirmed_transfer_promise(
                    offer_booking_before_checklist(
                        redirect_settled_question(
                            confirm_settled_time(
                                drop_control_plane_prose(
                                    buffer_sentences(stream), _current_call_id()
                                ),
                                offered_slots,
                                conversation_history,
                            ),
                            fitting_progress,
                            conversation_history,
                        ),
                        booking_offer_gate,
                    ),
                    conversation_history,
                )
                tts_stream = synthesize_stream(buffered, self._tts)

                # Pre-synthesize filler audio (usually cache-hit; capped by
                # _FILLER_PRESYNTH_TIMEOUT_SEC so a hung TTS API never blocks
                # the whole turn — we just skip filler for this round).
                filler_audio: bytes | None = None
                if not self._conn.is_closed:
                    phrase = self._next_thinking_filler()
                    try:
                        filler_audio = await asyncio.wait_for(
                            self._tts.synthesize(phrase),
                            timeout=_FILLER_PRESYNTH_TIMEOUT_SEC,
                        )
                        logger.debug(
                            "Pre-synthesized thinking filler: %r (%d bytes)",
                            phrase,
                            len(filler_audio),
                        )
                    except TimeoutError:
                        logger.warning(
                            "Pre-synthesize filler timed out after %.1fs — "
                            "skipping filler for this turn",
                            _FILLER_PRESYNTH_TIMEOUT_SEC,
                        )
                    except Exception:
                        logger.debug("Failed to pre-synthesize filler", exc_info=True)

                result = await send_audio_stream(
                    tts_stream,
                    self._conn,
                    self._barge_in,
                    turn_start_time=turn_start,
                    echo_canceller=self._echo_canceller,
                    filler_audio=filler_audio,
                    filler_delay_sec=_FILLER_DELAY_SEC,
                )
            except Exception:
                logger.exception("LLM streaming error in round %d", tool_round)
                return TurnResult(
                    spoken_text=" ".join(spoken_parts),
                    tool_calls_made=tool_calls_made,
                    stop_reason="error",
                    total_usage=Usage(
                total_input_tokens, total_output_tokens, total_cached_input_tokens
            ),
                    interrupted=False,
                    disconnected=False,
                )

            # Accumulate spoken text
            if result.spoken_text:
                spoken_parts.append(result.spoken_text)
                has_llm_text = True

            # Accumulate usage
            total_input_tokens += result.usage.input_tokens
            total_output_tokens += result.usage.output_tokens
            total_cached_input_tokens += result.usage.cached_input_tokens
            stop_reason = result.stop_reason
            provider_key = result.provider_key or provider_key

            # Build assistant content blocks for history
            assistant_content: list[dict[str, Any]] = []
            if result.spoken_text:
                assistant_content.append({"type": "text", "text": result.spoken_text})
            for tc in result.tool_calls:
                try:
                    args = json.loads(tc.arguments_json) if tc.arguments_json else {}
                except json.JSONDecodeError:
                    args = {}
                assistant_content.append(
                    {
                        "type": "tool_use",
                        "id": tc.id,
                        "name": tc.name,
                        "input": args,
                    }
                )

            if assistant_content:
                conversation_history.append({"role": "assistant", "content": assistant_content})

            # Check interruption/disconnection — remove dangling tool_calls from history
            if result.interrupted:
                interrupted = True
                if result.tool_calls and assistant_content:
                    # Remove the assistant message with tool_calls — no tool results will follow.
                    # Without this, OpenAI API returns 400: "tool_calls must be followed by tool messages"
                    conversation_history.pop()
                break
            if result.disconnected:
                disconnected = True
                if result.tool_calls and assistant_content:
                    conversation_history.pop()
                break

            # No tool calls → done (or retry if empty)
            if not result.tool_calls:
                if (
                    not result.spoken_text
                    and not has_llm_text
                    and empty_retries < _MAX_EMPTY_RETRIES
                ):
                    empty_retries += 1
                    # On last retry, switch to fallback provider
                    if empty_retries == _MAX_EMPTY_RETRIES and self._provider_override is None:
                        fallback = self._get_fallback_provider()
                        if fallback:
                            current_provider_override = fallback
                            logger.warning(
                                "Empty LLM response (retry %d/%d), "
                                "stop=%s, out_tokens=%d — switching to fallback %s",
                                empty_retries,
                                _MAX_EMPTY_RETRIES,
                                result.stop_reason,
                                result.usage.output_tokens,
                                fallback,
                            )
                            continue
                    logger.warning(
                        "Empty LLM response (retry %d/%d), "
                        "stop=%s, out_tokens=%d — retrying same provider",
                        empty_retries,
                        _MAX_EMPTY_RETRIES,
                        result.stop_reason,
                        result.usage.output_tokens,
                    )
                    continue
                break

            # Deduplicate tool calls (same name + same args → skip)
            seen_keys: set[str] = set()
            unique_tool_calls: list[Any] = []
            for tc in result.tool_calls:
                try:
                    args_parsed = json.loads(tc.arguments_json) if tc.arguments_json else {}
                except json.JSONDecodeError:
                    args_parsed = {}
                dedup_key = tc.name + ":" + json.dumps(args_parsed, sort_keys=True)
                if dedup_key in seen_keys:
                    logger.warning(
                        "Skipping duplicate tool call: %s(%s)",
                        tc.name,
                        json.dumps(args_parsed, ensure_ascii=False)[:200],
                    )
                    continue
                seen_keys.add(dedup_key)
                unique_tool_calls.append(tc)

            suppressed_ids: set[str] = set()

            # Execute tool calls in parallel (with per-tool timeout).
            # If LLM produced no text before the tool call, speak a contextual
            # wait-phrase in parallel so the caller doesn't hear silence.
            async def _execute_one_tool(
                tc: Any, _suppressed: set[str] = suppressed_ids
            ) -> dict[str, Any]:
                try:
                    args = json.loads(tc.arguments_json) if tc.arguments_json else {}
                except json.JSONDecodeError:
                    args = {}
                if self._pii_vault is not None:
                    args = self._pii_vault.restore_in_args(args)
                # Guard against hallucinated transfer_to_operator on early turns
                if tc.name == "transfer_to_operator":
                    block_msg = _should_block_false_transfer(args, conversation_history)
                    if block_msg is not None:
                        reason_label = str(args.get("reason", "unknown"))
                        false_transfer_blocked_total.labels(reason=reason_label).inc()
                        logger.warning(
                            "Blocked hallucinated transfer_to_operator "
                            "(reason=%s, user_turns=%d): %r",
                            reason_label,
                            len(_extract_user_text_turns(conversation_history)),
                            args.get("summary", "")[:120],
                        )
                        return {
                            "type": "tool_result",
                            "tool_use_id": tc.id,
                            "content": block_msg,
                        }
                refusal_key = tc.name + ":" + json.dumps(args, sort_keys=True)
                already_refused = refused_this_turn.get(refusal_key)
                if already_refused is not None:
                    guard_refusal_repeated_total.labels(
                        tool_name=tc.name, reason=already_refused
                    ).inc()
                    logger.warning(
                        "Tool %s already refused this turn (reason=%s) — not run again",
                        tc.name,
                        already_refused,
                    )
                    _suppressed.add(tc.id)
                    return {
                        "type": "tool_result",
                        "tool_use_id": tc.id,
                        "content": (
                            f"⛔ Ти вже викликав `{tc.name}` з тими самими аргументами "
                            "у цьому ході, і сервер відмовив. Відповідь не зміниться — "
                            "не викликай його знову. Або виконай ту дію, яку сервер "
                            "назвав у попередній відмові, або скажи клієнту словами, "
                            "що відбувається."
                        ),
                    }
                try:
                    raw = await asyncio.wait_for(
                        self._tool_router.execute(tc.name, args),
                        timeout=_TOOL_TIMEOUT_SEC,
                    )
                except TimeoutError:
                    logger.error("Tool %s timed out after %ds", tc.name, _TOOL_TIMEOUT_SEC)
                    tool_call_errors_total.labels(tool_name=tc.name, error_type="timeout").inc()
                    raw = {"error": "Сервіс тимчасово не відповідає, спробуйте ще раз"}
                if isinstance(raw, dict) and raw.get("error") is True:
                    refused_this_turn[refusal_key] = str(
                        raw.get("reason") or raw.get("action_required") or "unspecified"
                    )
                content = compress_tool_result(tc.name, raw)
                if self._pii_vault is not None:
                    content = self._pii_vault.mask(content)
                return {"type": "tool_result", "tool_use_id": tc.id, "content": content}

            # Speak wait-phrase during tool execution — unless the caller is
            # still hearing the thinking filler from this same round, in which
            # case the two run together as one babbling stretch. That was the
            # common case, not the rare one: measured on prod 2026-09-16, the
            # filler fired on 39 of ~40 rounds and the wait phrase added 19 more
            # utterances on top, 58 in four calls.
            #
            # The silence this gives up is small. Tool execution itself is
            # 2-150ms at p50 for every tool except book_fitting (~1s), and the
            # gap that follows belongs to the next LLM round, which opens with a
            # filler of its own after _FILLER_DELAY_SEC. Packet-level silence is
            # covered by pipeline's _keepalive_loop regardless.
            filler_still_ringing = _filler_still_ringing(result.filler_finished_at)
            if filler_still_ringing:
                logger.info(
                    "Suppressing wait-phrase — thinking filler %r ended %.1fs ago",
                    self._last_thinking_filler,
                    time.monotonic() - (result.filler_finished_at or 0.0),
                )
            need_wait_phrase = not interrupted and not disconnected and not filler_still_ringing
            if need_wait_phrase and not self._conn.is_closed:
                tool_names = [tc.name for tc in unique_tool_calls]
                wait_phrase = self._next_tool_wait_phrase(tool_names)
                logger.info("Speaking wait-phrase during tool exec: %r", wait_phrase)

                async def _speak_wait(_phrase: str = wait_phrase) -> None:
                    try:
                        tts = self._tts
                        audio = await tts.synthesize(_phrase)
                        if not (self._barge_in and self._barge_in.is_set()):
                            if self._echo_canceller is not None:
                                self._echo_canceller.record_far_end(audio)
                            await self._conn.send_audio(audio, cancel_event=self._barge_in)
                    except Exception:
                        logger.debug("Wait-phrase speak failed", exc_info=True)

                # Run wait-phrase and tool execution concurrently
                wait_task = asyncio.create_task(_speak_wait())
                tool_results = list(
                    await asyncio.gather(*[_execute_one_tool(tc) for tc in unique_tool_calls])
                )
                await wait_task
                wait_phrase_spoken = wait_phrase
            else:
                tool_results = list(
                    await asyncio.gather(*[_execute_one_tool(tc) for tc in unique_tool_calls])
                )
            tool_calls_made += len(tool_results)

            conversation_history.append({"role": "user", "content": tool_results})

            tool_round += 1
            if unique_tool_calls and len(suppressed_ids) == len(unique_tool_calls):
                # The round asked for nothing but calls this turn has already
                # been refused, so another round has nothing new to work with.
                # The condition is deliberately "the whole round", not "any
                # repeat": in `8e5fe347` the model interleaved a working
                # `get_fitting_price` between refusals, and that is a
                # continuation, not a loop. Stopping here does not send the turn
                # anywhere it was not already going — five rounds of the same
                # refusal end at the same exhaustion fallback, just later.
                logger.warning(
                    "Tool round %d contained only already-refused repeats — "
                    "ending the turn instead of spending the remaining rounds",
                    tool_round,
                )
                ended_on_refusal_loop = True
                break
            if tool_round >= self._max_tool_rounds:
                logger.warning("Max tool rounds reached (%d)", self._max_tool_rounds)
                break

        # Record per-turn metrics
        tool_rounds_per_turn.observe(tool_round)
        llm_stop_reason_total.labels(reason=stop_reason).inc()

        # Fallback: if the rounds ran out with no spoken text, ask LLM for a summary.
        # A turn cut short on a refusal loop needs this just as much — it has spent
        # fewer rounds, but it has exactly as little to say, and without the fallback
        # the caller would get silence.
        if not spoken_parts and (tool_round >= self._max_tool_rounds or ended_on_refusal_loop):
            tool_rounds_exhausted_total.inc()
            summary = await self._request_summary_fallback(system, conversation_history)
            if summary:
                spoken_parts.append(summary)
                # Synthesize and send the summary audio
                try:
                    tts = self._tts
                    audio = await tts.synthesize(summary)
                    if self._echo_canceller is not None:
                        self._echo_canceller.record_far_end(audio)
                    await self._conn.send_audio(audio, cancel_event=self._barge_in)
                except Exception:
                    logger.debug("Summary fallback audio send failed", exc_info=True)

        return TurnResult(
            spoken_text=" ".join(spoken_parts),
            tool_calls_made=tool_calls_made,
            stop_reason=stop_reason,
            total_usage=Usage(
                total_input_tokens, total_output_tokens, total_cached_input_tokens
            ),
            provider_key=provider_key,
            interrupted=interrupted,
            disconnected=disconnected,
            wait_phrase=wait_phrase_spoken,
        )
