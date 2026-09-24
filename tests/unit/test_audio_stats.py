"""Tests for the per-call inbound audio summary."""

import struct

from src.core.audio_stats import VOICED_RMS_THRESHOLD, InboundAudioStats


def _frame(amplitude: int, samples: int = 160) -> bytes:
    """A 20 ms frame whose RMS equals `amplitude` (constant signal)."""
    return struct.pack(f"<{samples}h", *([amplitude] * samples))


LOUD = _frame(int(VOICED_RMS_THRESHOLD) + 500)
QUIET = _frame(40)
ZERO = _frame(0)


class TestInboundAudioStats:
    def test_silent_line_is_told_apart_from_no_media(self) -> None:
        noise_floor = InboundAudioStats()
        no_media = InboundAudioStats()
        for stats, frame in ((noise_floor, QUIET), (no_media, ZERO)):
            stats.mark_listening()
            for _ in range(50):
                stats.observe(frame, bot_speaking=False)

        assert noise_floor.to_dict()["zero_frames"] == 0
        assert no_media.to_dict()["zero_frames"] == 50
        assert noise_floor.to_dict()["listening"]["voiced"] == 0

    def test_frames_before_listening_count_as_greeting(self) -> None:
        stats = InboundAudioStats()
        stats.observe(LOUD, bot_speaking=True)
        stats.observe(QUIET, bot_speaking=True)
        stats.mark_listening()
        stats.observe(LOUD, bot_speaking=False)

        d = stats.to_dict()
        assert d["frames"] == 3
        assert d["greeting"] == {
            "frames": 2,
            "voiced": 1,
            "peak_rms": int(VOICED_RMS_THRESHOLD) + 500,
        }
        assert d["listening"]["frames"] == 1
        assert d["listening"]["voiced"] == 1
        assert d["listening"]["first_voiced_ms"] is not None

    def test_echo_while_bot_speaks_after_greeting_is_not_caller_speech(self) -> None:
        stats = InboundAudioStats()
        stats.mark_listening()
        for _ in range(10):
            stats.observe(LOUD, bot_speaking=True)

        d = stats.to_dict()
        assert d["frames"] == 10
        assert d["listening"]["frames"] == 0
        assert d["listening"]["voiced"] == 0
        assert d["listening"]["first_voiced_ms"] is None

    def test_mark_listening_twice_keeps_the_first_start(self) -> None:
        stats = InboundAudioStats()
        stats.mark_listening()
        t0 = stats._listening_t0
        stats.mark_listening()
        assert stats._listening_t0 == t0


class _PacketConn:
    """Yields the given packets, then reports the socket closed."""

    def __init__(self, packets: list[object]) -> None:
        self._packets = list(packets)

    @property
    def is_closed(self) -> bool:
        return not self._packets

    async def read_audio_packet(self) -> object | None:
        return self._packets.pop(0) if self._packets else None


class TestPipelineWiring:
    async def test_reader_loop_feeds_raw_audio_to_the_stats(self) -> None:
        """The stats see every inbound frame, before the echo canceller touches it."""
        from unittest.mock import AsyncMock, MagicMock

        from src.core.audio_socket import AudioSocketPacket, PacketType
        from src.core.pipeline import CallPipeline

        stats = InboundAudioStats()
        stats.mark_listening()
        stt = MagicMock()
        stt.feed_audio = AsyncMock()
        # An echo canceller that mutes everything must not mute the stats.
        aec = MagicMock()
        aec.process = MagicMock(return_value=ZERO)
        conn = _PacketConn([AudioSocketPacket(type=PacketType.AUDIO, payload=LOUD)] * 3)
        pipeline = CallPipeline(
            conn=conn,
            stt=stt,
            tts=MagicMock(),
            agent=MagicMock(),
            session=MagicMock(),
            echo_canceller=aec,
            audio_stats=stats,
        )

        await pipeline._audio_reader_loop()

        d = stats.to_dict()
        assert d["frames"] == 3
        assert d["zero_frames"] == 0
        assert d["listening"]["voiced"] == 3
        assert stt.feed_audio.await_count == 3
