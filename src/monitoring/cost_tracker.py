"""Call cost tracking and calculation.

Tracks STT, LLM, and TTS costs per call and records them
in both Prometheus metrics and PostgreSQL.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from src.monitoring.metrics import call_cost_usd

logger = logging.getLogger(__name__)


# Pricing per unit (approximate, as of 2026)
PRICING = {
    # Google Cloud STT: $0.006 per 15-second interval
    "google_stt_per_15s": 0.006,
    # Faster-Whisper: ~$150/month for GPU, amortized per call
    # Assuming 500 calls/day, ~30 days = 15000 calls/month
    "whisper_per_call": 0.01,  # $150 / 15000
    # Claude Sonnet: $3 / 1M input tokens, $15 / 1M output tokens
    "claude_sonnet_input_per_1k": 0.003,
    "claude_sonnet_output_per_1k": 0.015,
    # Claude Haiku: $0.25 / 1M input tokens, $1.25 / 1M output tokens
    "claude_haiku_input_per_1k": 0.00025,
    "claude_haiku_output_per_1k": 0.00125,
    # Google TTS: $4 per 1M characters (Standard)
    "google_tts_per_1k_chars": 0.004,
}


@dataclass
class CostBreakdown:
    """Cost breakdown for a single call."""

    stt_cost: float = 0.0
    llm_cost: float = 0.0
    tts_cost: float = 0.0
    stt_provider: str = "google"
    llm_model: str = "sonnet"
    _llm_input_tokens: int = 0
    _llm_output_tokens: int = 0
    _stt_seconds: float = 0.0
    _tts_characters: int = 0
    _tts_cached: int = 0

    @property
    def total_cost(self) -> float:
        return self.stt_cost + self.llm_cost + self.tts_cost

    def add_stt_usage(self, duration_seconds: float, provider: str = "google") -> None:
        """Record STT usage."""
        self.stt_provider = provider
        self._stt_seconds += duration_seconds

        if provider == "whisper":
            self.stt_cost += PRICING["whisper_per_call"]
        else:
            # Google charges per 15-second interval
            intervals = max(1, int(duration_seconds / 15) + 1)
            self.stt_cost += intervals * PRICING["google_stt_per_15s"]

    def add_llm_usage(
        self,
        input_tokens: int,
        output_tokens: int,
        model: str = "sonnet",
    ) -> None:
        """Record LLM token usage."""
        self.llm_model = model
        self._llm_input_tokens += input_tokens
        self._llm_output_tokens += output_tokens

        if "haiku" in model:
            self.llm_cost += (
                input_tokens / 1000 * PRICING["claude_haiku_input_per_1k"]
                + output_tokens / 1000 * PRICING["claude_haiku_output_per_1k"]
            )
        else:
            self.llm_cost += (
                input_tokens / 1000 * PRICING["claude_sonnet_input_per_1k"]
                + output_tokens / 1000 * PRICING["claude_sonnet_output_per_1k"]
            )

    def add_tts_usage(self, characters: int, cached: bool = False) -> None:
        """Record TTS usage."""
        if cached:
            self._tts_cached += characters
        else:
            self._tts_characters += characters
            self.tts_cost += characters / 1000 * PRICING["google_tts_per_1k_chars"]

    def to_dict(self) -> dict[str, Any]:
        """Convert to dict for JSONB storage."""
        return {
            "stt_cost": round(self.stt_cost, 6),
            "llm_cost": round(self.llm_cost, 6),
            "tts_cost": round(self.tts_cost, 6),
            "total_cost": round(self.total_cost, 6),
            "stt_provider": self.stt_provider,
            "llm_model": self.llm_model,
            "llm_input_tokens": self._llm_input_tokens,
            "llm_output_tokens": self._llm_output_tokens,
            "stt_seconds": round(self._stt_seconds, 1),
            "tts_characters": self._tts_characters,
            "tts_cached_characters": self._tts_cached,
        }

    def record_metrics(self) -> None:
        """Record cost to Prometheus histogram."""
        call_cost_usd.observe(self.total_cost)
