"""Echo cancellation for the audio pipeline.

Three levels of protection against bot echo returning via caller's microphone:
1. Asterisk DENOISE(rx) — ambient noise reduction (configured in extensions.conf)
2. speexdsp AEC — true acoustic echo cancellation using reference signal (pyaec)
3. Energy gate — suppresses residual echo frames below RMS threshold

Graceful degradation: pyaec not installed → energy gate only → passthrough.
"""

from __future__ import annotations

import array
import logging
import math
import time
from collections import deque
from dataclasses import dataclass

from src.monitoring.metrics import aec_frames_processed, aec_frames_suppressed, aec_processing_us

logger = logging.getLogger(__name__)

# Audio constants for 8kHz 16-bit signed linear PCM
_SAMPLE_RATE = 8000
_FRAME_SIZE = 160  # samples per frame (20ms @ 8kHz)
_FRAME_BYTES = _FRAME_SIZE * 2  # 320 bytes per frame
_SILENCE_FRAME = b"\x00" * _FRAME_BYTES
_MAX_FAR_END_FRAMES = 256  # ~5.12s of buffered far-end audio


@dataclass
class EchoCancellerConfig:
    """Configuration for the echo canceller."""

    enabled: bool = True
    frame_size: int = _FRAME_SIZE
    filter_length: int = 2048  # adaptive filter length (~256ms echo path)
    sample_rate: int = _SAMPLE_RATE
    energy_gate_enabled: bool = True
    energy_threshold_rms: float = 50.0
    energy_gate_only: bool = False  # True → skip AEC, use energy gate only


class FarEndBuffer:
    """Ring buffer for far-end (bot TTS) audio frames.

    Stores 320-byte frames in a deque. Used by AEC as the reference signal.
    Thread-safe for single event loop (deque operations are atomic in CPython).
    """

    def __init__(self, maxlen: int = _MAX_FAR_END_FRAMES) -> None:
        self._frames: deque[bytes] = deque(maxlen=maxlen)

    def push(self, audio_data: bytes) -> None:
        """Split audio into frame-sized chunks and append to buffer."""
        offset = 0
        while offset + _FRAME_BYTES <= len(audio_data):
            self._frames.append(audio_data[offset : offset + _FRAME_BYTES])
            offset += _FRAME_BYTES
        # Pad remainder if any
        if offset < len(audio_data):
            remainder = audio_data[offset:]
            padded = remainder + b"\x00" * (_FRAME_BYTES - len(remainder))
            self._frames.append(padded)

    def pop_frame(self) -> bytes:
        """Pop the oldest frame, or return silence if empty."""
        if self._frames:
            return self._frames.popleft()
        return _SILENCE_FRAME

    def clear(self) -> None:
        """Clear all buffered frames."""
        self._frames.clear()

    def __len__(self) -> int:
        return len(self._frames)


def _compute_rms(frame: bytes) -> float:
    """Compute RMS energy of a 16-bit signed PCM frame."""
    samples = array.array("h", frame)
    if not samples:
        return 0.0
    sum_sq = sum(s * s for s in samples)
    return math.sqrt(sum_sq / len(samples))


class EchoCanceller:
    """Acoustic echo canceller with energy gate fallback.

    Usage:
        ec = EchoCanceller(config, FarEndBuffer())
        # TTS path: record what the bot sends
        ec.record_far_end(tts_audio)
        # STT path: clean incoming audio
        cleaned = ec.process(near_end_frame, speaking=self._speaking)
    """

    def __init__(self, config: EchoCancellerConfig, buffer: FarEndBuffer) -> None:
        self._config = config
        self._buffer = buffer
        self._aec: object | None = None
        self._available = False

        if config.enabled and not config.energy_gate_only:
            try:
                import pyaec

                self._aec = pyaec.EchoCanceller.create(
                    config.frame_size, config.filter_length, config.sample_rate
                )
                self._available = True
                logger.info(
                    "AEC initialized: frame_size=%d, filter_length=%d",
                    config.frame_size,
                    config.filter_length,
                )
            except ImportError:
                logger.warning("pyaec not installed — falling back to energy gate only")
            except Exception:
                logger.warning("AEC init failed — falling back to energy gate", exc_info=True)

        mode = "aec" if self._available else ("gate" if config.energy_gate_enabled else "off")
        logger.info("EchoCanceller mode: %s", mode)

    @property
    def aec_available(self) -> bool:
        """Whether speexdsp AEC is available and initialized."""
        return self._available

    def process(self, near_end_frame: bytes, speaking: bool) -> bytes:
        """Process a near-end (microphone) audio frame.

        Args:
            near_end_frame: Raw 320-byte PCM frame from caller's microphone.
            speaking: Whether the bot is currently sending TTS audio.

        Returns:
            Cleaned audio frame (same size as input).
        """
        if not self._config.enabled:
            return near_end_frame

        # No echo to cancel if the bot isn't speaking
        if not speaking:
            return near_end_frame

        t0 = time.monotonic()
        result = near_end_frame

        # Ensure frame is correct size for AEC processing
        if len(near_end_frame) != _FRAME_BYTES:
            aec_frames_processed.labels(mode="passthrough").inc()
            return near_end_frame

        # Step 1: AEC (if available)
        if self._available and self._aec is not None:
            far_frame = self._buffer.pop_frame()
            try:
                result = self._aec.process(near_end_frame, far_frame)
                aec_frames_processed.labels(mode="aec").inc()
            except Exception:
                logger.debug("AEC process error, passing through", exc_info=True)
                aec_frames_processed.labels(mode="passthrough").inc()
        else:
            aec_frames_processed.labels(mode="gate").inc()

        # Step 2: Energy gate (suppress low-energy residual echo)
        if self._config.energy_gate_enabled:
            rms = _compute_rms(result)
            if rms < self._config.energy_threshold_rms:
                result = _SILENCE_FRAME
                aec_frames_suppressed.inc()

        elapsed_us = (time.monotonic() - t0) * 1_000_000
        aec_processing_us.observe(elapsed_us)

        return result

    def record_far_end(self, audio_data: bytes) -> None:
        """Record far-end (bot TTS) audio as AEC reference signal.

        Call this BEFORE sending audio to AudioSocket.
        """
        if self._config.enabled:
            self._buffer.push(audio_data)

    def clear_far_end(self) -> None:
        """Clear far-end buffer (call after bot finishes speaking)."""
        self._buffer.clear()
