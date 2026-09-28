"""Sales-prompt fixes from the goldset run O (wave 1-B).

Under ``sales_enabled`` the bot must (1) name payment methods, the
cash-on-delivery fee and the instalment banks from the «Умови мережі» block
instead of «уточнить менеджер», (2) go to ``search_disks`` for wheels, not to
the tyre tools, (3) answer self-pickup with ``get_pickup_points``, not with
fitting stations. With the flag off the prompt stays the incumbent's byte for
byte — pinned by ``test_sales_scope_switch.py::TestFlagOffIsIncumbent``; here
only that the sales variant never leaks into it.
"""

from __future__ import annotations

import dataclasses
import re
from typing import Any

import pytest

import scripts.run_goldset as rg
import src.agent.prompts as prompts
from scripts.configure_tenants import PROKOLESO_CONFIG_PATCH, TVOYA_SHINA_CONFIG_PATCH
from src.agent.network_policy import NetworkPolicy


def _policy(patch_: dict[str, Any], *, sales: bool) -> NetworkPolicy:
    return NetworkPolicy.from_tenant_config({**patch_, "sales_enabled": sales})


TSH_ON = _policy(TVOYA_SHINA_CONFIG_PATCH, sales=True)
PK_ON = _policy(PROKOLESO_CONFIG_PATCH, sales=True)
TSH_OFF = _policy(TVOYA_SHINA_CONFIG_PATCH, sales=False)
PK_OFF = _policy(PROKOLESO_CONFIG_PATCH, sales=False)
ON = [TSH_ON, PK_ON]

#: The line that outvoted the block: instalments/prepay are the manager's —
#: «так і скажи клієнту». Any wording that sends the payment answer to the
#: manager this way is the defect, not just the one sentence.
_MANAGER_OWNS_PAYMENT = re.compile(
    r"(частинами|передоплат)[^\n]*оформить менеджер[^\n]*так і скажи", re.IGNORECASE
)
#: The rule that replaces it: fee and banks, named from the block.
_PAYMENT_FROM_BLOCK = re.compile(
    r"комісі[^\n]*банк[^\n]*«Умови мережі»|«Умови мережі»[^\n]*комісі[^\n]*банк",
    re.IGNORECASE,
)


def _sales_prompts(pol: NetworkPolicy) -> dict[str, str]:
    """Every path a sales prompt is built by: the default bundle, an IVR
    scenario, the unknown-scenario fallback and a mid-call expansion."""
    out = {
        scenario: prompts.assemble_prompt(scenario=scenario, network_policy=pol)
        for scenario in ("sales", "tire_search", "consultation", "no_such_scenario")
    }
    extra = prompts.infer_expanded_modules("order_status", {"create_order_draft"}, pol) or []
    out["expansion"] = "\n".join(extra)
    return out


def _lines_with(text: str, *needles: str) -> list[str]:
    return [ln for ln in text.splitlines() if all(n in ln for n in needles)]


# ── (1) payment ────────────────────────────────────────────────────────────


class TestPaymentFromNetworkBlock:
    @pytest.mark.parametrize("pol", ON)
    def test_no_sales_prompt_hands_payment_to_the_manager(self, pol: NetworkPolicy) -> None:
        for path, text in _sales_prompts(pol).items():
            assert not _MANAGER_OWNS_PAYMENT.search(text), path

    @pytest.mark.parametrize("pol", ON)
    def test_every_order_flow_path_names_fee_and_banks_from_the_block(
        self, pol: NetworkPolicy
    ) -> None:
        for path, text in _sales_prompts(pol).items():
            if "Сценарій: оформлення замовлення" in text:
                assert _PAYMENT_FROM_BLOCK.search(text), path

    def test_order_flow_is_in_the_default_sales_bundle(self) -> None:
        # otherwise the rule above would hold vacuously on the main path
        for pol in ON:
            assert "Сценарій: оформлення замовлення" in _sales_prompts(pol)["sales"]
            assert "Сценарій: оформлення замовлення" in _sales_prompts(pol)["expansion"]

    def test_arranging_instalments_stays_the_managers(self) -> None:
        # the manager still arranges the instalment — only the answer moved
        assert _lines_with(prompts._MOD_ORDER_FLOW_SALES, "Менеджер", "частинами")

    def test_variant_adds_no_literal_numbers(self) -> None:
        digits = re.compile(r"\d+")
        assert digits.findall(prompts._MOD_ORDER_FLOW_SALES) == digits.findall(
            prompts._MOD_ORDER_FLOW
        )

    @pytest.mark.parametrize("pol", [TSH_OFF, PK_OFF, None])
    def test_flag_off_keeps_the_incumbent_module(self, pol: NetworkPolicy | None) -> None:
        for scenario in ("tire_search", "consultation"):
            text = prompts.assemble_prompt(scenario=scenario, network_policy=pol)
            assert prompts._MOD_ORDER_FLOW in text
            assert prompts._MOD_ORDER_FLOW_SALES not in text


# ── (2) wheels, (3) self-pickup — the sales frame ─────────────────────────


class TestToolChoiceInSalesFrame:
    @pytest.mark.parametrize("pol", ON)
    def test_wheels_go_to_search_disks_not_to_tyre_tools(self, pol: NetworkPolicy) -> None:
        frame = prompts.render_sales_scope(pol)
        rule = _lines_with(frame, "search_disks", "get_vehicle_tire_sizes", "search_tires")
        assert rule, "no tool-choice rule for wheels"
        assert "навіть якщо авто відоме" in rule[0]
        assert "тільки для шин" in rule[0]
        # The rule itself: a wheels request goes straight to search_disks.
        assert re.search(r"диск\w*.*→\s*одразу search_disks", rule[0]), rule[0]

    @pytest.mark.parametrize("pol", ON)
    def test_the_rule_reaches_the_prompt(self, pol: NetworkPolicy) -> None:
        for path, text in _sales_prompts(pol).items():
            if path != "expansion":
                assert prompts.render_sales_scope(pol) in text, path

    @pytest.mark.parametrize("pol", ON)
    def test_self_pickup_goes_to_pickup_points(self, pol: NetworkPolicy) -> None:
        frame = prompts.render_sales_scope(pol)
        assert _lines_with(frame, "самовивіз", "get_pickup_points")

    def test_fitting_network_is_told_stations_are_not_for_pickup(self) -> None:
        line = _lines_with(prompts.render_sales_scope(TSH_ON), "самовивіз", "get_pickup_points")
        assert "get_fitting_stations" in line[0] and "не для самовивозу" in line[0]

    def test_network_without_fitting_hears_no_fitting_tool(self) -> None:
        assert "fitting" not in PK_ON.services
        assert "get_fitting_stations" not in prompts.render_sales_scope(PK_ON)

    @pytest.mark.parametrize("on", ON)
    def test_no_pickup_no_pickup_rule(self, on: NetworkPolicy) -> None:
        assert on.pickup_available  # the configured networks do offer pickup
        pol = dataclasses.replace(on, pickup_available=False)
        assert "get_pickup_points" not in prompts.render_sales_scope(pol)


# ── (4) goldset group C — multi-turn cases ─────────────────────────────────

# Wave 2-D: under sales an order is one submit_order_request.
_ORDER_TOOLS = {
    "search_tires",
    "check_availability",
    "get_pickup_points",
    "submit_order_request",
}


@pytest.fixture(scope="module")
def cases() -> dict[str, rg.Case]:
    pytest.importorskip("yaml")  # the local venv lacks PyYAML; the container has it
    return {c.id: c for c in rg.load_cases()}


def _turns_expecting(case: rg.Case, tool: str) -> list[int]:
    out = []
    for i, turn in enumerate(case.turns):
        for net in case.networks:
            specs = rg.effective_expect(turn, net).get("tool_called", [])
            names = {s if isinstance(s, str) else s["name"] for s in specs}
            if tool in names:
                out.append(i)
                break
    return out


class TestGroupCCases:
    @pytest.mark.parametrize(
        "cid", ["order_finish_is_a_request", "order_1c_failure_hands_to_manager"]
    )
    def test_order_case_plays_the_whole_script(self, cases: dict[str, rg.Case], cid: str) -> None:
        case = cases[cid]
        assert set(case.mocks) >= _ORDER_TOOLS
        assert len(case.turns) >= 6
        assert _turns_expecting(case, "submit_order_request") == [len(case.turns) - 1]
        assert not set(case.mocks) & {
            "create_order_draft",
            "update_order_delivery",
            "confirm_order",
        }

    @pytest.mark.parametrize(
        "cid", ["tyres_two_or_three_variants_by_priority", "studded_none_studless_with_caveat"]
    )
    def test_search_is_expected_after_the_brand_answer(
        self, cases: dict[str, rg.Case], cid: str
    ) -> None:
        case = cases[cid]
        assert len(case.turns) >= 2
        assert _turns_expecting(case, "search_tires") == [len(case.turns) - 1]
        assert re.search(r"будь-який|без побажань", case.turns[-1].user)

    def test_tracking_accepts_order_number_or_phone(self, cases: dict[str, rg.Case]) -> None:
        turn = cases["delivery_tracking_ttn"].turns[0]
        (pattern,) = turn.expect["must_contain"]
        for reply in (
            "Назвіть, будь ласка, номер замовлення, і я перевірю статус.",
            "Підкажіть номер телефону, на який оформлювали замовлення.",
            "Відстежити посилку можна за номером ТТН.",
        ):
            assert re.search(pattern, reply, re.IGNORECASE), reply
        assert not re.search(pattern, "З'єдную з оператором.", re.IGNORECASE)
