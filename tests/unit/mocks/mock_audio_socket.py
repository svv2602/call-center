"""Mock AudioSocket connection for unit tests."""

from __future__ import annotations

import asyncio  # noqa: TC003


class MockAudioSocketConnection:
    """Mock AudioSocket — records sent audio bytes.

    Supports simulating mid-stream disconnect via fail_after.
    """

    def __init__(self, *, closed: bool = False, fail_after: int | None = None) -> None:
        self.sent_chunks: list[bytes] = []
        self._closed = closed
        self._fail_after = fail_after

    @property
    def is_closed(self) -> bool:
        return self._closed

    def close_connection(self) -> None:
        self._closed = True

    async def send_audio(
        self, audio_data: bytes, cancel_event: asyncio.Event | None = None
    ) -> bool:
        """Send audio data.  Returns True if interrupted by cancel_event."""
        if self._closed:
            return False
        if cancel_event is not None and cancel_event.is_set():
            return True
        self.sent_chunks.append(audio_data)
        if self._fail_after and len(self.sent_chunks) >= self._fail_after:
            self._closed = True
            raise ConnectionResetError("mock disconnect")
        return False

    @property
    def sent_bytes(self) -> bytes:
        return b"".join(self.sent_chunks)
