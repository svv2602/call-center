"""Unit tests for Whisper STT engine and Fallback STT engine."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.stt.base import STTConfig, Transcript
from src.stt.whisper_stt import WhisperConfig, WhisperSTTEngine


class TestWhisperConfig:
    """Test Whisper configuration defaults."""

    def test_defaults(self) -> None:
        config = WhisperConfig()
        assert config.model_size == "large-v3"
        assert config.device == "cuda"
        assert config.compute_type == "float16"
        assert config.language == "uk"
        assert config.beam_size == 5
        assert config.vad_filter is True

    def test_custom_config(self) -> None:
        config = WhisperConfig(model_size="medium", device="cpu", compute_type="int8")
        assert config.model_size == "medium"
        assert config.device == "cpu"
        assert config.compute_type == "int8"


class TestWhisperSTTEngine:
    """Test Whisper STT engine."""

    @pytest.mark.asyncio
    async def test_start_stream_loads_model(self) -> None:
        engine = WhisperSTTEngine()

        with patch.object(engine, "_ensure_model", new_callable=AsyncMock):
            await engine.start_stream(STTConfig())
            assert engine._is_streaming is True

    @pytest.mark.asyncio
    async def test_feed_audio_buffers(self) -> None:
        engine = WhisperSTTEngine()
        engine._is_streaming = True
        engine._model = MagicMock()

        # Feed audio smaller than threshold
        small_chunk = b"\x00" * 1000
        await engine.feed_audio(small_chunk)
        assert len(engine._audio_buffer) == 1000

    @pytest.mark.asyncio
    async def test_feed_audio_triggers_transcription(self) -> None:
        engine = WhisperSTTEngine()
        engine._is_streaming = True
        engine._model = MagicMock()

        with patch.object(engine, "_transcribe_buffer", new_callable=AsyncMock) as mock_transcribe:
            # Feed audio exceeding threshold (64000 = 2 seconds at 16kHz 16-bit)
            large_chunk = b"\x00" * 65000
            await engine.feed_audio(large_chunk)
            mock_transcribe.assert_called_once()

    @pytest.mark.asyncio
    async def test_feed_audio_ignored_when_not_streaming(self) -> None:
        engine = WhisperSTTEngine()
        engine._is_streaming = False

        await engine.feed_audio(b"\x00" * 1000)
        assert len(engine._audio_buffer) == 0

    @pytest.mark.asyncio
    async def test_stop_stream_transcribes_remaining(self) -> None:
        engine = WhisperSTTEngine()
        engine._is_streaming = True
        engine._model = MagicMock()
        engine._audio_buffer = bytearray(b"\x00" * 5000)

        with patch.object(engine, "_transcribe_buffer", new_callable=AsyncMock) as mock_transcribe:
            await engine.stop_stream()
            mock_transcribe.assert_called_once()
            assert engine._is_streaming is False

    @pytest.mark.asyncio
    async def test_stop_stream_no_remaining_audio(self) -> None:
        engine = WhisperSTTEngine()
        engine._is_streaming = True

        with patch.object(engine, "_transcribe_buffer", new_callable=AsyncMock) as mock_transcribe:
            await engine.stop_stream()
            mock_transcribe.assert_not_called()

    @pytest.mark.asyncio
    async def test_transcribe_buffer_produces_transcript(self) -> None:
        engine = WhisperSTTEngine()
        engine._is_streaming = True
        engine._transcripts = asyncio.Queue()

        # Mock the model
        mock_segment = MagicMock()
        mock_segment.text = " Привіт, шукаю шини "
        mock_segment.avg_logprob = -0.2

        mock_info = MagicMock()
        mock_info.language = "uk"

        mock_model = MagicMock()
        mock_model.transcribe = MagicMock(return_value=([mock_segment], mock_info))
        engine._model = mock_model

        engine._audio_buffer = bytearray(b"\x00\x01" * 16000)

        with patch("asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
            mock_to_thread.return_value = ([mock_segment], mock_info)
            await engine._transcribe_buffer()

        assert not engine._transcripts.empty()
        transcript = await engine._transcripts.get()
        assert transcript.text == "Привіт, шукаю шини"
        assert transcript.is_final is True
        assert transcript.confidence > 0.0

    @pytest.mark.asyncio
    async def test_transcribe_empty_buffer_noop(self) -> None:
        engine = WhisperSTTEngine()
        engine._audio_buffer = bytearray()
        engine._model = MagicMock()
        engine._transcripts = asyncio.Queue()

        await engine._transcribe_buffer()
        assert engine._transcripts.empty()

    @pytest.mark.asyncio
    async def test_get_transcripts_yields_results(self) -> None:
        engine = WhisperSTTEngine()
        engine._is_streaming = False
        engine._transcripts = asyncio.Queue()

        t = Transcript(text="Тест", is_final=True, confidence=0.9, language="uk")
        await engine._transcripts.put(t)

        results = []
        async for transcript in engine.get_transcripts():
            results.append(transcript)

        assert len(results) == 1
        assert results[0].text == "Тест"

    @pytest.mark.asyncio
    async def test_ensure_model_import_error(self) -> None:
        engine = WhisperSTTEngine()

        with (
            patch.dict("sys.modules", {"faster_whisper": None}),
            pytest.raises(ImportError),
        ):
                await engine._ensure_model()


class TestFallbackSTTEngine:
    """Test Fallback STT engine (Whisper → Google)."""

    @pytest.mark.asyncio
    async def test_uses_whisper_when_available(self) -> None:
        from src.stt.fallback_stt import FallbackSTTEngine

        engine = FallbackSTTEngine()

        with patch.object(engine._whisper, "start_stream", new_callable=AsyncMock):
            await engine.start_stream(STTConfig())
            assert engine.active_provider == "whisper"
            assert engine._active_engine is engine._whisper

    @pytest.mark.asyncio
    async def test_falls_back_to_google_on_error(self) -> None:
        from src.stt.fallback_stt import FallbackSTTEngine

        engine = FallbackSTTEngine()

        with (
            patch.object(
                engine._whisper,
                "start_stream",
                new_callable=AsyncMock,
                side_effect=RuntimeError("Whisper unavailable"),
            ),
            patch.object(engine._google, "start_stream", new_callable=AsyncMock),
        ):
            await engine.start_stream(STTConfig())
            assert engine.active_provider == "google"
            assert engine._active_engine is engine._google

    @pytest.mark.asyncio
    async def test_feed_audio_delegates(self) -> None:
        from src.stt.fallback_stt import FallbackSTTEngine

        engine = FallbackSTTEngine()
        engine._active_engine = MagicMock()
        engine._active_engine.feed_audio = AsyncMock()

        await engine.feed_audio(b"\x00" * 640)
        engine._active_engine.feed_audio.assert_called_once_with(b"\x00" * 640)

    @pytest.mark.asyncio
    async def test_stop_stream_delegates(self) -> None:
        from src.stt.fallback_stt import FallbackSTTEngine

        engine = FallbackSTTEngine()
        engine._active_engine = MagicMock()
        engine._active_engine.stop_stream = AsyncMock()

        await engine.stop_stream()
        engine._active_engine.stop_stream.assert_called_once()
