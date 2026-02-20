"""Mock LLM router for streaming pipeline tests."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.llm.models import StreamEvent

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


class MockLLMRouter:
    """Mock LLM router — returns pre-configured stream event sequences.

    Each call to complete_stream pops the next response from the list.
    """

    def __init__(self, responses: list[list[StreamEvent]]) -> None:
        self._responses = list(responses)
        self._call_count = 0

    @property
    def call_count(self) -> int:
        return self._call_count

    async def complete_stream(
        self,
        task: Any,
        messages: Any,
        *,
        system: str | None = None,
        tools: Any = None,
        max_tokens: int = 1024,
        provider_override: str | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Async generator matching real LLMRouter.complete_stream signature."""
        self._call_count += 1
        if not self._responses:
            raise RuntimeError("MockLLMRouter: no more responses configured")
        events = self._responses.pop(0)
        for e in events:
            yield e


class ErrorLLMRouter:
    """LLM router that raises on complete_stream."""

    async def complete_stream(self, *args: Any, **kwargs: Any) -> AsyncIterator[StreamEvent]:
        raise RuntimeError("LLM provider down")
        yield  # make it a generator  # pragma: no cover
