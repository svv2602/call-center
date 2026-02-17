"""Unit tests for training data seed script."""

from __future__ import annotations

from scripts.seed_training_data import (
    get_dialogue_examples,
    get_response_templates,
    get_safety_rules,
)


class TestGetDialogueExamples:
    """Test dialogue example extraction."""

    def test_returns_list(self) -> None:
        examples = get_dialogue_examples()
        assert isinstance(examples, list)
        assert len(examples) >= 8

    def test_all_have_required_fields(self) -> None:
        for ex in get_dialogue_examples():
            assert "title" in ex
            assert "scenario_type" in ex
            assert "phase" in ex
            assert "dialogue" in ex
            assert isinstance(ex["dialogue"], list)
            assert len(ex["dialogue"]) > 0

    def test_valid_scenario_types(self) -> None:
        from src.api.training_dialogues import SCENARIO_TYPES

        for ex in get_dialogue_examples():
            assert ex["scenario_type"] in SCENARIO_TYPES, (
                f"Invalid scenario_type: {ex['scenario_type']}"
            )

    def test_valid_phases(self) -> None:
        from src.api.training_dialogues import PHASES

        for ex in get_dialogue_examples():
            assert ex["phase"] in PHASES, f"Invalid phase: {ex['phase']}"

    def test_dialogue_entries_have_role_and_text(self) -> None:
        for ex in get_dialogue_examples():
            for entry in ex["dialogue"]:
                assert "role" in entry
                assert "text" in entry
                assert entry["role"] in ("customer", "agent")

    def test_tools_used_populated(self) -> None:
        examples = get_dialogue_examples()
        with_tools = [ex for ex in examples if ex.get("tools_used")]
        assert len(with_tools) >= 5


class TestGetSafetyRules:
    """Test safety rule extraction."""

    def test_returns_list(self) -> None:
        rules = get_safety_rules()
        assert isinstance(rules, list)
        assert len(rules) >= 10

    def test_all_have_required_fields(self) -> None:
        for rule in get_safety_rules():
            assert "title" in rule
            assert "rule_type" in rule
            assert "trigger_input" in rule
            assert "expected_behavior" in rule
            assert "severity" in rule

    def test_valid_rule_types(self) -> None:
        from src.api.training_safety import RULE_TYPES

        for rule in get_safety_rules():
            assert rule["rule_type"] in RULE_TYPES, f"Invalid rule_type: {rule['rule_type']}"

    def test_valid_severities(self) -> None:
        from src.api.training_safety import SEVERITIES

        for rule in get_safety_rules():
            assert rule["severity"] in SEVERITIES, f"Invalid severity: {rule['severity']}"

    def test_has_prompt_injection_rules(self) -> None:
        rules = get_safety_rules()
        pi_rules = [r for r in rules if r["rule_type"] == "prompt_injection"]
        assert len(pi_rules) >= 2

    def test_has_critical_rules(self) -> None:
        rules = get_safety_rules()
        critical = [r for r in rules if r["severity"] == "critical"]
        assert len(critical) >= 2


class TestGetResponseTemplates:
    """Test response template extraction."""

    def test_returns_list(self) -> None:
        templates = get_response_templates()
        assert isinstance(templates, list)
        assert len(templates) == 36  # 7 keys × 5 variants + 1 extra for wait

    def test_all_have_required_fields(self) -> None:
        for tpl in get_response_templates():
            assert "template_key" in tpl
            assert "variant_number" in tpl
            assert "title" in tpl
            assert "content" in tpl
            assert len(tpl["content"]) > 0

    def test_valid_template_keys(self) -> None:
        from src.api.training_templates import TEMPLATE_KEYS

        for tpl in get_response_templates():
            assert tpl["template_key"] in TEMPLATE_KEYS, f"Invalid key: {tpl['template_key']}"

    def test_greeting_v1_matches_prompts(self) -> None:
        from src.agent.prompts import GREETING_TEXT

        templates = get_response_templates()
        greeting = next(
            t for t in templates if t["template_key"] == "greeting" and t["variant_number"] == 1
        )
        assert greeting["content"] == GREETING_TEXT

    def test_all_keys_covered(self) -> None:
        from src.api.training_templates import TEMPLATE_KEYS

        templates = get_response_templates()
        keys = {t["template_key"] for t in templates}
        assert keys == set(TEMPLATE_KEYS)

    def test_each_key_has_multiple_variants(self) -> None:
        from collections import Counter

        templates = get_response_templates()
        counts = Counter(t["template_key"] for t in templates)
        for key, count in counts.items():
            assert count >= 4, f"Key '{key}' has only {count} variants, expected >= 4"

    def test_variant_numbers_unique_per_key(self) -> None:
        from collections import defaultdict

        templates = get_response_templates()
        by_key: dict[str, list[int]] = defaultdict(list)
        for tpl in templates:
            by_key[tpl["template_key"]].append(tpl["variant_number"])
        for key, variants in by_key.items():
            assert len(variants) == len(set(variants)), f"Duplicate variant_number in '{key}'"
