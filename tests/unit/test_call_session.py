"""Unit tests for CallSession state machine."""

import uuid

import pytest

from src.core.call_session import (
    CallSession,
    CallState,
    MAX_TIMEOUTS_BEFORE_HANGUP,
)


class TestCallSessionStateTransitions:
    """Test state machine transitions."""

    def test_initial_state_is_connected(self) -> None:
        session = CallSession(uuid.uuid4())
        assert session.state == CallState.CONNECTED

    def test_valid_transition_connected_to_greeting(self) -> None:
        session = CallSession(uuid.uuid4())
        session.transition_to(CallState.GREETING)
        assert session.state == CallState.GREETING

    def test_valid_transition_greeting_to_listening(self) -> None:
        session = CallSession(uuid.uuid4())
        session.transition_to(CallState.GREETING)
        session.transition_to(CallState.LISTENING)
        assert session.state == CallState.LISTENING

    def test_valid_transition_listening_to_processing(self) -> None:
        session = CallSession(uuid.uuid4())
        session.transition_to(CallState.GREETING)
        session.transition_to(CallState.LISTENING)
        session.transition_to(CallState.PROCESSING)
        assert session.state == CallState.PROCESSING

    def test_invalid_transition_stays_in_current(self) -> None:
        session = CallSession(uuid.uuid4())
        session.transition_to(CallState.SPEAKING)  # invalid from CONNECTED
        assert session.state == CallState.CONNECTED

    def test_any_state_can_transition_to_ended(self) -> None:
        for start_state in [
            CallState.CONNECTED,
            CallState.GREETING,
            CallState.LISTENING,
            CallState.PROCESSING,
            CallState.SPEAKING,
            CallState.TRANSFERRING,
        ]:
            session = CallSession(uuid.uuid4())
            session.state = start_state  # force state for test
            session.transition_to(CallState.ENDED)
            assert session.state == CallState.ENDED


class TestCallSessionDialogHistory:
    """Test dialog history management."""

    def test_add_user_turn(self) -> None:
        session = CallSession(uuid.uuid4())
        session.add_user_turn("Привіт", stt_confidence=0.95, detected_language="uk-UA")
        assert len(session.dialog_history) == 1
        assert session.dialog_history[0].speaker == "user"
        assert session.dialog_history[0].content == "Привіт"

    def test_add_assistant_turn(self) -> None:
        session = CallSession(uuid.uuid4())
        session.add_assistant_turn("Добрий день!")
        assert len(session.dialog_history) == 1
        assert session.dialog_history[0].speaker == "assistant"

    def test_messages_for_llm_format(self) -> None:
        session = CallSession(uuid.uuid4())
        session.add_user_turn("Шукаю шини")
        session.add_assistant_turn("Які параметри?")
        messages = session.messages_for_llm
        assert messages == [
            {"role": "user", "content": "Шукаю шини"},
            {"role": "assistant", "content": "Які параметри?"},
        ]

    def test_detected_language_updates(self) -> None:
        session = CallSession(uuid.uuid4())
        session.add_user_turn("Привет", detected_language="ru-RU")
        assert session.detected_language == "ru-RU"


class TestCallSessionTimeout:
    """Test silence timeout handling."""

    def test_first_timeout_does_not_end(self) -> None:
        session = CallSession(uuid.uuid4())
        should_end = session.record_timeout()
        assert not should_end
        assert session.timeout_count == 1

    def test_max_timeouts_ends_call(self) -> None:
        session = CallSession(uuid.uuid4())
        for _ in range(MAX_TIMEOUTS_BEFORE_HANGUP - 1):
            session.record_timeout()
        should_end = session.record_timeout()
        assert should_end

    def test_user_turn_resets_timeout(self) -> None:
        session = CallSession(uuid.uuid4())
        session.record_timeout()
        session.add_user_turn("Алло")
        assert session.timeout_count == 0


class TestCallSessionTransfer:
    """Test operator transfer."""

    def test_mark_transfer(self) -> None:
        session = CallSession(uuid.uuid4())
        session.state = CallState.PROCESSING
        session.mark_transfer("customer_request")
        assert session.transferred
        assert session.transfer_reason == "customer_request"
        assert session.state == CallState.TRANSFERRING


class TestCallSessionSerialization:
    """Test session serialization/deserialization."""

    def test_round_trip(self) -> None:
        session = CallSession(uuid.uuid4())
        session.caller_id = "+380501234567"
        session.add_user_turn("Шукаю шини", stt_confidence=0.9, detected_language="uk-UA")
        session.add_assistant_turn("Які параметри?")
        session.transition_to(CallState.GREETING)

        raw = session.serialize()
        restored = CallSession.deserialize(raw)

        assert restored.channel_uuid == session.channel_uuid
        assert restored.caller_id == session.caller_id
        assert restored.state == session.state
        assert len(restored.dialog_history) == 2
        assert restored.dialog_history[0].content == "Шукаю шини"
        assert restored.dialog_history[0].stt_confidence == 0.9
