"""Reversible PII masking for LLM interactions.

Replaces phone numbers and names with placeholders before sending to
Claude API, and restores originals in tool call arguments. Each PIIVault
instance is per-call — counters and mappings are scoped to a single session.
"""

from __future__ import annotations

import re
from typing import Any

from src.logging.pii_sanitizer import _NAME_RE, _PHONE_RE


class PIIVault:
    """Reversible PII masking with placeholder ↔ original mapping."""

    def __init__(self) -> None:
        self._phone_counter = 0
        self._name_counter = 0
        # value → placeholder
        self._to_placeholder: dict[str, str] = {}
        # placeholder → value
        self._to_original: dict[str, str] = {}

    def mask(self, text: str) -> str:
        """Replace PII in *text* with placeholders like [PHONE_1], [NAME_1]."""
        text = _PHONE_RE.sub(self._mask_phone, text)
        text = _NAME_RE.sub(self._mask_name, text)
        return text

    def restore(self, text: str) -> str:
        """Replace placeholders back with original PII values."""
        for placeholder, original in self._to_original.items():
            text = text.replace(placeholder, original)
        return text

    def restore_in_args(self, args: dict[str, Any]) -> dict[str, Any]:
        """Recursively restore PII placeholders in tool call argument values."""
        return {k: self._restore_value(v) for k, v in args.items()}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _mask_phone(self, match: re.Match[str]) -> str:
        raw = match.group(0)
        if raw in self._to_placeholder:
            return self._to_placeholder[raw]
        self._phone_counter += 1
        placeholder = f"[PHONE_{self._phone_counter}]"
        self._to_placeholder[raw] = placeholder
        self._to_original[placeholder] = raw
        return placeholder

    def _mask_name(self, match: re.Match[str]) -> str:
        raw = match.group(0)
        if raw in self._to_placeholder:
            return self._to_placeholder[raw]
        self._name_counter += 1
        placeholder = f"[NAME_{self._name_counter}]"
        self._to_placeholder[raw] = placeholder
        self._to_original[placeholder] = raw
        return placeholder

    def _restore_value(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.restore(value)
        if isinstance(value, dict):
            return {k: self._restore_value(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self._restore_value(item) for item in value]
        return value
