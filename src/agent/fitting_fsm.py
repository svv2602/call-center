"""Deterministic state machine for the fitting-booking flow.

Design spec: `doc/development/fsm-refactor.md` §2.1–2.4 (Phase 2 — FSM Engine).

Why this module exists
----------------------
The fitting flow used to live entirely inside a 66K-char LLM prompt module
(`_MOD_CORE` + `_MOD_FITTING` in `src/agent/prompts.py`). Every regression from
Waves 4C→12 was patched by appending another guardrail to that prompt, and every
week another one was ignored under attention dilution. This module moves the
*flow control* — what to ask next and where to go after an answer — into code.
The LLM keeps generating natural Ukrainian phrasing; it no longer decides the
route.

Scope of Wave 1-A (this file's first version)
---------------------------------------------
* `FsmState` — 12 main-flow states + 3 side-states (§2.1).
* `FsmEvent` — transition events (§2.2).
* `StateConfig` / `STATES` — per-state config (§2.3), extended with a mandatory
  `resume_phrase` used by the Wave 3-B interrupt handlers.
* `TRANSITIONS` — the 48-row transition table (§2.2) as data.
* `FsmEngine` — current state, transitions (+ metrics + history), question
  rendering, `apply_field()` with auto-skip chaining.

**The engine never starts by itself.** Nothing here hooks into the request path;
`FsmEngine.start()` must be called explicitly by the pipeline (Wave 4-B). This
ordering is deliberate: the first attempt at this refactor (reverted in
`c8c6601`) shipped interrupt handlers before the state machine existed, so the
"resume" path had nothing to return to and spoke «Продовжуємо запис» when no
booking was in progress.

`freeze_for_interrupt()` and `resume()` are declared here with their final
signatures but are **not implemented** — that is Wave 4-B. The resume phrases
they will speak live in `StateConfig.resume_phrase` so a handler never has to
hardcode one.
"""

from __future__ import annotations

import enum
import logging
import re
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from src.core.call_session import FSM_HISTORY_LIMIT

if TYPE_CHECKING:
    from collections.abc import Callable

    from src.core.call_session import CallSession

logger = logging.getLogger(__name__)

# Prometheus counters live in `src/monitoring/metrics.py` (Wave 1-C, written in
# parallel). Guarded so this module still imports on its own if that work has
# not landed yet — a missing counter must never break the call flow.
try:  # pragma: no cover - import-time branch
    from src.monitoring.metrics import (
        fsm_interrupt_turn_total,
        fsm_parser_null_total,
        fsm_state_entered_total,
        fsm_transition_total,
    )

    _METRICS_AVAILABLE = True
except ImportError:  # pragma: no cover - Wave 1-C not merged yet
    fsm_interrupt_turn_total = None  # type: ignore[assignment]
    fsm_parser_null_total = None  # type: ignore[assignment]
    fsm_state_entered_total = None  # type: ignore[assignment]
    fsm_transition_total = None  # type: ignore[assignment]
    _METRICS_AVAILABLE = False
    logger.warning(
        "fitting_fsm: FSM Prometheus counters not found in src.monitoring.metrics "
        "(Wave 1-C pending) — transitions will be logged but not measured"
    )


class FsmState(enum.StrEnum):
    """States of the fitting flow (§2.1).

    Values are the uppercase member names so that `STATES["CITY"]` (the form the
    design doc uses in §2.3/§2.5) and `STATES[FsmState.CITY]` are the same key,
    and so a state read back from Redis is human-readable in a dump.
    """

    # === Main flow (linear, gated by field_filled events) ===
    WELCOME = "WELCOME"  # Greeting + await first user turn
    INTENT = "INTENT"  # Route by intent classifier
    CITY = "CITY"  # Krok 1a — determine/confirm the city
    STATION = "STATION"  # Krok 1b — get_fitting_stations, pick a point
    STORAGE = "STORAGE"  # Krok 2 — own tires vs tires in storage
    DATE = "DATE"  # Krok 3 — date (+3 working days when contract)
    TIME = "TIME"  # Krok 4 — get_fitting_slots → pick a slot
    COLOR = "COLOR"  # Krok 5 — car colour (replaced plate on 2026-08-18)
    BRAND = "BRAND"  # Krok 6 — car brand (with rare-brand STT guard)
    CONFIRM = "CONFIRM"  # Krok 8 — «Перевіримо: … Підтверджуєте?»
    BOOK = "BOOK"  # book_fitting tool call + result parsing
    DONE = "DONE"  # Krok 9 farewell → terminal

    # === Side-states (freeze the main state, resume afterwards) ===
    PRICE_INTERRUPT = "PRICE_INTERRUPT"  # Scenario 4 — price consultation
    CANCEL_INTERRUPT = "CANCEL_INTERRUPT"  # get_customer_bookings + cancel_fitting
    TRANSFER = "TRANSFER"  # transfer_to_operator → terminal

    @classmethod
    def coerce(cls, raw: str | FsmState | None) -> FsmState | None:
        """Parse a persisted state name, tolerating case differences.

        Returns None for unknown values and logs at WARNING — a state that fell
        out of the enum means a rename went half-applied, which must be visible.
        """
        if raw is None:
            return None
        if isinstance(raw, cls):
            return raw
        try:
            return cls(str(raw).strip().upper())
        except ValueError:
            logger.warning("fitting_fsm: unknown FSM state %r in session", raw)
            return None


class FsmEvent(enum.StrEnum):
    """Events that drive transitions (§2.2)."""

    # Field-level
    FIELD_FILLED = "field_filled"  # a parser returned a non-None value
    PARSER_NULL = "parser_null"  # a parser could not extract the field
    # Confirmation-level
    CONFIRM_YES = "confirm_yes"
    CONFIRM_NO = "confirm_no"
    # Interrupt-level (fired by the pipeline's pre-FSM step, Wave 4-B)
    INTERRUPT_PRICE = "interrupt_price"
    INTERRUPT_CANCEL = "interrupt_cancel"
    INTERRUPT_RESCHED = "interrupt_resched"
    # Explicit «оператор» request (row 48). The design doc describes this row as
    # "INTERRUPT (transfer keyword)" without naming an event — named here.
    INTERRUPT_TRANSFER = "interrupt_transfer"
    RESUME = "resume"  # side-state finished, back to the main flow
    # Ambient
    TIMEOUT = "timeout"  # silence (SILENCE_TIMEOUT_SEC = 18)
    ESCALATE = "escalate"  # 3× timeout / 3× parser_null / hard failure
    TOOL_ERROR = "tool_error"
    TOOL_SUCCESS = "tool_success"


# Backwards-compatible aliases for the names used in the design doc (§2.1/§2.2).
FSMState = FsmState
FSMEvent = FsmEvent

#: Main flow in linear order — used for forward walks and skip chains.
MAIN_FLOW: tuple[FsmState, ...] = (
    FsmState.WELCOME,
    FsmState.INTENT,
    FsmState.CITY,
    FsmState.STATION,
    FsmState.STORAGE,
    FsmState.DATE,
    FsmState.TIME,
    FsmState.COLOR,
    FsmState.BRAND,
    FsmState.CONFIRM,
    FsmState.BOOK,
    FsmState.DONE,
)

#: States that can be frozen by an interrupt and resumed afterwards (§2.5).
FROZEN_STATES: frozenset[FsmState] = frozenset(
    {
        FsmState.CITY,
        FsmState.STATION,
        FsmState.STORAGE,
        FsmState.DATE,
        FsmState.TIME,
        FsmState.COLOR,
        FsmState.BRAND,
        FsmState.CONFIRM,
    }
)

#: Side-state entered for each interrupt event (§2.5, rows 36/37/48).
INTERRUPT_TARGETS: dict[FsmEvent, FsmState] = {
    FsmEvent.INTERRUPT_PRICE: FsmState.PRICE_INTERRUPT,
    FsmEvent.INTERRUPT_CANCEL: FsmState.CANCEL_INTERRUPT,
    FsmEvent.INTERRUPT_RESCHED: FsmState.CANCEL_INTERRUPT,
    FsmEvent.INTERRUPT_TRANSFER: FsmState.TRANSFER,
}

#: No further dialog happens after these states.
TERMINAL_STATES: frozenset[FsmState] = frozenset({FsmState.DONE, FsmState.TRANSFER})

#: Hard stop for the auto-skip chain in `apply_field` — a cycle in `next_state`
#: or a buggy `auto_skip_if` must not spin forever.
MAX_AUTO_SKIP_HOPS = len(MAIN_FLOW)


def _never(session: CallSession) -> bool:
    """Default `auto_skip_if`: never skip."""
    return False


def _filled(session: CallSession, name: str) -> bool:
    """True if `name` is already pinned in `session.fsm_filled_fields`."""
    value = getattr(session, "fsm_filled_fields", {}).get(name)
    return value is not None and value != ""


#: Value COLOR stores when the colour could not be heard after
#: `max_parser_null` attempts. Copied verbatim from the Wave 5 prompt rule
#: (`src/agent/prompts.py`, Krok 5: «передавай "колір не розчула" у
#: book_fitting і рухайся далі»), so downstream guards keep recognising it and
#: the FSM introduces no new phrase of its own.
COLOR_NOT_HEARD = "колір не розчула"


# --- `on_null_exhausted` branches (spec §3.7 table, Wave 6-B) ------------------
#
# Three of the fifteen states have a documented answer to "the parser failed
# `max_parser_null` times in a row" that is *not* «hand the caller to an
# operator». The other twelve keep `None` and fall through to
# `escalate_target`, which is row 47 of §2.2.
#
# Two of the three write a value into the session. That side effect is what
# makes a 16th state `BRAND_TYPE_FALLBACK` pointless: `on_null_exhausted` is
# side-effecting by construction here, so BRAND setting a flag is not a new
# kind of thing (README §6 of the Wave 6-B checklist).


def _storage_null_exhausted(session: CallSession) -> FsmState:
    """STORAGE (row 12): default to «own» rather than ask a third time.

    The overwhelming majority of callers bring their own tires; a caller with a
    storage contract is asked about it again downstream (`find_storage` runs as
    STORAGE's `exit_tool`), so the cheap default is the safe one.
    """
    session.fsm_filled_fields["storage_choice"] = "own"
    return FsmState.DATE


def _color_null_exhausted(session: CallSession) -> FsmState:
    """COLOR (row 20): record «колір не розчула» and move on.

    Wording taken verbatim from the Wave 5 escape hatch already in the prompt —
    this is not a new phrase invented by the FSM.
    """
    session.fsm_filled_fields["color"] = COLOR_NOT_HEARD
    return FsmState.BRAND


def _brand_null_exhausted(session: CallSession) -> FsmState:
    """BRAND (row 23): stay in BRAND, but switch to the car-type question.

    Unlike the other two this writes no value: an unheard brand must not be
    guessed. It flips a session flag that `next_question()` reads, so the state
    asks `StateConfig.fallback_question` from here on.
    """
    session.fsm_brand_type_fallback = True
    return FsmState.BRAND


@dataclass(frozen=True)
class StateConfig:
    """Everything the engine needs to run one state (§2.3).

    `resume_phrase` is a Wave 1-A addition on top of the design doc: the phrase
    the bot speaks when a caller is brought back to this state after an
    interrupt. It lives with the state on purpose — Wave 3-B's interrupt handlers
    must read it from here instead of hardcoding one (the bug behind `c8c6601`).
    """

    # --- Identity ---
    state: FsmState
    #: Field this state collects, as stored in `session.fsm_filled_fields`.
    #: None for states that collect nothing (WELCOME/BOOK/DONE/TRANSFER).
    field_name: str | None

    # --- LLM-facing text (Ukrainian) ---
    question_template: str
    silence_reprompt: str | None
    resume_phrase: str

    # --- Parser wiring (parsers live in `src/agent/parsers/`, Wave 5-A) ---
    #: Registry key — see `src.agent.parsers.registry.PARSERS`.
    parser: str
    # `parser_input` used to sit here ("last_user_turn" / "last_3_turns" /
    # "compound_first_turn"). It was never read, no state overrode it, and
    # `ParseContext` made it redundant: a parser that needs the last three
    # turns reads `ctx.session.dialog_history` itself. Removed in Wave 5-A
    # (§3.8) rather than left as a third «declared and forgotten» field.

    # --- Routing ---
    next_state: FsmState | None = None
    required_context: tuple[str, ...] = ()
    auto_skip_if: Callable[[CallSession], bool] = field(default=_never)

    # --- Tools ---
    entry_tool: str | None = None
    exit_tool: str | None = None

    # --- Anti-regression ---
    #: How many turns in a row this state's parser may come back without a
    #: usable value before `on_null_exhausted` / `escalate_target` takes over.
    #: Read by `FsmEngine.on_parser_null()` (Wave 6-B).
    max_parser_null: int = 3
    #: How many turns in this state may be spent on a detected interrupt
    #: (price / cancel) before the caller gets an operator. Separate budget
    #: from `max_parser_null`: a question is not a failed answer, but the
    #: exemption still has to be bounded. Read by `on_interrupt_turn()`.
    max_interrupt_turns: int = 4
    #: Last resort when the attempts are used up and there is no branch. Row 47
    #: of §2.2 — an operator, never «hand it back to the LLM».
    escalate_target: FsmState = FsmState.TRANSFER
    #: What to do instead of escalating. `None` for the twelve states whose
    #: answer really is «transfer» (§3.7 table). The three that have one are
    #: STORAGE, COLOR and BRAND.
    on_null_exhausted: Callable[[CallSession], FsmState] | None = None
    #: Asked instead of `question_template` once `on_null_exhausted` flipped
    #: this state's fallback flag. Only BRAND has one (row 23). Every string
    #: the bot may speak lives in `STATES`, including this one — a handler that
    #: hardcodes a phrase is the `c8c6601` failure mode.
    fallback_question: str | None = None
    terminal: bool = False


STATES: dict[FsmState, StateConfig] = {
    FsmState.WELCOME: StateConfig(
        state=FsmState.WELCOME,
        field_name=None,
        question_template="{greeting_text}",  # rendered from tenant config
        silence_reprompt=None,  # no reprompt — barge-in expected
        resume_phrase="Слухаю вас.",
        parser="noop_parser",
        next_state=FsmState.INTENT,
    ),
    FsmState.INTENT: StateConfig(
        state=FsmState.INTENT,
        field_name="intent",
        question_template="",  # reactive to the first user turn
        silence_reprompt="Я на зв'язку, кажіть.",
        resume_phrase="Отже, чим можу допомогти?",
        parser="intent_classifier",
        next_state=FsmState.CITY,  # intent=fitting; other intents via rows 3-5
        auto_skip_if=lambda s: _filled(s, "intent"),
    ),
    FsmState.CITY: StateConfig(
        state=FsmState.CITY,
        field_name="city",
        question_template="У якому місті вам зручніше?",
        silence_reprompt="Оберіть: Київ, Дніпро, Запоріжжя, Харків, Черкаси.",
        resume_phrase="Повертаємось до запису. У якому місті вам зручніше?",
        parser="city_parser",
        next_state=FsmState.STATION,
        required_context=("intent",),
        auto_skip_if=lambda s: _filled(s, "city"),
    ),
    FsmState.STATION: StateConfig(
        state=FsmState.STATION,
        field_name="station_id",
        question_template="У [city] є [stations_count] точок у районах: [districts]. У якому вам зручніше?",
        silence_reprompt="Оберіть район, або скажіть «будь-яка».",
        resume_phrase="Повертаємось до вибору точки шиномонтажу.",
        parser="station_parser",
        next_state=FsmState.STORAGE,
        required_context=("city",),
        entry_tool="get_fitting_stations",
        # Single station in the city → auto-pin (§2.3). The doc's variant also
        # consulted an undefined `_profile_city_confirmed`; kept to what this
        # module can evaluate on its own.
        auto_skip_if=lambda s: _filled(s, "station_id") or len(s.fitting_station_ids) == 1,
    ),
    FsmState.STORAGE: StateConfig(
        state=FsmState.STORAGE,
        field_name="storage_choice",
        question_template="Шини привозите свої з собою чи ті, що у нас на зберіганні?",
        silence_reprompt="Скажіть: свої з собою або зі зберігання.",
        resume_phrase="Повертаємось до запису. Шини свої з собою чи ті, що у нас на зберіганні?",
        parser="storage_choice_parser",
        next_state=FsmState.DATE,
        required_context=("station_id",),
        exit_tool="find_storage",  # only when choice == "contract"
        max_parser_null=2,  # anti-loop: after 2 nulls default to "own"
        on_null_exhausted=_storage_null_exhausted,
        auto_skip_if=lambda s: _filled(s, "storage_choice"),
    ),
    FsmState.DATE: StateConfig(
        state=FsmState.DATE,
        field_name="date",
        question_template="На яку дату записуємо?",
        silence_reprompt="Назвіть, будь ласка, дату — наприклад, завтра або п'ятницю.",
        resume_phrase="Повертаємось до запису. На яку дату вас записати?",
        parser="date_parser",
        next_state=FsmState.TIME,
        required_context=("storage_choice",),
        # NB: no default date — Wave 8 anti-pattern «пропоную завтра» (anchor 1, §2.9).
        auto_skip_if=lambda s: _filled(s, "date"),
    ),
    FsmState.TIME: StateConfig(
        state=FsmState.TIME,
        field_name="time",
        question_template="На [date] вільно: [slots]. Який час зручніше?",
        silence_reprompt="Оберіть час зі списку.",
        resume_phrase="Повертаємось до вибору часу.",
        parser="time_parser",
        next_state=FsmState.COLOR,
        required_context=("date", "station_id"),
        entry_tool="get_fitting_slots",
        auto_skip_if=lambda s: _filled(s, "time"),
    ),
    FsmState.COLOR: StateConfig(
        state=FsmState.COLOR,
        field_name="color",
        question_template="Назвіть, будь ласка, колір автомобіля.",
        silence_reprompt="Скажіть колір — білий, чорний, сірий.",
        resume_phrase="Повертаємось до запису. Назвіть, будь ласка, колір автомобіля.",
        parser="color_parser",
        next_state=FsmState.BRAND,
        required_context=("time",),
        max_parser_null=3,  # after 3 nulls → «колір не розчула»
        on_null_exhausted=_color_null_exhausted,
        auto_skip_if=lambda s: _filled(s, "color"),
    ),
    FsmState.BRAND: StateConfig(
        state=FsmState.BRAND,
        field_name="brand",
        question_template="Яка марка вашого авто?",
        silence_reprompt="Скажіть марку — Toyota, VW, BMW.",
        resume_phrase="Повертаємось до запису. Яка марка вашого авто?",
        parser="brand_parser",
        next_state=FsmState.CONFIRM,
        required_context=("color",),
        max_parser_null=2,  # then the type-fallback branch (row 23)
        on_null_exhausted=_brand_null_exhausted,
        # Verbatim from the Wave 6 rule already in `src/agent/prompts.py`
        # (Krok 6, «ПІСЛЯ 2 НЕВДАЛИХ ПЕРЕПИТУВАНЬ МАРКИ → ПЕРЕЙДИ ДО ТИПУ
        # АВТО»). The FSM must not invent a phrase the prompt never had.
        fallback_question=(
            "Уточніть, будь ласка, тип автомобіля: легкове, позашляховик (SUV), "
            "мікроавтобус чи вантажне?"
        ),
        auto_skip_if=lambda s: _filled(s, "brand"),
    ),
    FsmState.CONFIRM: StateConfig(
        state=FsmState.CONFIRM,
        field_name="confirmed",
        question_template=(
            "{name}, перевіримо: [date] о [time], [address], [color] [brand]. Підтверджуєте?"
        ),
        silence_reprompt=None,  # Wave 5 Krok 8 emergency banner instead
        resume_phrase="Повертаємось до підтвердження запису.",
        parser="yes_no_parser",
        next_state=FsmState.BOOK,
        required_context=(
            "name",
            "city",
            "station_id",
            "storage_choice",
            "date",
            "time",
            "color",
            "brand",
        ),
    ),
    FsmState.BOOK: StateConfig(
        state=FsmState.BOOK,
        field_name=None,
        question_template="",  # no question — tool call only
        silence_reprompt=None,
        resume_phrase="Оформлюю ваш запис, ще хвилинку.",
        parser="noop_parser",  # the result comes from the tool, not the caller
        next_state=FsmState.DONE,
        entry_tool="book_fitting",  # errors route via rows 28-35
    ),
    FsmState.DONE: StateConfig(
        state=FsmState.DONE,
        field_name=None,
        question_template="{name}, ви записані. СМС підтвердження надійде. Дякуємо!",
        silence_reprompt=None,
        resume_phrase="Ваш запис уже оформлено. Дякую за звернення!",
        parser="noop_parser",
        next_state=FsmState.DONE,
        terminal=True,
    ),
    # --- Side-states ---
    FsmState.PRICE_INTERRUPT: StateConfig(
        state=FsmState.PRICE_INTERRUPT,
        field_name="diameter",
        question_template="Який діаметр коліс?",
        silence_reprompt="Скажіть, будь ласка, діаметр — від 14 до 21.",
        resume_phrase="Повертаємось до вартості шиномонтажу.",
        parser="diameter_parser",
        next_state=FsmState.PRICE_INTERRUPT,  # loops until CONFIRM_YES/CONFIRM_NO
        entry_tool="get_fitting_stations",  # for_price=true — pins station_id
    ),
    FsmState.CANCEL_INTERRUPT: StateConfig(
        state=FsmState.CANCEL_INTERRUPT,
        field_name="booking_id",
        question_template="Знайшла [bookings_count] запис(и): [bookings]. Який скасовуємо?",
        silence_reprompt="Скажіть, будь ласка, який запис скасувати.",
        resume_phrase="Повертаємось до скасування запису.",
        parser="booking_id_parser",
        next_state=FsmState.CANCEL_INTERRUPT,
        entry_tool="get_customer_bookings",
    ),
    FsmState.TRANSFER: StateConfig(
        state=FsmState.TRANSFER,
        field_name=None,
        question_template="Переключаю на оператора, зачекайте, будь ласка.",
        silence_reprompt=None,
        resume_phrase="З'єдную вас з оператором, зачекайте, будь ласка.",
        parser="noop_parser",
        next_state=FsmState.TRANSFER,
        terminal=True,
    ),
}


@dataclass(frozen=True)
class TransitionRule:
    """One row of the transition table (§2.2).

    `from_state=None` means "any state" (rows 46-48). `to_state=None` means the
    target is computed at runtime (row 42: back to `fsm_prev_state`) or is the
    same state (rows 7/9/12/…, encoded explicitly where the doc names a target).
    `guard` is the informal condition from the doc's Action column, used to pick
    between several rows that share the same (from_state, event) pair.
    """

    row: int
    from_state: FsmState | None
    event: FsmEvent
    to_state: FsmState | None
    action: str
    guard: str | None = None


# The full 48-row table from §2.2, kept as data so Wave 2-A can assert on it and
# so the guards from Waves 4C→12 map onto explicit rows (§2.8).
TRANSITIONS: tuple[TransitionRule, ...] = (
    TransitionRule(
        1,
        FsmState.WELCOME,
        FsmEvent.FIELD_FILLED,
        FsmState.INTENT,
        "store intent from the classifier",
        guard="intent",
    ),
    TransitionRule(
        2,
        FsmState.INTENT,
        FsmEvent.FIELD_FILLED,
        FsmState.CITY,
        "route_to_booking_flow",
        guard="intent=fitting",
    ),
    TransitionRule(
        3,
        FsmState.INTENT,
        FsmEvent.FIELD_FILLED,
        FsmState.PRICE_INTERRUPT,
        "enter price flow, freeze=None",
        guard="intent=price",
    ),
    TransitionRule(
        4,
        FsmState.INTENT,
        FsmEvent.FIELD_FILLED,
        FsmState.CANCEL_INTERRUPT,
        "get_customer_bookings, freeze=None",
        guard="intent=cancel_fitting",
    ),
    TransitionRule(
        5,
        FsmState.INTENT,
        FsmEvent.FIELD_FILLED,
        FsmState.TRANSFER,
        "transfer_to_operator(reason=out_of_scope)",
        guard="intent=other",
    ),
    TransitionRule(
        6,
        FsmState.CITY,
        FsmEvent.FIELD_FILLED,
        FsmState.STATION,
        "pin city, call get_fitting_stations(city)",
        guard="city",
    ),
    TransitionRule(
        7, FsmState.CITY, FsmEvent.PARSER_NULL, FsmState.CITY, "re-ask with the fallback city list"
    ),
    TransitionRule(
        8,
        FsmState.STATION,
        FsmEvent.FIELD_FILLED,
        FsmState.STORAGE,
        "pin last_fitting_station_id, save fitting_stations_seen",
        guard="station_id",
    ),
    TransitionRule(
        9,
        FsmState.STATION,
        FsmEvent.PARSER_NULL,
        FsmState.STATION,
        "ask «У якому районі?» with district options",
        guard="ambiguous_2plus",
    ),
    TransitionRule(
        10,
        FsmState.STORAGE,
        FsmEvent.FIELD_FILLED,
        FsmState.DATE,
        "storage_choice=own, storage_contract=''",
        guard="storage_choice=own",
    ),
    TransitionRule(
        11,
        FsmState.STORAGE,
        FsmEvent.FIELD_FILLED,
        FsmState.DATE,
        "find_storage, pin contract, min_date = today+3 working days",
        guard="storage_choice=contract",
    ),
    TransitionRule(
        12,
        FsmState.STORAGE,
        FsmEvent.PARSER_NULL,
        FsmState.STORAGE,
        "single re-ask; anti-loop: 3rd attempt defaults to own",
    ),
    TransitionRule(
        13,
        FsmState.DATE,
        FsmEvent.FIELD_FILLED,
        FsmState.TIME,
        "validate tomorrow ≤ date ≤ today+21 (+3 working days on contract), call get_fitting_slots",
        guard="date",
    ),
    TransitionRule(
        14,
        FsmState.DATE,
        FsmEvent.PARSER_NULL,
        FsmState.DATE,
        "re-ask «На яку дату?» — never offer a default date",
    ),
    TransitionRule(
        15,
        FsmState.TIME,
        FsmEvent.FIELD_FILLED,
        FsmState.COLOR,
        "validate time ∈ fitting_slots_offered, pin date+time",
        guard="time",
    ),
    TransitionRule(
        16,
        FsmState.TIME,
        FsmEvent.TOOL_ERROR,
        FsmState.DATE,
        "offer suggested_date; CONFIRM_YES returns to DATE with it",
        guard="no_slots",
    ),
    TransitionRule(
        17, FsmState.TIME, FsmEvent.PARSER_NULL, FsmState.TIME, "re-read the offered slots"
    ),
    TransitionRule(
        18,
        FsmState.COLOR,
        FsmEvent.FIELD_FILLED,
        FsmState.BRAND,
        "store colour in fitting_plate (historic field name)",
        guard="color",
    ),
    TransitionRule(
        19,
        FsmState.COLOR,
        FsmEvent.FIELD_FILLED,
        FsmState.BRAND,
        "Wave 5 escape hatch — only with a forget-keyword in the last 3 turns",
        guard="color=не назвали",
    ),
    TransitionRule(
        20,
        FsmState.COLOR,
        FsmEvent.PARSER_NULL,
        FsmState.COLOR,
        "targeted colour reprompt; 3rd null stores «колір не розчула»",
        guard="null_2x",
    ),
    TransitionRule(
        21,
        FsmState.BRAND,
        FsmEvent.FIELD_FILLED,
        FsmState.CONFIRM,
        "store fitting_vehicle_brand",
        guard="brand",
    ),
    TransitionRule(
        22,
        FsmState.BRAND,
        FsmEvent.PARSER_NULL,
        FsmState.BRAND,
        "rare-brand STT guard: «Ви маєте на увазі X?»",
        guard="rare_brand",
    ),
    TransitionRule(
        23,
        FsmState.BRAND,
        FsmEvent.PARSER_NULL,
        FsmState.BRAND,
        "Wave 6 type-fallback: ask the car type, store in vehicle_info",
        guard="null_2x",
    ),
    TransitionRule(
        24,
        FsmState.CONFIRM,
        FsmEvent.CONFIRM_YES,
        FsmState.BOOK,
        "call book_fitting with all pinned fields",
    ),
    TransitionRule(
        25,
        FsmState.CONFIRM,
        FsmEvent.CONFIRM_NO,
        FsmState.DATE,
        "return to the first ⏳ / caller-named field",
    ),
    TransitionRule(
        26,
        FsmState.CONFIRM,
        FsmEvent.PARSER_NULL,
        FsmState.CONFIRM,
        "repeat «Перевіримо: …» — «алло»/«що?» is not a YES",
    ),
    TransitionRule(
        27,
        FsmState.BOOK,
        FsmEvent.TOOL_SUCCESS,
        FsmState.DONE,
        "success TTS + persist calls.fitting_booking_id via shield",
    ),
    TransitionRule(
        28,
        FsmState.BOOK,
        FsmEvent.TOOL_ERROR,
        FsmState.TIME,
        "Wave 7 guard: date/slots not pinned → redo TIME",
        guard="krok3_4_guard",
    ),
    TransitionRule(
        29,
        FsmState.BOOK,
        FsmEvent.TOOL_ERROR,
        FsmState.COLOR,
        "Wave 5 guard: colour escape hatch without forget-keyword → re-ask",
        guard="escape_hatch",
    ),
    TransitionRule(
        30,
        FsmState.BOOK,
        FsmEvent.TOOL_ERROR,
        FsmState.BRAND,
        "Wave 7 guard: vehicle type used as brand → ask for the brand",
        guard="type_as_brand",
    ),
    TransitionRule(
        31,
        FsmState.BOOK,
        FsmEvent.TOOL_ERROR,
        FsmState.STORAGE,
        "Wave 5/6 guard: contract matched but storage_contract empty → re-ask once",
        guard="storage_contract_missing",
    ),
    TransitionRule(
        32,
        FsmState.BOOK,
        FsmEvent.TOOL_ERROR,
        FsmState.STATION,
        "Wave 9/10 regression guards: block the return to CITY",
        guard="cross_city_or_past_krok_2",
    ),
    TransitionRule(
        33,
        FsmState.BOOK,
        FsmEvent.TOOL_ERROR,
        FsmState.DATE,
        "Wave 8/12 weekday guard: date ≠ requested weekday",
        guard="weekday_mismatch",
    ),
    TransitionRule(
        34,
        FsmState.BOOK,
        FsmEvent.TOOL_ERROR,
        FsmState.BOOK,
        "retry once with identical parameters",
        guard="1c_network_1st",
    ),
    TransitionRule(
        35,
        FsmState.BOOK,
        FsmEvent.TOOL_ERROR,
        FsmState.TRANSFER,
        "transfer_to_operator(reason=fitting_service_unavailable), keep session data",
        guard="1c_network_2nd",
    ),
    TransitionRule(
        36,
        None,
        FsmEvent.INTERRUPT_PRICE,
        FsmState.PRICE_INTERRUPT,
        "freeze current state, enter Krok Ц-1..Ц-5",
        guard="from_frozen_states",
    ),
    TransitionRule(
        37,
        None,
        FsmEvent.INTERRUPT_CANCEL,
        FsmState.CANCEL_INTERRUPT,
        "freeze current state, get_customer_bookings",
        guard="from_frozen_states",
    ),
    TransitionRule(
        38,
        FsmState.PRICE_INTERRUPT,
        FsmEvent.FIELD_FILLED,
        FsmState.PRICE_INTERRUPT,
        "Wave 12 diameter guard: pin fitting_diameter_client, get_fitting_price",
        guard="diameter",
    ),
    TransitionRule(
        39,
        FsmState.PRICE_INTERRUPT,
        FsmEvent.TOOL_SUCCESS,
        FsmState.PRICE_INTERRUPT,
        "read the prices, then ask «Записуємо на монтаж?»",
        guard="prices",
    ),
    TransitionRule(
        40,
        FsmState.PRICE_INTERRUPT,
        FsmEvent.CONFIRM_YES,
        FsmState.STORAGE,
        "resume with pinned city+station — NOT CITY (Wave 3 #4 regression)",
    ),
    TransitionRule(
        41,
        FsmState.PRICE_INTERRUPT,
        FsmEvent.CONFIRM_NO,
        FsmState.DONE,
        "farewell «Дякую за звернення»",
    ),
    TransitionRule(
        42,
        FsmState.PRICE_INTERRUPT,
        FsmEvent.RESUME,
        None,
        "unfreeze into fsm_prev_state; DONE when there is none",
    ),
    TransitionRule(
        43,
        FsmState.CANCEL_INTERRUPT,
        FsmEvent.TOOL_SUCCESS,
        FsmState.CANCEL_INTERRUPT,
        "ask «Записати на інший час?»",
        guard="cancelled",
    ),
    TransitionRule(
        44,
        FsmState.CANCEL_INTERRUPT,
        FsmEvent.CONFIRM_YES,
        FsmState.DATE,
        "reschedule: reuse city+station+storage_choice of the cancelled booking",
    ),
    TransitionRule(45, FsmState.CANCEL_INTERRUPT, FsmEvent.CONFIRM_NO, FsmState.DONE, "farewell"),
    TransitionRule(
        46, None, FsmEvent.TIMEOUT, None, "stay in the state, play its silence_reprompt"
    ),
    TransitionRule(
        47,
        None,
        FsmEvent.ESCALATE,
        FsmState.TRANSFER,
        "3× timeout or 3× parser_null → transfer_to_operator(silence/cannot_parse)",
    ),
    TransitionRule(
        48,
        None,
        FsmEvent.INTERRUPT_TRANSFER,
        FsmState.TRANSFER,
        "explicit «оператор» → transfer_to_operator(reason=customer_request)",
    ),
)


def find_transitions(
    from_state: FsmState | None,
    event: FsmEvent,
    guard: str | None = None,
) -> list[TransitionRule]:
    """Return the table rows matching (from_state, event[, guard]).

    Exact-guard matches come first, then unguarded rows for the same state, then
    wildcard (`from_state=None`) rows. Returns an empty list when the table has
    nothing for the pair — the caller decides what that means.
    """
    exact: list[TransitionRule] = []
    unguarded: list[TransitionRule] = []
    wildcard: list[TransitionRule] = []
    for rule in TRANSITIONS:
        if rule.event != event:
            continue
        if rule.from_state is None:
            wildcard.append(rule)
            continue
        if rule.from_state != from_state:
            continue
        if guard is not None and rule.guard == guard:
            exact.append(rule)
        elif rule.guard is None or guard is None:
            unguarded.append(rule)
    return exact + unguarded + wildcard


# Placeholders in `question_template` come in two flavours: `{name}` (rendered
# from the session/tenant context) and `[date]` (values pinned by tools).
_CURLY_PLACEHOLDER = re.compile(r"\{([a-z_][a-z0-9_]*)\}")
_BRACKET_PLACEHOLDER = re.compile(r"\[([a-z_][a-z0-9_]*)\]")


@dataclass
class FsmEngine:
    """Runs the fitting flow over a `CallSession`.

    The engine is inert until something calls `start()`. Instantiating it has no
    side effects on the session.
    """

    session: CallSession

    # --- Reading state ---

    def current_state(self) -> FsmState:
        """Current state, defaulting to WELCOME when the FSM has not started."""
        return FsmState.coerce(self.session.fsm_state) or FsmState.WELCOME

    def is_started(self) -> bool:
        """True once `start()` (or an explicit transition) pinned a state."""
        return FsmState.coerce(self.session.fsm_state) is not None

    def config(self, state: FsmState | None = None) -> StateConfig:
        """Per-state config for `state` (defaults to the current state)."""
        return STATES[state or self.current_state()]

    def is_terminal(self) -> bool:
        """True when the call has reached DONE or TRANSFER."""
        return self.current_state() in TERMINAL_STATES

    def missing_context(self, state: FsmState | None = None) -> list[str]:
        """Fields from `required_context` that are not pinned yet."""
        cfg = self.config(state)
        return [name for name in cfg.required_context if not _filled(self.session, name)]

    # --- Lifecycle ---

    def start(self, state: FsmState = FsmState.WELCOME) -> FsmState:
        """Explicitly enter the FSM. Never called from this module.

        The pipeline calls this in Wave 4-B once the feature flag is on. Calling
        it twice is a no-op so a Redis-recovered session is not rewound.
        """
        already = FsmState.coerce(self.session.fsm_state)
        if already is not None:
            logger.debug(
                "fitting_fsm: start() ignored, call %s already in %s",
                self.session.channel_uuid,
                already,
            )
            return already
        self._enter(
            state, event=FsmEvent.FIELD_FILLED, from_state=None, payload={"reason": "start"}
        )
        return state

    # --- Transitions ---

    def transition(
        self,
        to_state: FsmState,
        event: FsmEvent = FsmEvent.FIELD_FILLED,
        payload: dict[str, Any] | None = None,
    ) -> FsmState:
        """Move to `to_state`, recording history and emitting metrics."""
        target = FsmState.coerce(to_state)
        if target is None:
            raise ValueError(f"unknown FSM target state: {to_state!r}")
        from_state = FsmState.coerce(self.session.fsm_state)
        if from_state == target and event is not FsmEvent.TIMEOUT:
            logger.debug(
                "fitting_fsm: call %s re-enters %s on %s",
                self.session.channel_uuid,
                target,
                event,
            )
        self._enter(target, event=event, from_state=from_state, payload=payload)
        return target

    def _enter(
        self,
        state: FsmState,
        event: FsmEvent,
        from_state: FsmState | None,
        payload: dict[str, Any] | None,
    ) -> None:
        self.session.fsm_state = state.value
        if from_state is not None and from_state is not state:
            # Leaving a state clears its parser_null budget. Without this the
            # counter is effectively global: three nulls spread over CITY,
            # DATE and BRAND would escalate a call in which every state was
            # answered on the second try.
            self._null_counts().pop(from_state.value, None)
            self._interrupt_turn_counts().pop(from_state.value, None)
            self._clear_fallback(from_state)
        self._record_history(from_state, state, event, payload)
        self._emit_metrics(from_state, state)
        # Fields go into the message, not into `extra`: `JSONFormatter` only
        # forwards a fixed whitelist of record attributes (`call_id`,
        # `request_id`, `duration_ms`, `tool`, `success`) and drops everything
        # else, so an `extra={"from_state": …}` would look done and log
        # nothing. `call_id` is the one key the whitelist does pass, so it is
        # given both ways — queryable as a field, readable in the line.
        logger.info(
            "fsm_transition call=%s from=%s to=%s event=%s fields=%d payload=%s",
            self.session.channel_uuid,
            from_state.value if from_state else None,
            state.value,
            event.value,
            len(self.session.fsm_filled_fields),
            payload,
            extra={"call_id": str(self.session.channel_uuid)},
        )

    def _record_history(
        self,
        from_state: FsmState | None,
        to_state: FsmState,
        event: FsmEvent,
        payload: dict[str, Any] | None,
    ) -> None:
        entry: dict[str, Any] = {
            "t": time.time(),
            "from": from_state.value if from_state else None,
            "to": to_state.value,
            "event": event.value,
            "payload": payload,
        }
        history = self.session.fsm_history
        history.append(entry)
        if len(history) > FSM_HISTORY_LIMIT:
            del history[:-FSM_HISTORY_LIMIT]

    def _emit_metrics(self, from_state: FsmState | None, to_state: FsmState) -> None:
        if not _METRICS_AVAILABLE:
            return
        try:
            fsm_state_entered_total.labels(state=to_state.value).inc()
            fsm_transition_total.labels(
                from_state=from_state.value if from_state else "none",
                to_state=to_state.value,
            ).inc()
        except Exception:
            # Metrics must never break a call, but a broken counter is a real
            # defect — log it loudly instead of hiding it at DEBUG.
            logger.exception(
                "fitting_fsm: failed to emit FSM metrics for %s → %s", from_state, to_state
            )

    # --- Field filling ---

    def apply_field(self, field_name: str, value: Any) -> FsmState:
        """Pin a parsed field, advance, and follow the auto-skip chain.

        Returns the state the caller ends up in. Raises ValueError on an empty
        field name — a silent no-op here would look like a parser that never
        fires.
        """
        if not field_name:
            raise ValueError("apply_field requires a non-empty field name")

        self.session.fsm_filled_fields[field_name] = value
        cfg = self.config()
        # FIELD_FILLED clears this state's parser_null budget (§3.7). Doing it
        # here rather than only on state exit matters for the self-looping
        # states (PRICE_INTERRUPT, CANCEL_INTERRUPT): a caller who finally
        # names a diameter on the third try must not be one null away from an
        # escalation for the rest of the call.
        if cfg.field_name == field_name:
            self._null_counts().pop(cfg.state.value, None)
            self._interrupt_turn_counts().pop(cfg.state.value, None)
            self._clear_fallback(cfg.state)
        target = cfg.next_state or cfg.state
        self.transition(
            target,
            event=FsmEvent.FIELD_FILLED,
            payload={"field": field_name, "value": value},
        )
        self._follow_auto_skips()
        return self.current_state()

    def refresh_auto_skips(self) -> FsmState:
        """Re-evaluate STATION's auto-skip outside `apply_field` (Wave 6-D).

        Seven of the eight `auto_skip_if` predicates read `_filled(s, "<field>")`,
        i.e. `fsm_filled_fields`, which only `apply_field` writes — so checking
        them once, inside `apply_field`, is exactly right. STATION is the
        exception: its second disjunct reads `session.fitting_station_ids`, and
        that set is written by the **tool** (`get_fitting_stations`,
        `src/main.py`), on a later turn than the one that walked the FSM into
        STATION. The single call site of `_follow_auto_skips()` therefore
        evaluated the predicate at the one moment it is guaranteed false and
        never again: the rule was correct and dead, and three of the sixteen
        replayed tester calls ended in TRANSFER holding exactly one station id
        and `station_id = None`.

        Gated on STATION on purpose. Re-running the whole chain every turn
        would fold unmeasured skips into a wave whose gain is measured, and
        there is nothing to re-run: for the other seven states the predicate
        can only have changed inside `apply_field`, which already follows the
        chain itself.

        Pure and synchronous — reads the session, writes a session field. Safe
        to call from the shadow seam (`no await → no network`).
        """
        if self.current_state() is not FsmState.STATION:
            return self.current_state()
        self._follow_auto_skips()
        return self.current_state()

    def _pin_single_station(self) -> None:
        """STATION with exactly one station in the session → pin `station_id`.

        `auto_skip_if` *skips* the state; it does not fill the field. Skipping
        alone would walk past STATION with `station_id` still empty and hand
        `book_fitting` a booking without a station — and falling back to a
        default station is forbidden (`945bcb7`). So the field is pinned first
        and the skip then follows from the predicate's own first disjunct.

        Writes `fsm_filled_fields` directly rather than calling `apply_field`:
        `apply_field` calls `_follow_auto_skips`, and this runs *inside*
        `_follow_auto_skips`, so going through it would be mutual recursion
        past the `visited` guard (which is local to one frame).
        """
        session = self.session
        # Never overwrite a station the caller actually chose.
        if _filled(session, "station_id"):
            return
        ids = getattr(session, "fitting_station_ids", None) or set()
        # Exactly one, not "at least one": two stations is a real question.
        if len(ids) != 1:
            return
        only_id = str(next(iter(ids)) or "").strip()
        seen = getattr(session, "fitting_stations_seen", None) or []
        matches = [
            s for s in seen if isinstance(s, dict) and str(s.get("id") or "").strip() == only_id
        ]
        if len(matches) != 1:
            # The id set and the snapshot list are written together by
            # `get_fitting_stations`, so this is a defensive branch (a session
            # restored from an older Redis payload). It is logged at WARNING
            # rather than skipped in silence — an invisible refusal on a
            # booking path is how 3/3 bookings were lost (`37fb2d0`).
            logger.warning(
                "fsm_station_autopin_refused call=%s reason=no_unique_snapshot "
                "id=%r matches=%d seen=%d",
                session.channel_uuid,
                only_id,
                len(matches),
                len(seen),
                extra={"call_id": str(session.channel_uuid)},
            )
            return
        station_id = str(matches[0].get("id") or "").strip()
        if not station_id:
            logger.warning(
                "fsm_station_autopin_refused call=%s reason=empty_station_id station=%r",
                session.channel_uuid,
                matches[0],
                extra={"call_id": str(session.channel_uuid)},
            )
            return
        session.fsm_filled_fields["station_id"] = station_id
        logger.info(
            "fsm_station_autopin call=%s station=%s reason=single_station",
            session.channel_uuid,
            station_id,
            extra={"call_id": str(session.channel_uuid)},
        )

    def _follow_auto_skips(self) -> None:
        """Skip forward while the current state's `auto_skip_if` holds."""
        visited: set[FsmState] = {self.current_state()}
        for _ in range(MAX_AUTO_SKIP_HOPS):
            current = self.current_state()
            cfg = STATES[current]
            if cfg.terminal or cfg.next_state is None or cfg.next_state is current:
                return
            if current is FsmState.STATION:
                # Fill before the predicate is read, so the state is left with
                # a station rather than merely left. One call site for the pin,
                # shared by both routes into STATION (`apply_field` and
                # `refresh_auto_skips`).
                self._pin_single_station()
            try:
                should_skip = bool(cfg.auto_skip_if(self.session))
            except Exception:
                logger.exception(
                    "fitting_fsm: auto_skip_if raised for state %s (call %s) — staying put",
                    current,
                    self.session.channel_uuid,
                )
                return
            if not should_skip:
                return
            if cfg.next_state in visited:
                logger.warning(
                    "fitting_fsm: auto-skip cycle %s → %s for call %s — stopping",
                    current,
                    cfg.next_state,
                    self.session.channel_uuid,
                )
                return
            visited.add(cfg.next_state)
            self.transition(
                cfg.next_state,
                event=FsmEvent.FIELD_FILLED,
                payload={"reason": "auto_skip", "skipped": current.value},
            )
        logger.warning(
            "fitting_fsm: auto-skip hop limit (%d) reached at %s for call %s",
            MAX_AUTO_SKIP_HOPS,
            self.current_state(),
            self.session.channel_uuid,
        )

    # --- parser_null (Wave 6-B, §3.7) ---

    def _null_counts(self) -> dict[str, int]:
        """The live `fsm_parser_null_counts` dict, tolerating an old session.

        Shaped after `_counts()` in `src/agent/interrupts.py`: a session
        restored from a Redis payload written before this field existed has no
        attribute at all, and the loop-breaker must degrade to «start counting»
        rather than to `AttributeError` inside a live call.
        """
        counts = getattr(self.session, "fsm_parser_null_counts", None)
        if not isinstance(counts, dict):
            counts = {}
            self.session.fsm_parser_null_counts = counts
        return counts

    def parser_null_count(self, state: FsmState | None = None) -> int:
        """How many consecutive nulls `state` has already collected."""
        return self._null_counts().get((state or self.current_state()).value, 0)

    def _interrupt_turn_counts(self) -> dict[str, int]:
        """The live `fsm_interrupt_turn_counts` dict, tolerating an old session."""
        counts = getattr(self.session, "fsm_interrupt_turn_counts", None)
        if not isinstance(counts, dict):
            counts = {}
            self.session.fsm_interrupt_turn_counts = counts
        return counts

    def interrupt_turn_count(self, state: FsmState | None = None) -> int:
        """How many interrupt turns `state` has already absorbed."""
        return self._interrupt_turn_counts().get(
            (state or self.current_state()).value, 0
        )

    def on_interrupt_turn(
        self,
        kind: str,
        customer_text: str = "",
        *,
        advance: bool = True,
    ) -> FsmState:
        """The caller used this turn to open an interrupt, not to answer.

        Charged to its own budget instead of `max_parser_null`. Asking what it
        costs is not a failure to name a colour, and three such turns used to
        spend the null budget and hand the caller to an operator for questions
        the bot was about to answer.

        The exemption is itself capped. `max_interrupt_turns` in the same state
        → `escalate_target`, deliberately *not* `on_null_exhausted`: a caller
        circling one state with price questions needs a human, not a silent
        default of «own tires». An uncapped escape from a loop-breaker is not a
        fix, it is the same unbounded loop one level up.

        `advance=False` is the shadow call and has the same contract as in
        `on_parser_null`: count, log, emit, do not move.
        """
        state = self.current_state()
        cfg = STATES[state]
        counts = self._interrupt_turn_counts()
        count = counts.get(state.value, 0) + 1
        counts[state.value] = count

        self._emit_interrupt_turn_metric(kind, state)
        logger.info(
            "fsm_interrupt_turn call=%s state=%s kind=%s attempt=%d/%d text=%r",
            self.session.channel_uuid,
            state.value,
            kind,
            count,
            cfg.max_interrupt_turns,
            (customer_text or "")[:100],
            extra={"call_id": str(self.session.channel_uuid)},
        )

        if not advance or count < cfg.max_interrupt_turns:
            return state

        counts.pop(state.value, None)
        return self.transition(
            cfg.escalate_target,
            event=FsmEvent.ESCALATE,
            payload={"kind": kind, "attempt": count, "reason": "interrupt_turns"},
        )

    def _emit_interrupt_turn_metric(self, kind: str, state: FsmState) -> None:
        if not _METRICS_AVAILABLE:
            return
        try:
            fsm_interrupt_turn_total.labels(state=state.value, kind=kind).inc()
        except Exception:
            logger.exception(
                "fitting_fsm: failed to emit fsm_interrupt_turn_total for %s/%s",
                state,
                kind,
            )

    def on_parser_null(
        self,
        field_name: str | None = None,
        customer_text: str = "",
        *,
        advance: bool = True,
    ) -> FsmState:
        """The state's parser produced nothing usable this turn (§3.7).

        Both `unresolved` («they said something we could not pin down») and
        `not_mentioned` («they said nothing about it») land here — the spec
        gives them the same PARSER_NULL row, and the difference is in how the
        re-ask is *phrased*, which is the LLM's job, not the router's.

        ::

            count = ++fsm_parser_null_counts[state]
            count <  max_parser_null → re-ask, stay put
            count >= max_parser_null → on_null_exhausted(), or
                                       ESCALATE → escalate_target

        Returns the state the caller ends up in. Staying put is a real answer
        and is returned as the current state, not as `None`.

        `advance=False` — observe only
        ------------------------------
        The counter, the metric and the log line still happen; the machine does
        not move. This is the shadow-mode call: shadow's whole contract is that
        it observes without reaching the customer, and an observer that walks
        itself into `escalate_target` lands in TERMINAL, where every later turn
        of that call is skipped and the observation stops. Since one WELCOME
        without a detected `intent` would spend the default budget of 3 in the
        first three turns, an advancing shadow would go blind on exactly the
        calls it exists to measure. The counter is deliberately still bumped:
        «how often would we have escalated» is the number the shadow period is
        being run to collect.
        """
        state = self.current_state()
        cfg = STATES[state]
        counts = self._null_counts()
        count = counts.get(state.value, 0) + 1
        counts[state.value] = count

        field = field_name or cfg.field_name or ""
        self._emit_parser_null_metric(field, state)
        # The customer's own words go into the *message*, never into `extra`:
        # `JSONFormatter` runs `sanitize_pii` on `record.getMessage()` only, so
        # an `extra={"text": ...}` would ship a raw phone number to the log
        # store untouched. Truncated to 100 chars — enough to see what the
        # parser choked on, short enough not to turn the log into a transcript.
        logger.info(
            "fsm_parser_null call=%s state=%s field=%s attempt=%d/%d text=%r",
            self.session.channel_uuid,
            state.value,
            field or "none",
            count,
            cfg.max_parser_null,
            (customer_text or "")[:100],
            extra={"call_id": str(self.session.channel_uuid)},
        )

        if not advance:
            logger.debug(
                "fitting_fsm: observe-only parser_null in %s (call %s) — the "
                "machine stays put",
                state,
                self.session.channel_uuid,
            )
            return state

        if count < cfg.max_parser_null:
            # Re-ask. The text is `question_template` / `silence_reprompt` of
            # this same state — the FSM never invents a new line for a retry.
            self.transition(
                state,
                event=FsmEvent.PARSER_NULL,
                payload={"field": field, "attempt": count, "action": "reask"},
            )
            return state

        if cfg.on_null_exhausted is not None:
            try:
                target = cfg.on_null_exhausted(self.session)
            except Exception:
                # A broken branch must not strand the caller in the state that
                # already failed them `max_parser_null` times. Escalate, and
                # make the defect visible with its traceback — `contextlib
                # .suppress` on this path is what `37fb2d0` cost us.
                logger.exception(
                    "fitting_fsm: on_null_exhausted raised for state %s (call %s) "
                    "— escalating to %s instead",
                    state,
                    self.session.channel_uuid,
                    cfg.escalate_target,
                )
                target = cfg.escalate_target
                return self.transition(
                    target,
                    event=FsmEvent.ESCALATE,
                    payload={"field": field, "attempt": count, "reason": "branch_failed"},
                )
            counts.pop(state.value, None)  # the branch answered it — budget spent
            return self.transition(
                target,
                event=FsmEvent.FIELD_FILLED,
                payload={"field": field, "attempt": count, "action": "null_exhausted"},
            )

        counts.pop(state.value, None)
        return self.transition(
            cfg.escalate_target,
            event=FsmEvent.ESCALATE,
            payload={"field": field, "attempt": count, "reason": "parser_null"},
        )

    def _emit_parser_null_metric(self, field: str, state: FsmState) -> None:
        if not _METRICS_AVAILABLE:
            return
        try:
            fsm_parser_null_total.labels(field=field or "none", state=state.value).inc()
        except Exception:
            logger.exception(
                "fitting_fsm: failed to emit fsm_parser_null_total for %s/%s", state, field
            )

    # --- Rendering ---

    def next_question(self, state: FsmState | None = None) -> str:
        """Question for `state` with placeholders filled from the session.

        When the state has a `fallback_question` and its session flag is set,
        that is what gets asked. Today that is BRAND only: after
        `max_parser_null` unheard brands the bot switches to the car *type*
        instead of asking «Яка марка вашого авто?» a third time (§3.7 row 23).
        """
        target = state or self.current_state()
        cfg = self.config(target)
        if cfg.fallback_question and self._fallback_active(target):
            return self.render(cfg.fallback_question)
        return self.render(cfg.question_template)

    def _fallback_active(self, state: FsmState) -> bool:
        """Is `state`'s `on_null_exhausted` fallback currently engaged?

        One state, one flag — deliberately not a generic dict. A second
        fallback would add a second named flag here, which keeps every one of
        them greppable instead of hiding behind a computed key.
        """
        if state is FsmState.BRAND:
            return bool(getattr(self.session, "fsm_brand_type_fallback", False))
        return False

    def _clear_fallback(self, state: FsmState) -> None:
        """Retire the fallback together with the counter that armed it.

        Same two moments as `fsm_parser_null_counts`: FIELD_FILLED for this
        state's own field, and leaving the state. A flag that outlives its
        counter would have BRAND asking for the car type on a later pass through
        the state, for a caller whose brand was heard the first time.
        """
        if state is FsmState.BRAND:
            self.session.fsm_brand_type_fallback = False

    def silence_reprompt(self, state: FsmState | None = None) -> str | None:
        """Per-state TIMEOUT text (Wave 5 targeted reprompts), if any."""
        template = self.config(state).silence_reprompt
        return self.render(template) if template else None

    def resume_phrase(self, state: FsmState | None = None) -> str:
        """Phrase spoken when the caller is brought back into `state`.

        Wave 3-B's interrupt handlers read this instead of inventing their own —
        a handler with a hardcoded «Продовжуємо запис» is what produced the
        five identical replies that got the first attempt reverted (`c8c6601`).
        """
        return self.render(self.config(state).resume_phrase)

    def render(self, template: str) -> str:
        """Substitute `{placeholder}` and `[placeholder]` from session context."""
        if not template:
            return ""
        context = self.render_context()
        missing: list[str] = []

        def _sub(match: re.Match[str]) -> str:
            key = match.group(1)
            value = context.get(key)
            if value in (None, ""):
                missing.append(key)
                return match.group(0)
            return str(value)

        rendered = _CURLY_PLACEHOLDER.sub(_sub, template)
        rendered = _BRACKET_PLACEHOLDER.sub(_sub, rendered)
        if missing:
            # An unresolved placeholder means the caller would hear a literal
            # «[date]». Visible at WARNING so it shows up before production does.
            logger.warning(
                "fitting_fsm: unresolved placeholders %s in state %s (call %s)",
                missing,
                self.current_state(),
                self.session.channel_uuid,
            )
        return rendered

    def render_context(self) -> dict[str, Any]:
        """Values available to `question_template` / `resume_phrase`.

        `fsm_filled_fields` wins; legacy `fitting_*` session fields are the
        fallback while both are written in parallel (§2.4 backward compat).
        """
        session = self.session
        context: dict[str, Any] = dict(session.fsm_filled_fields)
        context.setdefault("name", session.fitting_customer_name)
        context.setdefault("date", session.selected_fitting_date)
        context.setdefault("time", session.selected_fitting_time)
        context.setdefault("color", session.fitting_plate)
        context.setdefault("brand", session.fitting_vehicle_brand)
        context.setdefault("storage_choice", session.fitting_storage_choice)
        context.setdefault("diameter", session.fitting_diameter_client)

        stations = session.fitting_stations_seen or []
        context.setdefault("stations_count", len(stations) or None)
        districts = [s.get("district") or s.get("address") for s in stations]
        context.setdefault("districts", ", ".join(d for d in districts if d) or None)
        picked = next(
            (s for s in stations if s.get("id") == session.last_fitting_station_id),
            None,
        )
        context.setdefault("address", (picked or {}).get("address"))
        context.setdefault("city", (picked or {}).get("city"))

        slots = session.fitting_slots_offered or []
        context.setdefault(
            "slots", ", ".join(s.get("time", "") for s in slots if s.get("time")) or None
        )
        return {k: v for k, v in context.items() if v is not None}

    # --- Interrupts (Wave 4-B) ---

    def freeze_for_interrupt(self, event: FsmEvent) -> FsmState:
        """Snapshot the current main state before entering a side-state.

        Contract (§2.5, written by Wave 1-A and honoured here):

        * only freeze when `current_state()` is in `FROZEN_STATES`;
        * write the frozen state into `session.fsm_prev_state`;
        * transition to `INTERRUPT_TARGETS[event]`;
        * keep `fsm_filled_fields` intact — the caller must not redo the flow;
        * return the side-state that was entered.

        Refusing to freeze is a normal outcome, not an error: an interrupt fired
        from WELCOME/INTENT/BOOK/DONE has nothing to come back to, and pretending
        otherwise is exactly how `c8c6601` invited callers to continue a booking
        that never existed. The caller can tell the two apart by comparing the
        returned state with the one it passed in.
        """
        current = self.current_state()

        target = INTERRUPT_TARGETS.get(event)
        if target is None:
            # A new FsmEvent that nobody mapped is a wiring defect. It must be
            # visible, but it must not raise KeyError inside a live call.
            logger.error(
                "fsm_interrupt: no side-state mapped for event=%s (call %s) — not freezing",
                event,
                self.session.channel_uuid,
            )
            return current

        if current not in FROZEN_STATES:
            logger.info(
                "fsm_interrupt: event=%s prev_state=%s target=%s — refused, %s is not resumable",
                event.value,
                self.session.fsm_prev_state,
                target.value,
                current.value,
            )
            return current

        # First snapshot wins. An interrupt inside an interrupt must return the
        # caller to where the *booking* stopped, not to the previous side-state.
        if self.session.fsm_prev_state:
            logger.info(
                "fsm_interrupt: event=%s keeps the existing prev_state=%s (current=%s)",
                event.value,
                self.session.fsm_prev_state,
                current.value,
            )
        else:
            self.session.fsm_prev_state = current.value

        logger.info(
            "fsm_interrupt: event=%s prev_state=%s target=%s",
            event.value,
            self.session.fsm_prev_state,
            target.value,
        )
        # Same public transition every other move uses: history + metrics come
        # for free, and there is no second way of writing `fsm_state`.
        self.transition(target, event=event, payload={"frozen_from": self.session.fsm_prev_state})
        return target

    def resume(self, resume_target: FsmState | None = None) -> str:
        """Return from a side-state to the frozen main state.

        Contract (§2.5):

        * use `resume_target` when the table names one (row 40 →
          `FsmState.STORAGE`, row 44 → `FsmState.DATE`), otherwise
          `session.fsm_prev_state`;
        * with neither, go to `FsmState.DONE` — never speak a resume phrase for a
          booking that was never started (the `c8c6601` bug);
        * clear `fsm_prev_state` after resuming;
        * return `resume_phrase(target)` + the target's re-emitted question.

        The returned text is built from `StateConfig` only. Callers that already
        speak a resume phrase of their own (Wave 3-B's interrupt handlers do)
        must discard it rather than append it a second time.
        """
        prev = FsmState.coerce(self.session.fsm_prev_state)
        target = resume_target if resume_target is not None else prev

        if target is not None and target not in FROZEN_STATES:
            logger.error(
                "fsm_interrupt: resume target %s is not a resumable state (call %s) — "
                "going to DONE instead of parking the call in a side-state",
                target.value,
                self.session.channel_uuid,
            )
            target = None

        self.session.fsm_prev_state = None

        if target is None:
            # Nothing to come back to. Ending beats hanging: a call left in
            # PRICE_INTERRUPT answers every later turn from the side-state.
            logger.info(
                "fsm_interrupt: resume with no target for call %s — closing the flow, "
                "no resume phrase is spoken",
                self.session.channel_uuid,
            )
            self.transition(
                FsmState.DONE, event=FsmEvent.RESUME, payload={"reason": "nothing_to_resume"}
            )
            return ""

        logger.info(
            "fsm_interrupt: resume target=%s (call %s)", target.value, self.session.channel_uuid
        )
        self.transition(target, event=FsmEvent.RESUME, payload={"reason": "resume"})
        return self._resume_text(target)

    def _resume_text(self, target: FsmState) -> str:
        """`resume_phrase` for `target`, plus its question when that adds anything.

        Several `resume_phrase` values (CITY, STORAGE, DATE, COLOR, BRAND)
        already end in the state's own question. Appending `next_question`
        blindly would make the bot ask twice in one breath, which is the same
        "say it again" symptom the first attempt was reverted for.
        """
        phrase = self.resume_phrase(target)
        question = self.next_question(target)
        if question and question.strip() and question.strip() not in phrase:
            return f"{phrase} {question}".strip()
        return phrase


def _validate_states() -> None:
    """Fail loudly at import if the state table is incomplete or malformed."""
    missing = [s.value for s in FsmState if s not in STATES]
    if missing:
        raise RuntimeError(f"fitting_fsm: STATES is missing entries for {missing}")
    for state, cfg in STATES.items():
        if cfg.state is not state:
            raise RuntimeError(f"fitting_fsm: STATES[{state}] carries state={cfg.state}")
        if not cfg.resume_phrase.strip():
            # Wave 3-B reads this phrase; an empty one pushes the handler back to
            # hardcoding, which is the failure mode this refactor exists to fix.
            raise RuntimeError(f"fitting_fsm: STATES[{state}] has an empty resume_phrase")


_validate_states()
