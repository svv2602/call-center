"""Data models for the multi-provider LLM router."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any


class LLMTask(enum.StrEnum):
    """Logical LLM task types routed to different providers."""

    AGENT = "agent"
    # Its own type rather than AGENT: the classifier runs once per live FSM turn
    # and used to be logged as an agent turn, which put 567 of the 3191 "agent"
    # rows of the last fortnight under the wrong task. Routing is unaffected —
    # the classifier always passes `provider_override`, which short-circuits the
    # task-config lookup in `LLMRouter._resolve_chain`.
    INTENT_CLASSIFIER = "intent_classifier"
    ARTICLE_PROCESSOR = "article_processor"
    QUALITY_SCORING = "quality_scoring"
    PROMPT_OPTIMIZER = "prompt_optimizer"
    REGEX_GENERATOR = "regex_generator"


class ProviderType(enum.StrEnum):
    """Supported LLM provider types."""

    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    DEEPSEEK = "deepseek"
    GEMINI = "gemini"


@dataclass(frozen=True)
class ToolCall:
    """A single tool call from the LLM response."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class Usage:
    """Token usage for an LLM response.

    ``cached_input_tokens`` counts how many of the ``input_tokens`` were served
    from the provider's automatic prompt cache — for OpenAI this comes from
    ``usage.prompt_tokens_details.cached_tokens`` (enabled by default since
    Oct 2024 for prefixes ≥1024 tokens). Anthropic reports it as
    ``usage.cache_read_input_tokens``. Field is 0 when the provider does not
    report cache stats.

    How much cheaper these are is a per-model number, not the ~50% this
    docstring used to claim. Measured across the ten providers in production it
    ranges from 0.10x to 0.25x of the input rate, and it moves when a provider
    reprices. Do not read a ratio off this comment — the authoritative value is
    ``llm_model_pricing.cached_input_price_per_1m``, refreshed daily from the
    LiteLLM catalog by ``src.tasks.pricing_sync``.
    """

    input_tokens: int
    output_tokens: int
    cached_input_tokens: int = 0


@dataclass(frozen=True)
class LLMResponse:
    """Normalized response from any LLM provider."""

    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str = "end_turn"  # "end_turn" | "tool_use" | "max_tokens"
    usage: Usage = field(default_factory=lambda: Usage(0, 0))
    provider: str = ""  # which provider key served this
    model: str = ""  # actual model used


# Default routing configuration (used when Redis is empty)
@dataclass(frozen=True)
class TextDelta:
    """Incremental text chunk from streaming."""

    text: str


@dataclass(frozen=True)
class ToolCallStart:
    """Tool call started — id and name known, arguments building."""

    id: str
    name: str


@dataclass(frozen=True)
class ToolCallDelta:
    """Incremental JSON fragment for tool call arguments."""

    id: str
    arguments_chunk: str


@dataclass(frozen=True)
class ToolCallEnd:
    """Tool call finished — arguments complete."""

    id: str


@dataclass(frozen=True)
class StreamDone:
    """Stream finished. Carries final aggregated metadata."""

    stop_reason: str  # "end_turn" | "tool_use" | "max_tokens"
    usage: Usage
    provider_key: str = ""


# Union type for type hints
StreamEvent = TextDelta | ToolCallStart | ToolCallDelta | ToolCallEnd | StreamDone


# Default routing configuration (used when Redis is empty)
DEFAULT_ROUTING_CONFIG: dict[str, Any] = {
    "providers": {
        "anthropic-sonnet": {
            "type": "anthropic",
            "model": "claude-sonnet-4-5-20250929",
            "api_key_env": "ANTHROPIC_API_KEY",
            "enabled": False,
        },
        "anthropic-haiku": {
            "type": "anthropic",
            "model": "claude-haiku-4-5-20251001",
            "api_key_env": "ANTHROPIC_API_KEY",
            "enabled": False,
        },
        "openai-gpt41-mini": {
            "type": "openai",
            "model": "gpt-4.1-mini",
            "api_key_env": "OPENAI_API_KEY",
            "base_url": "https://api.openai.com/v1",
            "enabled": True,
        },
        "openai-gpt41-nano": {
            "type": "openai",
            "model": "gpt-4.1-nano",
            "api_key_env": "OPENAI_API_KEY",
            "base_url": "https://api.openai.com/v1",
            "enabled": True,
        },
        "deepseek-chat": {
            "type": "deepseek",
            "model": "deepseek-chat",
            "api_key_env": "DEEPSEEK_API_KEY",
            "base_url": "https://api.deepseek.com/v1",
            "enabled": True,
        },
        "gemini-2.5-flash": {
            "type": "gemini",
            "model": "gemini-2.5-flash",
            "api_key_env": "GEMINI_API_KEY",
            "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
            "enabled": True,
        },
        "gemini-3-flash": {
            "type": "gemini",
            "model": "gemini-3-flash-preview",
            "api_key_env": "GEMINI_API_KEY",
            "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
            "enabled": True,
        },
        "openai-gpt5-mini": {
            "type": "openai",
            "model": "gpt-5-mini",
            "api_key_env": "OPENAI_API_KEY",
            "base_url": "https://api.openai.com/v1",
            "enabled": True,
        },
        "openai-gpt5-nano": {
            "type": "openai",
            "model": "gpt-5-nano",
            "api_key_env": "OPENAI_API_KEY",
            "base_url": "https://api.openai.com/v1",
            "enabled": True,
        },
        # Released 2026-09-22 at $0.10/$0.50 per 1M — cheaper than gpt-4.1-mini.
        # Not routed to any task: it is a reasoning-effort model like gpt-5-mini,
        # which failed the voice A/B both ways (slow on "low", no tool calls on
        # "minimal"). Measure it with `scripts/llm_ab_replay.py` before routing.
        # No `reasoning_effort` here on purpose — which levels it accepts is
        # part of what that replay finds out.
        "openai-gpt6-luna": {
            "type": "openai",
            "model": "gpt-6-luna",
            "api_key_env": "OPENAI_API_KEY",
            "base_url": "https://api.openai.com/v1",
            "enabled": True,
        },
    },
    "tasks": {
        "agent": {"primary": "gemini-2.5-flash", "fallbacks": ["openai-gpt41-mini"]},
        "article_processor": {"primary": "gemini-2.5-flash", "fallbacks": ["openai-gpt41-nano"]},
        "quality_scoring": {"primary": "gemini-2.5-flash", "fallbacks": ["openai-gpt41-mini"]},
        "prompt_optimizer": {"primary": "openai-gpt41-mini", "fallbacks": ["openai-gpt41-nano"]},
        "regex_generator": {"primary": "openai-gpt5-mini", "fallbacks": ["openai-gpt41-mini"]},
    },
}
