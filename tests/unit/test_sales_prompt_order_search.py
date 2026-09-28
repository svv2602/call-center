"""Sales-prompt fixes from goldset №2 (wave 1-C).

Under ``sales_enabled``: (1) no fitting offer after an order — the owner
postponed «order + fitting in one call»; the confirmed stage says «заявку
прийнято» and nothing about fitting, and a confirmed summary is carried
through to ``confirm_order`` in the same turn; (2) the tyre-search module has
one scheme — size + season known → ``search_tires`` at once, brand and budget
are never asked; (3) self-pickup calls ``get_pickup_points`` before any
address is named. With the flag off the prompt stays the incumbent's byte for
byte — pinned by ``test_sales_scope_switch.py::TestFlagOffIsIncumbent``.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

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
OFF = [TSH_OFF, PK_OFF, None]

#: Any offer of a fitting booking, whatever the wording.
_FITTING_OFFER = re.compile(
    r"(запропонуй|бажаєте|потрібен вам|можу одразу записати)[^\n]*(шиномонтаж|монтаж)",
    re.IGNORECASE,
)
#: Asking the caller for a brand or a budget preference before the search.
_ASKS_BRAND = re.compile(r"побажання[^\n]*(бренд|бюджет)", re.IGNORECASE)


def _confirmed_prompt(pol: NetworkPolicy | None, scenario: str = "sales") -> str:
    base = prompts.assemble_prompt(scenario=scenario, network_policy=pol)
    return prompts.build_system_prompt_with_context(
        base,
        is_modular=True,
        scenario=scenario,
        order_stage="confirmed",
        order_id="ORD-x",
        tools_called={"create_order_draft", "update_order_delivery", "confirm_order"},
        network_policy=pol,
    )


def _sales_prompts(pol: NetworkPolicy) -> dict[str, str]:
    out = {
        scenario: prompts.assemble_prompt(scenario=scenario, network_policy=pol)
        for scenario in ("sales", "tire_search", "consultation", "no_such_scenario")
    }
    extra = prompts.infer_expanded_modules("order_status", {"search_tires"}, pol) or []
    out["expansion"] = "\n".join(extra)
    out["confirmed"] = _confirmed_prompt(pol)
    out["confirmed.tire_search"] = _confirmed_prompt(pol, "tire_search")
    return out


# ── (1) no fitting offer after an order ────────────────────────────────────


class TestNoFittingAfterOrder:
    @pytest.mark.parametrize("pol", ON)
    def test_confirmed_stage_offers_no_fitting(self, pol: NetworkPolicy) -> None:
        for path in ("sales", "tire_search"):
            text = _confirmed_prompt(pol, path)
            assert prompts._STAGE_ORDER_ACCEPTED_SALES in text, path
            assert prompts._STAGE_OFFER_FITTING not in text, path

    def test_sales_stage_says_request_accepted_and_no_fitting(self) -> None:
        stage = prompts._STAGE_ORDER_ACCEPTED_SALES
        assert "Заявку" in stage and "менеджер" in stage
        assert not _FITTING_OFFER.search(stage)
        assert "book_fitting" not in stage

    @pytest.mark.parametrize("pol", ON)
    def test_order_part_of_every_sales_prompt_offers_no_fitting(self, pol: NetworkPolicy) -> None:
        # the storage cross-sell (_MOD_STORAGE) is not an order and stays;
        # everything else in a sales prompt must not offer a booking
        storage = prompts._MOD_STORAGE
        for path, text in _sales_prompts(pol).items():
            hits = [
                ln for ln in text.replace(storage, "").splitlines() if _FITTING_OFFER.search(ln)
            ]
            assert not hits, (path, hits)

    def test_order_flow_sales_says_no_fitting_after_request(self) -> None:
        assert re.search(r"шиномонтаж НЕ пропонуй", prompts._MOD_ORDER_FLOW_SALES)

    def test_order_flow_sales_is_one_submit_call(self) -> None:
        # Wave 2-D: the chain rule did not hold — under sales there is no chain,
        # one submit_order_request hands the request over.
        text = prompts._MOD_ORDER_FLOW_SALES
        assert not re.search(r"create_order_draft|update_order_delivery|confirm_order", text)
        assert "submit_order_request ОДНИМ викликом" in text

    def test_variants_add_no_literal_numbers(self) -> None:
        digits = re.compile(r"\d+")
        assert not digits.findall(prompts._STAGE_ORDER_ACCEPTED_SALES)
        assert digits.findall(prompts._MOD_ORDER_FLOW_SALES) == digits.findall(
            prompts._MOD_ORDER_FLOW
        )

    @pytest.mark.parametrize("pol", OFF)
    def test_flag_off_keeps_the_incumbent_stage(self, pol: NetworkPolicy | None) -> None:
        text = _confirmed_prompt(pol, "tire_search")
        assert prompts._STAGE_OFFER_FITTING in text
        assert prompts._STAGE_ORDER_ACCEPTED_SALES not in text


# ── (2) one tyre-search scheme ─────────────────────────────────────────────


class TestTyreSearchScheme:
    def test_sales_module_never_asks_brand_or_budget(self) -> None:
        assert not _ASKS_BRAND.search(prompts._MOD_TIRE_SEARCH_SALES)

    @pytest.mark.parametrize("pol", ON)
    def test_no_sales_prompt_asks_brand_before_search(self, pol: NetworkPolicy) -> None:
        for path, text in _sales_prompts(pol).items():
            assert not _ASKS_BRAND.search(text), path

    def test_size_and_season_go_straight_to_search(self) -> None:
        line = [ln for ln in prompts._MOD_TIRE_SEARCH_SALES.splitlines() if ln.startswith("⚡")]
        assert len(line) == 1
        assert re.search(r"Розмір \+ сезон відомі.*одразу search_tires", line[0])
        assert "бренд" not in line[0].split("search_tires")[0].lower()

    def test_brand_only_if_the_caller_named_it(self) -> None:
        text = prompts._MOD_TIRE_SEARCH_SALES
        assert re.search(r"[Бб]ренд[^\n]*(не питай|НЕ питай)", text)

    def test_winter_still_asks_studs(self) -> None:
        # goldset studded_none_studless_with_caveat: the studs question stays
        assert "Шиповані чи без шипів?" in prompts._MOD_TIRE_SEARCH_SALES

    @pytest.mark.parametrize("pol", ON)
    def test_the_variant_is_wired_on(self, pol: NetworkPolicy) -> None:
        for path in ("sales", "tire_search", "consultation", "expansion"):
            text = _sales_prompts(pol)[path]
            assert prompts._MOD_TIRE_SEARCH_SALES in text, path
            assert prompts._MOD_TIRE_SEARCH not in text, path

    @pytest.mark.parametrize("pol", OFF)
    def test_flag_off_keeps_the_incumbent_module(self, pol: NetworkPolicy | None) -> None:
        text = prompts.assemble_prompt(scenario="tire_search", network_policy=pol)
        assert prompts._MOD_TIRE_SEARCH in text
        assert prompts._MOD_TIRE_SEARCH_SALES not in text


# ── (3) self-pickup: the tool first ────────────────────────────────────────


class TestPickupToolFirst:
    @pytest.mark.parametrize("pol", ON)
    def test_pickup_rule_calls_the_tool_before_addresses(self, pol: NetworkPolicy) -> None:
        frame = prompts.render_sales_scope(pol)
        (line,) = [
            ln for ln in frame.splitlines() if "самовивіз" in ln and "get_pickup_points" in ln
        ]
        assert re.search(r"спочатку виклич get_pickup_points[^\n]*потім[^\n]*адрес", line), line
        assert "не обіцяй" in line.lower()

    @pytest.mark.parametrize("pol", ON)
    def test_the_rule_reaches_the_prompt(self, pol: NetworkPolicy) -> None:
        assert "спочатку виклич get_pickup_points" in _sales_prompts(pol)["sales"]

    @pytest.mark.parametrize("pol", OFF)
    def test_flag_off_has_no_sales_pickup_rule(self, pol: NetworkPolicy | None) -> None:
        text = prompts.assemble_prompt(scenario="tire_search", network_policy=pol)
        assert "спочатку виклич get_pickup_points" not in text
