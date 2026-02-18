"""Data models for the multi-provider LLM router."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any


class LLMTask(enum.StrEnum):
    """Logical LLM task types routed to different providers."""

    AGENT = "agent"
    ARTICLE_PROCESSOR = "article_processor"
    QUALITY_SCORING = "quality_scoring"
    PROMPT_OPTIMIZER = "prompt_optimizer"


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
    """Token usage for an LLM response."""

    input_tokens: int
    output_tokens: int


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
DEFAULT_ROUTING_CONFIG: dict[str, Any] = {
    "providers": {
        "anthropic-sonnet": {
            "type": "anthropic",
            "model": "claude-sonnet-4-5-20250929",
            "api_key_env": "ANTHROPIC_API_KEY",
            "enabled": True,
        },
        "anthropic-haiku": {
            "type": "anthropic",
            "model": "claude-haiku-4-5-20251001",
            "api_key_env": "ANTHROPIC_API_KEY",
            "enabled": True,
        },
        "openai-gpt4o": {
            "type": "openai",
            "model": "gpt-4o",
            "api_key_env": "OPENAI_API_KEY",
            "base_url": "https://api.openai.com/v1",
            "enabled": False,
        },
        "openai-gpt4o-mini": {
            "type": "openai",
            "model": "gpt-4o-mini",
            "api_key_env": "OPENAI_API_KEY",
            "base_url": "https://api.openai.com/v1",
            "enabled": False,
        },
        "deepseek-chat": {
            "type": "deepseek",
            "model": "deepseek-chat",
            "api_key_env": "DEEPSEEK_API_KEY",
            "base_url": "https://api.deepseek.com/v1",
            "enabled": False,
        },
        "gemini-flash": {
            "type": "gemini",
            "model": "gemini-2.0-flash",
            "api_key_env": "GEMINI_API_KEY",
            "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
            "enabled": False,
        },
    },
    "tasks": {
        "agent": {"primary": "anthropic-sonnet", "fallbacks": []},
        "article_processor": {"primary": "anthropic-haiku", "fallbacks": []},
        "quality_scoring": {"primary": "anthropic-haiku", "fallbacks": []},
        "prompt_optimizer": {"primary": "anthropic-haiku", "fallbacks": []},
    },
}
