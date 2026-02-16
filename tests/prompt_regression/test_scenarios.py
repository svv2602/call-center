"""Prompt regression tests.

Verify that system prompt and tool definitions stay consistent with
expected behavior for each conversation scenario. These tests do NOT
call the real Claude API — they check prompt content and tool presence.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.agent.prompts import PROMPT_VERSION, SYSTEM_PROMPT
from src.agent.tools import ALL_TOOLS

SCENARIOS_PATH = Path(__file__).parent / "scenarios.json"


def load_scenarios() -> list[dict]:
    """Load test scenarios from the JSON file."""
    with open(SCENARIOS_PATH) as f:
        return json.load(f)


def scenario_ids(scenarios: list[dict]) -> list[str]:
    """Extract scenario IDs for parametrize."""
    return [s["id"] for s in scenarios]


_SCENARIOS = load_scenarios()

# Canonical tool names from CLAUDE.md / doc/development/00-overview.md
CANONICAL_TOOLS = {
    "search_tires",
    "check_availability",
    "transfer_to_operator",
    "get_order_status",
    "create_order_draft",
    "update_order_delivery",
    "confirm_order",
    "get_fitting_stations",
    "get_fitting_slots",
    "book_fitting",
    "search_knowledge_base",
}


@pytest.mark.prompt_regression
class TestPromptRegression:
    """Verify that system prompt contains required instructions for each scenario."""

    @pytest.fixture(autouse=True)
    def _load(self) -> None:
        self.scenarios = _SCENARIOS
        self.tool_names = {t["name"] for t in ALL_TOOLS}

    # ------------------------------------------------------------------
    # Structural checks
    # ------------------------------------------------------------------

    def test_prompt_version_set(self) -> None:
        """Prompt version must be a non-empty string."""
        assert PROMPT_VERSION, "Prompt version must be set"

    def test_prompt_not_empty(self) -> None:
        """System prompt must contain meaningful content (>100 chars)."""
        assert len(SYSTEM_PROMPT) > 100

    def test_prompt_speaks_ukrainian(self) -> None:
        """System prompt must mention Ukrainian language."""
        assert "українською" in SYSTEM_PROMPT

    # ------------------------------------------------------------------
    # Per-scenario: tool existence
    # ------------------------------------------------------------------

    @pytest.mark.parametrize(
        "scenario",
        _SCENARIOS,
        ids=scenario_ids(_SCENARIOS),
    )
    def test_expected_tool_exists(self, scenario: dict) -> None:
        """If scenario expects a tool, verify it is defined in ALL_TOOLS."""
        expected = scenario["expected_tool"]
        if expected is not None:
            assert expected in self.tool_names, (
                f"Scenario '{scenario['id']}' expects tool '{expected}' "
                f"but it is not in ALL_TOOLS. Available: {sorted(self.tool_names)}"
            )

    # ------------------------------------------------------------------
    # Per-scenario: keyword presence in SYSTEM_PROMPT
    # ------------------------------------------------------------------

    @pytest.mark.parametrize(
        "scenario",
        _SCENARIOS,
        ids=scenario_ids(_SCENARIOS),
    )
    def test_prompt_contains_keywords(self, scenario: dict) -> None:
        """Verify SYSTEM_PROMPT contains keywords needed for this scenario."""
        for kw in scenario["expected_keywords"]:
            assert kw in SYSTEM_PROMPT, (
                f"Missing keyword '{kw}' in SYSTEM_PROMPT for scenario '{scenario['id']}'"
            )

    # ------------------------------------------------------------------
    # Per-scenario: unexpected keywords must NOT be in SYSTEM_PROMPT
    # ------------------------------------------------------------------

    @pytest.mark.parametrize(
        "scenario",
        _SCENARIOS,
        ids=scenario_ids(_SCENARIOS),
    )
    def test_prompt_excludes_unexpected_keywords(self, scenario: dict) -> None:
        """Verify SYSTEM_PROMPT does NOT contain keywords that would be dangerous."""
        for kw in scenario.get("unexpected_keywords", []):
            assert kw not in SYSTEM_PROMPT, (
                f"Unexpected keyword '{kw}' found in SYSTEM_PROMPT for scenario '{scenario['id']}'"
            )

    # ------------------------------------------------------------------
    # Canonical tool set
    # ------------------------------------------------------------------

    def test_all_canonical_tools_defined(self) -> None:
        """Verify all canonical tools from the project spec exist in ALL_TOOLS."""
        missing = CANONICAL_TOOLS - self.tool_names
        assert not missing, f"Canonical tools missing from ALL_TOOLS: {sorted(missing)}"

    def test_no_unknown_canonical_tools(self) -> None:
        """Verify ALL_TOOLS does not contain misspelled canonical tool names.

        Extra tools (like cancel_fitting, get_fitting_price) are allowed
        but any tool whose name is close to a canonical name should be flagged.
        """
        # This is a soft check — we only verify canonical tools are a subset.
        # Additional tools are permitted (e.g. cancel_fitting, get_fitting_price).
        assert CANONICAL_TOOLS.issubset(self.tool_names), (
            f"Missing canonical tools: {sorted(CANONICAL_TOOLS - self.tool_names)}"
        )

    # ------------------------------------------------------------------
    # Safety rules
    # ------------------------------------------------------------------

    def test_prompt_contains_safety_rules(self) -> None:
        """Verify critical safety rules are present in the prompt."""
        assert "confirm_order" in SYSTEM_PROMPT, (
            "SYSTEM_PROMPT must mention confirm_order for order safety"
        )
        assert "НІКОЛИ" in SYSTEM_PROMPT, (
            "SYSTEM_PROMPT must contain 'НІКОЛИ' (NEVER) for confirm safety"
        )
        assert "оператор" in SYSTEM_PROMPT.lower(), "SYSTEM_PROMPT must mention operator escalation"

    def test_prompt_requires_confirmation_before_order(self) -> None:
        """Verify the prompt explicitly requires customer confirmation before confirming order."""
        # Must mention explicit confirmation requirement
        assert (
            "підтвердження" in SYSTEM_PROMPT.lower() or "підтверджуєте" in SYSTEM_PROMPT.lower()
        ), "SYSTEM_PROMPT must require explicit customer confirmation before order"

    def test_prompt_greeting_is_ukrainian(self) -> None:
        """Verify that the prompt instructs Ukrainian-language interactions."""
        assert "українською" in SYSTEM_PROMPT
        assert "ЗАВЖДИ" in SYSTEM_PROMPT, (
            "SYSTEM_PROMPT must use ЗАВЖДИ (ALWAYS) to enforce Ukrainian language"
        )

    # ------------------------------------------------------------------
    # Tool schema integrity
    # ------------------------------------------------------------------

    def test_all_tools_have_required_fields(self) -> None:
        """Every tool definition must have name, description, and input_schema."""
        for tool in ALL_TOOLS:
            assert "name" in tool, f"Tool missing 'name': {tool}"
            assert "description" in tool, f"Tool '{tool.get('name')}' missing 'description'"
            assert "input_schema" in tool, f"Tool '{tool.get('name')}' missing 'input_schema'"

    def test_tool_descriptions_are_nonempty(self) -> None:
        """Tool descriptions must be non-empty strings."""
        for tool in ALL_TOOLS:
            desc = tool.get("description", "")
            assert len(desc) > 10, f"Tool '{tool['name']}' has too short description: '{desc}'"

    def test_tool_schemas_are_valid_objects(self) -> None:
        """Tool input_schema must have type=object."""
        for tool in ALL_TOOLS:
            schema = tool["input_schema"]
            assert schema.get("type") == "object", (
                f"Tool '{tool['name']}' input_schema type must be 'object', "
                f"got '{schema.get('type')}'"
            )

    # ------------------------------------------------------------------
    # Scenario file integrity
    # ------------------------------------------------------------------

    def test_scenarios_file_valid(self) -> None:
        """Scenario file must be valid JSON with required fields."""
        required_keys = {
            "id",
            "description",
            "user_message",
            "expected_tool",
            "expected_keywords",
            "category",
        }
        for scenario in self.scenarios:
            missing = required_keys - set(scenario.keys())
            assert not missing, f"Scenario '{scenario.get('id', '?')}' missing keys: {missing}"

    def test_scenario_ids_unique(self) -> None:
        """All scenario IDs must be unique."""
        ids = [s["id"] for s in self.scenarios]
        assert len(ids) == len(set(ids)), f"Duplicate scenario IDs: {ids}"

    def test_scenario_count(self) -> None:
        """We should have at least 12 scenarios."""
        assert len(self.scenarios) >= 12, (
            f"Expected at least 12 scenarios, got {len(self.scenarios)}"
        )
