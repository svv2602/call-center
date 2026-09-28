"""NetworkPolicy — per-network conditions as tenant data, rendered into the prompt.

Invariants pinned here:
- ``sales_enabled`` off (the default and both real networks today) → the
  assembled system prompt is byte-for-byte what it was without the policy.
- Parsing is default-deny: missing/garbage config never becomes a promise.
- The two networks' real configs (``scripts/configure_tenants.py``) differ
  where the owner said they differ, and nowhere leaks one into the other.
- The policy actually reaches the prompt: main → LLMAgent/StreamingAgentLoop
  → ``build_system_prompt_with_context``; sandbox → LLMAgent.
"""

from __future__ import annotations

import ast
import asyncio
import logging
import pathlib
from typing import Any
from unittest.mock import AsyncMock, create_autospec

import pytest

from scripts.configure_tenants import PROKOLESO_CONFIG_PATCH, TVOYA_SHINA_CONFIG_PATCH
from src.agent.network_policy import (
    BANK_LABELS,
    DELIVERY_MODES,
    PAYMENT_LABELS,
    SERVICE_LABELS,
    NetworkPolicy,
    render_network_block,
)
from src.agent.prompts import build_system_prompt_with_context
from src.llm.models import LLMResponse, StreamDone, TextDelta, Usage
from tests.unit.mocks.mock_audio_socket import MockAudioSocketConnection
from tests.unit.mocks.mock_llm_router import MockLLMRouter
from tests.unit.mocks.mock_tts import MockTTSEngine

_BASE = "Базовий промпт для тесту."
_REPO = pathlib.Path(__file__).resolve().parents[2]


def _enabled(patch: dict[str, Any]) -> dict[str, Any]:
    """The real network config, with the master switch flipped on."""
    return {**patch, "sales_enabled": True}


def _block(cfg: Any) -> str:
    block = render_network_block(NetworkPolicy.from_tenant_config(cfg))
    assert block is not None
    return block


# ── Default-deny parsing ───────────────────────────────────────────────


class TestDefaults:
    @pytest.mark.parametrize("cfg", [None, {}, {"store_api_url": "http://x"}])
    def test_no_config_promises_nothing(self, cfg: Any) -> None:
        policy = NetworkPolicy.from_tenant_config(cfg)
        assert policy.sales_enabled is False
        assert policy.delivery_mode == "unknown"
        assert policy.services == frozenset()
        assert policy.payment_methods == ()
        assert policy.extended_warranty_brands == frozenset()
        assert policy.pickup_available is False

    @pytest.mark.parametrize(
        "cfg",
        [
            {"sales_enabled": True},
            {"sales_enabled": True, "network_policy": {}},
            {"sales_enabled": True, "network_policy": {"delivery_mode": "express"}},
            {"sales_enabled": True, "network_policy": {"delivery_mode": None}},
        ],
    )
    def test_unknown_delivery_is_not_free(self, cfg: dict[str, Any]) -> None:
        policy = NetworkPolicy.from_tenant_config(cfg)
        assert policy.delivery_mode == "unknown"
        block = _block(cfg)
        assert "безкоштовн" not in block.lower()
        assert "перевізника" not in block
        assert "уточнить менеджер" in block

    def test_bare_switch_offers_no_payment_services_or_warranty(self) -> None:
        block = _block({"sales_enabled": True})
        for label in PAYMENT_LABELS.values():
            assert label not in block
        assert "Послуги мережі" not in block
        assert "Розширена гарантія" not in block
        assert "Самовивіз" not in block


class TestGarbageConfig:
    def test_non_dict_config(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING, logger="src.agent.network_policy"):
            policy = NetworkPolicy.from_tenant_config(["sales_enabled"])
        assert policy == NetworkPolicy()
        assert caplog.records

    @pytest.mark.parametrize("value", ["true", 1, "yes", None, [True]])
    def test_sales_enabled_needs_real_bool(self, value: Any) -> None:
        assert NetworkPolicy.from_tenant_config({"sales_enabled": value}).sales_enabled is False

    def test_network_policy_not_a_dict(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING, logger="src.agent.network_policy"):
            policy = NetworkPolicy.from_tenant_config(
                {"sales_enabled": True, "network_policy": "free delivery"}
            )
        assert policy == NetworkPolicy(sales_enabled=True)
        assert caplog.records

    def test_garbage_fields_fall_back_with_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        cfg = {
            "sales_enabled": True,
            "network_policy": {
                "services": "fitting",
                "payment_methods": ["cod", "bitcoin", 7, "", "COD"],
                "installment_banks": ["monobank", "revolut"],
                "pickup_available": "yes",
                "delivery_eta_text": 3,
                "recommend_count": "three",
                "order_finish": "confirm_immediately",
            },
        }
        with caplog.at_level(logging.WARNING, logger="src.agent.network_policy"):
            policy = NetworkPolicy.from_tenant_config(cfg)
        assert policy.services == frozenset()
        assert policy.payment_methods == ("cod",)
        assert policy.installment_banks == ("monobank",)
        assert policy.pickup_available is False
        assert policy.delivery_eta_text is None
        assert policy.recommend_count == NetworkPolicy().recommend_count
        assert policy.order_finish == NetworkPolicy().order_finish
        assert len(caplog.records) >= 7

    @pytest.mark.parametrize("value", [0, 1, 99, -5, True])
    def test_recommend_count_clamped(self, value: Any) -> None:
        policy = NetworkPolicy.from_tenant_config({"network_policy": {"recommend_count": value}})
        assert 2 <= policy.recommend_count <= 3

    def test_unknown_members_never_rendered(self) -> None:
        block = _block(
            {
                "sales_enabled": True,
                "network_policy": {
                    "services": ["fitting", "car_wash"],
                    "payment_methods": ["bitcoin"],
                },
            }
        )
        assert "car_wash" not in block
        assert "bitcoin" not in block


# ── Rendering ──────────────────────────────────────────────────────────


class TestRender:
    @pytest.mark.parametrize("patch", [TVOYA_SHINA_CONFIG_PATCH, PROKOLESO_CONFIG_PATCH])
    def test_disabled_renders_nothing(self, patch: dict[str, Any]) -> None:
        assert render_network_block(NetworkPolicy.from_tenant_config(patch)) is None

    def test_none_policy_renders_nothing(self) -> None:
        assert render_network_block(None) is None

    @pytest.mark.parametrize(
        "cfg",
        [
            _enabled(TVOYA_SHINA_CONFIG_PATCH),
            _enabled(PROKOLESO_CONFIG_PATCH),
            {"sales_enabled": True},
            {"sales_enabled": True, "network_policy": {"delivery_mode": "free"}},
            {"sales_enabled": True, "network_policy": {"delivery_mode": "carrier_tariff"}},
            {
                "sales_enabled": True,
                "network_policy": {
                    "delivery_mode": "carrier_tariff",
                    "delivery_carriers": [],
                    "payment_methods": [],
                    "installment_banks": [],
                    "brand_priority": [],
                    "services": [],
                    "extended_warranty_brands": [],
                    "delivery_eta_text": "   ",
                    "cod_fee_text": "",
                },
            },
        ],
    )
    def test_no_empty_lines_or_dangling_fragments(self, cfg: dict[str, Any]) -> None:
        block = _block(cfg)
        lines = block.strip("\n").split("\n")
        assert all(line.strip() for line in lines), block
        assert "()" not in block
        assert ": ." not in block
        assert ", ," not in block
        assert not any(line.rstrip().endswith((":", ",")) for line in lines), block

    def test_every_value_comes_from_config(self) -> None:
        """Change each text value in config → the block changes with it."""
        cfg = _enabled(TVOYA_SHINA_CONFIG_PATCH)
        policy_cfg = dict(cfg["network_policy"])
        policy_cfg["cod_fee_text"] = "маркер-комісії"
        policy_cfg["delivery_eta_text"] = "маркер-терміну"
        policy_cfg["delivery_carriers"] = ["Маркер-Перевізник"]
        block = _block({**cfg, "network_policy": policy_cfg})
        assert "маркер-комісії" in block
        assert "маркер-терміну" in block
        assert "Маркер-Перевізник" in block
        for original in (
            TVOYA_SHINA_CONFIG_PATCH["network_policy"]["cod_fee_text"],
            TVOYA_SHINA_CONFIG_PATCH["network_policy"]["delivery_eta_text"],
        ):
            assert original not in block

    def test_never_says_order_confirmed_as_done(self) -> None:
        for patch in (TVOYA_SHINA_CONFIG_PATCH, PROKOLESO_CONFIG_PATCH):
            block = _block(_enabled(patch))
            assert "менеджер передзвонить" in block
            assert "Не кажи, що замовлення підтверджено" in block

    @pytest.mark.parametrize("mode", DELIVERY_MODES)
    def test_every_delivery_mode_has_exactly_one_delivery_line(self, mode: str) -> None:
        block = _block({"sales_enabled": True, "network_policy": {"delivery_mode": mode}})
        assert sum(line.startswith("- Доставка:") for line in block.split("\n")) == 1


# ── The two real networks ──────────────────────────────────────────────


class TestNetworkDifferences:
    def setup_method(self) -> None:
        self.ts = NetworkPolicy.from_tenant_config(_enabled(TVOYA_SHINA_CONFIG_PATCH))
        self.pk = NetworkPolicy.from_tenant_config(_enabled(PROKOLESO_CONFIG_PATCH))
        self.ts_block = render_network_block(self.ts) or ""
        self.pk_block = render_network_block(self.pk) or ""

    def test_both_disabled_until_acceptance(self) -> None:
        for patch in (TVOYA_SHINA_CONFIG_PATCH, PROKOLESO_CONFIG_PATCH):
            assert NetworkPolicy.from_tenant_config(patch).sales_enabled is False

    def test_patches_touch_only_own_keys(self) -> None:
        """``config || patch`` must not clobber keys other code owns."""
        protected = {"store_api_url", "store_api_key", "excluded_station_ids"}
        protected.add("agent_provider_override")
        for patch in (TVOYA_SHINA_CONFIG_PATCH, PROKOLESO_CONFIG_PATCH):
            assert set(patch) == {"sales_enabled", "network_policy"}
            assert not protected & set(patch)

    def test_every_config_update_merges(self) -> None:
        """A plain ``config = :patch`` would wipe store_api_url & co. on prod."""
        src = (_REPO / "scripts" / "configure_tenants.py").read_text()
        updates = [ln.strip() for ln in src.splitlines() if ln.strip().startswith("config =")]
        assert len(updates) == 2
        for line in updates:
            assert line.startswith("config = COALESCE(config, CAST('{}' AS jsonb)) ||"), line

    def test_delivery(self) -> None:
        assert self.ts.delivery_mode == "free"
        assert self.pk.delivery_mode == "carrier_tariff"
        assert "безкоштовн" in self.ts_block
        assert "безкоштовн" not in self.pk_block
        assert "Вартість доставки не називай" in self.pk_block

    def test_services_only_in_tvoya_shina(self) -> None:
        assert self.ts.services == frozenset(SERVICE_LABELS)
        assert self.pk.services == frozenset()
        for label in SERVICE_LABELS.values():
            assert f"Не надаємо: {label}" not in self.ts_block
            assert label in self.pk_block.split("- Не надаємо:")[1]
        assert "Послуги мережі" not in self.pk_block

    def test_extended_warranty_only_bridgestone_only_tvoya_shina(self) -> None:
        assert self.ts.extended_warranty_brands == frozenset({"bridgestone"})
        assert self.pk.extended_warranty_brands == frozenset()
        assert "Розширена гарантія: тільки на шини Bridgestone" in self.ts_block
        assert "Розширена гарантія" not in self.pk_block

    def test_same_payment_and_brand_priority(self) -> None:
        assert self.ts.payment_methods == self.pk.payment_methods
        assert set(self.ts.payment_methods) == set(PAYMENT_LABELS)
        assert self.ts.installment_banks == self.pk.installment_banks
        assert self.ts.brand_priority == self.pk.brand_priority
        assert self.ts.brand_priority[0] == "bridgestone"
        assert self.ts.pickup_available and self.pk.pickup_available

    def test_installments_name_the_banks(self) -> None:
        """«Оплата частинами» without the banks lets the LLM name any bank."""
        for policy, block in ((self.ts, self.ts_block), (self.pk, self.pk_block)):
            assert policy.installment_banks
            payment = next(ln for ln in block.splitlines() if ln.startswith("- Оплата:"))
            for bank in policy.installment_banks:
                assert BANK_LABELS[bank] in payment

    def test_blocks_differ_and_name_no_network(self) -> None:
        assert self.ts_block != self.pk_block
        for block in (self.ts_block, self.pk_block):
            assert "Про Колесо" not in block
            assert "Твоя Шина" not in block


# ── Prompt assembly: byte-for-byte invariant + wiring ─────────────────


class TestPromptInvariant:
    @pytest.mark.parametrize("patch", [TVOYA_SHINA_CONFIG_PATCH, PROKOLESO_CONFIG_PATCH, {}])
    def test_disabled_prompt_is_byte_identical(self, patch: dict[str, Any]) -> None:
        before = build_system_prompt_with_context(_BASE, promotions_context="\n## Акції")
        with_none = build_system_prompt_with_context(
            _BASE,
            promotions_context="\n## Акції",
            network_policy_context=render_network_block(None),
        )
        with_off = build_system_prompt_with_context(
            _BASE,
            promotions_context="\n## Акції",
            network_policy_context=render_network_block(NetworkPolicy.from_tenant_config(patch)),
        )
        assert before == with_none == with_off

    @pytest.mark.parametrize("patch", [TVOYA_SHINA_CONFIG_PATCH, PROKOLESO_CONFIG_PATCH])
    def test_disabled_leaves_neighbours_adjacent(self, patch: dict[str, Any]) -> None:
        """Pinned against the pre-change layout, not against the same function.

        Before this module promotions were followed directly by the customer
        profile (``"\n".join`` + the profile's own leading newline). Nothing,
        not even an empty part, may appear between them while sales are off.
        """
        prompt = build_system_prompt_with_context(
            _BASE,
            promotions_context="\n## Акції",
            network_policy_context=render_network_block(NetworkPolicy.from_tenant_config(patch)),
            customer_profile="Профіль клієнта",
        )
        assert "\n## Акції\n\nПрофіль клієнта" in prompt

    def test_enabled_block_lands_after_promotions(self) -> None:
        block = _block(_enabled(TVOYA_SHINA_CONFIG_PATCH))
        prompt = build_system_prompt_with_context(
            _BASE, promotions_context="\n## Акції", network_policy_context=block
        )
        assert prompt.count(block) == 1
        assert prompt.index("## Акції") < prompt.index("## Умови мережі")


def _llm_agent_system(policy: NetworkPolicy | None) -> str:
    """Run one LLMAgent turn (router path) and return the system prompt sent."""
    from src.agent.agent import LLMAgent
    from src.llm.router import LLMRouter

    llm_router = create_autospec(LLMRouter, instance=True)
    llm_router.complete = AsyncMock(
        return_value=LLMResponse(text="Відповідь", usage=Usage(10, 5), provider="test")
    )
    agent = LLMAgent(
        api_key="test-key", system_prompt=_BASE, llm_router=llm_router, network_policy=policy
    )
    asyncio.run(agent.process_message("Привіт", []))
    return llm_router.complete.call_args.kwargs["system"]


class _RecordingRouter(MockLLMRouter):
    def __init__(self, responses: Any) -> None:
        super().__init__(responses)
        self.systems: list[str | None] = []

    async def complete_stream(self, task: Any, messages: Any, **kwargs: Any):  # type: ignore[override]
        self.systems.append(kwargs.get("system"))
        async for event in super().complete_stream(task, messages, **kwargs):
            yield event


def _streaming_loop_system(policy: NetworkPolicy | None) -> str:
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
            system_prompt=_BASE,
            network_policy=policy,
        )
        await loop.run_turn("Привіт", [])

    asyncio.run(run())
    assert router.systems and router.systems[0] is not None
    return router.systems[0]


class TestAgentWiring:
    @pytest.mark.parametrize("build", [_llm_agent_system, _streaming_loop_system])
    def test_enabled_policy_reaches_prompt(self, build: Any) -> None:
        policy = NetworkPolicy.from_tenant_config(_enabled(PROKOLESO_CONFIG_PATCH))
        system = build(policy)
        assert render_network_block(policy) in system

    @pytest.mark.parametrize("build", [_llm_agent_system, _streaming_loop_system])
    @pytest.mark.parametrize("patch", [TVOYA_SHINA_CONFIG_PATCH, PROKOLESO_CONFIG_PATCH])
    def test_disabled_policy_prompt_byte_identical(self, build: Any, patch: Any) -> None:
        assert build(None) == build(NetworkPolicy.from_tenant_config(patch))
        assert "## Умови мережі" not in build(None)


def _calls_to(tree: ast.AST, name: str) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == name
    ]


def _kw(call: ast.Call, name: str) -> ast.expr | None:
    return next((kw.value for kw in call.keywords if kw.arg == name), None)


def _is_from_tenant_config(node: ast.AST | None) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "from_tenant_config"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "NetworkPolicy"
    )


class TestCallSiteWiring:
    """The call handler in main.py is not unit-runnable; pin its call sites."""

    def test_main_builds_policy_from_tenant_config(self) -> None:
        tree = ast.parse((_REPO / "src/main.py").read_text(encoding="utf-8"))
        assigns = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "network_policy" for t in node.targets)
        ]
        assert len(assigns) == 1
        call = assigns[0].value
        assert _is_from_tenant_config(call)
        assert isinstance(call, ast.Call)
        assert isinstance(call.args[0], ast.Name) and call.args[0].id == "tenant_config"

    @pytest.mark.parametrize("ctor", ["LLMAgent", "StreamingAgentLoop"])
    def test_main_passes_policy_to_agents(self, ctor: str) -> None:
        tree = ast.parse((_REPO / "src/main.py").read_text(encoding="utf-8"))
        calls = _calls_to(tree, ctor)
        assert calls, f"{ctor}(...) not found in main.py"
        for call in calls:
            value = _kw(call, "network_policy")
            assert isinstance(value, ast.Name) and value.id == "network_policy", ctor

    def test_sandbox_passes_tenant_policy(self) -> None:
        tree = ast.parse((_REPO / "src/sandbox/agent_runner.py").read_text(encoding="utf-8"))
        calls = _calls_to(tree, "LLMAgent")
        assert calls
        for call in calls:
            assert _is_from_tenant_config(_kw(call, "network_policy"))
