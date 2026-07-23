"""Reversible PII masking for LLM interactions.

Replaces phone numbers with placeholders before sending to the LLM,
and restores originals in tool call arguments. Each PIIVault instance
is per-call — counters and mappings are scoped to a single session.

Names are intentionally NOT masked: masking names (with `[NAME_1]`) caused
LLM providers (notably Gemini 2.5 Flash) to drift into meta-text about the
placeholder and, in the worst case, emit raw tool_code pseudo-Python that
was spoken to the caller (see call 43a4b637, 2026-07-23). Names are much
less sensitive than phone numbers and the payoff from masking them does
not justify the LLM stability cost.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.logging.pii_sanitizer import _PHONE_RE

if TYPE_CHECKING:
    import re


class PIIVault:
    """Reversible PII masking with placeholder ↔ original mapping.

    Only phone numbers are masked. Names pass through unchanged.
    """

    def __init__(self) -> None:
        self._phone_counter = 0
        # value → placeholder
        self._to_placeholder: dict[str, str] = {}
        # placeholder → value
        self._to_original: dict[str, str] = {}

    def mask(self, text: str) -> str:
        """Replace phone numbers in *text* with placeholders like [PHONE_1]."""
        return _PHONE_RE.sub(self._mask_phone, text)

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

    def _restore_value(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.restore(value)
        if isinstance(value, dict):
            return {k: self._restore_value(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self._restore_value(item) for item in value]
        return value
