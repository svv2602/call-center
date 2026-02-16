"""Unit tests for AudioSocket protocol parser."""

import asyncio
import struct
import uuid

import pytest

from src.core.audio_socket import (
    AUDIO_FRAME_BYTES,
    HEADER_SIZE,
    PacketType,
    build_audio_packet,
    parse_uuid,
    read_packet,
)


def _make_packet(ptype: int, payload: bytes) -> bytes:
    """Helper: build a raw AudioSocket packet."""
    return struct.pack("!BH", ptype, len(payload)) + payload


class TestPacketParsing:
    """Test AudioSocket packet parsing."""

    @pytest.mark.asyncio
    async def test_parse_uuid_packet(self) -> None:
        channel_uuid = uuid.uuid4()
        raw = _make_packet(PacketType.UUID, channel_uuid.bytes)
        reader = asyncio.StreamReader()
        reader.feed_data(raw)

        packet = await read_packet(reader)
        assert packet is not None
        assert packet.type == PacketType.UUID
        assert parse_uuid(packet.payload) == channel_uuid

    @pytest.mark.asyncio
    async def test_parse_audio_packet(self) -> None:
        audio_data = b"\x01\x02" * 320  # 640 bytes = 20ms frame
        raw = _make_packet(PacketType.AUDIO, audio_data)
        reader = asyncio.StreamReader()
        reader.feed_data(raw)

        packet = await read_packet(reader)
        assert packet is not None
        assert packet.type == PacketType.AUDIO
        assert packet.payload == audio_data
        assert len(packet.payload) == AUDIO_FRAME_BYTES

    @pytest.mark.asyncio
    async def test_parse_hangup_packet(self) -> None:
        raw = _make_packet(PacketType.HANGUP, b"")
        reader = asyncio.StreamReader()
        reader.feed_data(raw)

        packet = await read_packet(reader)
        assert packet is not None
        assert packet.type == PacketType.HANGUP
        assert packet.payload == b""

    @pytest.mark.asyncio
    async def test_parse_error_packet(self) -> None:
        error_msg = b"channel error"
        raw = _make_packet(PacketType.ERROR, error_msg)
        reader = asyncio.StreamReader()
        reader.feed_data(raw)

        packet = await read_packet(reader)
        assert packet is not None
        assert packet.type == PacketType.ERROR
        assert packet.payload == error_msg

    @pytest.mark.asyncio
    async def test_parse_incomplete_packet_buffers(self) -> None:
        """Test that incomplete data is buffered until full packet arrives."""
        audio_data = b"\xab" * 100
        raw = _make_packet(PacketType.AUDIO, audio_data)

        reader = asyncio.StreamReader()
        # Feed header first, then payload
        reader.feed_data(raw[:HEADER_SIZE])

        async def _delayed_feed() -> None:
            await asyncio.sleep(0.01)
            reader.feed_data(raw[HEADER_SIZE:])

        asyncio.get_event_loop().create_task(_delayed_feed())
        packet = await read_packet(reader)

        assert packet is not None
        assert packet.type == PacketType.AUDIO
        assert packet.payload == audio_data

    @pytest.mark.asyncio
    async def test_parse_eof_raises(self) -> None:
        """Test that EOF raises IncompleteReadError."""
        reader = asyncio.StreamReader()
        reader.feed_eof()

        with pytest.raises(asyncio.IncompleteReadError):
            await read_packet(reader)


class TestBuildAudioPacket:
    """Test outbound audio packet construction."""

    def test_build_audio_packet(self) -> None:
        audio_data = b"\xff" * 640
        packet = build_audio_packet(audio_data)

        assert packet[0] == PacketType.AUDIO
        length = struct.unpack("!H", packet[1:3])[0]
        assert length == 640
        assert packet[3:] == audio_data

    def test_build_small_packet(self) -> None:
        audio_data = b"\x00" * 100
        packet = build_audio_packet(audio_data)

        assert packet[0] == PacketType.AUDIO
        length = struct.unpack("!H", packet[1:3])[0]
        assert length == 100

    def test_packet_header_size(self) -> None:
        packet = build_audio_packet(b"")
        assert len(packet) == HEADER_SIZE


class TestParseUUID:
    """Test UUID parsing from packet payload."""

    def test_parse_valid_uuid(self) -> None:
        expected = uuid.uuid4()
        result = parse_uuid(expected.bytes)
        assert result == expected

    def test_parse_uuid_with_extra_bytes(self) -> None:
        expected = uuid.uuid4()
        payload = expected.bytes + b"\x00" * 10  # extra bytes ignored
        result = parse_uuid(payload)
        assert result == expected
