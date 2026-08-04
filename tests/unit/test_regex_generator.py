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
    _build_user_message,
    _extract_json,
    _fallback,
    _fix_space_injection,
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

    async def test_gpt_plain_space_pattern_fixed_to_s_star(self) -> None:
        """GPT returns literal-space pattern → _fix_space_injection converts to \\s*."""
        router = AsyncMock()
        router.complete.return_value = _StubResponse(
            text='{"pattern": "\\\\bодин на дцать\\\\b", "matched_forms": ["один на дцать"], "reasoning": "literal"}'
        )
        result = await generate_regex(
            router,
            bad_token="один на дцать",
            replacement="одинадцять",
            context="number",
            samples=[],
        )
        assert result["fallback"] is False
        assert r"\s*" in result["pattern"]
        # Must still match the original spaced form
        import re
        assert re.search(result["pattern"], "один на дцать", re.IGNORECASE)
        # Must also match the fused (no-space) form
        assert re.search(result["pattern"], "однадцать", re.IGNORECASE)

    async def test_gemini_escaped_space_pattern_fixed(self) -> None:
        """Gemini returns backslash-space pattern → fixed to \\s*."""
        # Gemini JSON: {"pattern": "\\bодин\\ над\\ цать\\b"}
        # The double-backslash in JSON decodes to single backslash in the string.
        gemini_json = '{"pattern": "\\\\bодин\\\\ над\\\\ цать\\\\b", "matched_forms": ["один над цать"], "reasoning": "escaped"}'
        router = AsyncMock()
        router.complete.return_value = _StubResponse(text=gemini_json)
        result = await generate_regex(
            router,
            bad_token="один над цать",
            replacement="одиннадцять",
            context="number",
            samples=[],
        )
        assert result["fallback"] is False
        assert r"\s*" in result["pattern"]
        assert " " not in result["pattern"]       # no bare spaces
        assert "\\ " not in result["pattern"]     # no escaped spaces
        import re
        assert re.search(result["pattern"], "один над цать", re.IGNORECASE)
        assert re.search(result["pattern"], "одиннадцать", re.IGNORECASE)

    async def test_good_s_star_pattern_not_mangled(self) -> None:
        """LLM already generated correct \\s* pattern → not double-processed."""
        good = r"\b(?:один\s*на[дт]\s*ця?т[ьъ]?)\b"
        json_text = f'{{"pattern": "{good.replace(chr(92), chr(92)+chr(92))}", "matched_forms": ["один над цать"], "reasoning": "good"}}'
        router = AsyncMock()
        router.complete.return_value = _StubResponse(text=json_text)
        result = await generate_regex(
            router,
            bad_token="один над цать",
            replacement="одиннадцять",
            context="number",
            samples=[],
        )
        assert result["fallback"] is False
        # Pattern should be functionally identical (spaces replaced by \s* already)
        import re
        assert re.search(result["pattern"], "один над цать", re.IGNORECASE)
        assert re.search(result["pattern"], "одиннадцять", re.IGNORECASE)

    async def test_fused_form_passes_sanity_check(self) -> None:
        """Pattern matching fused bad_token passes sanity even without spaces."""
        # "один над цать" fused = "однадцать"; LLM might generate pattern for the fused form
        router = AsyncMock()
        router.complete.return_value = _StubResponse(
            text='{"pattern": "\\\\bодиннадцать\\\\b", "matched_forms": ["одиннадцать"], "reasoning": "fused"}'
        )
        result = await generate_regex(
            router,
            bad_token="один над цать",
            replacement="одиннадцять",
            context="number",
            samples=[],
        )
        assert result["fallback"] is False


# ---------------------------------------------------------------------------
# _fix_space_injection (pure function — no LLM needed)
# ---------------------------------------------------------------------------


import re as _re


class TestFixSpaceInjection:
    def test_noop_when_no_spaces_in_bad_token(self) -> None:
        pat = r"\bодин\b"
        assert _fix_space_injection(pat, "один") == pat

    def test_noop_when_pattern_already_uses_s_star(self) -> None:
        pat = r"\bодин\s*над\s*цать\b"
        result = _fix_space_injection(pat, "один над цать")
        assert result == pat

    def test_noop_when_pattern_has_no_spaces(self) -> None:
        # bad_token has spaces but pattern somehow has none (unlikely but handled)
        pat = r"\bоднадцать\b"
        result = _fix_space_injection(pat, "один над цать")
        assert result == pat

    def test_gpt_plain_spaces_become_s_star(self) -> None:
        pat = r"\bодин на дцать\b"
        fixed = _fix_space_injection(pat, "один на дцать")
        assert fixed == r"\bодин\s*на\s*дцать\b"
        assert _re.compile(fixed, _re.IGNORECASE)

    def test_gemini_escaped_spaces_become_s_star(self) -> None:
        # Build pattern with actual backslash-space sequences
        pat = "\x5cbодин\x5c над\x5c цать\x5cb"   # \bодин\ над\ цать\b
        fixed = _fix_space_injection(pat, "один над цать")
        assert fixed == r"\bодин\s*над\s*цать\b"
        assert _re.compile(fixed, _re.IGNORECASE)

    def test_result_matches_spaced_form(self) -> None:
        pat = r"\bодин на дцать\b"
        fixed = _fix_space_injection(pat, "один на дцать")
        assert _re.search(fixed, "один на дцать", _re.IGNORECASE)

    def test_result_matches_fused_form(self) -> None:
        pat = r"\bодин на дцать\b"
        fixed = _fix_space_injection(pat, "один на дцать")
        # Fused: remove all spaces from bad_token
        assert _re.search(fixed, "однадцать", _re.IGNORECASE)

    def test_result_matches_single_space_variant(self) -> None:
        pat = r"\bодин на дцать\b"
        fixed = _fix_space_injection(pat, "один на дцать")
        assert _re.search(fixed, "один надцать", _re.IGNORECASE)

    def test_gemini_result_matches_fused_russian(self) -> None:
        pat = "\x5cbодин\x5c над\x5c цать\x5cb"
        fixed = _fix_space_injection(pat, "один над цать")
        assert _re.search(fixed, "одиннадцать", _re.IGNORECASE)

    def test_output_is_valid_regex(self) -> None:
        for bad in ["один на дцать", "два на дцять", "три над цять"]:
            pat = r"\b" + bad + r"\b"
            fixed = _fix_space_injection(pat, bad)
            _re.compile(fixed, _re.IGNORECASE)   # must not raise


# ---------------------------------------------------------------------------
# _build_user_message (pure function)
# ---------------------------------------------------------------------------


class TestBuildUserMessage:
    def test_no_spaces_no_hint(self) -> None:
        msg = _build_user_message("приятный", "пʼятницю", "date", [])
        assert "SPACE-INJECTION" not in msg

    def test_spaces_trigger_hint(self) -> None:
        msg = _build_user_message("один над цать", "одинадцять", "number", [])
        assert "SPACE-INJECTION DETECTED" in msg

    def test_hint_lists_fragments(self) -> None:
        msg = _build_user_message("один над цать", "одинадцять", "number", [])
        assert "'один'" in msg
        assert "'над'" in msg
        assert "'цать'" in msg

    def test_hint_fragment_count(self) -> None:
        msg = _build_user_message("один над цать", "одинадцять", "number", [])
        assert "3 fragments" in msg

    def test_samples_included_in_message(self) -> None:
        samples = ["він сказав один над цать"]
        msg = _build_user_message("один над цать", "одинадцять", "any", samples)
        assert "він сказав один над цать" in msg

    def test_samples_capped_at_5(self) -> None:
        samples = [f"sample {i}" for i in range(10)]
        msg = _build_user_message("token", "rep", "any", samples)
        assert "sample 4" in msg
        assert "sample 5" not in msg

    def test_context_appears_in_message(self) -> None:
        msg = _build_user_message("bad", "good", "plate", [])
        assert "plate" in msg

    def test_replacement_appears_in_message(self) -> None:
        msg = _build_user_message("bad", "одинадцять", "any", [])
        assert "одинадцять" in msg
