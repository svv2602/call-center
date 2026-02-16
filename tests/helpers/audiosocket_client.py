"""AudioSocket test client — simulates Asterisk connections for testing.

Implements the AudioSocket protocol (client side) for integration and E2E tests.
Protocol: [type:1B][length:2B BE][payload:NB]
Types: 0x01=UUID, 0x10=audio, 0x00=hangup, 0xFF=error
Audio: 16kHz, 16-bit signed linear PCM, little-endian
"""

from __future__ import annotations

import asyncio
import contextlib
import struct
import uuid
from dataclasses import dataclass, field

AUDIO_FRAME_BYTES = 640  # 20ms at 16kHz 16-bit mono


@dataclass
class ReceivedPacket:
    """A packet received from the Call Processor."""

    type: int
    payload: bytes


@dataclass
class AudioSocketTestClient:
    """Client-side mock of an Asterisk AudioSocket connection.

    Usage:
        client = AudioSocketTestClient()
        await client.connect("127.0.0.1", 9092)
        # Server receives UUID, starts pipeline

        # Send audio frames (simulating caller speech)
        await client.send_silence(duration_ms=500)

        # Read response audio from server
        packets = await client.read_packets(timeout=2.0)

        # Hang up
        await client.hangup()
        await client.close()
    """

    channel_uuid: uuid.UUID = field(default_factory=uuid.uuid4)
    _reader: asyncio.StreamReader | None = field(default=None, repr=False)
    _writer: asyncio.StreamWriter | None = field(default=None, repr=False)

    async def connect(self, host: str = "127.0.0.1", port: int = 9092) -> None:
        """Connect to the AudioSocket server and send the UUID packet."""
        self._reader, self._writer = await asyncio.open_connection(host, port)
        # Send UUID packet (type 0x01)
        uuid_payload = self.channel_uuid.bytes
        header = struct.pack("!BH", 0x01, len(uuid_payload))
        self._writer.write(header + uuid_payload)
        await self._writer.drain()

    async def send_audio_frame(self, audio: bytes) -> None:
        """Send a single audio frame (should be 640 bytes for 20ms)."""
        assert self._writer is not None, "Not connected"
        header = struct.pack("!BH", 0x10, len(audio))
        self._writer.write(header + audio)
        await self._writer.drain()

    async def send_audio_stream(self, audio_bytes: bytes, frame_delay: float = 0.0) -> None:
        """Send audio split into 640-byte frames with optional inter-frame delay."""
        for i in range(0, len(audio_bytes), AUDIO_FRAME_BYTES):
            chunk = audio_bytes[i : i + AUDIO_FRAME_BYTES]
            if len(chunk) < AUDIO_FRAME_BYTES:
                chunk = chunk + b"\x00" * (AUDIO_FRAME_BYTES - len(chunk))
            await self.send_audio_frame(chunk)
            if frame_delay > 0:
                await asyncio.sleep(frame_delay)

    async def send_silence(self, duration_ms: int = 500) -> None:
        """Send silence frames for the given duration."""
        num_frames = duration_ms // 20
        silence_frame = b"\x00" * AUDIO_FRAME_BYTES
        for _ in range(num_frames):
            await self.send_audio_frame(silence_frame)

    async def read_packet(self, timeout: float = 5.0) -> ReceivedPacket | None:
        """Read one packet from the server. Returns None on EOF."""
        assert self._reader is not None, "Not connected"
        try:
            header = await asyncio.wait_for(self._reader.readexactly(3), timeout=timeout)
        except (TimeoutError, asyncio.IncompleteReadError):
            return None

        ptype = header[0]
        length = struct.unpack("!H", header[1:3])[0]

        payload = b""
        if length > 0:
            try:
                payload = await asyncio.wait_for(self._reader.readexactly(length), timeout=timeout)
            except (TimeoutError, asyncio.IncompleteReadError):
                return None

        return ReceivedPacket(type=ptype, payload=payload)

    async def read_packets(
        self, timeout: float = 2.0, max_packets: int = 500
    ) -> list[ReceivedPacket]:
        """Read packets until timeout or max_packets reached."""
        packets: list[ReceivedPacket] = []
        for _ in range(max_packets):
            pkt = await self.read_packet(timeout=timeout)
            if pkt is None:
                break
            packets.append(pkt)
            if pkt.type == 0x00:  # hangup
                break
        return packets

    async def read_audio_response(self, timeout: float = 3.0) -> bytes:
        """Read audio packets until timeout, return concatenated audio bytes."""
        audio = bytearray()
        while True:
            pkt = await self.read_packet(timeout=timeout)
            if pkt is None:
                break
            if pkt.type == 0x10:  # audio
                audio.extend(pkt.payload)
            elif pkt.type == 0x00:  # hangup
                break
        return bytes(audio)

    async def hangup(self) -> None:
        """Send a hangup packet (type 0x00)."""
        assert self._writer is not None, "Not connected"
        header = struct.pack("!BH", 0x00, 0)
        self._writer.write(header)
        await self._writer.drain()

    async def close(self) -> None:
        """Close the connection."""
        if self._writer is not None:
            self._writer.close()
            with contextlib.suppress(Exception):
                await self._writer.wait_closed()
            self._writer = None
            self._reader = None
