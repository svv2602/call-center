"""Unit tests for the AudioSocket test client."""

from __future__ import annotations

import asyncio
import struct
import uuid

import pytest

from tests.helpers.audiosocket_client import AUDIO_FRAME_BYTES, AudioSocketTestClient


class TestAudioSocketClientPackets:
    """Test packet construction."""

    def test_default_uuid(self) -> None:
        client = AudioSocketTestClient()
        assert isinstance(client.channel_uuid, uuid.UUID)

    def test_custom_uuid(self) -> None:
        uid = uuid.uuid4()
        client = AudioSocketTestClient(channel_uuid=uid)
        assert client.channel_uuid == uid


class TestAudioSocketClientConnect:
    """Test connection and UUID handshake."""

    @pytest.mark.asyncio
    async def test_connect_sends_uuid_packet(self) -> None:
        """Verify that connect() sends the UUID packet to the server."""
        received_data = bytearray()

        async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            data = await reader.read(1024)
            received_data.extend(data)
            writer.close()

        server = await asyncio.start_server(handle_client, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]

        client = AudioSocketTestClient()
        await client.connect("127.0.0.1", port)
        await asyncio.sleep(0.1)
        await client.close()
        server.close()
        await server.wait_closed()

        # Parse the received UUID packet
        assert len(received_data) >= 19  # 3 header + 16 UUID
        ptype = received_data[0]
        length = struct.unpack("!H", received_data[1:3])[0]
        assert ptype == 0x01  # UUID type
        assert length == 16
        received_uuid = uuid.UUID(bytes=bytes(received_data[3:19]))
        assert received_uuid == client.channel_uuid


class TestAudioSocketClientAudio:
    """Test audio frame sending."""

    @pytest.mark.asyncio
    async def test_send_silence(self) -> None:
        """Verify silence frames are correct size."""
        received = bytearray()

        async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            while True:
                data = await reader.read(4096)
                if not data:
                    break
                received.extend(data)
            writer.close()

        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]

        client = AudioSocketTestClient()
        await client.connect("127.0.0.1", port)
        await client.send_silence(duration_ms=100)  # 5 frames
        await client.close()
        server.close()
        await server.wait_closed()

        # UUID packet (19 bytes) + 5 audio packets (3 header + 640 payload each)
        uuid_size = 3 + 16
        audio_packet_size = 3 + AUDIO_FRAME_BYTES
        expected = uuid_size + 5 * audio_packet_size
        assert len(received) == expected

    @pytest.mark.asyncio
    async def test_read_packet_from_server(self) -> None:
        """Test reading a packet sent by the server."""

        async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            await reader.read(1024)  # Consume UUID
            # Send an audio packet back
            audio = b"\x42" * 640
            header = struct.pack("!BH", 0x10, len(audio))
            writer.write(header + audio)
            await writer.drain()
            await asyncio.sleep(0.1)
            writer.close()

        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]

        client = AudioSocketTestClient()
        await client.connect("127.0.0.1", port)
        pkt = await client.read_packet(timeout=2.0)
        await client.close()
        server.close()
        await server.wait_closed()

        assert pkt is not None
        assert pkt.type == 0x10
        assert len(pkt.payload) == 640

    @pytest.mark.asyncio
    async def test_hangup(self) -> None:
        """Verify hangup sends correct packet."""
        received = bytearray()

        async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            while True:
                data = await reader.read(4096)
                if not data:
                    break
                received.extend(data)
            writer.close()

        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]

        client = AudioSocketTestClient()
        await client.connect("127.0.0.1", port)
        await client.hangup()
        await client.close()
        server.close()
        await server.wait_closed()

        # Last 3 bytes should be hangup packet: type=0x00, length=0x0000
        hangup_bytes = bytes(received[-3:])
        assert hangup_bytes == struct.pack("!BH", 0x00, 0)
