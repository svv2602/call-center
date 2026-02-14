"""Unit tests for STT module using MockSTTEngine."""

import pytest

from src.stt.base import STTConfig, Transcript
from tests.unit.mocks.mock_stt import MockSTTEngine


class TestMockSTTEngine:
    """Test mock STT engine functionality."""

    @pytest.mark.asyncio
    async def test_returns_predefined_transcripts(self) -> None:
        transcripts = [
            Transcript(text="Привіт", is_final=True, confidence=0.95, language="uk-UA"),
            Transcript(text="Шукаю шини", is_final=True, confidence=0.9, language="uk-UA"),
        ]
        engine = MockSTTEngine(transcripts=transcripts)
        await engine.start_stream(STTConfig())

        results = []
        async for t in engine.get_transcripts():
            results.append(t)

        assert len(results) == 2
        assert results[0].text == "Привіт"
        assert results[1].text == "Шукаю шини"

    @pytest.mark.asyncio
    async def test_feed_audio_counts(self) -> None:
        engine = MockSTTEngine()
        await engine.start_stream(STTConfig())
        await engine.feed_audio(b"\x00" * 640)
        await engine.feed_audio(b"\x00" * 640)
        assert engine.feed_count == 2

    @pytest.mark.asyncio
    async def test_error_on_feed(self) -> None:
        engine = MockSTTEngine(error_on_feed=RuntimeError("STT error"))
        await engine.start_stream(STTConfig())

        with pytest.raises(RuntimeError, match="STT error"):
            await engine.feed_audio(b"\x00" * 640)

    @pytest.mark.asyncio
    async def test_empty_transcripts(self) -> None:
        engine = MockSTTEngine(transcripts=[])
        await engine.start_stream(STTConfig())

        results = []
        async for t in engine.get_transcripts():
            results.append(t)

        assert len(results) == 0


class TestSTTConfig:
    """Test STT configuration defaults."""

    def test_default_language(self) -> None:
        config = STTConfig()
        assert config.language_code == "uk-UA"

    def test_default_alternatives(self) -> None:
        config = STTConfig()
        assert config.alternative_languages == ["ru-RU"]

    def test_default_sample_rate(self) -> None:
        config = STTConfig()
        assert config.sample_rate_hertz == 16000

    def test_default_interim_results(self) -> None:
        config = STTConfig()
        assert config.interim_results is True


class TestTranscript:
    """Test Transcript dataclass."""

    def test_transcript_is_frozen(self) -> None:
        t = Transcript(text="test", is_final=True)
        with pytest.raises(AttributeError):
            t.text = "modified"  # type: ignore[misc]

    def test_final_transcript(self) -> None:
        t = Transcript(text="Привіт", is_final=True, confidence=0.95, language="uk-UA")
        assert t.is_final
        assert t.confidence == 0.95

    def test_interim_transcript(self) -> None:
        t = Transcript(text="Прив", is_final=False)
        assert not t.is_final
        assert t.confidence == 0.0  # default
