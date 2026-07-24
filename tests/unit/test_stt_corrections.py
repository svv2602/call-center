"""Unit tests for the post-STT corrections module.

Covers:
  * pure apply_rules() — substitution, case, context, backreferences, order
  * validate_rule() — regex validity, length, context_hint whitelist
  * enabled=False rules are skipped
  * empty inputs
"""

from __future__ import annotations

import pytest

from src.stt.corrections import (
    VALID_CONTEXTS,
    apply_rules,
    validate_rule,
)


def _rule(pattern: str, replacement: str, **kw: object) -> dict[str, object]:
    return {
        "id": kw.get("id", pattern[:8]),
        "pattern": pattern,
        "replacement": replacement,
        "enabled": kw.get("enabled", True),
        "flags": kw.get("flags", "i"),
        "context_hint": kw.get("context_hint", ""),
        "note": kw.get("note", ""),
    }


class TestApplyRules:
    def test_no_rules_returns_input(self) -> None:
        assert apply_rules("Hello", []) == ("Hello", [])

    def test_empty_text_returns_empty(self) -> None:
        assert apply_rules("", [_rule("foo", "bar")]) == ("", [])

    def test_basic_substitution(self) -> None:
        rules = [_rule(r"паризьк(ому|е|ий)", r"запорізьк\1", id="par_zap")]
        result, applied = apply_rules("на паризькому шосе", rules)
        assert result == "на запорізькому шосе"
        assert applied == ["par_zap"]

    def test_case_insensitive_default(self) -> None:
        rules = [_rule("викторог", "вівторок")]
        # Rule has flags="i" by default in _rule helper.
        result, applied = apply_rules("на Викторог", rules)
        assert "вівторок" in result.lower()
        assert applied

    def test_case_sensitive_when_no_i_flag(self) -> None:
        rules = [_rule("Foo", "Bar", flags="")]
        result, applied = apply_rules("foo Foo", rules)
        assert result == "foo Bar"
        assert applied

    def test_disabled_rule_skipped(self) -> None:
        rules = [_rule("foo", "bar", enabled=False)]
        result, applied = apply_rules("foo", rules)
        assert result == "foo"
        assert applied == []

    def test_multiple_rules_apply_in_order(self) -> None:
        rules = [
            _rule("a", "b", id="r1"),
            _rule("b", "c", id="r2"),
        ]
        result, applied = apply_rules("a", rules)
        # r1 turns "a"→"b", then r2 turns "b"→"c"
        assert result == "c"
        assert applied == ["r1", "r2"]

    def test_rule_with_no_match_not_applied(self) -> None:
        rules = [
            _rule("foo", "bar", id="r1"),
            _rule("baz", "qux", id="r2"),
        ]
        result, applied = apply_rules("nothing here", rules)
        assert result == "nothing here"
        assert applied == []

    def test_context_hint_matches(self) -> None:
        rules = [_rule(r"\bлет\b", "липня", context_hint="date", id="lit_lyp")]
        result, applied = apply_rules("28 лет", rules, context_hint="date")
        assert result == "28 липня"
        assert applied == ["lit_lyp"]

    def test_context_hint_mismatch_skips(self) -> None:
        rules = [_rule(r"\bлет\b", "липня", context_hint="date", id="lit_lyp")]
        result, applied = apply_rules("28 лет", rules, context_hint="plate")
        assert result == "28 лет"
        assert applied == []

    def test_empty_context_hint_applies_to_any(self) -> None:
        rules = [_rule("foo", "bar", context_hint="", id="any1")]
        result, applied = apply_rules("foo", rules, context_hint="anything")
        assert result == "bar"
        assert applied == ["any1"]

    def test_any_context_hint_applies_everywhere(self) -> None:
        rules = [_rule("foo", "bar", context_hint="any", id="any2")]
        result, applied = apply_rules("foo", rules, context_hint="date")
        assert result == "bar"
        assert applied == ["any2"]

    def test_invalid_regex_skipped_silently(self) -> None:
        rules = [
            _rule("[unclosed", "x", id="bad"),
            _rule("foo", "bar", id="good"),
        ]
        result, applied = apply_rules("foo", rules)
        # Bad rule skipped, good rule applied
        assert result == "bar"
        assert applied == ["good"]

    def test_unicode_ukrainian(self) -> None:
        rules = [_rule(r"вифт(оророк|орок|орок|)", "вівторок", context_hint="date")]
        for src in ("вифт", "вифторок", "вифторорок"):
            result, applied = apply_rules(src, rules, context_hint="date")
            assert result == "вівторок"
            assert applied

    def test_backreference_supported(self) -> None:
        rules = [_rule(r"(\d{2}):(\d{2})", r"\1\2", id="colon_strip")]
        result, applied = apply_rules("номер 17:25", rules)
        assert result == "номер 1725"
        assert applied == ["colon_strip"]


class TestValidateRule:
    def test_valid_rule_accepted(self) -> None:
        validate_rule({"pattern": "foo", "replacement": "bar"})

    def test_empty_pattern_rejected(self) -> None:
        with pytest.raises(ValueError, match="pattern"):
            validate_rule({"pattern": "", "replacement": "bar"})

    def test_missing_pattern_rejected(self) -> None:
        with pytest.raises(ValueError, match="pattern"):
            validate_rule({"replacement": "bar"})

    def test_non_string_pattern_rejected(self) -> None:
        with pytest.raises(ValueError, match="pattern"):
            validate_rule({"pattern": 123, "replacement": "bar"})

    def test_invalid_regex_rejected(self) -> None:
        with pytest.raises(ValueError, match="invalid regex"):
            validate_rule({"pattern": "[unclosed", "replacement": "bar"})

    def test_pattern_too_long_rejected(self) -> None:
        with pytest.raises(ValueError, match="too long"):
            validate_rule({"pattern": "a" * 501, "replacement": "bar"})

    def test_replacement_too_long_rejected(self) -> None:
        with pytest.raises(ValueError, match="too long"):
            validate_rule({"pattern": "foo", "replacement": "b" * 501})

    def test_unknown_context_hint_rejected(self) -> None:
        with pytest.raises(ValueError, match="context_hint"):
            validate_rule(
                {"pattern": "foo", "replacement": "bar", "context_hint": "invalid"}
            )

    def test_empty_context_hint_accepted(self) -> None:
        validate_rule({"pattern": "foo", "replacement": "bar", "context_hint": ""})

    @pytest.mark.parametrize("ctx", VALID_CONTEXTS)
    def test_all_whitelisted_contexts_accepted(self, ctx: str) -> None:
        validate_rule({"pattern": "foo", "replacement": "bar", "context_hint": ctx})
