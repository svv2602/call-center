"""Admin API for TTS voice configuration.

Manage voice name, speaking rate, and pitch with Redis persistence and hot-reload.
"""

from __future__ import annotations

import base64
import json
import logging
import re
import struct
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from redis.asyncio import Redis

from src.api.auth import require_permission
from src.config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin/tts", tags=["tts-config"])

REDIS_KEY = "tts:config"

_redis: Redis | None = None

_perm_r = Depends(require_permission("configuration:read"))
_perm_w = Depends(require_permission("configuration:write"))

# Known Google Cloud TTS voices for uk-UA
KNOWN_VOICES = [
    "uk-UA-Wavenet-A",
    "uk-UA-Standard-A",
    "uk-UA-Neural2-A",
    "uk-UA-Chirp3-HD-Achernar",
    "uk-UA-Chirp3-HD-Aoede",
    "uk-UA-Chirp3-HD-Charon",
    "uk-UA-Chirp3-HD-Fenrir",
    "uk-UA-Chirp3-HD-Kore",
    "uk-UA-Chirp3-HD-Leda",
    "uk-UA-Chirp3-HD-Orus",
    "uk-UA-Chirp3-HD-Puck",
    "uk-UA-Chirp3-HD-Sulafat",
    "uk-UA-Chirp3-HD-Zephyr",
]


async def _get_redis() -> Redis:
    global _redis
    if _redis is None:
        settings = get_settings()
        _redis = Redis.from_url(settings.redis.url, decode_responses=True)
    return _redis


_BREAK_FIELDS = (
    "break_comma_ms",
    "break_period_ms",
    "break_exclamation_ms",
    "break_colon_ms",
    "break_semicolon_ms",
    "break_em_dash_ms",
)


class TTSConfigPatch(BaseModel):
    voice_name: str | None = None
    speaking_rate: float | None = None
    pitch: float | None = None
    break_comma_ms: int | None = None
    break_period_ms: int | None = None
    break_exclamation_ms: int | None = None
    break_colon_ms: int | None = None
    break_semicolon_ms: int | None = None
    break_em_dash_ms: int | None = None

    @field_validator("voice_name")
    @classmethod
    def validate_voice_name(cls, v: str | None) -> str | None:
        if v is not None and not re.match(r"^[a-zA-Z0-9_-]+$", v):
            raise ValueError("voice_name must match ^[a-zA-Z0-9_-]+$")
        return v

    @field_validator("speaking_rate")
    @classmethod
    def validate_speaking_rate(cls, v: float | None) -> float | None:
        if v is not None and not (0.25 <= v <= 4.0):
            raise ValueError("speaking_rate must be between 0.25 and 4.0")
        return v

    @field_validator("pitch")
    @classmethod
    def validate_pitch(cls, v: float | None) -> float | None:
        if v is not None and not (-20.0 <= v <= 20.0):
            raise ValueError("pitch must be between -20.0 and 20.0")
        return v

    @field_validator(*_BREAK_FIELDS)
    @classmethod
    def validate_break_ms(cls, v: int | None) -> int | None:
        if v is not None and not (0 <= v <= 1000):
            raise ValueError("break duration must be between 0 and 1000 ms")
        return v


def _get_env_defaults() -> dict[str, Any]:
    """Get TTS defaults from env vars / Settings."""
    settings = get_settings()
    tts = settings.google_tts
    return {
        "voice_name": tts.voice,
        "speaking_rate": tts.speaking_rate,
        "pitch": tts.pitch,
        "break_comma_ms": tts.break_comma_ms,
        "break_period_ms": tts.break_period_ms,
        "break_exclamation_ms": tts.break_exclamation_ms,
        "break_colon_ms": tts.break_colon_ms,
        "break_semicolon_ms": tts.break_semicolon_ms,
        "break_em_dash_ms": tts.break_em_dash_ms,
    }


async def _get_effective_config(redis: Redis) -> tuple[dict[str, Any], str]:
    """Return (config_dict, source) — merged Redis over env defaults."""
    env_defaults = _get_env_defaults()
    raw = await redis.get(REDIS_KEY)
    if raw:
        redis_config = json.loads(raw)
        merged = {**env_defaults, **redis_config}
        return merged, "redis"
    return env_defaults, "env"


def _pcm_to_wav(pcm: bytes, sample_rate: int = 8000, channels: int = 1, bits: int = 16) -> bytes:
    """Prepend a WAV/RIFF header to raw PCM data."""
    data_size = len(pcm)
    byte_rate = sample_rate * channels * (bits // 8)
    block_align = channels * (bits // 8)
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + data_size,
        b"WAVE",
        b"fmt ",
        16,  # fmt chunk size
        1,  # PCM format
        channels,
        sample_rate,
        byte_rate,
        block_align,
        bits,
        b"data",
        data_size,
    )
    return header + pcm


@router.get("/config")
async def get_tts_config(_: dict[str, Any] = _perm_r) -> dict[str, Any]:
    """Get current TTS configuration (Redis + env fallback)."""
    redis = await _get_redis()
    config, source = await _get_effective_config(redis)
    return {
        "config": config,
        "source": source,
        "known_voices": KNOWN_VOICES,
    }


@router.patch("/config")
async def update_tts_config(request: TTSConfigPatch, _: dict[str, Any] = _perm_w) -> dict[str, Any]:
    """Update TTS config in Redis (merge patch) and reinitialize engine."""
    redis = await _get_redis()

    # Load existing Redis config (not merged with env)
    raw = await redis.get(REDIS_KEY)
    config = json.loads(raw) if raw else {}

    # Merge patch
    if request.voice_name is not None:
        config["voice_name"] = request.voice_name
    if request.speaking_rate is not None:
        config["speaking_rate"] = request.speaking_rate
    if request.pitch is not None:
        config["pitch"] = request.pitch
    for field in _BREAK_FIELDS:
        value = getattr(request, field)
        if value is not None:
            config[field] = value

    await redis.set(REDIS_KEY, json.dumps(config))
    logger.info("TTS config updated: %s", config)

    # Reinitialize engine with new config
    effective, source = await _get_effective_config(redis)
    await _reinitialize_with_config(effective)

    return {"message": "TTS config updated", "config": effective, "source": source}


@router.post("/test")
async def test_tts(_: dict[str, Any] = _perm_w) -> dict[str, Any]:
    """Synthesize a test phrase with current config, return base64 WAV audio."""
    from src.tts import get_engine

    engine = get_engine()
    if engine is None:
        raise HTTPException(status_code=503, detail="TTS engine not initialized")

    test_phrase = "Привіт! Це тестове повідомлення системи синтезу мовлення."
    start = time.monotonic()
    try:
        pcm = await engine.synthesize(test_phrase)
        duration_ms = int((time.monotonic() - start) * 1000)
        wav = _pcm_to_wav(pcm)
        audio_b64 = base64.b64encode(wav).decode("ascii")
        return {
            "success": True,
            "audio_base64": audio_b64,
            "duration_ms": duration_ms,
            "phrase": test_phrase,
        }
    except Exception as exc:
        duration_ms = int((time.monotonic() - start) * 1000)
        logger.warning("TTS test synthesis failed: %s", exc)
        return {
            "success": False,
            "error": str(exc)[:200],
            "duration_ms": duration_ms,
        }


@router.post("/config/reset")
async def reset_tts_config(_: dict[str, Any] = _perm_w) -> dict[str, Any]:
    """Delete Redis config, reinitialize with env defaults."""
    redis = await _get_redis()
    await redis.delete(REDIS_KEY)
    logger.info("TTS config reset to env defaults")

    env_defaults = _get_env_defaults()
    await _reinitialize_with_config(env_defaults)

    return {"message": "TTS config reset to defaults", "config": env_defaults, "source": "env"}


async def _reinitialize_with_config(config: dict[str, Any]) -> None:
    """Reinitialize the global TTS engine with the given config dict."""
    from src.tts import reinitialize_engine
    from src.tts.base import TTSConfig

    tts_config = TTSConfig(
        voice_name=config.get("voice_name", "uk-UA-Wavenet-A"),
        speaking_rate=config.get("speaking_rate", 0.93),
        pitch=config.get("pitch", -1.0),
        break_comma_ms=config.get("break_comma_ms", 100),
        break_period_ms=config.get("break_period_ms", 200),
        break_exclamation_ms=config.get("break_exclamation_ms", 250),
        break_colon_ms=config.get("break_colon_ms", 200),
        break_semicolon_ms=config.get("break_semicolon_ms", 150),
        break_em_dash_ms=config.get("break_em_dash_ms", 150),
    )

    try:
        await reinitialize_engine(tts_config)
    except Exception:
        logger.exception("TTS engine reinitialize failed")
        raise HTTPException(status_code=500, detail="TTS engine reinitialization failed") from None
