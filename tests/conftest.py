"""Shared pytest fixtures for all test types."""

from __future__ import annotations

import uuid

import pytest

from src.core.call_session import CallSession
from src.stt.base import STTConfig, Transcript
from tests.unit.mocks.mock_stt import MockSTTEngine
from tests.unit.mocks.mock_tts import MockTTSEngine


@pytest.fixture
def call_session() -> CallSession:
    """Create a fresh CallSession for testing."""
    return CallSession(uuid.uuid4())


@pytest.fixture
def mock_stt() -> MockSTTEngine:
    """Create a MockSTTEngine with default transcripts."""
    return MockSTTEngine(
        transcripts=[
            Transcript(text="Привіт", is_final=True, confidence=0.95, language="uk-UA"),
        ]
    )


@pytest.fixture
def mock_tts() -> MockTTSEngine:
    """Create a MockTTSEngine."""
    return MockTTSEngine()


@pytest.fixture
def stt_config() -> STTConfig:
    """Create a default STTConfig."""
    return STTConfig()
