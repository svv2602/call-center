"""TTS engine management — global engine reference and hot-reload."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.tts.base import TTSConfig
    from src.tts.google_tts import GoogleTTSEngine

__all__ = [
    "get_engine",
    "reinitialize_engine",
    "set_engine",
]

logger = logging.getLogger(__name__)

_engine_instance: GoogleTTSEngine | None = None


def set_engine(engine: GoogleTTSEngine | None) -> None:
    """Set the global TTS engine instance (called from main at startup)."""
    global _engine_instance
    _engine_instance = engine


def get_engine() -> GoogleTTSEngine | None:
    """Get the global TTS engine instance."""
    return _engine_instance


async def reinitialize_engine(config: TTSConfig) -> GoogleTTSEngine:
    """Create a new TTS engine with the given config, initialize it, and swap the global reference.

    Both active and new calls pick up the new engine via the _tts property
    (get_engine() is called on each synthesize, not cached at call start).
    """
    from src.tts.google_tts import GoogleTTSEngine

    new_engine = GoogleTTSEngine(config=config)
    await new_engine.initialize()
    set_engine(new_engine)
    logger.info(
        "TTS engine reinitialized with voice=%s, rate=%s, pitch=%s",
        config.voice_name,
        config.speaking_rate,
        config.pitch,
    )
    return new_engine
