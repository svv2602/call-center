"""Tests for StreamingTTSSynthesizer — Layer 3 of the streaming pipeline."""

from __future__ import annotations

import pytest

from src.core.sentence_buffer import SentenceReady
from src.llm.models import StreamDone, ToolCallDelta, ToolCallEnd, ToolCallStart, Usage
from src.tts.streaming_tts import AudioReady, StreamingTTSSynthesizer, synthesize_stream
from tests.unit.mocks.mock_tts import MockTTSEngine


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _aiter(*events):
    """Create an async iterator from a sequence of events."""
    for e in events:
        yield e


async def _collect(aiter):
    """Collect all items from an async iterator."""
    return [item async for item in aiter]


# ---------------------------------------------------------------------------
# AudioReady dataclass
# ---------------------------------------------------------------------------

class TestAudioReadyDataclass:
    def test_fields(self):
        ev = AudioReady(audio=b"\x00\x01", text="hello")
        assert ev.audio == b"\x00\x01"
        assert ev.text == "hello"

    def test_frozen(self):
        ev = AudioReady(audio=b"\x00", text="hi")
        with pytest.raises(AttributeError):
            ev.text = "changed"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Basic synthesis
# ---------------------------------------------------------------------------

class TestBasicSynthesis:
    @pytest.mark.asyncio
    async def test_single_sentence(self):
        tts = MockTTSEngine()
        synth = StreamingTTSSynthesizer(tts)
        events = await _collect(
            synth.process(_aiter(SentenceReady(text="Привіт, як справи?")))
        )
        assert len(events) == 1
        assert isinstance(events[0], AudioReady)
        assert len(events[0].audio) > 0

    @pytest.mark.asyncio
    async def test_two_sentences(self):
        tts = MockTTSEngine()
        synth = StreamingTTSSynthesizer(tts)
        events = await _collect(
            synth.process(
                _aiter(
                    SentenceReady(text="Перше речення."),
                    SentenceReady(text="Друге речення."),
                )
            )
        )
        assert len(events) == 2
        assert all(isinstance(e, AudioReady) for e in events)
        assert tts.synthesize_count == 2

    @pytest.mark.asyncio
    async def test_preserves_original_text(self):
        tts = MockTTSEngine()
        synth = StreamingTTSSynthesizer(tts)
        text = "Шини 225/45 R17 зимові."
        events = await _collect(
            synth.process(_aiter(SentenceReady(text=text)))
        )
        assert events[0].text == text


# ---------------------------------------------------------------------------
# Tool call passthrough
# ---------------------------------------------------------------------------

class TestToolCallPassthrough:
    @pytest.mark.asyncio
    async def test_tool_calls_pass_through(self):
        tts = MockTTSEngine()
        synth = StreamingTTSSynthesizer(tts)
        tc_start = ToolCallStart(id="t1", name="search_tires")
        tc_delta = ToolCallDelta(id="t1", arguments_chunk='{"size":')
        tc_end = ToolCallEnd(id="t1")
        events = await _collect(
            synth.process(_aiter(tc_start, tc_delta, tc_end))
        )
        assert events == [tc_start, tc_delta, tc_end]
        assert tts.synthesize_count == 0

    @pytest.mark.asyncio
    async def test_sentence_before_tool_call(self):
        tts = MockTTSEngine()
        synth = StreamingTTSSynthesizer(tts)
        tc_start = ToolCallStart(id="t1", name="check_availability")
        events = await _collect(
            synth.process(
                _aiter(
                    SentenceReady(text="Зараз перевірю наявність."),
                    tc_start,
                )
            )
        )
        assert len(events) == 2
        assert isinstance(events[0], AudioReady)
        assert events[1] is tc_start


# ---------------------------------------------------------------------------
# StreamDone passthrough
# ---------------------------------------------------------------------------

class TestStreamDonePassthrough:
    @pytest.mark.asyncio
    async def test_stream_done_passes_through(self):
        tts = MockTTSEngine()
        synth = StreamingTTSSynthesizer(tts)
        done = StreamDone(stop_reason="end_turn", usage=Usage(10, 20))
        events = await _collect(synth.process(_aiter(done)))
        assert events == [done]
        assert tts.synthesize_count == 0

    @pytest.mark.asyncio
    async def test_only_stream_done(self):
        tts = MockTTSEngine()
        synth = StreamingTTSSynthesizer(tts)
        done = StreamDone(stop_reason="end_turn", usage=Usage(0, 0))
        events = await _collect(synth.process(_aiter(done)))
        assert len(events) == 1
        assert isinstance(events[0], StreamDone)


# ---------------------------------------------------------------------------
# TTS error propagation
# ---------------------------------------------------------------------------

class TestTTSError:
    @pytest.mark.asyncio
    async def test_error_propagates(self):
        tts = MockTTSEngine(error=RuntimeError("TTS service unavailable"))
        synth = StreamingTTSSynthesizer(tts)
        with pytest.raises(RuntimeError, match="TTS service unavailable"):
            await _collect(
                synth.process(_aiter(SentenceReady(text="Привіт.")))
            )


# ---------------------------------------------------------------------------
# Convenience function
# ---------------------------------------------------------------------------

class TestConvenienceFunction:
    @pytest.mark.asyncio
    async def test_synthesize_stream_matches_class(self):
        tts = MockTTSEngine()
        done = StreamDone(stop_reason="end_turn", usage=Usage(5, 15))
        events = await _collect(
            synthesize_stream(
                _aiter(SentenceReady(text="Один."), done),
                tts=tts,
            )
        )
        assert len(events) == 2
        assert isinstance(events[0], AudioReady)
        assert isinstance(events[1], StreamDone)
        assert tts.synthesize_count == 1


# ---------------------------------------------------------------------------
# Integration-style: realistic event sequence
# ---------------------------------------------------------------------------

class TestRealisticSequence:
    @pytest.mark.asyncio
    async def test_sentences_tool_call_more_sentences_done(self):
        tts = MockTTSEngine()
        synth = StreamingTTSSynthesizer(tts)
        events = await _collect(
            synth.process(
                _aiter(
                    SentenceReady(text="Вітаю!"),
                    SentenceReady(text="Зараз знайду шини."),
                    ToolCallStart(id="t1", name="search_tires"),
                    ToolCallDelta(id="t1", arguments_chunk='{"size":"225/45R17"}'),
                    ToolCallEnd(id="t1"),
                    SentenceReady(text="Знайдено 3 варіанти."),
                    StreamDone(stop_reason="end_turn", usage=Usage(100, 50)),
                )
            )
        )

        assert len(events) == 7
        # Sentences → AudioReady
        assert isinstance(events[0], AudioReady)
        assert events[0].text == "Вітаю!"
        assert isinstance(events[1], AudioReady)
        assert events[1].text == "Зараз знайду шини."
        # Tool call passthrough
        assert isinstance(events[2], ToolCallStart)
        assert isinstance(events[3], ToolCallDelta)
        assert isinstance(events[4], ToolCallEnd)
        # More audio
        assert isinstance(events[5], AudioReady)
        assert events[5].text == "Знайдено 3 варіанти."
        # Done
        assert isinstance(events[6], StreamDone)
        # Only 3 synthesize calls (3 sentences)
        assert tts.synthesize_count == 3
