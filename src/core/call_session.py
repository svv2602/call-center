"""Call session state machine and Redis persistence.

Manages the lifecycle of a single phone call through states:
  Connected → Greeting → Listening → Processing → Speaking → Listening (cycle)
                                                 → Transferring → Ended
  Listening → Timeout (15s) → prompt → Timeout (15s) → Ended
"""

from __future__ import annotations

import enum
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from redis.asyncio import Redis

logger = logging.getLogger(__name__)

# Session constants
SESSION_KEY_PREFIX = "call_session"
SESSION_TTL = 1800  # 30 minutes
SILENCE_TIMEOUT_SEC = 18
MAX_TIMEOUTS_BEFORE_HANGUP = 3
# Number of consecutive empty LLM responses before escalating to the
# operator-transfer template. Below the threshold we ask the caller to
# repeat with a gentle prompt instead of announcing a transfer.
MAX_EMPTY_RESPONSES_BEFORE_ESCALATE = 3
# Ring-buffer cap for CallSession.fsm_history. Keeps the Redis payload bounded —
# the history is a debugging/regression-detection aid, not an audit log.
FSM_HISTORY_LIMIT = 20


class CallState(enum.StrEnum):
    """States of a call session."""

    CONNECTED = "connected"
    GREETING = "greeting"
    LISTENING = "listening"
    PROCESSING = "processing"
    SPEAKING = "speaking"
    TRANSFERRING = "transferring"
    ENDED = "ended"


@dataclass
class DialogTurn:
    """Single turn in the conversation."""

    speaker: str  # "user" or "assistant"
    content: str
    timestamp: float = field(default_factory=time.time)
    stt_confidence: float | None = None
    detected_language: str | None = None


class CallSession:
    """State machine for a single phone call.

    Tracks the call lifecycle, dialog history, and timeout counters.
    Serializable to/from Redis for stateless horizontal scaling.
    """

    def __init__(self, channel_uuid: uuid.UUID) -> None:
        self.channel_uuid = channel_uuid
        self.state = CallState.CONNECTED
        self.caller_id: str | None = None
        self.caller_phone: str | None = None
        self.customer_id: str | None = None
        self.needs_phone_verification: bool = False
        self.started_at: float = time.time()
        self.dialog_history: list[DialogTurn] = []
        self.timeout_count: int = 0
        self.empty_response_count: int = 0
        self.detected_language: str = "uk-UA"
        self.scenario: str | None = None
        self.transferred: bool = False
        self.transfer_reason: str | None = None
        self.order_id: str | None = None
        self.order_draft: dict[str, Any] | None = None
        self.fitting_booked: bool = False
        self.fitting_station_ids: set[str] = set()  # station IDs from get_fitting_stations
        # Full station dicts (id, name, city, address, district, phone) seen during
        # the call — used to inject the picked station into every LLM turn so the
        # model doesn't hallucinate a different city/address when memory drifts.
        self.fitting_stations_seen: list[dict[str, Any]] = []
        # Slot picked after get_fitting_slots — used to lock down date/time so the
        # LLM cannot drift to a different date at book_fitting time.
        self.selected_fitting_date: str | None = None  # YYYY-MM-DD
        self.selected_fitting_time: str | None = None  # HH:MM
        # Set of (date, time) slots offered by the last get_fitting_slots call so
        # the pipeline can capture the caller's choice from the LLM confirmation.
        self.fitting_slots_offered: list[dict[str, str]] = []
        # Station the LLM last acted on via get_fitting_slots / book_fitting —
        # used to inject the picked station even when fitting_stations_seen has
        # multiple entries (customer verbally chose one).
        self.last_fitting_station_id: str | None = None
        self.tools_called: set[str] = set()
        self.active_scenarios: set[str] = set()  # accumulated detected scenarios
        # Storage contract Numbers returned by find_storage during this call.
        # Used to guard book_fitting: if find_storage matched a contract but LLM
        # omits storage_contract=, we reject once and force a retry (call 07-30 16:25).
        self.storage_contracts_found: list[str] = []
        self.storage_contract_guard_triggered: bool = False
        # --- Fitting progress fields (used by _build_fitting_progress) ---
        # These surface state to the LLM as a "## 📋 Прогрес запису" block so
        # the model does not loop back to already-completed steps.
        self.fitting_customer_name: str | None = None      # Ім'я з профілю або питання
        # True if fitting_customer_name was loaded from the caller's profile at
        # call start. Used to block LLM-issued update_customer_profile(name=X)
        # from silently overwriting a trusted profile name — a common failure
        # when STT mishears a rare brand ("Zeekr" → "Віктор") and the LLM
        # interprets "X правильно" as a name correction (call 98ee0296 2026-08-14).
        self.name_from_profile: bool = False
        # Wave 6 (2026-09-03) — True if fitting_customer_name was
        # captured by the Krok 0 backend auto-detect (parallel to
        # name_from_profile). Same overwrite protection.
        self.name_from_krok0: bool = False
        # Wave 6 (2026-09-03) — True if the last bot turn contained a
        # «Перевіримо: …, Підтверджуєте?» while a checklist field was
        # still ⏳ (LLM invented values). Pipeline sets it; state guard
        # renders a correction banner on the NEXT turn to tell the LLM
        # its confirmation was hallucinated and to ask the ⏳ field.
        self.krok8_confabulation_pending: bool = False
        self.fitting_plate: str | None = None              # Ідентифікатор авто. З 2026-08-18: колір (напр. "синій"). Історично: держномер. Preparse може ще писати сюди платити, якщо клієнт САМ добровільно назвав.
        self.fitting_vehicle_brand: str | None = None      # Марка/модель авто
        # storage_choice: None=pending, "own"=клієнт привезе свої, "contract"=зі зберігання
        self.fitting_storage_choice: str | None = None
        self.fitting_storage_contract: str | None = None   # Обраний Number коли choice="contract"
        # Requested weekday (0=Mon..6=Sun) extracted from user text early in the
        # call — carried across intervening turns so the bot doesn't re-ask
        # "На яку дату?" after storage/city clarifications (call 2026-08-03).
        self.fitting_requested_weekday: int | None = None
        # Wave 12 (2026-09-07) — Tire diameter last mentioned by the customer
        # (13-24 inclusive). Set by pipeline when bot's last utterance asked
        # about diameter AND customer answered with a number/word. Read by
        # `_get_fitting_price` to reject LLM hallucinations where the model
        # invents an R16 default price before the customer stated a diameter
        # (Wave 3 P0 regression, call ebe7dfcb 2026-09-07).
        self.fitting_diameter_client: int | None = None
        # Wave 14 (2026-09-07) — set once `_get_fitting_slots` has already
        # bounced the LLM for picking a date the customer never named. One
        # bounce per call only: a client who answers vaguely («будь-коли»)
        # must not be trapped in a re-ask loop.
        self.fitting_date_guard_fired: bool = False
        self.tenant_id: str | None = None
        self.tenant_slug: str | None = None
        self.tenant_name: str | None = None
        self.network_id: str | None = None
        # Station IDs that this tenant chooses to hide from the LLM
        # (e.g. tvoya-shina excludes 000000022 Дніпрошина because it's
        # truck-only). Populated from tenant.config.excluded_station_ids
        # at call start; read by _get_fitting_stations to filter results.
        self.excluded_station_ids: set[str] = set()
        # Tenant working-hours schedule (JSONB from tenants.working_hours).
        # None = no schedule → treat as 24/7 and never trigger after-hours flow.
        self.working_hours: dict[str, Any] | None = None
        # --- FSM refactor (Wave 1-A, 2026-09-08) ---
        # Deterministic fitting-flow state machine (`src/agent/fitting_fsm.py`).
        # The engine never starts on its own — the pipeline enables it explicitly
        # (Wave 4-B). Until then these fields stay at their empty defaults.
        # Current FSM state name (value of FsmState, e.g. "CITY"). None = FSM not
        # started for this call. Persisted so a mid-call Redis recovery on another
        # Call Processor instance resumes from the right step.
        self.fsm_state: str | None = None
        # field name → parsed value ({"city": "Київ", "date": "2026-09-10", …}).
        # Authoritative source for required_context checks and for building the
        # book_fitting payload once the migration path (§2.7) completes.
        self.fsm_filled_fields: dict[str, Any] = {}
        # Main-flow state frozen while a side-state (PRICE_INTERRUPT /
        # CANCEL_INTERRUPT) is active. Wave 4-B resumes into it.
        self.fsm_prev_state: str | None = None
        # Ring buffer of the last FSM_HISTORY_LIMIT transitions:
        # {"t": float, "from": str|None, "to": str, "event": str, "payload": dict|None}
        self.fsm_history: list[dict[str, Any]] = []
        # --- Side-door interrupt state (Wave 3-B, 2026-09-08) ---
        # Read/written by `src/agent/interrupts.py`. These MUST survive the
        # Redis round-trip: the Call Processor is stateless and reloads the
        # session on every turn, so a field kept only on the in-memory object
        # is reset before the next turn ever sees it. That is precisely what
        # broke the first attempt (`c8c6601`): the loop-breaker counter below
        # was lost each turn, the cap never tripped, and the PRICE handler
        # repeated the same reply five times in a row.
        # Handler name → how many times it fired during this call. The cap on
        # this counter is the structural loop-breaker for the interrupt
        # handlers (a prompt rule cannot do this job).
        self.interrupt_counts: dict[str, int] = {}
        # Multi-turn cancel sub-flow: None | "awaiting_selection" |
        # "awaiting_confirmation:<booking_id>".
        self.pending_cancel_action: str | None = None
        # True while the price handler is waiting for the caller to name a
        # wheel diameter it asked for on a previous turn.
        self.pending_price_interrupt_needs_diameter: bool = False

    # --- State transitions ---

    def transition_to(self, new_state: CallState) -> None:
        """Transition to a new state with validation."""
        valid = _VALID_TRANSITIONS.get(self.state, set())
        if new_state not in valid:
            logger.warning(
                "Invalid state transition %s → %s for call %s",
                self.state.value,
                new_state.value,
                self.channel_uuid,
            )
            return
        old = self.state
        self.state = new_state
        logger.debug("Call %s: %s → %s", self.channel_uuid, old.value, new_state.value)

    def add_user_turn(
        self,
        content: str,
        stt_confidence: float | None = None,
        detected_language: str | None = None,
    ) -> None:
        """Record a user (caller) utterance."""
        self.dialog_history.append(
            DialogTurn(
                speaker="user",
                content=content,
                stt_confidence=stt_confidence,
                detected_language=detected_language,
            )
        )
        if detected_language:
            self.detected_language = detected_language
        self.timeout_count = 0

    def add_assistant_turn(self, content: str) -> None:
        """Record an assistant (bot) response."""
        self.dialog_history.append(DialogTurn(speaker="assistant", content=content))

    def record_timeout(self) -> bool:
        """Record a silence timeout. Returns True if call should be ended."""
        self.timeout_count += 1
        return self.timeout_count >= MAX_TIMEOUTS_BEFORE_HANGUP

    def record_empty_response(self) -> bool:
        """Record an empty LLM response. Returns True if escalation is due."""
        self.empty_response_count += 1
        return self.empty_response_count >= MAX_EMPTY_RESPONSES_BEFORE_ESCALATE

    def reset_empty_response(self) -> None:
        """Called after a successful LLM turn."""
        self.empty_response_count = 0

    def mark_transfer(self, reason: str) -> None:
        """Mark the call as transferred to an operator."""
        self.transferred = True
        self.transfer_reason = reason
        self.transition_to(CallState.TRANSFERRING)

    @property
    def messages_for_llm(self) -> list[dict[str, str]]:
        """Return dialog history formatted for the Claude API."""
        return [{"role": turn.speaker, "content": turn.content} for turn in self.dialog_history]

    @property
    def duration_seconds(self) -> int:
        """Call duration in seconds from start."""
        return int(time.time() - self.started_at)

    # --- Serialization ---

    def to_dict(self) -> dict[str, Any]:
        """Serialize session to a dictionary for Redis/JSON storage.

        Serializes all conversation-relevant fields needed for mid-call
        recovery. Transient fields (audio queues, locks) are NOT included.
        """
        return {
            "channel_uuid": str(self.channel_uuid),
            "state": self.state.value,
            "caller_id": self.caller_id,
            "caller_phone": self.caller_phone,
            "customer_id": self.customer_id,
            "needs_phone_verification": self.needs_phone_verification,
            "started_at": self.started_at,
            "timeout_count": self.timeout_count,
            "empty_response_count": self.empty_response_count,
            "detected_language": self.detected_language,
            "scenario": self.scenario,
            "transferred": self.transferred,
            "transfer_reason": self.transfer_reason,
            "order_id": self.order_id,
            "order_draft": self.order_draft,
            "fitting_booked": self.fitting_booked,
            "fitting_station_ids": sorted(self.fitting_station_ids),
            "fitting_stations_seen": self.fitting_stations_seen,
            "selected_fitting_date": self.selected_fitting_date,
            "selected_fitting_time": self.selected_fitting_time,
            "fitting_slots_offered": self.fitting_slots_offered,
            "last_fitting_station_id": self.last_fitting_station_id,
            "storage_contracts_found": list(self.storage_contracts_found),
            "storage_contract_guard_triggered": self.storage_contract_guard_triggered,
            "fitting_customer_name": self.fitting_customer_name,
            "name_from_profile": self.name_from_profile,
            "fitting_plate": self.fitting_plate,
            "fitting_vehicle_brand": self.fitting_vehicle_brand,
            "fitting_storage_choice": self.fitting_storage_choice,
            "fitting_storage_contract": self.fitting_storage_contract,
            "fitting_requested_weekday": self.fitting_requested_weekday,
            "fitting_diameter_client": self.fitting_diameter_client,
            "fitting_date_guard_fired": self.fitting_date_guard_fired,
            "tools_called": sorted(self.tools_called),
            "active_scenarios": sorted(self.active_scenarios),
            "tenant_id": self.tenant_id,
            "tenant_slug": self.tenant_slug,
            "tenant_name": self.tenant_name,
            "network_id": self.network_id,
            "working_hours": self.working_hours,
            "fsm_state": self.fsm_state,
            "fsm_filled_fields": dict(self.fsm_filled_fields),
            "fsm_prev_state": self.fsm_prev_state,
            "fsm_history": list(self.fsm_history[-FSM_HISTORY_LIMIT:]),
            "interrupt_counts": dict(self.interrupt_counts),
            "pending_cancel_action": self.pending_cancel_action,
            "pending_price_interrupt_needs_diameter": (
                self.pending_price_interrupt_needs_diameter
            ),
            "dialog_history": [
                {
                    "speaker": t.speaker,
                    "content": t.content,
                    "timestamp": t.timestamp,
                    "stt_confidence": t.stt_confidence,
                    "detected_language": t.detected_language,
                }
                for t in self.dialog_history
            ],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CallSession:
        """Restore a session from a dictionary (e.g. loaded from Redis).

        Reconstructs all fields saved by to_dict(), including sets
        and nested DialogTurn objects.
        """
        session = cls(uuid.UUID(data["channel_uuid"]))
        session.state = CallState(data["state"])
        session.caller_id = data.get("caller_id")
        session.caller_phone = data.get("caller_phone")
        session.customer_id = data.get("customer_id")
        session.needs_phone_verification = data.get("needs_phone_verification", False)
        session.started_at = data["started_at"]
        session.timeout_count = data.get("timeout_count", 0)
        session.empty_response_count = data.get("empty_response_count", 0)
        session.detected_language = data.get("detected_language", "uk-UA")
        session.scenario = data.get("scenario")
        session.transferred = data.get("transferred", False)
        session.transfer_reason = data.get("transfer_reason")
        session.order_id = data.get("order_id")
        session.order_draft = data.get("order_draft")
        session.fitting_booked = data.get("fitting_booked", False)
        session.fitting_station_ids = set(data.get("fitting_station_ids", []))
        session.fitting_stations_seen = list(data.get("fitting_stations_seen", []))
        session.selected_fitting_date = data.get("selected_fitting_date")
        session.selected_fitting_time = data.get("selected_fitting_time")
        session.fitting_slots_offered = list(data.get("fitting_slots_offered", []))
        session.last_fitting_station_id = data.get("last_fitting_station_id")
        session.storage_contracts_found = list(data.get("storage_contracts_found", []))
        session.storage_contract_guard_triggered = data.get(
            "storage_contract_guard_triggered", False
        )
        session.fitting_customer_name = data.get("fitting_customer_name")
        session.name_from_profile = data.get("name_from_profile", False)
        session.fitting_plate = data.get("fitting_plate")
        session.fitting_vehicle_brand = data.get("fitting_vehicle_brand")
        session.fitting_storage_choice = data.get("fitting_storage_choice")
        session.fitting_storage_contract = data.get("fitting_storage_contract")
        session.fitting_requested_weekday = data.get("fitting_requested_weekday")
        session.fitting_diameter_client = data.get("fitting_diameter_client")
        session.fitting_date_guard_fired = bool(
            data.get("fitting_date_guard_fired", False)
        )
        session.tools_called = set(data.get("tools_called", []))
        session.active_scenarios = set(data.get("active_scenarios", []))
        session.tenant_id = data.get("tenant_id")
        session.tenant_slug = data.get("tenant_slug")
        session.tenant_name = data.get("tenant_name")
        session.network_id = data.get("network_id")
        session.working_hours = data.get("working_hours")
        session.fsm_state = data.get("fsm_state")
        session.fsm_prev_state = data.get("fsm_prev_state")
        filled = data.get("fsm_filled_fields") or {}
        if isinstance(filled, dict):
            session.fsm_filled_fields = dict(filled)
        else:
            # Never silently drop collected fields — a wrong type here means the
            # writer is buggy and the caller would be re-asked every field.
            logger.warning(
                "Call %s: fsm_filled_fields has unexpected type %s — ignoring",
                data.get("channel_uuid"),
                type(filled).__name__,
            )
        history = data.get("fsm_history") or []
        if isinstance(history, list):
            session.fsm_history = [h for h in history if isinstance(h, dict)][-FSM_HISTORY_LIMIT:]
        else:
            logger.warning(
                "Call %s: fsm_history has unexpected type %s — ignoring",
                data.get("channel_uuid"),
                type(history).__name__,
            )
        # --- Side-door interrupt state (Wave 3-B) ---
        # A malformed value here must never silently become an empty default:
        # that would reset the loop-breaker and let a handler fire forever.
        counts = data.get("interrupt_counts") or {}
        if isinstance(counts, dict):
            clean: dict[str, int] = {}
            for key, value in counts.items():
                if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                    logger.warning(
                        "Call %s: interrupt_counts[%r] has unexpected value %r — ignoring",
                        data.get("channel_uuid"),
                        key,
                        value,
                    )
                    continue
                clean[str(key)] = value
            session.interrupt_counts = clean
        else:
            logger.warning(
                "Call %s: interrupt_counts has unexpected type %s — ignoring",
                data.get("channel_uuid"),
                type(counts).__name__,
            )
        pending_cancel = data.get("pending_cancel_action")
        if pending_cancel is None or isinstance(pending_cancel, str):
            session.pending_cancel_action = pending_cancel
        else:
            logger.warning(
                "Call %s: pending_cancel_action has unexpected type %s — ignoring",
                data.get("channel_uuid"),
                type(pending_cancel).__name__,
            )
        needs_diameter = data.get("pending_price_interrupt_needs_diameter", False)
        if isinstance(needs_diameter, bool):
            session.pending_price_interrupt_needs_diameter = needs_diameter
        else:
            logger.warning(
                "Call %s: pending_price_interrupt_needs_diameter has unexpected type %s "
                "— ignoring",
                data.get("channel_uuid"),
                type(needs_diameter).__name__,
            )
        session.dialog_history = [
            DialogTurn(
                speaker=t["speaker"],
                content=t["content"],
                timestamp=t.get("timestamp", 0),
                stt_confidence=t.get("stt_confidence"),
                detected_language=t.get("detected_language"),
            )
            for t in data.get("dialog_history", [])
        ]
        return session

    def serialize(self) -> str:
        """Serialize session to JSON string for Redis storage."""
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def deserialize(cls, raw: str) -> CallSession:
        """Deserialize session from a JSON string."""
        data = json.loads(raw)
        return cls.from_dict(data)


# Valid state transitions
_VALID_TRANSITIONS: dict[CallState, set[CallState]] = {
    CallState.CONNECTED: {CallState.GREETING, CallState.ENDED},
    CallState.GREETING: {CallState.LISTENING, CallState.SPEAKING, CallState.ENDED},
    CallState.LISTENING: {
        CallState.PROCESSING,
        CallState.SPEAKING,
        CallState.TRANSFERRING,
        CallState.ENDED,
    },
    CallState.PROCESSING: {CallState.SPEAKING, CallState.TRANSFERRING, CallState.ENDED},
    CallState.SPEAKING: {
        CallState.LISTENING,
        CallState.PROCESSING,
        CallState.SPEAKING,
        CallState.ENDED,
    },
    CallState.TRANSFERRING: {CallState.ENDED},
}


class SessionStore:
    """Redis-backed storage for call sessions.

    Key pattern: call_session:{channel_uuid}
    TTL: 1800 seconds (renewed on each save).
    """

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def save(self, session: CallSession) -> None:
        """Save session to Redis with TTL renewal."""
        key = f"{SESSION_KEY_PREFIX}:{session.channel_uuid}"
        await self._redis.setex(key, SESSION_TTL, session.serialize())

    async def load(self, channel_uuid: uuid.UUID) -> CallSession | None:
        """Load session from Redis. Returns None if not found or expired."""
        key = f"{SESSION_KEY_PREFIX}:{channel_uuid}"
        raw = await self._redis.get(key)
        if raw is None:
            return None
        return CallSession.deserialize(raw if isinstance(raw, str) else raw.decode())

    async def delete(self, channel_uuid: uuid.UUID) -> None:
        """Delete session from Redis (normal call termination)."""
        key = f"{SESSION_KEY_PREFIX}:{channel_uuid}"
        await self._redis.delete(key)

    async def exists(self, channel_uuid: uuid.UUID) -> bool:
        """Check if a session exists in Redis."""
        key = f"{SESSION_KEY_PREFIX}:{channel_uuid}"
        return bool(await self._redis.exists(key))
