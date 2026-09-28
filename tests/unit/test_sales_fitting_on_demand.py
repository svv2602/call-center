"""Wave 2-D: the fitting/storage modules join a sales prompt only on demand.

`_MOD_FITTING` was 71 % of the Tvoya Shina sales prompt (93 639 chars against
23 933 for Prokoleso) and the «Умови мережі» lines drowned in it. The `sales`
bundle no longer carries it or `_MOD_STORAGE`; `sales_fitting_requested` adds
them once the call is about fitting — a caller turn detected as fitting, a
fitting/storage tool call, or a fitting field in the progress. The one
invariant that matters for the live ТШ fitting flow: from the first sign on,
including the turn that sign is said on, the module is in the prompt.

Every prompt here is built by the production builders from `src/`; the
live-path tests take the kwargs the pipeline hands the streaming loop after a
real `_transcript_processor_loop` turn.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, create_autospec, patch

import pytest

from scripts.configure_tenants import (
    PROKOLESO_CONFIG_PATCH,
    PROKOLESO_ENABLED_TOOLS,
    TVOYA_SHINA_CONFIG_PATCH,
)
from src.agent import prompts
from src.agent.network_policy import NetworkPolicy, render_network_block
from src.agent.tools import ALL_TOOLS
from src.core.pipeline import FSM_VOICE_STATES
from src.llm.models import LLMResponse, StreamDone, TextDelta, ToolCall, Usage
from tests.unit.mocks.mock_audio_socket import MockAudioSocketConnection
from tests.unit.mocks.mock_llm_router import MockLLMRouter
from tests.unit.mocks.mock_tts import MockTTSEngine
from tests.unit.test_pipeline_fsm_wire import Harness, fsm_flags, intent


def _policy(patch_: dict[str, Any], *, sales: bool) -> NetworkPolicy:
    return NetworkPolicy.from_tenant_config({**patch_, "sales_enabled": sales})


TSH_ON = _policy(TVOYA_SHINA_CONFIG_PATCH, sales=True)
TSH_OFF = _policy(TVOYA_SHINA_CONFIG_PATCH, sales=False)
PK_ON = _policy(PROKOLESO_CONFIG_PATCH, sales=True)
ALL_NAMES = {t["name"] for t in ALL_TOOLS}
PK_TOOLS = set(PROKOLESO_ENABLED_TOOLS)

FITTING_TURN = "Добрий день, хочу записатися на шиномонтаж"
PRICE_TURN = "Хочу дізнатися вартість шин 155 на 70 на 13"


def _base(policy: NetworkPolicy, tools: set[str] | None = None) -> str:
    return prompts.assemble_prompt(
        scenario="sales",
        include_pronunciation=False,
        enabled_tools=tools if tools is not None else ALL_NAMES,
        network_policy=policy,
    )


def _build(policy: NetworkPolicy, tools: set[str] | None = None, **kw: Any) -> str:
    enabled = tools if tools is not None else ALL_NAMES
    kw.setdefault("scenario", "sales")
    return prompts.build_system_prompt_with_context(
        _base(policy, enabled),
        is_modular=True,
        enabled_tools=enabled,
        network_policy=policy,
        network_policy_context=render_network_block(policy, None),
        **kw,
    )


def _has_fitting(system: str) -> bool:
    return prompts._MOD_FITTING in system


def _block(progress: dict[str, Any]) -> str:
    return prompts._render_fitting_progress(progress)


#: First line of the rendered block. `_MOD_FITTING` quotes «СТАН ЗАПИСУ» too,
#: so only a prompt without the module may be checked by the heading alone.
_BLOCK_HEAD = "## 🛑 СТАН ЗАПИСУ"


# ── the bundle ─────────────────────────────────────────────────────────


class TestBundle:
    def test_sales_bundle_has_no_on_demand_module(self) -> None:
        bundle = prompts.SCENARIO_MODULES["sales"]
        for mod in prompts._SALES_ON_DEMAND_MODULES:
            assert all(m is not mod for m in bundle)

    def test_tvoya_shina_sales_prompt_without_fitting_is_small(self) -> None:
        # A typical non-fitting turn: CallerID in the progress, network block on.
        system = _build(
            TSH_ON, caller_phone="+380501112233", fitting_progress={"caller_phone": "+380501112233"}
        )
        assert not _has_fitting(system)
        assert prompts._MOD_STORAGE not in system
        assert len(system) < 35_000

    def test_fitting_scenario_bundle_untouched(self) -> None:
        assert prompts.SCENARIO_MODULES["fitting"] == [prompts._MOD_FITTING, prompts._MOD_STORAGE]


# ── the predicate ──────────────────────────────────────────────────────


class TestTrigger:
    def test_nothing_is_not_fitting(self) -> None:
        assert not prompts.sales_fitting_requested(None, None, None)
        assert not prompts.sales_fitting_requested({"sales", "tire_search"}, {"search_tires"}, {})

    def test_fitting_scenario(self) -> None:
        assert prompts.sales_fitting_requested({"sales", "fitting"}, None, None)

    @pytest.mark.parametrize("tool", sorted(prompts._SALES_ON_DEMAND_TOOLS))
    def test_every_fitting_or_storage_tool(self, tool: str) -> None:
        assert prompts.sales_fitting_requested(None, {tool}, None)

    def test_tool_set_is_derived_from_the_map(self) -> None:
        assert {
            "get_fitting_stations",
            "get_fitting_slots",
            "book_fitting",
            "cancel_fitting",
            "get_fitting_price",
            "get_customer_bookings",
            "find_storage",
        } <= prompts._SALES_ON_DEMAND_TOOLS
        assert "search_tires" not in prompts._SALES_ON_DEMAND_TOOLS

    @pytest.mark.parametrize(
        ("text", "fitting"),
        [
            (FITTING_TURN, True),
            ("Шиномонтаж на Оболоні є?", True),
            ("треба перевзути машину", True),
            (PRICE_TURN, False),
            ("скільки коштує доставка у Львів", False),
        ],
    )
    def test_detect_scenario(self, text: str, fitting: bool) -> None:
        assert (prompts.detect_scenario_from_text(text) == "fitting") is fitting


def _progress(**session_fields: Any) -> dict[str, Any]:
    """The progress block as the pipeline builds it from session state."""
    h = Harness()
    h.session.caller_phone = "+380501112233"
    for k, v in session_fields.items():
        setattr(h.session, k, v)
    return h.pipeline._build_fitting_progress(None, krok8_confirmed=False)


class TestProgressTrigger:
    def test_neutral_fields_do_not_trigger(self) -> None:
        progress = _progress(
            fitting_customer_name="Ігор",
            fitting_vehicle_brand="Toyota",
            fitting_requested_weekday=4,
        )
        assert any(progress.values())  # would render the old block
        assert not prompts.sales_fitting_requested(None, None, progress)

    @pytest.mark.parametrize(
        "fields",
        [
            {"selected_fitting_date": "2026-09-30"},
            {"selected_fitting_time": "10:00"},
            {"fitting_storage_choice": "own"},
            {"fitting_plate": "сірий"},
            {"fitting_slots_offered": [{"date": "2026-09-30", "time": "10:00"}]},
            {"fsm_filled_fields": {"city": "Київ"}},
        ],
    )
    def test_booking_field_triggers(self, fields: dict[str, Any]) -> None:
        assert prompts.sales_fitting_requested(None, None, _progress(**fields))

    def test_new_progress_key_triggers_by_default(self) -> None:
        assert prompts.sales_fitting_requested(None, None, {"some_future_key": "x"})


# ── the builder ────────────────────────────────────────────────────────


class TestBuilder:
    @pytest.mark.parametrize(
        "kw",
        [
            {"active_scenarios": {"sales", "fitting"}},
            {"tools_called": {"get_fitting_stations"}},
            {"tools_called": {"find_storage"}},
            {"fitting_progress": _progress(selected_fitting_date="2026-09-30")},
            {"active_scenarios": {"sales", "fitting"}, "tools_called": {"get_fitting_stations"}},
        ],
    )
    def test_trigger_adds_both_modules_once(self, kw: dict[str, Any]) -> None:
        system = _build(TSH_ON, **kw)
        assert system.count(prompts._MOD_FITTING) == 1
        assert system.count(prompts._MOD_STORAGE) == 1

    def test_consultation_does_not_pull_fitting_in(self) -> None:
        # The consultation bundle lists _MOD_FITTING — not a fitting sign.
        system = _build(TSH_ON, active_scenarios={"sales", "consultation", "tire_search"})
        assert not _has_fitting(system)
        assert prompts._MOD_STORAGE not in system

    def test_tyre_tools_do_not_pull_fitting_in(self) -> None:
        system = _build(TSH_ON, tools_called={"search_tires", "get_vehicle_tire_sizes"})
        assert not _has_fitting(system)

    @pytest.mark.parametrize(
        "kw",
        [
            {"active_scenarios": {"sales", "fitting"}},
            {"tools_called": set(prompts._SALES_ON_DEMAND_TOOLS)},
            {"fitting_progress": _progress(selected_fitting_date="2026-09-30")},
        ],
    )
    def test_prokoleso_never_gets_fitting(self, kw: dict[str, Any]) -> None:
        system = _build(PK_ON, PK_TOOLS, **kw)
        for mod in (prompts._MOD_FITTING, prompts._MOD_FITTING_UNAVAILABLE, prompts._MOD_STORAGE):
            assert mod not in system
        assert _BLOCK_HEAD not in system

    def test_progress_block_only_with_the_module(self) -> None:
        # The module itself quotes the block's heading, so assert on the
        # rendered block, never on the heading alone.
        neutral = _progress(fitting_customer_name="Ігор")
        assert _BLOCK_HEAD not in _build(TSH_ON, fitting_progress=neutral)
        booking = _progress(selected_fitting_date="2026-09-30")
        assert _block(booking) in _build(TSH_ON, fitting_progress=booking)
        # a fitting sign elsewhere brings the block with the module
        assert _block(neutral) in _build(
            TSH_ON, fitting_progress=neutral, active_scenarios={"sales", "fitting"}
        )

    @pytest.mark.parametrize("active", [None, {"sales", "fitting"}])
    def test_ivr_fitting_scenario_under_sales_keeps_block(self, active: set[str] | None) -> None:
        neutral = _progress(fitting_customer_name="Ігор")
        base = prompts.assemble_prompt(scenario="fitting", network_policy=TSH_ON)
        system = prompts.build_system_prompt_with_context(
            base,
            is_modular=True,
            scenario="fitting",
            active_scenarios=active,
            network_policy=TSH_ON,
            fitting_progress=neutral,
        )
        assert system.count(prompts._MOD_FITTING) == 1
        assert system.count(prompts._MOD_STORAGE) == 1
        assert _block(neutral) in system

    def test_sales_off_progress_block_as_before(self) -> None:
        neutral = _progress(fitting_customer_name="Ігор")
        base = prompts.assemble_prompt(scenario="fitting", network_policy=TSH_OFF)
        system = prompts.build_system_prompt_with_context(
            base,
            is_modular=True,
            scenario="fitting",
            network_policy=TSH_OFF,
            fitting_progress=neutral,
        )
        assert _block(neutral) in system


# ── live path: the pipeline turn, then the streaming loop ──────────────


def _live_harness() -> Harness:
    h = Harness()
    h.streaming_loop._network_policy = TSH_ON
    h.session.scenario = "sales"
    h.session.active_scenarios.add("sales")
    h.session.caller_phone = "+380501112233"
    return h


def _system_from_turn(kwargs: dict[str, Any]) -> str:
    return _build(
        TSH_ON,
        scenario=kwargs["scenario"],
        active_scenarios=set(kwargs["active_scenarios"]),
        tools_called=set(kwargs["tools_called"] or ()),
        fitting_progress=kwargs["fitting_progress"],
    )


class TestLivePath:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("fsm", ["off", "shadow", "live"])
    async def test_first_fitting_turn_has_the_module(self, fsm: str) -> None:
        async def _classify(**_kw: Any) -> Any:
            return intent("BOOK")

        h = _live_harness()
        with (
            fsm_flags(enabled=fsm != "off", shadow_mode=fsm != "live"),
            patch("src.agent.intent_classifier.classify_intent", _classify),
        ):
            await h.run(FITTING_TURN)
        assert len(h.llm_kwargs) == 1  # the LLM got the turn
        assert _has_fitting(_system_from_turn(h.llm_kwargs[0]))

    @pytest.mark.asyncio
    async def test_module_stays_on_later_turns(self) -> None:
        h = _live_harness()
        with fsm_flags(enabled=False):
            await h.run(FITTING_TURN, "Київ", "так")
        assert len(h.llm_kwargs) == 3
        for kw in h.llm_kwargs:
            assert _has_fitting(_system_from_turn(kw))

    @pytest.mark.asyncio
    async def test_price_turn_has_no_module(self) -> None:
        h = _live_harness()
        with fsm_flags(enabled=False):
            await h.run(PRICE_TURN)
        assert not _has_fitting(_system_from_turn(h.llm_kwargs[0]))

    def test_fsm_still_does_not_speak(self) -> None:
        assert not FSM_VOICE_STATES


class _RecordingRouter(MockLLMRouter):
    def __init__(self, responses: Any) -> None:
        super().__init__(responses)
        self.systems: list[str | None] = []

    async def complete_stream(self, task: Any, messages: Any, **kwargs: Any):  # type: ignore[override]
        self.systems.append(kwargs.get("system"))
        async for event in super().complete_stream(task, messages, **kwargs):
            yield event


def _streaming_system(**turn: Any) -> str:
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
            system_prompt=_base(TSH_ON),
            tools=list(ALL_TOOLS),
            network_policy=TSH_ON,
            is_modular=True,
        )
        await loop.run_turn(FITTING_TURN, [], scenario="sales", **turn)

    asyncio.run(run())
    return str(router.systems[0])


class TestStreamingLoop:
    def test_fitting_scenario_reaches_the_llm(self) -> None:
        assert _has_fitting(_streaming_system(active_scenarios={"sales", "fitting"}))

    def test_no_sign_no_module(self) -> None:
        assert not _has_fitting(_streaming_system(active_scenarios={"sales"}))


# ── text path: LLMAgent detects and accumulates like the pipeline ──────


def _agent(policy: NetworkPolicy, replies: list[LLMResponse]) -> tuple[Any, Any]:
    from src.agent.agent import LLMAgent, ToolRouter
    from src.llm.router import LLMRouter

    llm_router = create_autospec(LLMRouter, instance=True)
    llm_router.complete = AsyncMock(side_effect=replies)
    tool_router = ToolRouter()

    async def _stations(**_kw: object) -> dict[str, Any]:
        return {"stations": []}

    tool_router.register("get_fitting_stations", _stations)
    tool_router.register("search_tires", _stations)
    agent = LLMAgent(
        api_key="test-key",
        system_prompt=_base(policy),
        llm_router=llm_router,
        tool_router=tool_router,
        tools=list(ALL_TOOLS),
        network_policy=policy,
        is_modular=True,
    )
    return agent, llm_router


def _text(reply: str = "Відповідь") -> LLMResponse:
    return LLMResponse(text=reply, usage=Usage(10, 5), provider="test")


def _systems(router: Any) -> list[str]:
    return [str(c.kwargs["system"]) for c in router.complete.call_args_list]


class TestTextPath:
    def test_same_turn_without_caller_state(self) -> None:
        agent, router = _agent(TSH_ON, [_text()])
        asyncio.run(agent.process_message(FITTING_TURN, []))
        assert _has_fitting(_systems(router)[0])

    def test_price_turn_has_no_module(self) -> None:
        agent, router = _agent(TSH_ON, [_text()])
        asyncio.run(agent.process_message(PRICE_TURN, []))
        assert not _has_fitting(_systems(router)[0])

    def test_scenario_accumulates_across_turns(self) -> None:
        agent, router = _agent(TSH_ON, [_text(), _text()])
        history: list[dict[str, Any]] = []
        asyncio.run(agent.process_message(FITTING_TURN, history))
        asyncio.run(agent.process_message("так", history))
        assert _has_fitting(_systems(router)[1])

    def test_tyre_tool_does_not_duplicate_the_sales_module(self) -> None:
        # The agent frames a scenario-less call as `sales`, as the live call
        # does — tool expansion against the fitting fallback would append a
        # second copy of the tyre-search module.
        tool_turn = LLMResponse(
            text="",
            tool_calls=[ToolCall(id="t1", name="search_tires", arguments={"size": "205/55 R16"})],
            stop_reason="tool_use",
            usage=Usage(10, 5),
            provider="test",
        )
        agent, router = _agent(TSH_ON, [tool_turn, _text(), _text()])
        history: list[dict[str, Any]] = []
        asyncio.run(agent.process_message("205/55 R16 літні", history))
        asyncio.run(agent.process_message("добре", history))
        assert _systems(router)[-1].count(prompts._MOD_TIRE_SEARCH_SALES) == 1
        assert not _has_fitting(_systems(router)[-1])

    def test_tool_call_accumulates(self) -> None:
        tool_turn = LLMResponse(
            text="",
            tool_calls=[ToolCall(id="t1", name="get_fitting_stations", arguments={"city": "Київ"})],
            stop_reason="tool_use",
            usage=Usage(10, 5),
            provider="test",
        )
        agent, router = _agent(TSH_ON, [tool_turn, _text(), _text()])
        history: list[dict[str, Any]] = []
        asyncio.run(agent.process_message("а в Києві де у вас?", history))
        assert not _has_fitting(_systems(router)[0])
        asyncio.run(agent.process_message("добре", history))
        assert _has_fitting(_systems(router)[-1])

    def test_sales_off_passes_through(self) -> None:
        agent, router = _agent(TSH_OFF, [_text()])
        asyncio.run(agent.process_message(FITTING_TURN, []))
        assert agent._call_scenarios == set()
        assert agent._call_tools == set()
        # nothing the caller did not pass reaches the builder
        expected = prompts.build_system_prompt_with_context(
            _base(TSH_OFF),
            is_modular=True,
            network_policy_context=render_network_block(TSH_OFF, None),
            enabled_tools=ALL_NAMES,
            network_policy=TSH_OFF,
        )
        assert _systems(router)[0] == expected
