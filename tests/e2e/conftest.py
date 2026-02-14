"""E2E test fixtures — self-contained AudioSocket server with mocked pipeline.

Starts a real AudioSocketServer on a random free port so E2E tests
don't require an external Call Processor or staging environment.
"""

from __future__ import annotations

from typing import Any

import pytest
import pytest_asyncio

from src.core.audio_socket import AudioSocketConnection, AudioSocketServer, PacketType
from tests.unit.mocks.mock_tts import MockTTSEngine


class MockLLMAgent:
    """Minimal LLM agent that returns a canned response."""

    async def process_message(
        self,
        user_text: str,
        conversation_history: list[dict[str, Any]],
        **kwargs: Any,
    ) -> tuple[str, list[dict[str, Any]]]:
        return "Дякую за звернення. Чим можу допомогти?", conversation_history


async def _mock_handle_call(conn: AudioSocketConnection) -> None:
    """Lightweight call handler that mimics the real pipeline.

    1. Sends greeting audio (TTS)
    2. Reads incoming audio packets
    3. On hangup/EOF — stops
    4. After receiving some audio — sends a canned LLM response via TTS
    """
    tts = MockTTSEngine()
    agent = MockLLMAgent()

    # Step 1: send greeting
    greeting_audio = await tts.synthesize(
        "Добрий день! Чим можу допомогти?"
    )
    await conn.send_audio(greeting_audio)

    # Step 2: read audio packets until hangup or EOF
    got_audio = False
    while not conn.is_closed:
        packet = await conn.read_audio_packet()
        if packet is None:
            break
        if packet.type == PacketType.HANGUP:
            break
        if packet.type == PacketType.AUDIO:
            got_audio = True
            continue

    # Step 3: if we got audio and connection is still open, send response
    if got_audio and not conn.is_closed:
        response_text, _ = await agent.process_message("test", [])
        response_audio = await tts.synthesize(response_text)
        await conn.send_audio(response_audio)


@pytest_asyncio.fixture
async def e2e_server():
    """Start an AudioSocketServer on a random free port for each test."""
    server = AudioSocketServer(
        host="127.0.0.1",
        port=0,
        on_connection=_mock_handle_call,
    )
    await server.start()
    # Get the actual port assigned by the OS
    port = server._server.sockets[0].getsockname()[1]
    yield "127.0.0.1", port
    await server.stop()


@pytest.fixture
def audiosocket_host(e2e_server: tuple[str, int]) -> str:
    """Host of the E2E AudioSocket server."""
    return e2e_server[0]


@pytest.fixture
def audiosocket_port(e2e_server: tuple[str, int]) -> int:
    """Port of the E2E AudioSocket server."""
    return e2e_server[1]
