"""Unit tests for TTS module using MockTTSEngine."""

import pytest

from src.tts.base import TTSConfig
from src.tts.google_tts import GoogleTTSEngine
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
        assert config.voice_name == "uk-UA-Wavenet-A"

    def test_default_speaking_rate(self) -> None:
        config = TTSConfig()
        assert config.speaking_rate == 0.93

    def test_default_pitch(self) -> None:
        config = TTSConfig()
        assert config.pitch == -1.0

    def test_default_sample_rate(self) -> None:
        config = TTSConfig()
        assert config.sample_rate_hertz == 8000


class TestSSMLConversion:
    """Test SSML generation with natural pauses."""

    def test_simple_text_wrapped_in_speak(self) -> None:
        result = GoogleTTSEngine._to_ssml("Привіт")
        assert result == "<speak>Привіт</speak>"

    def test_xml_entities_escaped(self) -> None:
        result = GoogleTTSEngine._to_ssml("Ціна < 1000 & знижка > 5%")
        assert "&lt;" in result
        assert "&amp;" in result
        assert "&gt;" in result
        assert result.startswith("<speak>")
        assert result.endswith("</speak>")

    def test_break_after_comma(self) -> None:
        result = GoogleTTSEngine._to_ssml("Добрий день, як справи")
        assert ',<break time="100ms"/> як' in result

    def test_break_after_em_dash(self) -> None:
        result = GoogleTTSEngine._to_ssml("шини зимові — нешиповані")
        assert '—<break time="150ms"/> нешиповані' in result

    def test_break_after_colon(self) -> None:
        result = GoogleTTSEngine._to_ssml("Результат: знайдено 3 варіанти")
        assert ':<break time="200ms"/> знайдено' in result

    def test_multiple_breaks_in_one_text(self) -> None:
        text = "Добрий день, ось варіанти: Michelin — 2500 грн, Nokian — 2200 грн"
        result = GoogleTTSEngine._to_ssml(text)
        assert result.count("<break") == 5  # 2 commas + 1 colon + 2 em-dashes

    def test_no_breaks_in_clean_text(self) -> None:
        result = GoogleTTSEngine._to_ssml("Привіт як справи")
        assert "<break" not in result

    def test_comma_without_space_no_break(self) -> None:
        """Comma not followed by space (e.g. numbers) should not get a break."""
        result = GoogleTTSEngine._to_ssml("розмір 225/45R17")
        assert "<break" not in result


class TestSSMLFallback:
    """Test automatic SSML → plain text fallback for voices like Chirp3-HD."""

    @pytest.mark.asyncio
    async def test_ssml_failure_falls_back_to_plain_text(self) -> None:
        """When SSML fails (e.g. Chirp3-HD), engine retries with plain text."""
        from unittest.mock import AsyncMock

        engine = GoogleTTSEngine()
        engine._client = AsyncMock()
        engine._voice = "fake-voice"
        engine._audio_config = "fake-config"

        # First call (SSML) fails, second call (plain text) succeeds
        mock_response = AsyncMock()
        mock_response.audio_content = b"\x00" * 100
        engine._client.synthesize_speech = AsyncMock(
            side_effect=[Exception("SSML not supported"), mock_response]
        )

        audio = await engine._synthesize_uncached("Привіт")
        assert audio == b"\x00" * 100
        assert engine._ssml_supported is False
        assert engine._client.synthesize_speech.call_count == 2

    @pytest.mark.asyncio
    async def test_after_fallback_uses_plain_text_directly(self) -> None:
        """Once SSML is disabled, subsequent calls skip SSML attempt."""
        from unittest.mock import AsyncMock

        engine = GoogleTTSEngine()
        engine._client = AsyncMock()
        engine._voice = "fake-voice"
        engine._audio_config = "fake-config"
        engine._ssml_supported = False  # already downgraded

        mock_response = AsyncMock()
        mock_response.audio_content = b"\x00" * 50
        engine._client.synthesize_speech = AsyncMock(return_value=mock_response)

        await engine._synthesize_uncached("Тест")
        # Only one call — no SSML attempt
        assert engine._client.synthesize_speech.call_count == 1

    def test_ssml_supported_default_true(self) -> None:
        engine = GoogleTTSEngine()
        assert engine._ssml_supported is True

    @pytest.mark.asyncio
    async def test_stress_marks_stripped_for_chirp_voice(self) -> None:
        """Combining acute accents (U+0301) are stripped for Chirp voices."""
        from unittest.mock import AsyncMock

        engine = GoogleTTSEngine(config=TTSConfig(voice_name="uk-UA-Chirp3-HD-Accel"))
        engine._client = AsyncMock()
        engine._voice = "fake-voice"
        engine._audio_config = "fake-config"
        engine._ssml_supported = False

        mock_response = AsyncMock()
        mock_response.audio_content = b"\x00" * 50
        engine._client.synthesize_speech = AsyncMock(return_value=mock_response)

        await engine._synthesize_uncached("Дя\u0301кую за дзвіно\u0301к!")

        actual_input = engine._client.synthesize_speech.call_args.kwargs["input"]
        assert actual_input.text == "Дякую за дзвінок!"

    @pytest.mark.asyncio
    async def test_stress_marks_kept_for_wavenet_voice(self) -> None:
        """Combining acute accents (U+0301) are preserved for Wavenet/Standard voices."""
        from unittest.mock import AsyncMock

        engine = GoogleTTSEngine()  # default: uk-UA-Wavenet-A
        engine._client = AsyncMock()
        engine._voice = "fake-voice"
        engine._audio_config = "fake-config"
        engine._ssml_supported = False

        mock_response = AsyncMock()
        mock_response.audio_content = b"\x00" * 50
        engine._client.synthesize_speech = AsyncMock(return_value=mock_response)

        await engine._synthesize_uncached("Дя\u0301кую за дзвіно\u0301к!")

        actual_input = engine._client.synthesize_speech.call_args.kwargs["input"]
        assert actual_input.text == "Дя\u0301кую за дзвіно\u0301к!"
