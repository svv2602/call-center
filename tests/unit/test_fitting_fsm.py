"""Unit tests for the deterministic fitting FSM (`src/agent/fitting_fsm.py`).

Wave 2-A of the FSM refactor (`development-checklists/fsm-refactor-2026-09-07/`).

Two groups here are load-bearing rather than box-ticking:

* `TestResumePhrases` — the first attempt at this refactor was reverted
  (`c8c6601`) because the interrupt handler hardcoded «Продовжуємо запис» and
  spoke it even when no booking was in progress. The fix is structural: the
  phrase belongs to the state, `StateConfig.resume_phrase` is mandatory, and
  `_validate_states()` refuses to import a table with an empty one. These tests
  guard that structure, not the wording.
* `TestShadowSafety` — the gate for the D1 shadow deploy (`FSM_ENABLED=false`,
  engine runs in parallel and only logs for 24-48h). It must be impossible for
  the engine to change a turn's outcome while disabled, and any exception inside
  it must be contained *and* logged at ERROR. Silent containment is explicitly
  not acceptable: a `contextlib.suppress(Exception)` around a DB write once lost
  3 of 3 bookings with nothing in the logs.

Freeze/resume, interrupt stacking and `fsm_interrupt_total` are **not** tested
here — the mechanism lands in Wave 4-B. The only assertion about them is that
they are still unimplemented stubs, which is itself the `c8c6601` guard.
"""

from __future__ import annotations

import ast
import asyncio
import dataclasses
import inspect
import itertools
import json
import logging
import uuid
from typing import TYPE_CHECKING, Any

import pytest
from prometheus_client import REGISTRY

from src.agent import fitting_fsm
from src.agent.fitting_fsm import (
    FROZEN_STATES,
    INTERRUPT_TARGETS,
    MAIN_FLOW,
    MAX_AUTO_SKIP_HOPS,
    STATES,
    TERMINAL_STATES,
    TRANSITIONS,
    FsmEngine,
    FsmEvent,
    FsmState,
    StateConfig,
    find_transitions,
)
from src.config import FsmSettings
from src.core.call_session import FSM_HISTORY_LIMIT, CallSession

if TYPE_CHECKING:
    from collections.abc import Callable

FSM_LOGGER = "src.agent.fitting_fsm"

#: Fields the engine knows how to auto-skip on, in main-flow order.
FLOW_FIELDS: tuple[tuple[FsmState, str], ...] = (
    (FsmState.INTENT, "intent"),
    (FsmState.CITY, "city"),
    (FsmState.STATION, "station_id"),
    (FsmState.STORAGE, "storage_choice"),
    (FsmState.DATE, "date"),
    (FsmState.TIME, "time"),
    (FsmState.COLOR, "color"),
    (FsmState.BRAND, "brand"),
)


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


@pytest.fixture
def session() -> CallSession:
    """Fresh session with every `fsm_*` field at its empty default."""
    return CallSession(uuid.uuid4())


@pytest.fixture
def engine(session: CallSession) -> FsmEngine:
    """Engine over a fresh session. Instantiating it must not start the FSM."""
    return FsmEngine(session)


@pytest.fixture
def started(session: CallSession) -> FsmEngine:
    """Engine already moved into WELCOME via an explicit `start()`."""
    eng = FsmEngine(session)
    eng.start()
    return eng


@pytest.fixture
def at_state() -> Callable[[CallSession, FsmState], FsmEngine]:
    """Factory: put a session directly into `state` without walking there."""

    def _make(sess: CallSession, state: FsmState) -> FsmEngine:
        sess.fsm_state = state.value
        return FsmEngine(sess)

    return _make


def counter_value(name: str, labels: dict[str, str]) -> float:
    """Current value of a Prometheus counter sample (0.0 when never touched)."""
    return REGISTRY.get_sample_value(name, labels) or 0.0


def _runtime_string_literals(module: Any) -> list[str]:
    """Every string literal in `module` except docstrings."""
    tree = ast.parse(inspect.getsource(module))
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        first = node.body[0] if node.body else None
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            docstrings.add(id(first.value))
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


# --------------------------------------------------------------------------
# TestFsmStateEnum
# --------------------------------------------------------------------------


class TestFsmStateEnum:
    """The state enum and the tables keyed by it (§2.1)."""

    def test_all_fifteen_states_exist(self) -> None:
        # 12 main-flow + 3 side-states (§2.1). A change here is a design change.
        assert len(list(FsmState)) == 15
        assert set(MAIN_FLOW) | {
            FsmState.PRICE_INTERRUPT,
            FsmState.CANCEL_INTERRUPT,
            FsmState.TRANSFER,
        } == set(FsmState)

    def test_string_values_equal_member_names(self) -> None:
        # `STATES["CITY"]` and `STATES[FsmState.CITY]` must be the same key, and a
        # state dumped from Redis must be human-readable.
        for state in FsmState:
            assert state.value == state.name
            assert state.value.isupper()
            assert isinstance(state, str)

    def test_main_flow_ordering_is_linear_and_complete(self) -> None:
        assert MAIN_FLOW[0] is FsmState.WELCOME
        assert MAIN_FLOW[-1] is FsmState.DONE
        assert len(set(MAIN_FLOW)) == len(MAIN_FLOW) == 12
        # Every non-terminal main-flow state points at its successor.
        for current, following in itertools.pairwise(MAIN_FLOW):
            if STATES[current].terminal:
                continue
            assert STATES[current].next_state is following

    def test_every_state_has_a_config(self) -> None:
        assert set(STATES) == set(FsmState)
        for state, cfg in STATES.items():
            assert cfg.state is state

    def test_terminal_and_frozen_sets_are_disjoint(self) -> None:
        assert sorted(TERMINAL_STATES) == [FsmState.DONE, FsmState.TRANSFER]
        assert not (TERMINAL_STATES & FROZEN_STATES)
        # Only main-flow question states can be frozen by an interrupt (§2.5).
        assert FROZEN_STATES.issubset(MAIN_FLOW)
        assert len(FROZEN_STATES) < len(MAIN_FLOW)

    def test_coerce_accepts_names_case_insensitively(self) -> None:
        assert FsmState.coerce("CITY") is FsmState.CITY
        assert FsmState.coerce("city") is FsmState.CITY
        assert FsmState.coerce("  Date  ") is FsmState.DATE
        assert FsmState.coerce(FsmState.BOOK) is FsmState.BOOK

    def test_coerce_returns_none_and_warns_on_unknown(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING, logger=FSM_LOGGER):
            assert FsmState.coerce("HALF_RENAMED") is None
        assert FsmState.coerce(None) is None
        # A state that fell out of the enum means a rename went half-applied.
        assert any("unknown FSM state" in r.message for r in caplog.records)

    def test_interrupt_targets_cover_every_interrupt_event(self) -> None:
        interrupt_events = {e for e in FsmEvent if e.value.startswith("interrupt_")}
        assert set(INTERRUPT_TARGETS) == interrupt_events
        assert INTERRUPT_TARGETS[FsmEvent.INTERRUPT_RESCHED] is FsmState.CANCEL_INTERRUPT


# --------------------------------------------------------------------------
# TestFsmEngineInit
# --------------------------------------------------------------------------


class TestFsmEngineInit:
    """Construction, `start()`, and reading state back out of a session."""

    def test_fresh_session_has_no_fsm_state(self, session: CallSession) -> None:
        assert session.fsm_state is None
        assert session.fsm_prev_state is None
        assert session.fsm_filled_fields == {}
        assert session.fsm_history == []

    def test_construction_is_inert(self, session: CallSession) -> None:
        # The engine never starts by itself — Wave 4-B calls start() explicitly.
        FsmEngine(session)
        assert session.fsm_state is None
        assert session.fsm_history == []

    def test_current_state_defaults_to_welcome_without_starting(
        self, engine: FsmEngine, session: CallSession
    ) -> None:
        assert engine.current_state() is FsmState.WELCOME
        assert engine.is_started() is False
        # Reading must not pin the default into the session.
        assert session.fsm_state is None

    def test_start_pins_welcome_and_records_history(self, session: CallSession) -> None:
        eng = FsmEngine(session)
        assert eng.start() is FsmState.WELCOME
        assert session.fsm_state == "WELCOME"
        assert eng.is_started() is True
        assert len(session.fsm_history) == 1
        assert session.fsm_history[0]["to"] == "WELCOME"
        assert session.fsm_history[0]["from"] is None

    def test_start_at_explicit_state(self, session: CallSession) -> None:
        eng = FsmEngine(session)
        assert eng.start(FsmState.CITY) is FsmState.CITY
        assert eng.current_state() is FsmState.CITY

    def test_start_twice_does_not_rewind_a_recovered_session(self, session: CallSession) -> None:
        session.fsm_state = FsmState.TIME.value
        eng = FsmEngine(session)
        assert eng.start() is FsmState.TIME
        assert session.fsm_state == "TIME"
        assert session.fsm_history == []  # no transition was recorded

    def test_engine_reads_existing_state_from_session(self, session: CallSession) -> None:
        session.fsm_state = "confirm"  # lowercase, as a sloppy writer might store it
        eng = FsmEngine(session)
        assert eng.current_state() is FsmState.CONFIRM
        assert eng.config().field_name == "confirmed"

    def test_engine_resumes_a_from_dict_restored_session(self, session: CallSession) -> None:
        eng = FsmEngine(session)
        eng.start()
        eng.apply_field("intent", "fitting")
        eng.apply_field("city", "Дніпро")
        restored = CallSession.from_dict(json.loads(json.dumps(session.to_dict())))
        assert FsmEngine(restored).current_state() is eng.current_state()

    def test_config_and_is_terminal(self, session: CallSession) -> None:
        eng = FsmEngine(session)
        assert eng.config(FsmState.DATE).parser == "date_parser"
        assert eng.is_terminal() is False
        eng.start(FsmState.DONE)
        assert eng.is_terminal() is True


# --------------------------------------------------------------------------
# TestBasicTransitions
# --------------------------------------------------------------------------


class TestBasicTransitions:
    """Happy path WELCOME → … → DONE, one test per link."""

    def test_welcome_to_intent(self, started: FsmEngine, session: CallSession) -> None:
        started.transition(FsmState.INTENT)
        assert started.current_state() is FsmState.INTENT
        assert session.fsm_history[-1]["from"] == "WELCOME"

    def test_intent_to_city(self, session: CallSession, at_state: Callable[..., FsmEngine]) -> None:
        eng = at_state(session, FsmState.INTENT)
        assert eng.apply_field("intent", "fitting") is FsmState.CITY

    def test_city_to_station(
        self, session: CallSession, at_state: Callable[..., FsmEngine]
    ) -> None:
        eng = at_state(session, FsmState.CITY)
        assert eng.apply_field("city", "Дніпро") is FsmState.STATION
        assert session.fsm_filled_fields["city"] == "Дніпро"

    def test_station_to_storage(
        self, session: CallSession, at_state: Callable[..., FsmEngine]
    ) -> None:
        eng = at_state(session, FsmState.STATION)
        assert eng.apply_field("station_id", "000000012") is FsmState.STORAGE

    def test_storage_to_date(
        self, session: CallSession, at_state: Callable[..., FsmEngine]
    ) -> None:
        eng = at_state(session, FsmState.STORAGE)
        assert eng.apply_field("storage_choice", "own") is FsmState.DATE

    def test_date_to_time(self, session: CallSession, at_state: Callable[..., FsmEngine]) -> None:
        eng = at_state(session, FsmState.DATE)
        assert eng.apply_field("date", "2026-09-10") is FsmState.TIME

    def test_time_to_color(self, session: CallSession, at_state: Callable[..., FsmEngine]) -> None:
        eng = at_state(session, FsmState.TIME)
        assert eng.apply_field("time", "14:20") is FsmState.COLOR

    def test_color_to_brand(self, session: CallSession, at_state: Callable[..., FsmEngine]) -> None:
        eng = at_state(session, FsmState.COLOR)
        assert eng.apply_field("color", "синій") is FsmState.BRAND

    def test_brand_to_confirm(
        self, session: CallSession, at_state: Callable[..., FsmEngine]
    ) -> None:
        eng = at_state(session, FsmState.BRAND)
        assert eng.apply_field("brand", "Toyota") is FsmState.CONFIRM

    def test_confirm_to_book(
        self, session: CallSession, at_state: Callable[..., FsmEngine]
    ) -> None:
        eng = at_state(session, FsmState.CONFIRM)
        eng.transition(FsmState.BOOK, FsmEvent.CONFIRM_YES)
        assert eng.current_state() is FsmState.BOOK

    def test_book_to_done(self, session: CallSession, at_state: Callable[..., FsmEngine]) -> None:
        eng = at_state(session, FsmState.BOOK)
        eng.transition(FsmState.DONE, FsmEvent.TOOL_SUCCESS)
        assert eng.current_state() is FsmState.DONE
        assert eng.is_terminal() is True

    def test_full_happy_path_walk(self, session: CallSession) -> None:
        eng = FsmEngine(session)
        eng.start()
        for _, field_name in FLOW_FIELDS:
            eng.apply_field(field_name, f"{field_name}-value")
        assert eng.current_state() is FsmState.CONFIRM
        eng.transition(FsmState.BOOK, FsmEvent.CONFIRM_YES)
        eng.transition(FsmState.DONE, FsmEvent.TOOL_SUCCESS)
        assert eng.current_state() is FsmState.DONE
        visited = [h["to"] for h in session.fsm_history]
        assert visited[:4] == ["WELCOME", "INTENT", "CITY", "STATION"]

    def test_transition_records_event_and_payload(
        self, started: FsmEngine, session: CallSession
    ) -> None:
        started.transition(FsmState.TRANSFER, FsmEvent.INTERRUPT_TRANSFER, {"reason": "customer"})
        entry = session.fsm_history[-1]
        assert entry["event"] == "interrupt_transfer"
        assert entry["payload"] == {"reason": "customer"}
        assert isinstance(entry["t"], float)

    def test_transition_table_has_48_unique_rows(self) -> None:
        rows = [r.row for r in TRANSITIONS]
        assert rows == sorted(rows)
        assert len(rows) == len(set(rows)) == 48

    def test_find_transitions_prefers_exact_guard_then_wildcard(self) -> None:
        # BOOK/TOOL_ERROR fans out into the Waves 4C→12 guards (rows 28-35).
        all_book_errors = [r.row for r in find_transitions(FsmState.BOOK, FsmEvent.TOOL_ERROR)]
        assert all_book_errors == [28, 29, 30, 31, 32, 33, 34, 35]
        weekday = find_transitions(FsmState.BOOK, FsmEvent.TOOL_ERROR, "weekday_mismatch")
        assert [r.row for r in weekday] == [33]
        assert weekday[0].to_state is FsmState.DATE
        # Row 46 is a wildcard: TIMEOUT applies from every state.
        assert [r.row for r in find_transitions(FsmState.CITY, FsmEvent.TIMEOUT)] == [46]


# --------------------------------------------------------------------------
# TestApplyFieldAutoSkip
# --------------------------------------------------------------------------


class TestApplyFieldAutoSkip:
    """`apply_field` + the auto-skip chain (compound pre-parse's payoff)."""

    def test_no_prefill_stops_at_city(self, session: CallSession) -> None:
        eng = FsmEngine(session)
        eng.start()
        assert eng.apply_field("intent", "fitting") is FsmState.CITY

    def test_prefilled_city_skips_city(
        self, session: CallSession, at_state: Callable[..., FsmEngine]
    ) -> None:
        session.fsm_filled_fields["city"] = "Дніпро"
        eng = at_state(session, FsmState.INTENT)
        assert eng.apply_field("intent", "fitting") is FsmState.STATION

    def test_prefilled_city_and_station_skips_both(
        self, session: CallSession, at_state: Callable[..., FsmEngine]
    ) -> None:
        session.fsm_filled_fields.update({"city": "Дніпро", "station_id": "000000012"})
        eng = at_state(session, FsmState.INTENT)
        assert eng.apply_field("intent", "fitting") is FsmState.STORAGE

    def test_prefilled_through_storage_lands_on_date(
        self, session: CallSession, at_state: Callable[..., FsmEngine]
    ) -> None:
        session.fsm_filled_fields.update(
            {"city": "Дніпро", "station_id": "000000012", "storage_choice": "own"}
        )
        eng = at_state(session, FsmState.INTENT)
        assert eng.apply_field("intent", "fitting") is FsmState.DATE

    def test_prefilled_through_time_lands_on_color(
        self, session: CallSession, at_state: Callable[..., FsmEngine]
    ) -> None:
        session.fsm_filled_fields.update(
            {
                "city": "Дніпро",
                "station_id": "000000012",
                "storage_choice": "own",
                "date": "2026-09-10",
                "time": "14:20",
            }
        )
        eng = at_state(session, FsmState.INTENT)
        assert eng.apply_field("intent", "fitting") is FsmState.COLOR

    def test_everything_prefilled_lands_on_confirm_not_book(
        self, session: CallSession, at_state: Callable[..., FsmEngine]
    ) -> None:
        session.fsm_filled_fields.update({name: "x" for _, name in FLOW_FIELDS})
        eng = at_state(session, FsmState.INTENT)
        # CONFIRM has no auto_skip_if: the caller is always read the summary.
        assert eng.apply_field("intent", "fitting") is FsmState.CONFIRM

    def test_single_station_in_city_is_auto_pinned(
        self, session: CallSession, at_state: Callable[..., FsmEngine]
    ) -> None:
        session.fitting_station_ids = {"000000012"}
        eng = at_state(session, FsmState.CITY)
        # Only one station → do not ask which one (§2.3).
        assert eng.apply_field("city", "Черкаси") is FsmState.STORAGE

    def test_two_stations_still_ask(
        self, session: CallSession, at_state: Callable[..., FsmEngine]
    ) -> None:
        session.fitting_station_ids = {"000000012", "000000022"}
        eng = at_state(session, FsmState.CITY)
        assert eng.apply_field("city", "Київ") is FsmState.STATION

    def test_empty_string_does_not_count_as_filled(
        self, session: CallSession, at_state: Callable[..., FsmEngine]
    ) -> None:
        session.fsm_filled_fields["city"] = ""
        eng = at_state(session, FsmState.INTENT)
        assert eng.apply_field("intent", "fitting") is FsmState.CITY

    def test_none_does_not_count_as_filled(
        self, session: CallSession, at_state: Callable[..., FsmEngine]
    ) -> None:
        session.fsm_filled_fields["city"] = None
        eng = at_state(session, FsmState.INTENT)
        assert eng.apply_field("intent", "fitting") is FsmState.CITY

    def test_auto_skip_records_a_reason_in_history(
        self, session: CallSession, at_state: Callable[..., FsmEngine]
    ) -> None:
        session.fsm_filled_fields["city"] = "Дніпро"
        eng = at_state(session, FsmState.INTENT)
        eng.apply_field("intent", "fitting")
        skips = [
            h for h in session.fsm_history if (h["payload"] or {}).get("reason") == "auto_skip"
        ]
        assert [s["payload"]["skipped"] for s in skips] == ["CITY"]

    def test_self_looping_state_does_not_spin(
        self, session: CallSession, at_state: Callable[..., FsmEngine]
    ) -> None:
        # PRICE_INTERRUPT.next_state is itself — the chain must stop immediately.
        eng = at_state(session, FsmState.PRICE_INTERRUPT)
        assert eng.apply_field("diameter", 18) is FsmState.PRICE_INTERRUPT
        assert len(session.fsm_history) <= MAX_AUTO_SKIP_HOPS

    def test_apply_field_advances_current_state_regardless_of_field_owner(
        self, session: CallSession, at_state: Callable[..., FsmEngine]
    ) -> None:
        # Documented footgun for Wave 2-B: apply_field() advances the *current*
        # state, it does not look up which state owns `field_name`. Compound
        # pre-parse must therefore write fsm_filled_fields directly and let
        # auto-skip do the walking, not call apply_field once per parsed field.
        eng = at_state(session, FsmState.CITY)
        assert eng.apply_field("color", "синій") is FsmState.STATION
        assert session.fsm_filled_fields == {"color": "синій"}


# --------------------------------------------------------------------------
# TestResumePhrases  (regression guard on the c8c6601 revert)
# --------------------------------------------------------------------------


class TestResumePhrases:
    """Every state owns its resume phrase; none of them is a generic default."""

    def test_every_state_has_a_non_empty_resume_phrase(self) -> None:
        for state in FsmState:
            phrase = STATES[state].resume_phrase
            assert phrase and phrase.strip(), f"{state} has no resume phrase"

    def test_resume_phrase_is_a_mandatory_field(self) -> None:
        spec = {f.name: f for f in dataclasses.fields(StateConfig)}["resume_phrase"]
        assert spec.default is dataclasses.MISSING
        assert spec.default_factory is dataclasses.MISSING
        with pytest.raises(TypeError):
            StateConfig(  # type: ignore[call-arg]
                state=FsmState.CITY,
                field_name="city",
                question_template="?",
                silence_reprompt=None,
                parser="city_parser",
            )

    def test_import_time_validation_rejects_a_blank_resume_phrase(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        blank = dataclasses.replace(STATES[FsmState.CITY], resume_phrase="   ")
        monkeypatch.setitem(STATES, FsmState.CITY, blank)
        with pytest.raises(RuntimeError, match="empty resume_phrase"):
            fitting_fsm._validate_states()

    def test_phrases_are_state_specific_not_one_generic_line(self) -> None:
        phrases = [STATES[s].resume_phrase for s in FsmState]
        # The revert (c8c6601) was one hardcoded line replayed everywhere. A
        # near-1:1 phrase-to-state ratio is what makes a resume audible.
        assert len(set(phrases)) >= len(phrases) - 1

    def test_no_unconditional_prodovzhuemo_zapys_in_the_engine(self) -> None:
        # The line the first attempt prefixed onto every reply must not exist as
        # a runtime string anywhere in the engine — not in a state config, not in
        # a fallback branch. Docstrings explaining the bug are allowed.
        for literal in _runtime_string_literals(fitting_fsm):
            assert "Продовжуємо запис" not in literal, f"hardcoded resume phrase: {literal!r}"
        assert all(STATES[s].resume_phrase != "Продовжуємо запис" for s in FsmState)

    def test_engine_renders_every_resume_phrase_non_empty(self, engine: FsmEngine) -> None:
        # A phrase that renders to "" on an empty session would silently degrade
        # into "no phrase at all" for Wave 3-B.
        for state in FsmState:
            assert engine.resume_phrase(state).strip()

    def test_resume_phrase_defaults_to_current_state(
        self, session: CallSession, at_state: Callable[..., FsmEngine]
    ) -> None:
        eng = at_state(session, FsmState.BRAND)
        assert eng.resume_phrase() == STATES[FsmState.BRAND].resume_phrase

    def test_frozen_states_all_carry_a_booking_specific_phrase(self) -> None:
        # These are exactly the states an interrupt can return the caller to.
        for state in FROZEN_STATES:
            phrase = STATES[state].resume_phrase
            assert "Поверта" in phrase or "Перевір" in phrase, f"{state}: {phrase}"


# --------------------------------------------------------------------------
# TestCompoundParseIntegration  (Wave 2-B — skipped until that module lands)
# --------------------------------------------------------------------------


class TestCompoundParseIntegration:
    """compound_parse → bulk prefill → auto-skip.

    Wave 2-B writes `src/agent/compound_parse.py` in parallel; until it merges
    these skip rather than fail.
    """

    @pytest.fixture
    def compound(self) -> Any:
        return pytest.importorskip(
            "src.agent.compound_parse",
            reason="Wave 2-B (compound-parse) not merged yet",
        )

    @staticmethod
    def _prefill(
        session: CallSession,
        fields: dict[str, Any],
        min_confidence: float = 0.7,
        confidence: dict[str, float] | None = None,
    ) -> None:
        """Apply only FSM-known fields above the confidence floor (§2.6)."""
        known = {name for _, name in FLOW_FIELDS}
        confidence = confidence or {}
        for name, value in fields.items():
            if name in known and confidence.get(name, 1.0) >= min_confidence:
                session.fsm_filled_fields[name] = value

    def test_result_contract(self, compound: Any) -> None:
        result = compound.compound_parse("шиномонтаж R18 в Дніпрі")
        assert isinstance(result.fields, dict)
        assert isinstance(result.fields_confidence, dict)

    def test_city_and_diameter_utterance_starts_at_station(
        self, compound: Any, session: CallSession, at_state: Callable[..., FsmEngine]
    ) -> None:
        result = compound.compound_parse("шиномонтаж R18 в Дніпрі")
        assert result.fields.get("city")
        self._prefill(session, result.fields, confidence=result.fields_confidence)
        eng = at_state(session, FsmState.INTENT)
        assert eng.apply_field("intent", "fitting") is FsmState.STATION

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "Wave 4-A owes a mapping layer. compound_parse emits contract keys "
            "(date_hint/time_hint/station_hint) while the FSM states own "
            "date/time/station_id, so hints are dropped by a name-filtered "
            "prefill; and 'шини з собою' yields no storage_choice at all "
            "because storage is absent from the Wave 2-B key list. Flips to "
            "XPASS the moment that mapping lands."
        ),
    )
    def test_city_storage_date_utterance_skips_to_time(
        self, compound: Any, session: CallSession, at_state: Callable[..., FsmEngine]
    ) -> None:
        result = compound.compound_parse("на монтаж у Дніпрі, шини з собою, на 5 серпня")
        self._prefill(session, result.fields, confidence=result.fields_confidence)
        session.fsm_filled_fields.setdefault("station_id", "000000012")
        eng = at_state(session, FsmState.INTENT)
        assert eng.apply_field("intent", "fitting") in {FsmState.DATE, FsmState.TIME}

    def test_low_confidence_fields_are_not_applied(
        self, compound: Any, session: CallSession, at_state: Callable[..., FsmEngine]
    ) -> None:
        self._prefill(
            session,
            {"city": "Дніпро"},
            confidence={"city": 0.4},
        )
        eng = at_state(session, FsmState.INTENT)
        assert eng.apply_field("intent", "fitting") is FsmState.CITY

    def test_empty_utterance_yields_no_skips(
        self, compound: Any, session: CallSession, at_state: Callable[..., FsmEngine]
    ) -> None:
        result = compound.compound_parse("")
        self._prefill(session, result.fields, confidence=result.fields_confidence)
        eng = at_state(session, FsmState.INTENT)
        assert eng.apply_field("intent", "fitting") is FsmState.CITY


# --------------------------------------------------------------------------
# TestPersistenceRoundtrip
# --------------------------------------------------------------------------


class TestPersistenceRoundtrip:
    """The engine is stateless; the session lives in Redis (§2.4)."""

    def test_roundtrip_preserves_all_fsm_fields(self, session: CallSession) -> None:
        eng = FsmEngine(session)
        eng.start()
        eng.apply_field("intent", "fitting")
        eng.apply_field("city", "Дніпро")
        session.fsm_prev_state = FsmState.STORAGE.value

        restored = CallSession.from_dict(json.loads(json.dumps(session.to_dict())))
        assert restored.fsm_state == session.fsm_state
        assert restored.fsm_filled_fields == session.fsm_filled_fields
        assert restored.fsm_prev_state == FsmState.STORAGE.value
        assert restored.fsm_history == session.fsm_history

    def test_restored_session_continues_the_walk(self, session: CallSession) -> None:
        eng = FsmEngine(session)
        eng.start(FsmState.DATE)
        restored = CallSession.from_dict(session.to_dict())
        eng2 = FsmEngine(restored)
        assert eng2.current_state() is FsmState.DATE
        assert eng2.apply_field("date", "2026-09-10") is FsmState.TIME

    def test_roundtrip_caps_history_at_the_limit(self, session: CallSession) -> None:
        eng = FsmEngine(session)
        for _ in range(FSM_HISTORY_LIMIT * 2):
            eng.transition(FsmState.CITY, FsmEvent.TIMEOUT)
        restored = CallSession.from_dict(session.to_dict())
        assert len(restored.fsm_history) == FSM_HISTORY_LIMIT

    def test_history_entries_are_json_serialisable(self, session: CallSession) -> None:
        eng = FsmEngine(session)
        eng.start()
        eng.apply_field("intent", "fitting")
        # Redis stores JSON: an enum leaking into a payload would break the write.
        assert json.loads(json.dumps(session.fsm_history)) == session.fsm_history


# --------------------------------------------------------------------------
# TestMetricsEmitted
# --------------------------------------------------------------------------


class TestMetricsEmitted:
    """Prometheus counters (Wave 1-C) — the shadow deploy's only output."""

    def test_state_entered_counter_increments(self, session: CallSession) -> None:
        before = counter_value("callcenter_fsm_state_entered_total", {"state": "STORAGE"})
        FsmEngine(session).transition(FsmState.STORAGE)
        after = counter_value("callcenter_fsm_state_entered_total", {"state": "STORAGE"})
        assert after == before + 1

    def test_transition_counter_carries_from_and_to_labels(
        self, session: CallSession, at_state: Callable[..., FsmEngine]
    ) -> None:
        labels = {"from_state": "COLOR", "to_state": "BRAND"}
        before = counter_value("callcenter_fsm_transition_total", labels)
        at_state(session, FsmState.COLOR).apply_field("color", "синій")
        assert counter_value("callcenter_fsm_transition_total", labels) == before + 1

    def test_start_labels_the_missing_source_as_none(self, session: CallSession) -> None:
        labels = {"from_state": "none", "to_state": "WELCOME"}
        before = counter_value("callcenter_fsm_transition_total", labels)
        FsmEngine(session).start()
        assert counter_value("callcenter_fsm_transition_total", labels) == before + 1

    def test_auto_skips_are_counted_too(
        self, session: CallSession, at_state: Callable[..., FsmEngine]
    ) -> None:
        session.fsm_filled_fields["city"] = "Дніпро"
        labels = {"from_state": "CITY", "to_state": "STATION"}
        before = counter_value("callcenter_fsm_transition_total", labels)
        at_state(session, FsmState.INTENT).apply_field("intent", "fitting")
        assert counter_value("callcenter_fsm_transition_total", labels) == before + 1


# --------------------------------------------------------------------------
# TestEdgeCases
# --------------------------------------------------------------------------


class TestEdgeCases:
    """Malformed input, limits, and concurrency."""

    def test_transition_to_unknown_state_raises(self, started: FsmEngine) -> None:
        with pytest.raises(ValueError, match="unknown FSM target state"):
            started.transition("NOT_A_STATE")  # type: ignore[arg-type]

    def test_failed_transition_leaves_state_untouched(
        self, started: FsmEngine, session: CallSession
    ) -> None:
        with pytest.raises(ValueError):
            started.transition("NOT_A_STATE")  # type: ignore[arg-type]
        assert session.fsm_state == "WELCOME"
        assert len(session.fsm_history) == 1

    def test_apply_field_with_empty_name_raises(self, started: FsmEngine) -> None:
        # A silent no-op here would look exactly like a parser that never fires.
        with pytest.raises(ValueError, match="non-empty field name"):
            started.apply_field("", "x")

    def test_apply_field_with_unknown_name_still_advances(
        self, started: FsmEngine, session: CallSession
    ) -> None:
        # The engine does not police field names — the parser layer owns that.
        assert started.apply_field("nonexistent_field", 1) is FsmState.INTENT
        assert session.fsm_filled_fields["nonexistent_field"] == 1

    def test_history_is_trimmed_to_twenty_entries(self, session: CallSession) -> None:
        eng = FsmEngine(session)
        for _ in range(50):
            eng.transition(FsmState.DATE, FsmEvent.TIMEOUT)
        assert len(session.fsm_history) == FSM_HISTORY_LIMIT == 20

    def test_history_keeps_the_newest_entries(self, session: CallSession) -> None:
        eng = FsmEngine(session)
        for i in range(30):
            eng.transition(FsmState.DATE, FsmEvent.TIMEOUT, {"i": i})
        assert [h["payload"]["i"] for h in session.fsm_history] == list(range(10, 30))

    def test_reentering_the_same_state_is_allowed(
        self, session: CallSession, at_state: Callable[..., FsmEngine]
    ) -> None:
        eng = at_state(session, FsmState.DATE)
        eng.transition(FsmState.DATE, FsmEvent.PARSER_NULL)
        assert eng.current_state() is FsmState.DATE
        assert session.fsm_history[-1]["from"] == "DATE"

    def test_find_transitions_returns_empty_for_an_impossible_pair(self) -> None:
        assert find_transitions(FsmState.DONE, FsmEvent.CONFIRM_YES) == []

    def test_missing_context_lists_unfilled_required_fields(
        self, session: CallSession, at_state: Callable[..., FsmEngine]
    ) -> None:
        eng = at_state(session, FsmState.TIME)
        assert eng.missing_context() == ["date", "station_id"]
        session.fsm_filled_fields["date"] = "2026-09-10"
        assert eng.missing_context() == ["station_id"]

    def test_render_keeps_unresolved_placeholders_and_warns(
        self, session: CallSession, caplog: pytest.LogCaptureFixture
    ) -> None:
        eng = FsmEngine(session)
        with caplog.at_level(logging.WARNING, logger=FSM_LOGGER):
            rendered = eng.render("На [date] вільно: [slots].")
        assert "[date]" in rendered and "[slots]" in rendered
        assert any("unresolved placeholders" in r.message for r in caplog.records)

    def test_render_substitutes_from_legacy_session_fields(self, session: CallSession) -> None:
        session.selected_fitting_date = "2026-09-10"
        session.fitting_customer_name = "Олена"
        eng = FsmEngine(session)
        assert eng.render("{name}, на [date]?") == "Олена, на 2026-09-10?"

    def test_fsm_filled_fields_win_over_legacy_fields(self, session: CallSession) -> None:
        session.selected_fitting_date = "2026-09-01"
        session.fsm_filled_fields["date"] = "2026-09-10"
        assert FsmEngine(session).render("[date]") == "2026-09-10"

    def test_render_of_empty_template_is_empty(self, engine: FsmEngine) -> None:
        assert engine.render("") == ""
        assert engine.next_question(FsmState.BOOK) == ""
        assert engine.silence_reprompt(FsmState.BOOK) is None

    async def test_concurrent_transitions_do_not_corrupt_state(self, session: CallSession) -> None:
        # `transition()` is synchronous, so two coroutines cannot interleave
        # inside it: the session ends on one of the two targets with a complete
        # history and no half-written state. NB: Wave 1-A has no lock, so the
        # checklist's "the loser raises" is *not* implemented — the invariant
        # this asserts is the weaker (and real) one.
        eng = FsmEngine(session)
        eng.start()

        async def go(target: FsmState) -> None:
            await asyncio.sleep(0)
            eng.transition(target)

        await asyncio.gather(go(FsmState.CITY), go(FsmState.STATION))
        assert FsmState.coerce(session.fsm_state) in {FsmState.CITY, FsmState.STATION}
        assert [h["to"] for h in session.fsm_history[1:]] == ["CITY", "STATION"]

    def test_states_table_validation_catches_a_missing_entry(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delitem(STATES, FsmState.CITY)
        with pytest.raises(RuntimeError, match="missing entries"):
            fitting_fsm._validate_states()

    def test_states_table_validation_catches_a_mislabelled_config(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        wrong = dataclasses.replace(STATES[FsmState.CITY], state=FsmState.DATE)
        monkeypatch.setitem(STATES, FsmState.CITY, wrong)
        with pytest.raises(RuntimeError, match="carries state"):
            fitting_fsm._validate_states()


# --------------------------------------------------------------------------
# TestShadowSafety  (gate for the D1 shadow deploy)
# --------------------------------------------------------------------------


class TestShadowSafety:
    """With `FSM_ENABLED=false` the engine must be observably inert."""

    def test_flags_default_to_off_and_shadow(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # These defaults *are* the rollback path — a prior attempt shipped
        # without a flag and had to be reverted with git.
        assert FsmSettings.model_fields["enabled"].default is False
        assert FsmSettings.model_fields["shadow_mode"].default is True
        for var in ("FSM_ENABLED", "FSM_SHADOW_MODE", "FSM_LOG_TRANSITIONS", "FSM_ENABLED_TENANTS"):
            monkeypatch.delenv(var, raising=False)
        cfg = FsmSettings()
        assert cfg.enabled is False
        assert cfg.shadow_mode is True
        assert cfg.log_transitions is True
        assert cfg.enabled_tenant_list == []

    def test_engine_has_no_implicit_entry_point(self) -> None:
        # The only way in is an explicit FsmEngine(...) + start(). No dataclass
        # __post_init__, no import of the pipeline that could hook itself in.
        assert not hasattr(FsmEngine, "__post_init__")
        assert [f.name for f in dataclasses.fields(FsmEngine)] == ["session"]
        source = inspect.getsource(fitting_fsm)
        assert "core.pipeline" not in source
        assert "streaming_loop" not in source

    def test_read_only_use_never_mutates_the_session(self, session: CallSession) -> None:
        before = json.dumps(session.to_dict(), sort_keys=True, default=str)
        eng = FsmEngine(session)
        eng.current_state()
        eng.is_started()
        eng.is_terminal()
        eng.config()
        eng.missing_context()
        eng.next_question(FsmState.CITY)
        eng.silence_reprompt(FsmState.CITY)
        eng.resume_phrase(FsmState.CITY)
        assert json.dumps(session.to_dict(), sort_keys=True, default=str) == before

    def test_a_full_shadow_walk_touches_only_fsm_fields(self, session: CallSession) -> None:
        before = {k: v for k, v in vars(session).items() if not k.startswith("fsm_")}
        before_snapshot = json.dumps(before, sort_keys=True, default=str)

        eng = FsmEngine(session)
        eng.start()
        for _, field_name in FLOW_FIELDS:
            eng.apply_field(field_name, "x")
        eng.transition(FsmState.BOOK, FsmEvent.CONFIRM_YES)

        after = {k: v for k, v in vars(session).items() if not k.startswith("fsm_")}
        # No legacy fitting_* field, no dialog turn, no transfer flag was written:
        # a shadow run cannot change what the customer hears.
        assert json.dumps(after, sort_keys=True, default=str) == before_snapshot
        assert session.fitting_booked is False
        assert session.transferred is False
        assert session.dialog_history == []

    def test_auto_skip_exception_is_contained_and_logged_at_error(
        self,
        session: CallSession,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
        at_state: Callable[..., FsmEngine],
    ) -> None:
        def boom(_session: CallSession) -> bool:
            raise RuntimeError("parser blew up")

        monkeypatch.setitem(
            STATES, FsmState.CITY, dataclasses.replace(STATES[FsmState.CITY], auto_skip_if=boom)
        )
        eng = at_state(session, FsmState.INTENT)
        with caplog.at_level(logging.DEBUG, logger=FSM_LOGGER):
            result = eng.apply_field("intent", "fitting")

        assert result is FsmState.CITY  # contained: engine stays put
        errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert errors, "auto_skip_if failure was swallowed without an ERROR log"
        assert errors[0].exc_info is not None  # stack trace, not just a message

    def test_metrics_failure_is_contained_and_logged_at_error(
        self,
        session: CallSession,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        class Exploding:
            def labels(self, **_kwargs: Any) -> Any:
                raise RuntimeError("counter is broken")

        monkeypatch.setattr(fitting_fsm, "fsm_state_entered_total", Exploding())
        eng = FsmEngine(session)
        with caplog.at_level(logging.DEBUG, logger=FSM_LOGGER):
            eng.start()  # must not raise into the pipeline

        assert session.fsm_state == "WELCOME"  # the transition still happened
        errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert errors, "broken counter was hidden at DEBUG"
        assert errors[0].exc_info is not None

    def test_no_silent_exception_swallowing_anywhere_in_the_module(self) -> None:
        # Incident guard: `contextlib.suppress(Exception)` + a DEBUG log around a
        # critical write once lost 3 of 3 bookings with nothing in the logs.
        source = inspect.getsource(fitting_fsm)
        assert "suppress(" not in source

        tree = ast.parse(source)
        for handler in ast.walk(tree):
            if not isinstance(handler, ast.ExceptHandler):
                continue
            logged_loudly = any(
                isinstance(node, ast.Attribute)
                and node.attr in {"exception", "error", "critical", "warning"}
                for node in ast.walk(ast.Module(body=handler.body, type_ignores=[]))
            )
            reraises = any(
                isinstance(node, ast.Raise)
                for node in ast.walk(ast.Module(body=handler.body, type_ignores=[]))
            )
            assert logged_loudly or reraises, (
                f"except handler at line {handler.lineno} neither logs nor re-raises"
            )

    def test_interrupt_handlers_cannot_ship_before_the_machine(self, session: CallSession) -> None:
        # This ordering is the whole point of the second attempt: Wave 4-B fills
        # these in. If either silently starts returning a string before then,
        # the c8c6601 «Продовжуємо запис» bug is back.
        eng = FsmEngine(session)
        with pytest.raises(NotImplementedError, match="Wave 4-B"):
            eng.freeze_for_interrupt(FsmEvent.INTERRUPT_PRICE)
        with pytest.raises(NotImplementedError, match="Wave 4-B"):
            eng.resume()
        assert session.fsm_prev_state is None


# --------------------------------------------------------------------------
# TestAnchorCases  (tester complaints of 2026-09-07, expressed as FSM facts)
# --------------------------------------------------------------------------


class TestAnchorCases:
    """Real production regressions, restated as state-machine invariants (§2.9)."""

    def test_compound_city_and_diameter_starts_at_station(
        self, session: CallSession, at_state: Callable[..., FsmEngine]
    ) -> None:
        # «шиномонтаж R18 в Дніпрі» — city + diameter arrive in the first turn;
        # the prompt flow used to re-ask the city anyway.
        session.fsm_filled_fields.update({"city": "Дніпро", "diameter": 18})
        session.fitting_station_ids = {"000000012", "000000022"}
        eng = at_state(session, FsmState.INTENT)
        assert eng.apply_field("intent", "fitting") is FsmState.STATION

    def test_na_seredu_from_date_advances_to_time(
        self, session: CallSession, at_state: Callable[..., FsmEngine]
    ) -> None:
        # «на середу» answered in DATE must move the flow on, not re-ask.
        eng = at_state(session, FsmState.DATE)
        assert eng.apply_field("date", "2026-09-09") is FsmState.TIME
        assert session.fsm_filled_fields["date"] == "2026-09-09"

    def test_date_state_has_no_default_and_never_self_fills(
        self, session: CallSession, at_state: Callable[..., FsmEngine]
    ) -> None:
        # Anchor 1 (Wave 8 anti-pattern «пропоную завтра»): nothing in the engine
        # may put a date into the session on the customer's behalf.
        cfg = STATES[FsmState.DATE]
        assert cfg.auto_skip_if(session) is False
        eng = at_state(session, FsmState.DATE)
        eng.transition(FsmState.DATE, FsmEvent.PARSER_NULL)
        assert "date" not in session.fsm_filled_fields
        assert session.selected_fitting_date is None
        assert eng.current_state() is FsmState.DATE

    def test_parser_null_in_date_re_asks_instead_of_advancing(self) -> None:
        rows = find_transitions(FsmState.DATE, FsmEvent.PARSER_NULL)
        assert [r.row for r in rows] == [14]
        assert rows[0].to_state is FsmState.DATE  # row 14 — never offer a default

    def test_time_state_cannot_forget_date_or_station(
        self, session: CallSession, at_state: Callable[..., FsmEngine]
    ) -> None:
        # Anchor 2 («14:20 не в списку», state loss on 2026-08-31).
        assert STATES[FsmState.TIME].required_context == ("date", "station_id")
        assert STATES[FsmState.TIME].entry_tool == "get_fitting_slots"
        session.fsm_filled_fields.update({"date": "2026-08-31", "station_id": "000000012"})
        assert at_state(session, FsmState.TIME).missing_context() == []

    def test_price_interrupt_resumes_at_storage_not_city(self) -> None:
        # Wave 3 #4 regression: after the price detour the bot re-asked the city
        # it had already pinned. Row 40 hard-codes the resume target.
        rows = find_transitions(FsmState.PRICE_INTERRUPT, FsmEvent.CONFIRM_YES)
        assert [r.row for r in rows] == [40]
        assert rows[0].to_state is FsmState.STORAGE
        assert rows[0].to_state is not FsmState.CITY

    def test_transfer_only_on_an_explicit_customer_request(self) -> None:
        # Waves 15/16: the LLM probed several reasons to hand off mid-booking.
        # In the table TRANSFER is reachable from a mid-flow state only via the
        # explicit keyword (row 48) or the escalation counter (row 47).
        from_booking = [
            r
            for r in TRANSITIONS
            if r.to_state is FsmState.TRANSFER and r.from_state in FROZEN_STATES
        ]
        assert from_booking == []
        wildcard_rows = {
            r.row for r in TRANSITIONS if r.to_state is FsmState.TRANSFER and r.from_state is None
        }
        assert wildcard_rows == {47, 48}

    def test_book_errors_never_bounce_the_caller_back_to_city(self) -> None:
        # Waves 9/10 cross-city + past_krok_2 guards: a book_fitting failure may
        # rewind at most to STATION.
        targets = {r.to_state for r in find_transitions(FsmState.BOOK, FsmEvent.TOOL_ERROR)}
        assert FsmState.CITY not in targets
        assert FsmState.STATION in targets
