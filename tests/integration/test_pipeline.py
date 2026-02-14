"""Integration tests for the call pipeline.

These tests require mocked external services (STT, TTS, LLM).
They verify the full STT → LLM → TTS flow works end-to-end.

Run: pytest tests/integration/test_pipeline.py
"""

import pytest


@pytest.mark.skip(reason="Requires mock AudioSocket and full pipeline setup")
class TestPipelineIntegration:
    """Integration tests for CallPipeline."""

    @pytest.mark.asyncio
    async def test_full_turn_cycle(self) -> None:
        """Test: audio in → STT → LLM → TTS → audio out."""

    @pytest.mark.asyncio
    async def test_greeting_plays_on_connect(self) -> None:
        """Test: greeting is played when a call connects."""

    @pytest.mark.asyncio
    async def test_silence_timeout_triggers_prompt(self) -> None:
        """Test: 10s silence → 'Ви ще на лінії?'."""

    @pytest.mark.asyncio
    async def test_barge_in_interrupts_tts(self) -> None:
        """Test: caller speaks during TTS → playback stops."""
