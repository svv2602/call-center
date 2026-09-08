"""Targeted field parsers for the fitting FSM (Wave 5-A, T19).

One module per parser, named after the `StateConfig.parser` value that
addresses it, plus `base.py` (the contract) and `registry.py` (the map).

Nothing here is wired into the call flow yet — `src/core/pipeline.py` still
calls the detectors directly, and switching it over is Wave 6-B (T21).
"""

from __future__ import annotations

from src.agent.parsers.base import (
    APPLY_THRESHOLD,
    NOT_MENTIONED,
    FieldParser,
    ParseContext,
    ParseOutcome,
    ParseStatus,
)
from src.agent.parsers.registry import (
    NON_FIELD_PARSERS,
    PARSERS,
    PASSIVE_PARSERS,
    get_parser,
    unregistered_state_parsers,
)

__all__ = [
    "APPLY_THRESHOLD",
    "NON_FIELD_PARSERS",
    "NOT_MENTIONED",
    "PARSERS",
    "PASSIVE_PARSERS",
    "FieldParser",
    "ParseContext",
    "ParseOutcome",
    "ParseStatus",
    "get_parser",
    "unregistered_state_parsers",
]
