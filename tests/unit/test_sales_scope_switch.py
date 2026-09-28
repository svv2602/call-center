"""Sales scope switch (``tenants.config.sales_enabled``) — wave 3-G.

Invariant №1: while ``sales_enabled`` is false (production in both networks)
the prompt of every turn, the tool set and the transfer-guard verdicts are
byte-for-byte those of the incumbent ``709c4d2``. The golden hashes below were
taken from the incumbent BEFORE this change (snapshots and script in the
checklist ``logs/baseline``) — they are not the function compared with itself.

With the flag on: the fitting-only frame gives way to the network frame
rendered from ``NetworkPolicy.services``; the default scenario is ``sales``;
tyre and order topics stop being evidence for a legitimate transfer.
"""

from __future__ import annotations

import ast
import asyncio
import datetime
import hashlib
import json
import pathlib
import re
import types
from typing import Any
from unittest.mock import AsyncMock, MagicMock, create_autospec, patch

import pytest

import src.agent.prompts as prompts
import src.agent.streaming_loop as sl
from scripts.configure_tenants import (
    PROKOLESO_CONFIG_PATCH,
    PROKOLESO_ENABLED_TOOLS,
    PROKOLESO_PROMPT_SUFFIX,
    TVOYA_SHINA_CONFIG_PATCH,
)
from src.agent import intent_classifier as ic
from src.agent.network_policy import SERVICE_LABELS, NetworkPolicy, render_network_block
from src.agent.prompt_manager import inject_pronunciation_rules
from src.agent.tools import ALL_TOOLS
from src.llm.models import (
    LLMResponse,
    StreamDone,
    TextDelta,
    ToolCallDelta,
    ToolCallEnd,
    ToolCallStart,
    Usage,
)
from tests.unit.mocks.mock_audio_socket import MockAudioSocketConnection
from tests.unit.mocks.mock_llm_router import MockLLMRouter
from tests.unit.mocks.mock_tts import MockTTSEngine

_REPO = pathlib.Path(__file__).resolve().parents[2]


def _policy(patch_: dict[str, Any], *, sales: bool) -> NetworkPolicy:
    return NetworkPolicy.from_tenant_config({**patch_, "sales_enabled": sales})


TSH_OFF = _policy(TVOYA_SHINA_CONFIG_PATCH, sales=False)
PK_OFF = _policy(PROKOLESO_CONFIG_PATCH, sales=False)
TSH_ON = _policy(TVOYA_SHINA_CONFIG_PATCH, sales=True)
PK_ON = _policy(PROKOLESO_CONFIG_PATCH, sales=True)


@pytest.fixture
def frozen_date(monkeypatch: pytest.MonkeyPatch) -> None:
    """The date table is part of the prompt — freeze it to the snapshot day."""

    class _Day(datetime.date):
        @classmethod
        def today(cls) -> _Day:  # type: ignore[override]
            return cls(2026, 9, 28)

    shim = types.SimpleNamespace(
        **{k: getattr(datetime, k) for k in dir(datetime) if not k.startswith("__")}
    )
    shim.date = _Day
    monkeypatch.setattr(prompts, "datetime", shim)


# ── Invariant №1: sales off == incumbent 709c4d2, byte for byte ─────────

_GOLDEN_SHA256: dict[str, str] = {
    "guard_verdicts": "b0b5446836b48dd9033f85bccf681736a310b5709e1f27e024cb952b6216f377",
    "pk.assemble.None": "4b80542874aef56fe85904723efba877801d363daae7bd29149ce8bfbafdaac9",
    "pk.assemble.compact": "9e145148f73ee393cb02f18d2d5f3bfd5757d48a8ee26917169b07faa6813595",
    "pk.assemble.consultation": "dba6d0ec7d0841dfeece0d98584da123a5282b6bc6a1497a46981662ad5cb080",
    "pk.assemble.fitting": "4b80542874aef56fe85904723efba877801d363daae7bd29149ce8bfbafdaac9",
    "pk.assemble.fitting.pron": "15f106a513acc46082fcd6f77ffcb1320eb007cc1b38e04bb4ca483e191978dd",
    "pk.assemble.order_status": "a7a3b5a4a139e685dfad60600a04c8244c2908cf74d2d087f84abdd82d40e283",
    "pk.assemble.tire_search": "b92a5d9d11f6c002437abfc816d51e17f9ad1a40083213ec4d5404182316c02e",
    "pk.call_base": "d7211d4794d3cca5679de1e77593995aca26a0ca92b84bc7a737b347dc7732dc",
    "pk.ctx.compact_upgrade": "6361759f96e372f71360328ee919c986637f716338b7210673705ec83147d4c1",
    "pk.ctx.s0": "a2ec41329592febc67da02384b6b937bf72828774b947990956c7ab2f1820dee",
    "pk.ctx.s1": "792fbf3a031a5754d4feacde241e2bf3aa24a38eaff7641f81131b68190f8897",
    "pk.ctx.s2": "0674fe76ae38ef6eda6731c017181dddbd934bd6745b8608ec981d9916ebc290",
    "pk.tools": "085c336e54f25b2787b0990bc5a8752062daf8885151d121e445e6533484e2a7",
    "pronunciation_rules": "b15b8a05cda5638e504a6d873d33a1de02032ff6c34379fc3c718decd6fb0058",
    "tsh.assemble.None": "7a2c5abbd9f2ffd73ef86bd88268ccb3b2cd8aa2bc8c56789c0836bcbbfd14f4",
    "tsh.assemble.compact": "9e145148f73ee393cb02f18d2d5f3bfd5757d48a8ee26917169b07faa6813595",
    "tsh.assemble.consultation": "706ecae9a79e382b03caf308c305b1fad1ee2bc87c53368a81dff6e7ee07401f",
    "tsh.assemble.fitting": "7a2c5abbd9f2ffd73ef86bd88268ccb3b2cd8aa2bc8c56789c0836bcbbfd14f4",
    "tsh.assemble.fitting.pron": "a4580bba350bdf1f8369b388ef4a62eb288a69753b5295060fd997e3d18f4451",
    "tsh.assemble.order_status": "a7a3b5a4a139e685dfad60600a04c8244c2908cf74d2d087f84abdd82d40e283",
    "tsh.assemble.tire_search": "b92a5d9d11f6c002437abfc816d51e17f9ad1a40083213ec4d5404182316c02e",
    "tsh.call_base": "ae1daa152b366df6cbcf368b8d65a6d05d93d3595e0ebe4603442a16df086956",
    "tsh.ctx.compact_upgrade": "6361759f96e372f71360328ee919c986637f716338b7210673705ec83147d4c1",
    "tsh.ctx.s0": "b6513b74a621ea5ee7d53c6a342138d13cedf178505d6ce53f5e29ae5ded9515",
    "tsh.ctx.s1": "0b6719e1270a853ec603fe9eb22cb205a5cff57653cc31352fa63629932af516",
    "tsh.ctx.s2": "294cda59056a1d652589564b12a8a89ba2be7a5da3a6e94ee1b024a21ecb8a10",
    "tsh.tools": "673e9fa62ba11210a98bd480b38628073f8316b1161e370a108dd49e5e194b55",
}

_INCUMBENT_TOOLS = {
    "tsh": [
        "transfer_to_operator",
        "get_fitting_stations",
        "get_fitting_slots",
        "book_fitting",
        "cancel_fitting",
        "get_fitting_price",
        "get_customer_bookings",
        "find_storage",
        "search_knowledge_base",
    ],
    "pk": ["transfer_to_operator", "search_knowledge_base"],
}


def _h(*turns: str) -> list[dict[str, Any]]:
    return [{"role": "user", "content": t} for t in turns]


_GUARD_CORPUS: list[tuple[str, list[dict[str, Any]]]] = [
    ("non_fitting_scope", _h("хочу купити шини 205/55 R16")),
    ("non_fitting_scope", _h("де мій заказ?")),
    ("non_fitting_scope", _h("скільки коштує доставка")),
    ("non_fitting_scope", _h("є в наявності Michelin?")),
    ("non_fitting_scope", _h("гарантія на шини")),
    ("non_fitting_scope", _h("мені потрібна виписка по рахунку")),
    ("non_fitting_scope", _h("хочу повернути шини")),
    ("non_fitting_scope", _h("запис на монтаж у Києві")),
    ("complex_question", _h("купити шини")),
    ("complex_question", _h("Іван")),
    ("fitting_service_unavailable", _h("шиномонтаж")),
    ("customer_request", _h("з'єднайте з оператором")),
    ("customer_request", _h("купити шини")),
    ("cannot_help", _h("не працює нічого")),
    ("negative_emotion", _h("купити")),
    ("non_fitting_scope", _h("підібрати диски")),
]


def _snapshots_with_flag_off() -> dict[str, str]:
    """Mirror of the incumbent snapshot script, run through the NEW entry
    points with a real policy whose ``sales_enabled`` is false."""
    from src.main import _SCENARIO_EMPHASIS, _default_scenario, _scenario_tool_names

    snaps: dict[str, str] = {}
    nets = {
        "tsh": (None, None, TSH_OFF),
        "pk": (set(PROKOLESO_ENABLED_TOOLS), PROKOLESO_PROMPT_SUFFIX, PK_OFF),
    }
    for net, (et, suffix, pol) in nets.items():
        for sc in ("fitting", "tire_search", "order_status", "consultation", None):
            snaps[f"{net}.assemble.{sc}"] = prompts.assemble_prompt(
                scenario=sc, include_pronunciation=False, enabled_tools=et, network_policy=pol
            )
        snaps[f"{net}.assemble.compact"] = prompts.assemble_prompt(
            scenario=None,
            include_pronunciation=False,
            compact=True,
            enabled_tools=et,
            network_policy=pol,
        )
        snaps[f"{net}.assemble.fitting.pron"] = prompts.assemble_prompt(
            scenario="fitting", enabled_tools=et, network_policy=pol
        )
        scenario = _default_scenario(pol)
        base = inject_pronunciation_rules(
            prompts.assemble_prompt(
                scenario=scenario,
                include_pronunciation=False,
                enabled_tools=et,
                network_policy=pol,
            ),
            prompts.pronunciation_rules_for_policy(prompts.PRONUNCIATION_RULES, pol),
        )
        if suffix:
            base = base + "\n\n" + suffix
        base = base + _SCENARIO_EMPHASIS[scenario]
        snaps[f"{net}.call_base"] = base

        tools = list(ALL_TOOLS)
        if et:
            allowed = set(et) | {"create_callback_request"}
            tools = [t for t in tools if t["name"] in allowed]
        allowed_scenario = _scenario_tool_names(scenario, pol)
        assert allowed_scenario is not None
        names = [t["name"] for t in tools if t["name"] in allowed_scenario]
        snaps[f"{net}.tools"] = json.dumps(names)
        assert names == _INCUMBENT_TOOLS[net]

        states: dict[str, dict[str, Any]] = {
            "s0": {"scenario": "fitting", "active_scenarios": {"fitting"}},
            "s1": {
                "scenario": "fitting",
                "active_scenarios": {"fitting", "tire_search"},
                "tools_called": {"get_fitting_stations", "search_tires"},
                "order_stage": "draft",
                "selected_station": {"id": "S1", "city": "Київ", "address": "вул. Тестова, 1"},
                "fitting_progress": {"name": "Іван", "city": "Київ"},
                "caller_phone": "+380000000000",
            },
            "s2": {
                "scenario": "fitting",
                "active_scenarios": {"fitting", "consultation", "order_status"},
                "tools_called": {"search_knowledge_base"},
                "order_stage": "confirmed",
                "order_id": "ORD-X",
            },
        }
        for key, kw in states.items():
            snaps[f"{net}.ctx.{key}"] = prompts.build_system_prompt_with_context(
                base,
                is_modular=True,
                network_policy_context=render_network_block(pol),
                agent_name="Олена",
                enabled_tools=set(names),
                network_policy=pol,
                **kw,
            )
        snaps[f"{net}.ctx.compact_upgrade"] = prompts.build_system_prompt_with_context(
            snaps[f"{net}.assemble.compact"],
            is_modular=True,
            scenario="tire_search",
            active_scenarios={"tire_search"},
            enabled_tools=set(names),
            network_policy=pol,
        )
    snaps["pronunciation_rules"] = prompts.pronunciation_rules_for_policy(
        prompts.PRONUNCIATION_RULES, TSH_OFF
    )
    verdicts = [
        sl._should_block_false_transfer({"reason": r}, h, sales_enabled=False)
        for r, h in _GUARD_CORPUS
    ]
    snaps["guard_verdicts"] = json.dumps(verdicts, ensure_ascii=False)
    return snaps


@pytest.mark.usefixtures("frozen_date")
class TestFlagOffIsIncumbent:
    def test_every_snapshot_matches_incumbent(self) -> None:
        snaps = _snapshots_with_flag_off()
        assert set(snaps) == set(_GOLDEN_SHA256)
        diverged = [
            k
            for k, v in snaps.items()
            if hashlib.sha256(v.encode()).hexdigest() != _GOLDEN_SHA256[k]
        ]
        assert diverged == []

    def test_no_policy_equals_policy_off(self) -> None:
        for pol in (TSH_OFF, PK_OFF):
            assert prompts.assemble_prompt(scenario="fitting", network_policy=pol) == (
                prompts.assemble_prompt(scenario="fitting")
            )

    def test_core_is_the_three_parts(self) -> None:
        assert (
            prompts._MOD_CORE
            == prompts._MOD_CORE_HEAD + prompts._MOD_SCOPE_FITTING_ONLY + prompts._MOD_CORE_TAIL
        )
        assert prompts.core_for_policy(None) is prompts._MOD_CORE
        assert prompts.core_for_policy(PK_OFF) is prompts._MOD_CORE

    def test_default_scenario_off_is_fitting(self) -> None:
        from src.main import _default_scenario

        assert _default_scenario(TSH_OFF) == "fitting"
        assert _default_scenario(PK_OFF) == "fitting"


# ── Flag on: the network frame ─────────────────────────────────────────


class TestSalesFrame:
    @pytest.mark.parametrize("pol", [TSH_ON, PK_ON])
    def test_fitting_only_frame_replaced(self, pol: NetworkPolicy) -> None:
        prompt = prompts.assemble_prompt(scenario="sales", network_policy=pol)
        assert prompts._MOD_SCOPE_FITTING_ONLY not in prompt
        assert "non_fitting_scope" not in prompt
        assert prompts.render_sales_scope(pol) in prompt
        assert prompt.startswith(prompts._MOD_CORE_HEAD + prompts.render_sales_scope(pol))

    @pytest.mark.parametrize("pol", [TSH_ON, PK_ON])
    def test_sales_modules_loaded(self, pol: NetworkPolicy) -> None:
        prompt = prompts.assemble_prompt(scenario="sales", network_policy=pol)
        for mod in (prompts._MOD_TIRE_SEARCH, prompts._MOD_ORDER_FLOW, prompts._MOD_CONSULTATION):
            assert mod in prompt
        # «заказ + монтаж в одном звонке» — позже (owner decision)
        assert prompts._MOD_COMBINED_FLOW not in prompt

    def test_tvoya_shina_gets_fitting_and_storage(self) -> None:
        prompt = prompts.assemble_prompt(scenario="sales", network_policy=TSH_ON)
        assert prompts._MOD_FITTING in prompt
        assert prompts._MOD_STORAGE in prompt
        frame = prompts.render_sales_scope(TSH_ON)
        assert "Не надаємо" not in frame
        assert "**Шиномонтаж**" in frame and "**Зберігання шин**" in frame

    @pytest.mark.parametrize("enabled_tools", [None, set(PROKOLESO_ENABLED_TOOLS)])
    def test_prokoleso_gets_no_fitting_or_storage(self, enabled_tools: set[str] | None) -> None:
        prompt = prompts.assemble_prompt(
            scenario="sales", network_policy=PK_ON, enabled_tools=enabled_tools
        )
        for mod in (prompts._MOD_FITTING, prompts._MOD_FITTING_UNAVAILABLE, prompts._MOD_STORAGE):
            assert mod not in prompt
        frame = prompts.render_sales_scope(PK_ON)
        assert "Не надаємо: послуги шиномонтажу, послуги зберігання шин." in frame
        assert "НЕ переводь на оператора" in frame
        # no fitting-station lookup for repairs, no fitting in the opening question
        assert "## ⛔ ЩО НЕ БРОНЮЄМО ЧЕРЕЗ БОТ" not in prompt
        assert "запис на шиномонтаж, чи є питання" not in prompt

    @pytest.mark.parametrize("pol", [TSH_ON, PK_ON])
    def test_frame_names_no_network(self, pol: NetworkPolicy) -> None:
        frame = prompts.render_sales_scope(pol).lower()
        for name in ("твоя шина", "про колесо", "prokoleso", "tvoya"):
            assert name not in frame

    def test_frame_follows_services_not_slug(self) -> None:
        only_storage = NetworkPolicy(sales_enabled=True, services=frozenset({"storage"}))
        frame = prompts.render_sales_scope(only_storage)
        assert "**Зберігання шин**" in frame
        assert "**Шиномонтаж**" not in frame
        assert "Не надаємо: послуги шиномонтажу." in frame
        prompt = prompts.assemble_prompt(scenario="sales", network_policy=only_storage)
        assert prompts._MOD_STORAGE in prompt and prompts._MOD_FITTING not in prompt

    def test_every_service_has_phrases_modules_and_tools(self) -> None:
        from src.main import _SERVICE_TOOLS

        assert set(prompts._SERVICE_NOT_PROVIDED_PHRASE) == set(SERVICE_LABELS)
        assert set(prompts._SERVICE_SCOPE_ITEM) == set(SERVICE_LABELS)
        assert set(prompts._SERVICE_MODULES) == set(SERVICE_LABELS)
        assert set(_SERVICE_TOOLS) == set(SERVICE_LABELS)

    @pytest.mark.parametrize("pol", [TSH_ON, PK_ON])
    def test_frame_has_no_literal_numbers(self, pol: NetworkPolicy) -> None:
        # list numbering «1.» at line start is the only digit allowed
        body = re.sub(r"(?m)^\d+\. ", "", prompts.render_sales_scope(pol))
        assert not re.search(r"\d", body)

    def test_disks_go_to_manager(self) -> None:
        frame = prompts.render_sales_scope(PK_ON)
        assert "Диски" in frame and "менеджер" in frame

    @pytest.mark.parametrize("pol", [TSH_ON, PK_ON])
    def test_scenario_less_call_gets_sales_bundle_not_router(self, pol: NetworkPolicy) -> None:
        compact = prompts.assemble_prompt(scenario=None, compact=True, network_policy=pol)
        assert prompts._COMPACT_MARKER not in compact
        assert compact == prompts.assemble_prompt(scenario="sales", network_policy=pol)

    def test_tenant_agent_name_still_replaced(self) -> None:
        base = prompts.assemble_prompt(scenario="sales", network_policy=PK_ON)
        out = prompts.build_system_prompt_with_context(base, agent_name="Марія")
        assert "Тебе звати Марія" in out and "ти Марія" in out
        assert "Олена" not in out


class TestSalesPronunciation:
    @pytest.mark.parametrize("pol", [TSH_ON, PK_ON])
    def test_order_number_not_dictated(self, pol: NetworkPolicy) -> None:
        rules = prompts.pronunciation_rules_for_policy(prompts.PRONUNCIATION_RULES, pol)
        assert "AI-1234" not in rules
        assert "Номер замовлення диктуй" not in rules
        assert rules.count(prompts.SALES_ORDER_NUMBER_RULE) == 1
        prompt = prompts.assemble_prompt(scenario="sales", network_policy=pol)
        assert "AI-1234" not in prompt
        assert "Номери замовлень диктуй" not in prompt
        assert "Номер заявки клієнту не називай" in prompt

    def test_custom_rules_without_the_line_get_it(self) -> None:
        rules = prompts.pronunciation_rules_for_policy("## Свої правила\n- щось", TSH_ON)
        assert rules.endswith(prompts.SALES_ORDER_NUMBER_RULE)

    def test_off_leaves_rules_untouched(self) -> None:
        custom = "- Номер замовлення диктуй: AI-1234"
        assert prompts.pronunciation_rules_for_policy(custom, PK_OFF) is custom
        assert prompts.pronunciation_rules_for_policy(custom, None) is custom


class TestSalesExpansion:
    """Per-turn prompt: module expansion never adds a service the network lacks."""

    def test_prokoleso_fitting_mention_adds_no_fitting_module(self) -> None:
        base = prompts.assemble_prompt(scenario="consultation", network_policy=PK_ON)
        kwargs: dict[str, Any] = {
            "is_modular": True,
            "scenario": "consultation",
            "active_scenarios": {"consultation", "fitting", "tire_search"},
            "tools_called": {"find_storage", "get_fitting_stations"},
            "enabled_tools": set(PROKOLESO_ENABLED_TOOLS),
        }
        on = prompts.build_system_prompt_with_context(base, network_policy=PK_ON, **kwargs)
        for mod in (prompts._MOD_FITTING, prompts._MOD_FITTING_UNAVAILABLE, prompts._MOD_STORAGE):
            assert mod not in on
        assert prompts._MOD_COMBINED_FLOW not in on
        # the same turn with the flag off still expands (incumbent behaviour)
        off = prompts.build_system_prompt_with_context(base, network_policy=PK_OFF, **kwargs)
        assert prompts._MOD_STORAGE in off

    def test_compact_upgrade_keeps_sales_frame(self) -> None:
        compact = prompts.assemble_prompt(scenario=None, compact=True)  # e.g. a restored session
        out = prompts.build_system_prompt_with_context(
            compact, is_modular=True, scenario="tire_search", network_policy=PK_ON
        )
        assert prompts.render_sales_scope(PK_ON) in out
        assert prompts._MOD_SCOPE_FITTING_ONLY not in out


# ── Agents pass the policy into the per-turn build ─────────────────────

_PK_CONSULT_BASE = prompts.assemble_prompt(
    scenario="consultation", include_pronunciation=False, network_policy=PK_ON
)
_EXPANDING_TURN: dict[str, Any] = {
    "scenario": "consultation",
    "active_scenarios": {"consultation", "fitting"},
}


def _llm_agent_system(policy: NetworkPolicy) -> str:
    from src.agent.agent import LLMAgent
    from src.llm.router import LLMRouter

    llm_router = create_autospec(LLMRouter, instance=True)
    llm_router.complete = AsyncMock(
        return_value=LLMResponse(text="Відповідь", usage=Usage(10, 5), provider="test")
    )
    agent = LLMAgent(
        api_key="test-key",
        system_prompt=_PK_CONSULT_BASE,
        llm_router=llm_router,
        network_policy=policy,
        is_modular=True,
    )
    asyncio.run(agent.process_message("Привіт", [], **_EXPANDING_TURN))
    return str(llm_router.complete.call_args.kwargs["system"])


class _RecordingRouter(MockLLMRouter):
    def __init__(self, responses: Any) -> None:
        super().__init__(responses)
        self.systems: list[str | None] = []

    async def complete_stream(self, task: Any, messages: Any, **kwargs: Any):  # type: ignore[override]
        self.systems.append(kwargs.get("system"))
        async for event in super().complete_stream(task, messages, **kwargs):
            yield event


def _streaming_system(policy: NetworkPolicy) -> str:
    from src.agent.agent import ToolRouter
    from src.agent.streaming_loop import StreamingAgentLoop

    router = _RecordingRouter(
        [[TextDelta(text="Добрий день."), StreamDone(stop_reason="end_turn", usage=Usage(1, 1))]]
    )

    async def run() -> None:
        loop = StreamingAgentLoop(
            llm_router=router,  # type: ignore[arg-type]
            tool_router=ToolRouter(),
            tts=MockTTSEngine(),  # type: ignore[arg-type]
            conn=MockAudioSocketConnection(),  # type: ignore[arg-type]
            barge_in_event=asyncio.Event(),
            system_prompt=_PK_CONSULT_BASE,
            network_policy=policy,
            is_modular=True,
        )
        await loop.run_turn("Привіт", [], **_EXPANDING_TURN)

    asyncio.run(run())
    return str(router.systems[0])


class TestAgentWiring:
    @pytest.mark.parametrize("build", [_llm_agent_system, _streaming_system])
    def test_agent_turn_honours_policy(self, build: Any) -> None:
        system = build(PK_ON)
        assert prompts._MOD_FITTING_UNAVAILABLE not in system
        assert prompts._MOD_STORAGE not in system

    @pytest.mark.parametrize("build", [_llm_agent_system, _streaming_system])
    def test_agent_turn_off_expands_as_before(self, build: Any) -> None:
        assert prompts._MOD_STORAGE in build(PK_OFF)


# ── main.py: default scenario, prompt, tools ───────────────────────────


def _tenant(slug: str, config: dict[str, Any], enabled_tools: list[str]) -> dict[str, Any]:
    import uuid

    return {
        "id": uuid.uuid4(),
        "slug": slug,
        "name": slug,
        "network_id": slug,
        "agent_name": "Олена",
        "greeting": None,
        "enabled_tools": enabled_tools,
        "prompt_suffix": None,
        "config": config,
        "is_active": True,
    }


def _run_handle_call(tenant: dict[str, Any]) -> tuple[dict[str, Any], Any]:
    """Drive handle_call to LLMAgent construction; return its kwargs and the session."""
    from uuid import uuid4

    from src.agent.prompt_manager import PromptManager
    from src.main import _resolve_tenant, handle_call

    captured: dict[str, Any] = {}
    sessions: list[Any] = []

    class FakeAgent:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

    class FakePipeline:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            sessions.append(args[4] if len(args) > 4 else kwargs.get("session"))

        async def run(self) -> None:
            return None

    pm = create_autospec(PromptManager, instance=True)
    pm.get_active_templates.return_value = {"greeting": "Привіт"}
    pm.get_version.return_value = None
    conn = MagicMock(spec=["channel_uuid", "is_closed", "close", "read_audio_packet"])
    conn.channel_uuid = uuid4()
    conn.is_closed = False

    with (
        patch("src.main._db_engine", MagicMock(spec=[])),
        patch("src.main._redis", None),
        patch("src.main._tts_engine", MagicMock(spec=[])),
        patch("src.main._store_client", MagicMock(spec=[])),
        patch("src.main._resolve_tenant", autospec=True, return_value=tenant) as rt,
        patch("src.main.PromptManager", return_value=pm),
        patch("src.main.get_tools_with_overrides", AsyncMock(return_value=list(ALL_TOOLS))),
        patch("src.main.GoogleSTTEngine", return_value=MagicMock(spec=[])),
        patch("src.main.LLMAgent", FakeAgent),
        patch("src.main.CallPipeline", FakePipeline),
        patch("src.main._build_tool_router", return_value=MagicMock(spec=["set_execute_hook"])),
        patch("src.main.PIIVault", return_value=MagicMock(spec=[])),
        patch("src.main.publish_event", AsyncMock()),
        patch("src.main.active_calls"),
        patch("src.main.calls_total"),
    ):
        assert rt is not _resolve_tenant
        asyncio.run(handle_call(conn))
    return captured, sessions[0] if sessions else None


class TestMainWiring:
    def test_tvoya_shina_on_gets_sales_prompt_and_tools(self) -> None:
        cfg = {**TVOYA_SHINA_CONFIG_PATCH, "sales_enabled": True}
        kwargs, session = _run_handle_call(_tenant("tvoya-shina", cfg, []))
        system = kwargs["system_prompt"]
        assert prompts.render_sales_scope(TSH_ON) in system
        assert prompts._MOD_SCOPE_FITTING_ONLY not in system
        assert prompts._MOD_ORDER_FLOW in system and prompts._MOD_TIRE_SEARCH in system
        names = {t["name"] for t in kwargs["tools"]}
        assert {"search_tires", "create_order_draft", "confirm_order", "book_fitting"} <= names
        assert "find_storage" in names
        assert session is not None
        assert session.scenario == "sales"
        assert "sales" in session.active_scenarios

    def test_prokoleso_on_gets_no_fitting(self) -> None:
        cfg = {**PROKOLESO_CONFIG_PATCH, "sales_enabled": True}
        kwargs, _ = _run_handle_call(_tenant("prokoleso", cfg, list(PROKOLESO_ENABLED_TOOLS)))
        system = kwargs["system_prompt"]
        assert prompts.render_sales_scope(PK_ON) in system
        for mod in (prompts._MOD_FITTING, prompts._MOD_FITTING_UNAVAILABLE, prompts._MOD_STORAGE):
            assert mod not in system
        names = {t["name"] for t in kwargs["tools"]}
        assert "search_tires" in names and "create_order_draft" in names
        assert not names & {"book_fitting", "get_fitting_stations", "find_storage"}

    def test_prokoleso_on_without_tenant_tool_list_still_no_fitting(self) -> None:
        # default-deny by services, not by enabled_tools alone
        cfg = {**PROKOLESO_CONFIG_PATCH, "sales_enabled": True}
        kwargs, _ = _run_handle_call(_tenant("prokoleso", cfg, []))
        names = {t["name"] for t in kwargs["tools"]}
        assert not names & {"book_fitting", "get_fitting_stations", "find_storage"}

    @pytest.mark.parametrize(
        ("slug", "patch_", "tools"),
        [
            ("tvoya-shina", TVOYA_SHINA_CONFIG_PATCH, []),
            ("prokoleso", PROKOLESO_CONFIG_PATCH, list(PROKOLESO_ENABLED_TOOLS)),
        ],
    )
    def test_off_is_fitting_scope(
        self, slug: str, patch_: dict[str, Any], tools: list[str]
    ) -> None:
        kwargs, session = _run_handle_call(_tenant(slug, dict(patch_), tools))
        assert prompts._MOD_SCOPE_FITTING_ONLY in kwargs["system_prompt"]
        assert [t["name"] for t in kwargs["tools"]] == _INCUMBENT_TOOLS[
            "tsh" if slug == "tvoya-shina" else "pk"
        ]
        assert session is not None
        assert session.scenario == "fitting"
        assert "fitting" in session.active_scenarios


def _calls_to(tree: ast.AST, name: str) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == name
    ]


def _kw(call: ast.Call, name: str) -> ast.expr | None:
    return next((kw.value for kw in call.keywords if kw.arg == name), None)


class TestCallSiteWiring:
    def test_main_pronunciation_goes_through_policy(self) -> None:
        tree = ast.parse((_REPO / "src/main.py").read_text(encoding="utf-8"))
        calls = _calls_to(tree, "inject_pronunciation_rules")
        assert calls
        for call in calls:
            rules = call.args[1]
            assert isinstance(rules, ast.Call)
            assert isinstance(rules.func, ast.Name)
            assert rules.func.id == "pronunciation_rules_for_policy"
            arg = rules.args[1]
            assert isinstance(arg, ast.Name) and arg.id == "network_policy"

    @pytest.mark.parametrize(
        ("path", "func"),
        [
            ("src/agent/agent.py", "build_system_prompt_with_context"),
            ("src/agent/streaming_loop.py", "build_system_prompt_with_context"),
            ("src/main.py", "assemble_prompt"),
        ],
    )
    def test_policy_passed(self, path: str, func: str) -> None:
        tree = ast.parse((_REPO / path).read_text(encoding="utf-8"))
        calls = _calls_to(tree, func)
        assert calls
        for call in calls:
            value = _kw(call, "network_policy")
            assert value is not None, f"{path}: {func} without network_policy"

    def test_streaming_loop_guard_calls_pass_flag(self) -> None:
        tree = ast.parse((_REPO / "src/agent/streaming_loop.py").read_text(encoding="utf-8"))
        for func in ("_should_block_false_transfer", "hold_unconfirmed_transfer_promise"):
            calls = _calls_to(tree, func)
            assert calls
            for call in calls:
                assert _kw(call, "sales_enabled") is not None, func


# ── streaming_loop: transfer guard under sales ─────────────────────────


class TestTransferGuardSales:
    @pytest.mark.parametrize(
        "text",
        [
            "хочу купити шини 205/55 R16",
            "хочу купить резину",
            "де мій заказ?",
            "оформити замовлення",
            "скільки коштує доставка",
            "є в наявності Michelin?",
            "а в наличии есть?",
            "гарантія на шини яка",
            "можна оплатити карткою",
            "а розстрочка є",
        ],
    )
    @pytest.mark.parametrize("reason", ["non_fitting_scope", "complex_question"])
    def test_sales_topic_transfer_blocked(self, text: str, reason: str) -> None:
        msg = sl._should_block_false_transfer({"reason": reason}, _h(text), sales_enabled=True)
        assert msg is not None and sl._GUARD_MARKER in msg
        assert "чекліст запису" not in msg
        # incumbent: the same words are evidence and let the transfer through
        assert sl._should_block_false_transfer({"reason": reason}, _h(text)) is None

    @pytest.mark.parametrize(
        "text",
        [
            "мені потрібна виписка по рахунку",
            "хочу повернути шини",
            "рекламація на товар",
            "підібрати диски на авто",
            "не працює ваш сайт, жах",
        ],
    )
    def test_non_sales_topic_still_passes(self, text: str) -> None:
        assert (
            sl._should_block_false_transfer(
                {"reason": "non_fitting_scope"}, _h(text), sales_enabled=True
            )
            is None
        )

    def test_customer_request_unchanged(self) -> None:
        hist = _h("купити шини", "з'єднайте з оператором")
        assert (
            sl._should_block_false_transfer(
                {"reason": "customer_request"}, hist, sales_enabled=True
            )
            is None
        )

    def test_customer_request_block_does_not_send_to_fitting(self) -> None:
        on = sl._should_block_false_transfer(
            {"reason": "customer_request"}, _h("Іван"), sales_enabled=True
        )
        off = sl._should_block_false_transfer({"reason": "customer_request"}, _h("Іван"))
        assert on is not None and off is not None
        assert "fitting-чекліст" not in on and "Крок 1" not in on
        assert "fitting-чекліст" in off

    def test_loop_breaker_kept(self) -> None:
        hist: list[dict[str, Any]] = _h("купити шини")
        for _ in range(sl._MAX_BLOCKS_PER_CALL):
            msg = sl._should_block_false_transfer(
                {"reason": "non_fitting_scope"}, hist, sales_enabled=True
            )
            assert msg is not None
            hist.append({"role": "user", "content": [{"type": "tool_result", "content": msg}]})
        assert (
            sl._should_block_false_transfer(
                {"reason": "non_fitting_scope"}, hist, sales_enabled=True
            )
            is None
        )

    def test_sales_keywords_are_a_subset(self) -> None:
        assert set(sl._SALES_SCOPE_KEYWORDS) <= set(sl._OUT_OF_SCOPE_KEYWORDS)
        assert not set(sl._SALES_SCOPE_KEYWORDS) & set(sl._OUT_OF_SCOPE_KEYWORDS_SALES)

    @pytest.mark.parametrize("stem", sl._SALES_SCOPE_KEYWORDS)
    def test_every_sales_stem_is_a_sales_intent(self, stem: str) -> None:
        assert ic.sales_intents(stem + "а")

    def test_block_message_hints_by_intent(self) -> None:
        order = sl._should_block_false_transfer(
            {"reason": "non_fitting_scope"}, _h("хочу купити"), sales_enabled=True
        )
        consult = sl._should_block_false_transfer(
            {"reason": "non_fitting_scope"}, _h("є в наявності?"), sales_enabled=True
        )
        assert order is not None and "заявка на замовлення" in order
        assert consult is not None and "search_knowledge_base" in consult


def _transfer_turn(text: str) -> list[Any]:
    return [
        TextDelta(text=text),
        ToolCallStart(id="t1", name="transfer_to_operator"),
        ToolCallDelta(
            id="t1", arguments_chunk=json.dumps({"reason": "non_fitting_scope", "summary": "x"})
        ),
        ToolCallEnd(id="t1"),
        TextDelta(text=" Які шини шукаєте?"),
        StreamDone(stop_reason="tool_use", usage=Usage(1, 1)),
    ]


def _run_transfer(policy: NetworkPolicy) -> tuple[AsyncMock, str]:
    from src.agent.agent import ToolRouter
    from src.agent.streaming_loop import StreamingAgentLoop

    async def transfer_to_operator(**kwargs: Any) -> dict[str, Any]:
        return {"status": "transferred"}

    handler = create_autospec(transfer_to_operator)
    router = MockLLMRouter(
        [
            _transfer_turn("Одну секунду, з'єдную вас з оператором."),
            [
                TextDelta(text="Які шини шукаєте?"),
                StreamDone(stop_reason="end_turn", usage=Usage(1, 1)),
            ],
        ]
    )
    tool_router = ToolRouter()
    tool_router.register("transfer_to_operator", handler)
    tts = MockTTSEngine()

    async def run() -> Any:
        loop = StreamingAgentLoop(
            llm_router=router,  # type: ignore[arg-type]
            tool_router=tool_router,
            tts=tts,  # type: ignore[arg-type]
            conn=MockAudioSocketConnection(),  # type: ignore[arg-type]
            barge_in_event=asyncio.Event(),
            system_prompt="Test",
            network_policy=policy,
        )
        return await loop.run_turn("хочу купити шини", [])

    result = asyncio.run(run())
    return handler, result.spoken_text


class TestStreamingLoopWiring:
    def test_sales_on_blocks_transfer_and_promise(self) -> None:
        handler, spoken = _run_transfer(TSH_ON)
        handler.assert_not_called()
        assert "з'єдную" not in spoken

    def test_sales_off_transfers_as_before(self) -> None:
        handler, _ = _run_transfer(TSH_OFF)
        handler.assert_called_once()


# ── intent_classifier: ORDER / CONSULT without touching the fitting path ─


class TestSalesIntents:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("хочу купити шини", ("ORDER", "CONSULT")),
            ("хочу купить резину", ("ORDER", "CONSULT")),
            ("оформіть замовлення", ("ORDER",)),
            ("где мой заказ", ("ORDER",)),
            ("доставка новою поштою", ("ORDER",)),
            ("які диски порадите", ("CONSULT",)),
            ("что лучше посоветуете", ("CONSULT",)),
            ("чи є в наявності", ("CONSULT",)),
            ("запис на шиномонтаж", ()),
            ("хочу переобутися завтра", ()),
            ("", ()),
        ],
    )
    def test_detects(self, text: str, expected: tuple[str, ...]) -> None:
        assert ic.sales_intents(text) == expected

    def test_fitting_classifier_contract_unchanged(self) -> None:
        assert ic._ALLOWED_INTENTS == ("BOOK", "PRICE", "CANCEL", "RESCHEDULE", "TRANSFER")
        assert "ORDER" not in ic._SYSTEM_PROMPT and "CONSULT" not in ic._SYSTEM_PROMPT
        assert not set(ic.SALES_INTENTS) & set(ic._ALLOWED_INTENTS)

    def test_fsm_voice_states_stay_empty(self) -> None:
        from src.core.pipeline import FSM_VOICE_STATES

        assert not FSM_VOICE_STATES
