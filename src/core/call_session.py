"""Call session state machine and Redis persistence.

Manages the lifecycle of a single phone call through states:
  Connected → Greeting → Listening → Processing → Speaking → Listening (cycle)
                                                 → Transferring → Ended
  Listening → Timeout (10s) → prompt → Timeout (10s) → Ended
"""

from __future__ import annotations

import enum
import json
import logging
import time
import uuid
from dataclasses import dataclass, field

from redis.asyncio import Redis

logger = logging.getLogger(__name__)

# Session constants
SESSION_KEY_PREFIX = "call_session"
SESSION_TTL = 1800  # 30 minutes
SILENCE_TIMEOUT_SEC = 10
MAX_TIMEOUTS_BEFORE_HANGUP = 2


class CallState(str, enum.Enum):
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
        self.customer_id: str | None = None
        self.started_at: float = time.time()
        self.dialog_history: list[DialogTurn] = []
        self.timeout_count: int = 0
        self.detected_language: str = "uk-UA"
        self.scenario: str | None = None
        self.transferred: bool = False
        self.transfer_reason: str | None = None

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
        logger.debug(
            "Call %s: %s → %s", self.channel_uuid, old.value, new_state.value
        )

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
        self.dialog_history.append(
            DialogTurn(speaker="assistant", content=content)
        )

    def record_timeout(self) -> bool:
        """Record a silence timeout. Returns True if call should be ended."""
        self.timeout_count += 1
        return self.timeout_count >= MAX_TIMEOUTS_BEFORE_HANGUP

    def mark_transfer(self, reason: str) -> None:
        """Mark the call as transferred to an operator."""
        self.transferred = True
        self.transfer_reason = reason
        self.transition_to(CallState.TRANSFERRING)

    @property
    def messages_for_llm(self) -> list[dict[str, str]]:
        """Return dialog history formatted for the Claude API."""
        return [
            {"role": turn.speaker, "content": turn.content}
            for turn in self.dialog_history
        ]

    @property
    def duration_seconds(self) -> int:
        """Call duration in seconds from start."""
        return int(time.time() - self.started_at)

    # --- Serialization ---

    def serialize(self) -> str:
        """Serialize session to JSON string for Redis storage."""
        data = {
            "channel_uuid": str(self.channel_uuid),
            "state": self.state.value,
            "caller_id": self.caller_id,
            "customer_id": self.customer_id,
            "started_at": self.started_at,
            "timeout_count": self.timeout_count,
            "detected_language": self.detected_language,
            "scenario": self.scenario,
            "transferred": self.transferred,
            "transfer_reason": self.transfer_reason,
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
        return json.dumps(data, ensure_ascii=False)

    @classmethod
    def deserialize(cls, raw: str) -> CallSession:
        """Deserialize session from a JSON string."""
        data = json.loads(raw)
        session = cls(uuid.UUID(data["channel_uuid"]))
        session.state = CallState(data["state"])
        session.caller_id = data.get("caller_id")
        session.customer_id = data.get("customer_id")
        session.started_at = data["started_at"]
        session.timeout_count = data.get("timeout_count", 0)
        session.detected_language = data.get("detected_language", "uk-UA")
        session.scenario = data.get("scenario")
        session.transferred = data.get("transferred", False)
        session.transfer_reason = data.get("transfer_reason")
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


# Valid state transitions
_VALID_TRANSITIONS: dict[CallState, set[CallState]] = {
    CallState.CONNECTED: {CallState.GREETING, CallState.ENDED},
    CallState.GREETING: {CallState.LISTENING, CallState.ENDED},
    CallState.LISTENING: {CallState.PROCESSING, CallState.SPEAKING, CallState.TRANSFERRING, CallState.ENDED},
    CallState.PROCESSING: {CallState.SPEAKING, CallState.TRANSFERRING, CallState.ENDED},
    CallState.SPEAKING: {CallState.LISTENING, CallState.PROCESSING, CallState.ENDED},
    CallState.TRANSFERRING: {CallState.ENDED},
}


class SessionStore:
    """Redis-backed storage for call sessions.

    Key pattern: call_session:{channel_uuid}
    TTL: 1800 seconds (renewed on each save).
    """

    def __init__(self, redis: Redis) -> None:  # type: ignore[type-arg]
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
