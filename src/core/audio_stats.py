"""Per-call summary of the audio the caller sent us.

A call that ends after the greeting with no customer turn has two causes that
look identical in `call_turns`: the caller said nothing, or the caller spoke
and the audio never reached STT. On 2026-09-21..23 ten calls in a row ended
that way and the question could not be answered — the container logs were
gone after a redeploy, and nothing about the inbound audio was ever stored.

This accumulator answers it from the row in `calls`: how many frames arrived,
how many were pure digital silence (no media at all — a real line always has
a noise floor), and how many were loud enough to be speech, split by whether
the bot was greeting or waiting for the caller.

The RMS is taken from the raw AudioSocket payload, before the echo canceller,
so an energy gate cannot hide the caller. Frames that arrive while the bot is
speaking after the greeting are counted in `frames` only: they carry our own
echo and would make a silent caller look talkative.
"""

from __future__ import annotations

import time
from typing import Any

from src.core.echo_canceller import _compute_rms

# 8 kHz / 16-bit telephony: line noise sits well under 100, speech at a normal
# distance from the handset is 500-3000. 300 keeps breathing and hiss out.
VOICED_RMS_THRESHOLD = 300.0


class _Phase:
    __slots__ = ("frames", "peak_rms", "voiced")

    def __init__(self) -> None:
        self.frames = 0
        self.voiced = 0
        self.peak_rms = 0.0

    def observe(self, rms: float) -> None:
        self.frames += 1
        if rms >= VOICED_RMS_THRESHOLD:
            self.voiced += 1
        if rms > self.peak_rms:
            self.peak_rms = rms

    def to_dict(self) -> dict[str, Any]:
        return {"frames": self.frames, "voiced": self.voiced, "peak_rms": round(self.peak_rms)}


class InboundAudioStats:
    """Counts what the caller's side of the line carried during one call."""

    def __init__(self) -> None:
        self.frames = 0
        self.zero_frames = 0
        self._greeting = _Phase()
        self._listening = _Phase()
        self._listening_t0: float | None = None
        self._first_voiced_ms: int | None = None

    def mark_listening(self) -> None:
        """The greeting is over; from here on the bot is waiting for the caller."""
        if self._listening_t0 is None:
            self._listening_t0 = time.monotonic()

    def observe(self, frame: bytes, *, bot_speaking: bool) -> None:
        self.frames += 1
        if not any(frame):
            self.zero_frames += 1
        if self._listening_t0 is None:
            self._greeting.observe(_compute_rms(frame))
            return
        if bot_speaking:
            return
        rms = _compute_rms(frame)
        self._listening.observe(rms)
        if self._first_voiced_ms is None and rms >= VOICED_RMS_THRESHOLD:
            self._first_voiced_ms = int((time.monotonic() - self._listening_t0) * 1000)

    def to_dict(self) -> dict[str, Any]:
        return {
            "frames": self.frames,
            "zero_frames": self.zero_frames,
            "voiced_rms_threshold": VOICED_RMS_THRESHOLD,
            "greeting": self._greeting.to_dict(),
            "listening": {
                **self._listening.to_dict(),
                "first_voiced_ms": self._first_voiced_ms,
            },
        }
