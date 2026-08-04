"""Unit tests for the regex_generator LLM helper.

The pure logic (JSON extraction, output validation, fallback) is tested
here without a live LLM. Integration with the router is exercised via a
minimal AsyncMock stand-in.
"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock

import pytest

from src.stt.regex_generator import (
    _extract_json,
    _fallback,
    _validate_output,
    generate_regex,
)


@dataclass
class _StubResponse:
    """Minimal stand-in for llm.LLMResponse."""

    text: str
    provider: str = "test-provider"


class TestExtractJson:
    def test_plain_json(self) -> None:
        assert _extract_json('{"pattern": "\\\\bfoo\\\\b"}') == {"pattern": "\\bfoo\\b"}

    def test_fenced_json_block(self) -> None:
        text = 'Here you go:\n```json\n{"pattern": "\\\\bfoo\\\\b"}\n```\nThanks.'
        assert _extract_json(text) == {"pattern": "\\bfoo\\b"}

    def test_fenced_without_lang(self) -> None:
        text = '```\n{"pattern": "x"}\n```'
        assert _extract_json(text) == {"pattern": "x"}

    def test_prose_wrapping_json(self) -> None:
        # No fence, but braces are extractable.
        text = 'Sure! {"pattern": "y"} — hope that helps.'
        assert _extract_json(text) == {"pattern": "y"}

    def test_invalid_json_returns_none(self) -> None:
        assert _extract_json("not json at all") is None

    def test_empty_returns_none(self) -> None:
        assert _extract_json("") is None

    def test_non_dict_returns_none(self) -> None:
        # A JSON array is valid JSON but not a dict — reject.
        assert _extract_json("[1, 2, 3]") is None


class TestValidateOutput:
    def test_happy_path(self) -> None:
        raw = {
            "pattern": r"\bпонед[іi]лок\b",
            "matched_forms": ["понеділок", "понедiлок"],
            "reasoning": "covers both correct spelling and the STT variant with a Latin 'i'",
        }
        result = _validate_output(raw)
        assert result is not None
        assert result["pattern"] == r"\bпонед[іi]лок\b"
        assert result["matched_forms"] == ["понеділок", "понедiлок"]
        assert result["fallback"] is False

    def test_missing_pattern(self) -> None:
        assert _validate_output({"replacement": "x"}) is None

    def test_empty_pattern(self) -> None:
        assert _validate_output({"pattern": ""}) is None

    def test_pattern_too_long(self) -> None:
        assert _validate_output({"pattern": "x" * 501}) is None

    def test_invalid_regex(self) -> None:
        # Unbalanced parenthesis — re.compile raises.
        assert _validate_output({"pattern": r"(unclosed"}) is None

    def test_non_list_matched_forms_normalised_to_empty(self) -> None:
        result = _validate_output({"pattern": r"\bfoo\b", "matched_forms": "not-a-list"})
        assert result is not None
        assert result["matched_forms"] == []

    def test_matched_forms_capped(self) -> None:
        # More than the cap → trimmed.
        many = [f"form{i}" for i in range(50)]
        result = _validate_output({"pattern": r"\bfoo\b", "matched_forms": many})
        assert result is not None
        assert 0 < len(result["matched_forms"]) <= 12


class TestFallback:
    def test_fallback_shape(self) -> None:
        f = _fallback("викторах", "вівторок")
        assert f["pattern"] == r"\bвикторах\b"
        assert f["matched_forms"] == ["викторах"]
        assert f["fallback"] is True

    def test_fallback_escapes_special_chars(self) -> None:
        # Dot and star are regex metacharacters — must be escaped.
        f = _fallback("what?", "y")
        assert "\\?" in f["pattern"]


@pytest.mark.asyncio
class TestGenerateRegex:
    async def test_uses_llm_result_when_valid(self) -> None:
        router = AsyncMock()
        router.complete.return_value = _StubResponse(
            text='{"pattern": "\\\\bприятн\\\\w+\\\\b", "matched_forms": ["приятный","приятная"], "reasoning": "cover paradigm"}'
        )
        result = await generate_regex(
            router,
            bad_token="приятный",
            replacement="пʼятницю",
            context="date",
            samples=["на приятный", "на приятный трюм"],
        )
        assert result["fallback"] is False
        assert result["pattern"] == r"\bприятн\w+\b"
        assert "приятный" in result["matched_forms"]

    async def test_falls_back_on_llm_error(self) -> None:
        router = AsyncMock()
        router.complete.side_effect = RuntimeError("provider down")
        result = await generate_regex(
            router,
            bad_token="харких",
            replacement="Харків",
            context="address",
            samples=[],
        )
        assert result["fallback"] is True
        assert result["pattern"] == r"\bхарких\b"

    async def test_falls_back_on_invalid_regex(self) -> None:
        router = AsyncMock()
        router.complete.return_value = _StubResponse(text='{"pattern": "(unclosed"}')
        result = await generate_regex(
            router,
            bad_token="потребно",
            replacement="потрібно",
            context="any",
            samples=[],
        )
        assert result["fallback"] is True

    async def test_falls_back_when_pattern_does_not_match_token(self) -> None:
        # LLM went off-topic and produced a pattern unrelated to the token.
        router = AsyncMock()
        router.complete.return_value = _StubResponse(
            text='{"pattern": "\\\\bfoobar\\\\b", "matched_forms": ["foobar"], "reasoning": "?"}'
        )
        result = await generate_regex(
            router,
            bad_token="приятный",
            replacement="пʼятницю",
            context="date",
            samples=[],
        )
        assert result["fallback"] is True
