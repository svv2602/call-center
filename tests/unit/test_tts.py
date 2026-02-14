"""Unit tests for TTS module using MockTTSEngine."""

import pytest

from src.tts.base import TTSConfig
from tests.unit.mocks.mock_tts import MockTTSEngine


class TestMockTTSEngine:
    """Test mock TTS engine functionality."""

    @pytest.mark.asyncio
    async def test_synthesize_returns_audio(self) -> None:
        engine = MockTTSEngine()
        audio = await engine.synthesize("Привіт")
        assert len(audio) > 0
        assert isinstance(audio, bytes)

    @pytest.mark.asyncio
    async def test_audio_length_proportional_to_words(self) -> None:
        engine = MockTTSEngine(frames_per_word=8)
        short = await engine.synthesize("Привіт")
        long = await engine.synthesize("Добрий день, як справи у вас")
        assert len(long) > len(short)

    @pytest.mark.asyncio
    async def test_synthesize_stream(self) -> None:
        engine = MockTTSEngine()
        text = "Перше речення. Друге речення. Третє."
        chunks = []
        async for chunk in engine.synthesize_stream(text):
            chunks.append(chunk)
        assert len(chunks) == 3

    @pytest.mark.asyncio
    async def test_synthesize_count(self) -> None:
        engine = MockTTSEngine()
        await engine.synthesize("Один")
        await engine.synthesize("Два")
        assert engine.synthesize_count == 2

    @pytest.mark.asyncio
    async def test_error_on_synthesize(self) -> None:
        engine = MockTTSEngine(error=RuntimeError("TTS unavailable"))
        with pytest.raises(RuntimeError, match="TTS unavailable"):
            await engine.synthesize("test")


class TestTTSConfig:
    """Test TTS configuration defaults."""

    def test_default_language(self) -> None:
        config = TTSConfig()
        assert config.language_code == "uk-UA"

    def test_default_voice(self) -> None:
        config = TTSConfig()
        assert config.voice_name == "uk-UA-Standard-A"

    def test_default_speaking_rate(self) -> None:
        config = TTSConfig()
        assert config.speaking_rate == 1.0

    def test_default_sample_rate(self) -> None:
        config = TTSConfig()
        assert config.sample_rate_hertz == 16000
