"""Abstract base class for LLM providers."""

from __future__ import annotations

import abc
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.llm.models import LLMResponse


class AbstractProvider(abc.ABC):
    """Abstract LLM provider interface.

    All providers accept tools in Anthropic format (canonical source: src/agent/tools.py).
    OpenAI-compatible providers convert internally.
    """

    @abc.abstractmethod
    async def complete(
        self,
        messages: list[dict[str, Any]],
        system: str | None = None,
        max_tokens: int = 1024,
    ) -> LLMResponse:
        """Send a completion request without tools."""

    @abc.abstractmethod
    async def complete_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        system: str | None = None,
        max_tokens: int = 300,
    ) -> LLMResponse:
        """Send a completion request with tool definitions (Anthropic format)."""

    @abc.abstractmethod
    async def health_check(self) -> bool:
        """Check if the provider is reachable. Returns True if healthy."""

    async def close(self) -> None:  # noqa: B027
        """Close any open connections. Override if cleanup is needed."""
