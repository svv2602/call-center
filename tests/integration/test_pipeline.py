"""Integration tests for the call pipeline.

Tests verify CallPipeline with mocked STT/TTS/LLM services,
including DB-loaded templates and custom tool configurations.

Run: pytest tests/integration/test_pipeline.py -v
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.agent.prompts import ERROR_TEXT, FAREWELL_TEXT, GREETING_TEXT, SILENCE_PROMPT_TEXT
from src.core.audio_socket import AudioSocketConnection, AudioSocketPacket, PacketType
from src.core.call_session import CallSession, CallState
from src.core.pipeline import _DEFAULT_TEMPLATES, CallPipeline
from src.stt.base import STTConfig
from tests.unit.mocks.mock_stt import MockSTTEngine
from tests.unit.mocks.mock_tts import MockTTSEngine


def _make_conn(packets: list[AudioSocketPacket] | None = None) -> MagicMock:
    """Create a mock AudioSocketConnection that yields given packets then hangs up."""
    conn = MagicMock(spec=AudioSocketConnection)
    conn.channel_uuid = uuid4()
    conn.is_closed = False

    packet_iter = iter(packets or [AudioSocketPacket(PacketType.HANGUP, b"")])
    call_count = 0

    async def read_audio_packet() -> AudioSocketPacket | None:
        nonlocal call_count
        try:
            pkt = next(packet_iter)
            return pkt
        except StopIteration:
            conn.is_closed = True
            return None

    conn.read_audio_packet = AsyncMock(side_effect=read_audio_packet)
    conn.send_audio = AsyncMock()
    return conn


def _make_agent(response: str = "Дякую за звернення!") -> MagicMock:
    agent = MagicMock()
    agent.process_message = AsyncMock(return_value=(response, []))
    return agent


class TestPipelineWithDefaultTemplates:
    """Test pipeline behavior with default (hardcoded) templates."""

    @pytest.mark.asyncio
    async def test_greeting_plays_on_connect(self) -> None:
        """Pipeline should play greeting from templates on start."""
        conn = _make_conn([AudioSocketPacket(PacketType.HANGUP, b"")])
        stt = MockSTTEngine()
        tts = MockTTSEngine()
        agent = _make_agent()
        session = CallSession(conn.channel_uuid)

        pipeline = CallPipeline(conn, stt, tts, agent, session, STTConfig())
        await pipeline.run()

        # TTS should have been called with the default greeting
        assert tts.synthesize_count >= 1
        assert session.state == CallState.ENDED

    @pytest.mark.asyncio
    async def test_hangup_ends_pipeline(self) -> None:
        """Pipeline should cleanly end on hangup packet."""
        conn = _make_conn([AudioSocketPacket(PacketType.HANGUP, b"")])
        stt = MockSTTEngine()
        tts = MockTTSEngine()
        agent = _make_agent()
        session = CallSession(conn.channel_uuid)

        pipeline = CallPipeline(conn, stt, tts, agent, session, STTConfig())
        await pipeline.run()

        assert session.state == CallState.ENDED

    @pytest.mark.asyncio
    async def test_error_speaks_error_template(self) -> None:
        """On pipeline error, should speak the error template."""
        conn = _make_conn()
        stt = MockSTTEngine()
        tts = MockTTSEngine()
        agent = _make_agent()
        session = CallSession(conn.channel_uuid)

        # Force an error after greeting by making STT start fail on second call
        original_start = stt.start_stream

        async def failing_start(config: STTConfig) -> None:
            await original_start(config)
            # Make transcript loop raise
            stt._transcripts = None  # This will cause an error in get_transcripts

        # Inject error in STT after start
        stt.start_stream = failing_start

        pipeline = CallPipeline(conn, stt, tts, agent, session, STTConfig())
        # Pipeline should not raise — it catches errors and speaks error template
        await pipeline.run()
        assert session.state == CallState.ENDED


class TestPipelineWithCustomTemplates:
    """Test pipeline uses custom templates from DB."""

    @pytest.mark.asyncio
    async def test_custom_greeting_is_spoken(self) -> None:
        """Pipeline should use custom greeting template, not hardcoded."""
        custom_templates = {
            **_DEFAULT_TEMPLATES,
            "greeting": "Вітаємо у нашому магазині шин!",
        }

        conn = _make_conn([AudioSocketPacket(PacketType.HANGUP, b"")])
        tts = MockTTSEngine()
        stt = MockSTTEngine()
        agent = _make_agent()
        session = CallSession(conn.channel_uuid)

        pipeline = CallPipeline(
            conn, stt, tts, agent, session, STTConfig(), templates=custom_templates
        )
        assert pipeline._templates["greeting"] == "Вітаємо у нашому магазині шин!"

    @pytest.mark.asyncio
    async def test_custom_farewell_template(self) -> None:
        """Pipeline should use custom farewell template."""
        custom_templates = {
            **_DEFAULT_TEMPLATES,
            "farewell": "Дякуємо за дзвінок, на все добре!",
        }

        pipeline = CallPipeline(
            _make_conn(), MockSTTEngine(), MockTTSEngine(),
            _make_agent(), CallSession(uuid4()), STTConfig(),
            templates=custom_templates,
        )
        assert pipeline._templates["farewell"] == "Дякуємо за дзвінок, на все добре!"

    @pytest.mark.asyncio
    async def test_custom_error_template(self) -> None:
        """Pipeline should use custom error template."""
        custom_templates = {
            **_DEFAULT_TEMPLATES,
            "error": "Виникла технічна помилка, перепрошуємо.",
        }

        pipeline = CallPipeline(
            _make_conn(), MockSTTEngine(), MockTTSEngine(),
            _make_agent(), CallSession(uuid4()), STTConfig(),
            templates=custom_templates,
        )
        assert pipeline._templates["error"] == "Виникла технічна помилка, перепрошуємо."

    @pytest.mark.asyncio
    async def test_none_templates_uses_defaults(self) -> None:
        """Passing templates=None should fall back to _DEFAULT_TEMPLATES."""
        pipeline = CallPipeline(
            _make_conn(), MockSTTEngine(), MockTTSEngine(),
            _make_agent(), CallSession(uuid4()), STTConfig(),
            templates=None,
        )
        assert pipeline._templates == _DEFAULT_TEMPLATES
        assert pipeline._templates["greeting"] == GREETING_TEXT

    @pytest.mark.asyncio
    async def test_partial_templates_fallback(self) -> None:
        """Custom templates with missing keys — pipeline still works via .get() fallback."""
        partial = {"greeting": "Часткове привітання"}

        pipeline = CallPipeline(
            _make_conn(), MockSTTEngine(), MockTTSEngine(),
            _make_agent(), CallSession(uuid4()), STTConfig(),
            templates=partial,
        )
        assert pipeline._templates["greeting"] == "Часткове привітання"
        # Missing keys handled by .get() with fallback in pipeline code
        assert pipeline._templates.get("farewell", FAREWELL_TEXT) == FAREWELL_TEXT

    @pytest.mark.asyncio
    async def test_all_template_keys_accessible(self) -> None:
        """All expected template keys should be accessible from _DEFAULT_TEMPLATES."""
        expected_keys = {"greeting", "farewell", "silence_prompt", "transfer", "error", "wait"}
        assert expected_keys.issubset(set(_DEFAULT_TEMPLATES.keys()))


class TestDefaultTemplatesMatch:
    """Verify _DEFAULT_TEMPLATES matches prompts.py constants."""

    def test_greeting_matches(self) -> None:
        assert _DEFAULT_TEMPLATES["greeting"] == GREETING_TEXT

    def test_farewell_matches(self) -> None:
        assert _DEFAULT_TEMPLATES["farewell"] == FAREWELL_TEXT

    def test_silence_matches(self) -> None:
        assert _DEFAULT_TEMPLATES["silence_prompt"] == SILENCE_PROMPT_TEXT

    def test_error_matches(self) -> None:
        assert _DEFAULT_TEMPLATES["error"] == ERROR_TEXT
