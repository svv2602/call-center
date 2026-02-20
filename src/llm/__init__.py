"""Multi-provider LLM router with fallback and circuit breakers."""

from __future__ import annotations

from src.llm.models import (
    LLMResponse,
    LLMTask,
    ProviderType,
    StreamDone,
    StreamEvent,
    TextDelta,
    ToolCall,
    ToolCallDelta,
    ToolCallEnd,
    ToolCallStart,
    Usage,
)
from src.llm.router import LLMRouter

__all__ = [
    "LLMResponse",
    "LLMRouter",
    "LLMTask",
    "ProviderType",
    "StreamDone",
    "StreamEvent",
    "TextDelta",
    "ToolCall",
    "ToolCallDelta",
    "ToolCallEnd",
    "ToolCallStart",
    "Usage",
    "get_router",
    "set_router",
]

# Shared router reference — avoids __main__ vs src.main module identity issue.
_router_instance: LLMRouter | None = None


def set_router(router: LLMRouter | None) -> None:
    """Set the global LLM router instance (called from main at startup)."""
    global _router_instance
    _router_instance = router


def get_router() -> LLMRouter | None:
    """Get the global LLM router instance."""
    return _router_instance
