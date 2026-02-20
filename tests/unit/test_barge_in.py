"""Tests for barge-in detection — transcript fan-out and interruptible send."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.pipeline import CallPipeline
from src.stt.base import STTConfig, Transcript

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeSTT:
    """Minimal STT stub that yields pre-configured transcripts."""

    def __init__(self, transcripts: list[Transcript]) -> None:
        self._transcripts = transcripts
        self._started = False

    async def start_stream(self, config: STTConfig) -> None:
        self._started = True

    async def feed_audio(self, chunk: bytes) -> None:
        pass

    async def get_transcripts(self) -> AsyncIterator[Transcript]:
        for t in self._transcripts:
            yield t

    async def stop_stream(self) -> None:
        pass


class _FakeConn:
    """Minimal AudioSocket connection stub."""

    def __init__(self) -> None:
        self.sent_chunks: list[bytes] = []
        self._closed = False

    @property
    def is_closed(self) -> bool:
        return self._closed

    async def read_audio_packet(self) -> None:
        # Simulate immediate hangup so audio_reader_loop exits
        self._closed = True
        return None

    async def send_audio(
        self, audio_data: bytes, cancel_event: asyncio.Event | None = None
    ) -> bool:
        if cancel_event is not None and cancel_event.is_set():
            return True
        self.sent_chunks.append(audio_data)
        return False


# ---------------------------------------------------------------------------
# _transcript_reader_loop tests
# ---------------------------------------------------------------------------


class TestTranscriptReaderLoop:
    """Tests for the _transcript_reader_loop that drives barge-in detection."""

    def _make_pipeline(
        self, stt: _FakeSTT, barge_in: asyncio.Event | None = None
    ) -> CallPipeline:
        """Create a minimal CallPipeline with fakes."""
        conn = _FakeConn()
        tts = MagicMock()
        agent = MagicMock()
        session = MagicMock()
        session.channel_uuid = "test-uuid"

        pipeline = CallPipeline(
            conn=conn,
            stt=stt,
            tts=tts,
            agent=agent,
            session=session,
            barge_in_event=barge_in,
        )
        return pipeline

    @pytest.mark.asyncio
    async def test_sets_barge_in_on_interim_while_speaking(self) -> None:
        """Interim transcript while _speaking=True should trigger barge-in."""
        stt = _FakeSTT([
            Transcript(text="Алло", is_final=False, confidence=0.8),
        ])
        barge_in = asyncio.Event()
        pipeline = self._make_pipeline(stt, barge_in)
        pipeline._speaking = True

        with patch("src.core.pipeline.barge_in_total") as mock_metric:
            await pipeline._transcript_reader_loop()

        assert barge_in.is_set()
        mock_metric.inc.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_barge_in_on_interim_when_not_speaking(self) -> None:
        """Interim transcript while _speaking=False should NOT trigger barge-in."""
        stt = _FakeSTT([
            Transcript(text="Алло", is_final=False, confidence=0.8),
        ])
        barge_in = asyncio.Event()
        pipeline = self._make_pipeline(stt, barge_in)
        pipeline._speaking = False

        await pipeline._transcript_reader_loop()

        assert not barge_in.is_set()

    @pytest.mark.asyncio
    async def test_forwards_final_transcripts_to_queue(self) -> None:
        """Final transcripts should be forwarded to _final_transcript_queue."""
        final = Transcript(text="Добрий день", is_final=True, confidence=0.95)
        stt = _FakeSTT([final])
        pipeline = self._make_pipeline(stt)

        await pipeline._transcript_reader_loop()

        # Queue should have: final transcript + None sentinel
        item = pipeline._final_transcript_queue.get_nowait()
        assert item is not None
        assert item.text == "Добрий день"
        assert item.is_final is True

        sentinel = pipeline._final_transcript_queue.get_nowait()
        assert sentinel is None

    @pytest.mark.asyncio
    async def test_puts_sentinel_on_stream_end(self) -> None:
        """Queue should get None sentinel when STT stream ends."""
        stt = _FakeSTT([])  # empty stream
        pipeline = self._make_pipeline(stt)

        await pipeline._transcript_reader_loop()

        sentinel = pipeline._final_transcript_queue.get_nowait()
        assert sentinel is None

    @pytest.mark.asyncio
    async def test_ignores_empty_interim(self) -> None:
        """Interim with empty text should not trigger barge-in."""
        stt = _FakeSTT([
            Transcript(text="  ", is_final=False, confidence=0.1),
        ])
        barge_in = asyncio.Event()
        pipeline = self._make_pipeline(stt, barge_in)
        pipeline._speaking = True

        await pipeline._transcript_reader_loop()

        assert not barge_in.is_set()

    @pytest.mark.asyncio
    async def test_ignores_empty_final(self) -> None:
        """Final with empty text should not be queued."""
        stt = _FakeSTT([
            Transcript(text="", is_final=True, confidence=0.0),
        ])
        pipeline = self._make_pipeline(stt)

        await pipeline._transcript_reader_loop()

        # Only sentinel should be in the queue
        sentinel = pipeline._final_transcript_queue.get_nowait()
        assert sentinel is None
        assert pipeline._final_transcript_queue.empty()


# ---------------------------------------------------------------------------
# _speak() barge-in tests
# ---------------------------------------------------------------------------


class TestSpeakBargeIn:
    """Tests for barge-in interruption during _speak()."""

    @pytest.mark.asyncio
    async def test_speak_stops_on_barge_in_during_synthesis(self) -> None:
        """_speak() should skip sending when barge-in fires during TTS synthesis."""
        conn = _FakeConn()
        barge_in = asyncio.Event()

        tts = AsyncMock()

        async def _synthesize_with_barge_in(text: str) -> bytes:
            # Simulate caller speaking during TTS synthesis
            barge_in.set()
            return b"\x00" * 6400  # 200ms of audio

        tts.synthesize = _synthesize_with_barge_in

        session = MagicMock()
        session.channel_uuid = "test-uuid"

        pipeline = CallPipeline(
            conn=conn,
            stt=MagicMock(),
            tts=tts,
            agent=MagicMock(),
            session=session,
            barge_in_event=barge_in,
        )

        await pipeline._speak("Тестовий текст")

        # Audio should not have been sent (barge-in detected after synthesis)
        assert conn.sent_chunks == []
        assert pipeline._speaking is False

    @pytest.mark.asyncio
    async def test_speak_stops_on_barge_in_mid_send(self) -> None:
        """_speak() should stop sending when barge-in fires during audio send."""
        conn = _FakeConn()
        barge_in = asyncio.Event()

        tts = AsyncMock()
        tts.synthesize = AsyncMock(return_value=b"\x00" * 6400)

        session = MagicMock()
        session.channel_uuid = "test-uuid"

        pipeline = CallPipeline(
            conn=conn,
            stt=MagicMock(),
            tts=tts,
            agent=MagicMock(),
            session=session,
            barge_in_event=barge_in,
        )

        # Override send_audio: set barge-in during the send
        original_send = conn.send_audio

        async def _send_with_barge_in(
            audio_data: bytes, cancel_event: asyncio.Event | None = None
        ) -> bool:
            barge_in.set()  # caller starts speaking mid-send
            if cancel_event is not None and cancel_event.is_set():
                return True
            conn.sent_chunks.append(audio_data)
            return False

        conn.send_audio = _send_with_barge_in  # type: ignore[assignment]

        await pipeline._speak("Тестовий текст")

        # send_audio should have returned True (interrupted)
        assert conn.sent_chunks == []
        assert pipeline._speaking is False

    @pytest.mark.asyncio
    async def test_speak_completes_without_barge_in(self) -> None:
        """_speak() sends full audio when no barge-in occurs."""
        conn = _FakeConn()
        tts = AsyncMock()
        tts.synthesize = AsyncMock(return_value=b"\x00" * 100)

        session = MagicMock()
        session.channel_uuid = "test-uuid"

        pipeline = CallPipeline(
            conn=conn,
            stt=MagicMock(),
            tts=tts,
            agent=MagicMock(),
            session=session,
        )

        await pipeline._speak("Привіт")

        assert len(conn.sent_chunks) == 1
        assert conn.sent_chunks[0] == b"\x00" * 100
        assert pipeline._speaking is False


# ---------------------------------------------------------------------------
# _speak_streaming() barge-in tests
# ---------------------------------------------------------------------------


class TestSpeakStreamingBargeIn:
    """Tests for barge-in interruption during _speak_streaming()."""

    @pytest.mark.asyncio
    async def test_speak_streaming_stops_between_sentences(self) -> None:
        """_speak_streaming() should stop between sentences when barge-in is set."""
        conn = _FakeConn()
        tts = AsyncMock()
        barge_in = asyncio.Event()

        async def _fake_stream(text: str) -> AsyncIterator[bytes]:
            yield b"\x01" * 100  # sentence 1
            barge_in.set()  # caller starts speaking
            yield b"\x02" * 100  # sentence 2 — should not be sent

        tts.synthesize_stream = _fake_stream

        session = MagicMock()
        session.channel_uuid = "test-uuid"

        pipeline = CallPipeline(
            conn=conn,
            stt=MagicMock(),
            tts=tts,
            agent=MagicMock(),
            session=session,
            barge_in_event=barge_in,
        )

        await pipeline._speak_streaming("Перше речення. Друге речення.")

        # Only first sentence should have been sent
        assert len(conn.sent_chunks) == 1
        assert conn.sent_chunks[0] == b"\x01" * 100
        assert pipeline._speaking is False
