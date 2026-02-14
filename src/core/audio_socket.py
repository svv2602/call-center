"""AudioSocket protocol parser and TCP server.

Implements the Asterisk AudioSocket protocol:
  Packet: [type:1B][length:2B big-endian][payload:NB]

Packet types:
  0x01 = UUID (channel ID, first packet after connection)
  0x10 = Audio data (16 kHz, 16-bit signed linear PCM, little-endian)
  0x00 = Hangup
  0xFF = Error
"""

from __future__ import annotations

import asyncio
import enum
import logging
import struct
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

logger = logging.getLogger(__name__)

# AudioSocket constants
HEADER_SIZE = 3  # 1 byte type + 2 bytes length
AUDIO_SAMPLE_RATE = 16000
AUDIO_FRAME_BYTES = 640  # 20 ms × 16000 Hz × 2 bytes/sample
AUDIO_FRAME_DURATION_MS = 20


class PacketType(enum.IntEnum):
    """AudioSocket packet types."""

    HANGUP = 0x00
    UUID = 0x01
    AUDIO = 0x10
    ERROR = 0xFF


@dataclass(slots=True)
class AudioSocketPacket:
    """Parsed AudioSocket packet."""

    type: PacketType
    payload: bytes


async def read_packet(reader: asyncio.StreamReader) -> AudioSocketPacket | None:
    """Read and parse one AudioSocket packet from the stream.

    Returns None on EOF (connection closed).
    """
    header = await reader.readexactly(HEADER_SIZE)
    ptype = PacketType(header[0])
    length = struct.unpack("!H", header[1:3])[0]

    payload = b""
    if length > 0:
        payload = await reader.readexactly(length)

    return AudioSocketPacket(type=ptype, payload=payload)


def build_audio_packet(audio_data: bytes) -> bytes:
    """Build an AudioSocket audio packet (type 0x10) for sending to Asterisk."""
    header = struct.pack("!BH", PacketType.AUDIO, len(audio_data))
    return header + audio_data


def parse_uuid(payload: bytes) -> uuid.UUID:
    """Parse UUID from a type-0x01 packet payload."""
    return uuid.UUID(bytes=payload[:16])


class AudioSocketConnection:
    """Represents a single AudioSocket connection from Asterisk.

    Handles bidirectional audio: reads incoming audio packets and
    provides a method to send outbound audio back to Asterisk.
    """

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        channel_uuid: uuid.UUID,
    ) -> None:
        self.reader = reader
        self.writer = writer
        self.channel_uuid = channel_uuid
        self._closed = False

    @property
    def is_closed(self) -> bool:
        return self._closed

    async def read_audio_packet(self) -> AudioSocketPacket | None:
        """Read the next packet. Returns None on EOF or hangup."""
        if self._closed:
            return None
        try:
            packet = await read_packet(self.reader)
        except (asyncio.IncompleteReadError, ConnectionResetError, OSError):
            self._closed = True
            return None

        if packet is not None and packet.type == PacketType.HANGUP:
            self._closed = True

        return packet

    async def send_audio(self, audio_data: bytes) -> None:
        """Send audio data back to Asterisk as 0x10 packets.

        Splits data into AUDIO_FRAME_BYTES-sized chunks to maintain
        proper 20 ms frame timing.
        """
        if self._closed:
            return

        offset = 0
        while offset < len(audio_data):
            chunk = audio_data[offset : offset + AUDIO_FRAME_BYTES]
            self.writer.write(build_audio_packet(chunk))
            offset += AUDIO_FRAME_BYTES

        try:
            await self.writer.drain()
        except (ConnectionResetError, OSError):
            self._closed = True

    async def close(self) -> None:
        """Close the connection."""
        if not self._closed:
            self._closed = True
            try:
                self.writer.close()
                await self.writer.wait_closed()
            except OSError:
                pass


class AudioSocketServer:
    """TCP server that accepts AudioSocket connections from Asterisk.

    Each connection is handled in a separate asyncio.Task. A callback is
    invoked for every new connection with the AudioSocketConnection object.
    """

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 9092,
        on_connection: Callable[..., Coroutine[Any, Any, None]] | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self._on_connection = on_connection
        self._server: asyncio.Server | None = None
        self._tasks: set[asyncio.Task[None]] = set()

    async def start(self) -> None:
        """Start listening for AudioSocket connections."""
        self._server = await asyncio.start_server(
            self._handle_client,
            self.host,
            self.port,
        )
        addrs = ", ".join(str(s.getsockname()) for s in self._server.sockets)
        logger.info("AudioSocket server listening on %s", addrs)

    async def stop(self) -> None:
        """Stop the server and close all active connections."""
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            logger.info("AudioSocket server stopped accepting connections")

        # Cancel all active connection tasks
        for task in self._tasks:
            task.cancel()

        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
            logger.info("All AudioSocket connections closed (%d)", len(self._tasks))
            self._tasks.clear()

    @property
    def active_connections(self) -> int:
        """Number of currently active connections."""
        return len(self._tasks)

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle a new TCP connection — read UUID, then delegate to callback."""
        peer = writer.get_extra_info("peername")
        logger.info("New AudioSocket connection from %s", peer)

        try:
            # First packet must be UUID (type 0x01)
            packet = await asyncio.wait_for(read_packet(reader), timeout=5.0)
        except (TimeoutError, asyncio.IncompleteReadError, OSError) as exc:
            logger.warning("Failed to read UUID from %s: %s", peer, exc)
            writer.close()
            return

        if packet is None or packet.type != PacketType.UUID:
            logger.warning(
                "Expected UUID packet from %s, got type=%s",
                peer,
                packet.type if packet else "EOF",
            )
            writer.close()
            return

        channel_uuid = parse_uuid(packet.payload)
        logger.info("AudioSocket connection %s: channel_uuid=%s", peer, channel_uuid)

        conn = AudioSocketConnection(reader, writer, channel_uuid)

        # Run the connection handler in a tracked task
        task = asyncio.current_task()
        if task is not None:
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

        if self._on_connection is not None:
            try:
                await self._on_connection(conn)
            except Exception:
                logger.exception("Error handling connection %s", channel_uuid)
            finally:
                await conn.close()
                logger.info("AudioSocket connection ended: %s", channel_uuid)
        else:
            await conn.close()
