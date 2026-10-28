"""Nova Poshta delivery quote — `get_delivery_cost` (np-delivery-quote, checklist C).

Owner's words (2026-10-27): sent from Dnipro, the recipient pays unless the
tyres are under a free-delivery promotion, «орієнтовно 810 грн, при
накладеному платежі ще додатково 2% від суми замовлення» (2026-10-28: the fee is the carrier's real «2 % + 20 грн»), a courier surcharge
only when asked about the door, declared value = the order sum, no API call
under a promotion, the old «за тарифами перевізника» phrase on any failure.

The handler is a closure inside ``_build_tool_router``: every handler test
drives the **registered** tool through the real router. Nova Poshta is a
``create_autospec`` of the real client — never the live API.
"""

from __future__ import annotations

import ast
import asyncio
import datetime
import pathlib
import uuid
from typing import Any
from unittest.mock import create_autospec, patch

import pytest

from scripts.configure_tenants import (
    PROKOLESO_CONFIG_PATCH,
    PROKOLESO_ENABLED_TOOLS,
    TVOYA_SHINA_CONFIG_PATCH,
)
from src.agent import delivery_quote as dq
from src.agent.agent import ToolRouter
from src.agent.network_policy import NetworkPolicy
from src.agent.promotions import ActivePromotion, promo_overrides
from src.agent.tools import ALL_TOOLS, sales_tenant_allowlist
from src.core.call_session import CallSession
from src.integrations.novaposhta import NovaPoshtaClient, Quote
from src.main import _build_tool_router
from src.monitoring.metrics import delivery_quote_total
from src.store_client.client import StoreClient

_REPO = pathlib.Path(__file__).resolve().parents[2]
KEY_ENV = "NP_TEST_QUOTE_KEY"
TARIFF = "Доставка — за тарифами перевізника Нова Пошта."
COD = "ще додатково 2 % від суми замовлення + 20 грн"


def _pk_config(*, sales: bool = True, flag: bool = True) -> dict[str, Any]:
    raw = dict(PROKOLESO_CONFIG_PATCH["network_policy"])
    if flag:
        raw["delivery_quote"] = "novaposhta"
        raw["delivery_quote_key_env"] = KEY_ENV
    return {**PROKOLESO_CONFIG_PATCH, "network_policy": raw, "sales_enabled": sales}


def _pk_policy(**kw: Any) -> NetworkPolicy:
    return NetworkPolicy.from_tenant_config(_pk_config(**kw))


@pytest.fixture
def np_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(KEY_ENV, "k" * 32)


TIRES = {
    "total": 2,
    "items": [
        {
            "id": "t-mich",
            "brand": "Michelin",
            "size": "205/55 R16",
            "price": 3200,
            "in_stock": True,
        },
        {
            "id": "t-lauf",
            "brand": "Laufenn",
            "size": "215/75 R17.5",
            "price": 2500,
            "in_stock": True,
        },
    ],
}
DISKS = {
    "total": 1,
    "items": [{"id": "d-1", "brand": "Kosei", "size": "7x17", "diameter": 17.0, "price": 4000}],
}


def _store() -> Any:
    store = create_autospec(StoreClient, instance=True)
    store.search_tires.return_value = TIRES
    store.search_disks.return_value = DISKS
    store.check_availability.return_value = {"available": True, "quantity": 8, "price": 3300}
    return store


def _np(quote: Any = None, city: Any = "city-ref-kyiv") -> Any:
    client = create_autospec(NovaPoshtaClient, instance=True)
    if isinstance(city, BaseException):
        client.resolve_city.side_effect = city
    else:
        client.resolve_city.return_value = city
    client.quote.return_value = quote
    return client


def _promos(*brand_sets: tuple[str, ...]) -> Any:
    return promo_overrides(
        [
            ActivePromotion(
                title=f"promo {i}",
                bot_text=f"Безкоштовна доставка на шини {i}.",
                valid_to=datetime.date(2099, 1, 1),
                overrides={"free_delivery": True},
                mention_brands=brands,
            )
            for i, brands in enumerate(brand_sets)
        ]
    )


def _session() -> CallSession:
    session = CallSession(uuid.uuid4())
    session.tire_query = {"season": "summer"}
    return session


async def _drive(
    calls: list[tuple[str, dict[str, Any]]],
    *,
    policy: NetworkPolicy | None = None,
    np_client: Any = None,
    promos: Any = None,
    session: CallSession | None = None,
) -> tuple[list[Any], CallSession]:
    session = session or _session()
    router = _build_tool_router(
        session,
        store_client=_store(),
        network_policy=policy if policy is not None else _pk_policy(),
        promo_overrides=promos,
    )
    results = []
    with patch("src.main._nova_poshta_client", return_value=np_client) as factory:
        for name, args in calls:
            results.append(await router.execute(name, args))
    if np_client is None:
        factory.assert_not_called()
    return results, session


def _count(outcome: str) -> float:
    return delivery_quote_total.labels(outcome=outcome)._value.get()


SEARCH = ("search_tires", {"width": 205, "profile": 55, "diameter": 16, "season": "summer"})


def _ask(items: list[dict[str, Any]], city: str = "Київ", courier: bool = False) -> Any:
    return ("get_delivery_cost", {"city": city, "items": items, "courier": courier})


# --------------------------------------------------------------- pure helpers


class TestPhrase:
    def test_branch_only(self) -> None:
        phrase = dq.quote_phrase(Quote(cost=810.0, door_cost=None, cod_fee=260.0), courier=False)
        assert phrase == (
            "Доставка Новою поштою — орієнтовно 810 грн. "
            "При накладеному платежі — ще додатково 2 % від суми замовлення + 20 грн."
        )

    def test_courier_names_the_surcharge(self) -> None:
        phrase = dq.quote_phrase(Quote(cost=810.0, door_cost=870.0, cod_fee=260.0), courier=True)
        assert "орієнтовно 810 грн" in phrase
        assert "кур'єром до дверей — ще 60 грн" in phrase
        assert COD in phrase

    def test_no_surcharge_without_the_question(self) -> None:
        phrase = dq.quote_phrase(Quote(cost=810.0, door_cost=870.0, cod_fee=260.0), courier=False)
        assert "кур'єр" not in phrase and "60" not in phrase

    def test_whole_hryvnias(self) -> None:
        phrase = dq.quote_phrase(Quote(cost=809.6, door_cost=None, cod_fee=0.0), courier=False)
        assert "орієнтовно 810 грн" in phrase

    def test_quoted_sums(self) -> None:
        q = Quote(cost=810.0, door_cost=870.0, cod_fee=260.0)
        assert dq.quoted_sums(q, courier=True) == [810, 60]
        assert dq.quoted_sums(q, courier=False) == [810]


class TestItems:
    @pytest.mark.parametrize(
        ("size", "rim"),
        [
            ("205/55 R16", (16.0, False)),
            ("225/45 ZR17", (17.0, False)),
            ("195/70 R15C", (15.0, False)),
            ("215/75 R17.5", (17.5, True)),
            ("235/65 R17 (перед)", (17.0, False)),
            ("без розміру", None),
        ],
    )
    def test_tire_rim(self, size: str, rim: Any) -> None:
        assert dq.tire_rim(size) == rim

    def test_declared_value_is_price_times_qty(self) -> None:
        offered: dict[str, dict[str, Any]] = {}
        dq.remember_offered(offered, TIRES, "tire")
        lines = dq.resolve_items(
            [{"product_id": "t-mich", "quantity": 4}, {"product_id": "t-lauf", "quantity": 2}],
            offered,
        )
        assert lines is not None
        assert dq.declared_value(lines) == 3200 * 4 + 2500 * 2

    def test_not_offered_is_refused(self) -> None:
        offered: dict[str, dict[str, Any]] = {}
        dq.remember_offered(offered, TIRES, "tire")
        assert dq.resolve_items([{"product_id": "unknown", "quantity": 4}], offered) is None
        assert dq.resolve_items([{"product_id": "t-mich", "quantity": 0}], offered) is None
        assert dq.resolve_items([], offered) is None

    def test_item_without_price_or_rim_not_remembered(self) -> None:
        offered: dict[str, dict[str, Any]] = {}
        dq.remember_offered(
            offered,
            {"items": [{"id": "a", "size": "205/55 R16"}, {"id": "b", "price": 10, "size": "?"}]},
            "tire",
        )
        assert offered == {}


class TestAvailability:
    def test_flag_and_key(self, np_key: None) -> None:
        assert dq.quote_available(_pk_policy())

    def test_no_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(KEY_ENV, "  ")
        assert not dq.quote_available(_pk_policy())
        monkeypatch.delenv(KEY_ENV)
        assert not dq.quote_available(_pk_policy())

    def test_flag_off_or_sales_off(self, np_key: None) -> None:
        assert not dq.quote_available(_pk_policy(flag=False))
        assert not dq.quote_available(_pk_policy(sales=False))
        assert not dq.quote_available(None)

    def test_tvoya_shina_never(self, np_key: None) -> None:
        raw = {
            **TVOYA_SHINA_CONFIG_PATCH["network_policy"],
            "delivery_quote": "novaposhta",
            "delivery_quote_key_env": KEY_ENV,
        }
        cfg = {**TVOYA_SHINA_CONFIG_PATCH, "network_policy": raw, "sales_enabled": True}
        assert not dq.quote_available(NetworkPolicy.from_tenant_config(cfg))

    def test_allowlist(self) -> None:
        assert dq.DELIVERY_COST_TOOL not in sales_tenant_allowlist({"search_tires"})
        assert dq.DELIVERY_COST_TOOL in sales_tenant_allowlist(
            {"search_tires"}, delivery_quote=True
        )


# --------------------------------------------------------------- the handler


@pytest.mark.usefixtures("np_key")
class TestHandler:
    def test_quoted_from_the_search_not_the_args(self) -> None:
        np_client = _np(Quote(cost=810.0, door_cost=None, cod_fee=260.0))
        before = _count("quoted")
        (_, res), session = asyncio.run(
            _drive(
                [SEARCH, _ask([{"product_id": "t-mich", "quantity": 4, "price": 1}])],
                np_client=np_client,
            )
        )
        assert res["status"] == "quoted"
        assert "орієнтовно 810 грн" in res["phrase"] and COD in res["phrase"]
        assert res["cost"] == 810
        np_client.resolve_city.assert_awaited_once_with("Київ")
        np_client.quote.assert_awaited_once_with(
            "city-ref-kyiv", [("tire", 16.0, 4, False)], declared_value=12800, door=False
        )
        assert session.delivery_quotes == [810]
        assert _count("quoted") == before + 1

    def test_courier(self) -> None:
        np_client = _np(Quote(cost=810.0, door_cost=870.0, cod_fee=260.0))
        (_, res), session = asyncio.run(
            _drive(
                [SEARCH, _ask([{"product_id": "t-mich", "quantity": 4}], courier=True)],
                np_client=np_client,
            )
        )
        assert res["status"] == "quoted"
        assert "кур'єром до дверей — ще 60 грн" in res["phrase"]
        assert np_client.quote.await_args.kwargs["door"] is True
        assert session.delivery_quotes == [810, 60]

    def test_wheel_and_truck_lines(self) -> None:
        np_client = _np(Quote(cost=900.0, door_cost=None, cod_fee=0.0))
        (_, _, res), _ = asyncio.run(
            _drive(
                [
                    SEARCH,
                    ("search_disks", {"diameter": 17}),
                    _ask(
                        [
                            {"product_id": "d-1", "quantity": 4},
                            {"product_id": "t-lauf", "quantity": 2},
                        ]
                    ),
                ],
                np_client=np_client,
            )
        )
        assert res["status"] == "quoted"
        np_client.quote.assert_awaited_once_with(
            "city-ref-kyiv",
            [("wheel", 17.0, 4, False), ("tire", 17.5, 2, True)],
            declared_value=4000 * 4 + 2500 * 2,
            door=False,
        )

    def test_availability_refreshes_the_price(self) -> None:
        np_client = _np(Quote(cost=810.0, door_cost=None, cod_fee=0.0))
        asyncio.run(
            _drive(
                [
                    SEARCH,
                    ("check_availability", {"product_id": "t-mich"}),
                    _ask([{"product_id": "t-mich", "quantity": 2}]),
                ],
                np_client=np_client,
            )
        )
        assert np_client.quote.await_args.kwargs["declared_value"] == 3300 * 2

    def test_not_offered_is_need_more(self) -> None:
        np_client = _np(Quote(cost=810.0, door_cost=None, cod_fee=0.0))
        before = _count("need_more")
        (_, res), session = asyncio.run(
            _drive([SEARCH, _ask([{"product_id": "t-other", "quantity": 4}])], np_client=np_client)
        )
        assert res["status"] == "need_more"
        assert res["missing"] == "items"
        assert res["phrase"].startswith(TARIFF)
        np_client.resolve_city.assert_not_awaited()
        np_client.quote.assert_not_awaited()
        assert session.delivery_quotes == []
        assert _count("need_more") == before + 1

    def test_nothing_searched_is_need_more(self) -> None:
        np_client = _np(Quote(cost=810.0, door_cost=None, cod_fee=0.0))
        (res,), _ = asyncio.run(
            _drive([_ask([{"product_id": "t-mich", "quantity": 4}])], np_client=np_client)
        )
        assert res["status"] == "need_more"
        np_client.quote.assert_not_awaited()

    def test_no_city_is_need_more(self) -> None:
        np_client = _np(Quote(cost=810.0, door_cost=None, cod_fee=0.0))
        (_, res), _ = asyncio.run(
            _drive(
                [SEARCH, _ask([{"product_id": "t-mich", "quantity": 4}], city=" ")],
                np_client=np_client,
            )
        )
        assert res["status"] == "need_more" and res["missing"] == "city"
        np_client.quote.assert_not_awaited()

    def test_promo_on_every_item_no_api(self) -> None:
        np_client = _np(Quote(cost=810.0, door_cost=None, cod_fee=0.0))
        before = _count("promo_free")
        (_, res), session = asyncio.run(
            _drive(
                [SEARCH, _ask([{"product_id": "t-mich", "quantity": 4}])],
                np_client=np_client,
                promos=_promos(("Michelin",)),
            )
        )
        assert res["status"] == "promo_free"
        assert res["phrase"] == "Безкоштовна доставка на шини 0."
        np_client.resolve_city.assert_not_awaited()
        np_client.quote.assert_not_awaited()
        assert session.delivery_quotes == []
        assert _count("promo_free") == before + 1

    def test_promo_for_every_brand(self) -> None:
        np_client = _np(Quote(cost=810.0, door_cost=None, cod_fee=0.0))
        (_, res), _ = asyncio.run(
            _drive(
                [SEARCH, _ask([{"product_id": "t-lauf", "quantity": 4}])],
                np_client=np_client,
                promos=_promos(()),
            )
        )
        assert res["status"] == "promo_free"
        np_client.quote.assert_not_awaited()

    def test_promo_on_some_items_goes_to_manager(self) -> None:
        np_client = _np(Quote(cost=810.0, door_cost=None, cod_fee=0.0))
        (_, res), _ = asyncio.run(
            _drive(
                [
                    SEARCH,
                    _ask(
                        [
                            {"product_id": "t-mich", "quantity": 2},
                            {"product_id": "t-lauf", "quantity": 2},
                        ]
                    ),
                ],
                np_client=np_client,
                promos=_promos(("michelin",)),
            )
        )
        assert res["status"] == "promo_mixed"
        assert res["phrase"] == "Умови доставки для цих шин уточнить менеджер."
        np_client.quote.assert_not_awaited()

    def test_promo_of_another_brand_still_quotes(self) -> None:
        np_client = _np(Quote(cost=810.0, door_cost=None, cod_fee=0.0))
        (_, res), _ = asyncio.run(
            _drive(
                [SEARCH, _ask([{"product_id": "t-mich", "quantity": 4}])],
                np_client=np_client,
                promos=_promos(("bridgestone",)),
            )
        )
        assert res["status"] == "quoted"

    @pytest.mark.parametrize("failure", ["quote_none", "city_none", "raises", "no_client"])
    def test_failure_is_the_tariff_phrase(self, failure: str) -> None:
        if failure == "quote_none":
            np_client: Any = _np(None)
        elif failure == "city_none":
            np_client = _np(Quote(cost=810.0, door_cost=None, cod_fee=0.0), city=None)
        elif failure == "raises":
            np_client = _np(None, city=TimeoutError("np down"))
        else:
            np_client = None
        before = _count("api_failed")
        session = _session()
        router = _build_tool_router(session, store_client=_store(), network_policy=_pk_policy())

        async def run() -> Any:
            with patch("src.main._nova_poshta_client", return_value=np_client):
                await router.execute(*SEARCH)
                return await router.execute(*_ask([{"product_id": "t-mich", "quantity": 4}]))

        res = asyncio.run(run())
        assert res == {"status": "api_failed", "phrase": TARIFF}
        assert session.delivery_quotes == []
        assert _count("api_failed") == before + 1


class TestRegistration:
    def _names(self, policy: NetworkPolicy) -> set[str]:
        router = _build_tool_router(_session(), store_client=_store(), network_policy=policy)
        return set(router._handlers)

    def test_registered_with_flag_and_key(self, np_key: None) -> None:
        assert dq.DELIVERY_COST_TOOL in self._names(_pk_policy())

    def test_not_registered_without_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(KEY_ENV, raising=False)
        assert dq.DELIVERY_COST_TOOL not in self._names(_pk_policy())

    def test_not_registered_flag_off(self, np_key: None) -> None:
        assert dq.DELIVERY_COST_TOOL not in self._names(_pk_policy(flag=False))
        assert dq.DELIVERY_COST_TOOL not in self._names(_pk_policy(sales=False))

    def test_unregistered_call_never_reaches_np(self, np_key: None) -> None:
        np_client = _np(Quote(cost=810.0, door_cost=None, cod_fee=0.0))
        (_, res), _ = asyncio.run(
            _drive(
                [SEARCH, _ask([{"product_id": "t-mich", "quantity": 4}])],
                policy=_pk_policy(flag=False),
            )
        )
        assert "error" in res
        np_client.quote.assert_not_awaited()


# --------------------------------------------------------------- the tool set


def _tool_names(tenant_cfg: dict[str, Any], slug: str, enabled: list[str]) -> set[str]:
    from tests.unit.test_sales_scope_switch import _run_handle_call, _tenant

    kwargs, _ = _run_handle_call(_tenant(slug, tenant_cfg, enabled))
    return {t["name"] for t in kwargs["tools"]}


class TestToolSet:
    def test_schema_in_canon(self) -> None:
        assert dq.DELIVERY_COST_TOOL in {t["name"] for t in ALL_TOOLS}

    @pytest.mark.parametrize("enabled", [list(PROKOLESO_ENABLED_TOOLS), []])
    def test_prokoleso_flag_and_key_offers_it(self, np_key: None, enabled: list[str]) -> None:
        names = _tool_names(_pk_config(), "prokoleso", enabled)
        assert dq.DELIVERY_COST_TOOL in names
        assert "submit_order_request" in names

    @pytest.mark.parametrize("enabled", [list(PROKOLESO_ENABLED_TOOLS), []])
    def test_no_key_cuts_it(self, monkeypatch: pytest.MonkeyPatch, enabled: list[str]) -> None:
        monkeypatch.delenv(KEY_ENV, raising=False)
        names = _tool_names(_pk_config(), "prokoleso", enabled)
        assert dq.DELIVERY_COST_TOOL not in names
        assert "submit_order_request" in names

    @pytest.mark.parametrize("enabled", [list(PROKOLESO_ENABLED_TOOLS), []])
    def test_flag_off_cuts_it(self, np_key: None, enabled: list[str]) -> None:
        names = _tool_names(_pk_config(flag=False), "prokoleso", enabled)
        assert dq.DELIVERY_COST_TOOL not in names
        assert "submit_order_request" in names

    def test_tenant_list_naming_it_does_not_beat_the_flag(self, np_key: None) -> None:
        enabled = [*PROKOLESO_ENABLED_TOOLS, dq.DELIVERY_COST_TOOL]
        names = _tool_names(_pk_config(flag=False), "prokoleso", enabled)
        assert dq.DELIVERY_COST_TOOL not in names

    def test_sales_off_cuts_it(self, np_key: None) -> None:
        names = _tool_names(_pk_config(sales=False), "prokoleso", list(PROKOLESO_ENABLED_TOOLS))
        assert dq.DELIVERY_COST_TOOL not in names

    def test_tvoya_shina_never(self, np_key: None) -> None:
        raw = {
            **TVOYA_SHINA_CONFIG_PATCH["network_policy"],
            "delivery_quote": "novaposhta",
            "delivery_quote_key_env": KEY_ENV,
        }
        cfg = {**TVOYA_SHINA_CONFIG_PATCH, "network_policy": raw, "sales_enabled": True}
        names = _tool_names(cfg, "tvoya-shina", [])
        assert dq.DELIVERY_COST_TOOL not in names
        assert "submit_order_request" in names


# --------------------------------------------------------------- session + wiring


class TestSessionAndWiring:
    def test_round_trip(self) -> None:
        session = CallSession(uuid.uuid4())
        session.delivery_quotes.extend([810, 60])
        restored = CallSession.from_dict(session.to_dict())
        assert restored.delivery_quotes == [810, 60]

    def test_garbage_dropped(self) -> None:
        data = CallSession(uuid.uuid4()).to_dict()
        data["delivery_quotes"] = [810, "60", True, None]
        assert CallSession.from_dict(data).delivery_quotes == [810]

    def test_streaming_loop_keeps_the_same_list(self) -> None:
        from src.agent.streaming_loop import StreamingAgentLoop
        from tests.unit.mocks.mock_audio_socket import MockAudioSocketConnection
        from tests.unit.mocks.mock_llm_router import MockLLMRouter
        from tests.unit.mocks.mock_tts import MockTTSEngine

        quotes: list[int] = []
        loop = StreamingAgentLoop(
            MockLLMRouter([]),  # type: ignore[arg-type]
            create_autospec(ToolRouter, instance=True),
            MockTTSEngine(),  # type: ignore[arg-type]
            MockAudioSocketConnection(),  # type: ignore[arg-type]
            asyncio.Event(),
            delivery_quotes=quotes,
        )
        assert loop._delivery_quotes is quotes

    def test_main_passes_the_session_list(self) -> None:
        tree = ast.parse((_REPO / "src/main.py").read_text(encoding="utf-8"))
        calls = [
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "StreamingAgentLoop"
        ]
        assert calls
        for call in calls:
            value = next((k.value for k in call.keywords if k.arg == "delivery_quotes"), None)
            assert isinstance(value, ast.Attribute) and value.attr == "delivery_quotes"
            assert isinstance(value.value, ast.Name) and value.value.id == "session"

    def test_main_passes_promotions_to_the_router(self) -> None:
        tree = ast.parse((_REPO / "src/main.py").read_text(encoding="utf-8"))
        calls = [
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "_build_tool_router"
        ]
        assert calls
        for call in calls:
            value = next((k.value for k in call.keywords if k.arg == "promo_overrides"), None)
            assert isinstance(value, ast.Name) and value.id == "claim_overrides"


class TestNovaPoshtaClientFactory:
    """`_nova_poshta_client` itself: no key in the env → no client, never a blank-key one."""

    def test_no_key_variable_named_is_no_client(self) -> None:
        from src.main import _nova_poshta_client

        assert _nova_poshta_client(None) is None

    def test_blank_key_in_the_env_is_no_client(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import src.main as main_module

        monkeypatch.setattr(main_module, "_np_clients", {})
        monkeypatch.setenv("NOVA_POSHTA_API_KEY_FACTORY_TEST", "   ")
        assert main_module._nova_poshta_client("NOVA_POSHTA_API_KEY_FACTORY_TEST") is None
        assert main_module._np_clients == {}

    def test_one_client_per_key_variable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import src.main as main_module

        monkeypatch.setattr(main_module, "_np_clients", {})
        monkeypatch.setenv("NOVA_POSHTA_API_KEY_FACTORY_TEST2", "k" * 32)
        first = main_module._nova_poshta_client("NOVA_POSHTA_API_KEY_FACTORY_TEST2")
        assert isinstance(first, NovaPoshtaClient)
        assert main_module._nova_poshta_client("NOVA_POSHTA_API_KEY_FACTORY_TEST2") is first


# ------------------------------------------- branch number of an order (R12)


class TestWarehouseNumber:
    """``dq.warehouse_number`` — the parser itself, imported from ``src``."""

    @pytest.mark.parametrize(
        ("address", "number"),
        [
            ("відділення 12", "12"),
            ("Відділення №5", "5"),
            ("відділення № 25, Київ", "25"),
            ("№12", "12"),
            ("12-е відділення", "12"),
            ("3-тє відділення", "3"),
            ("у відділенні 7", "7"),
            ("відділення НП номер 31", "31"),
            ("поштомат 5012", "5012"),
            ("почтомат 3456", "3456"),
            ("отделение 12", "12"),
            ("отделение номер 12", "12"),
            ("12 отделение", "12"),
        ],
    )
    def test_branch_or_postomat(self, address: str, number: str) -> None:
        assert dq.warehouse_number(address) == number

    @pytest.mark.parametrize(
        "address",
        [
            "вул. Шевченка 12",
            "вул. Шевченка, буд. №12",
            "просп. Перемоги 10, кв. 5",
            "улица Ленина, дом №3",
            "Нова пошта 12",
            "відділення перевізника",
            "",
            None,
            12,
        ],
    )
    def test_not_a_branch_is_none(self, address: Any) -> None:
        assert dq.warehouse_number(address) is None

    def test_question_shape(self) -> None:
        q = dq.warehouse_question("12", "Київ")
        assert (q["status"], q["field"], q["reason"]) == (
            "missing_field",
            "address",
            dq.STATUS_WAREHOUSE_NOT_FOUND,
        )
        assert "відділення №12 у місті Київ" in q["message"]
        assert "НЕ створено" in q["message"]


class TestDeadline:
    """One deadline for the whole quote: a slow Nova Poshta never holds the caller."""

    def test_past_the_deadline_is_the_tariff_line(
        self, monkeypatch: pytest.MonkeyPatch, np_key: None
    ) -> None:
        from types import SimpleNamespace

        # Settings.nova_poshta is built at import: the deadline is patched where read.
        monkeypatch.setattr(
            "src.main.get_settings",
            lambda: SimpleNamespace(nova_poshta=SimpleNamespace(deadline_sec=0.05)),
        )

        async def _slow(*_: Any, **__: Any) -> str:
            await asyncio.sleep(1)
            return "city-ref-kyiv"

        np_client = _np(Quote(cost=810.0, door_cost=None, cod_fee=260.0))
        np_client.resolve_city.side_effect = _slow
        before = _count("api_failed")
        (_, res), session = asyncio.run(
            _drive([SEARCH, _ask([{"product_id": "t-mich", "quantity": 4}])], np_client=np_client)
        )
        assert res == {"status": "api_failed", "phrase": TARIFF}
        np_client.quote.assert_not_awaited()
        assert session.delivery_quotes == []
        assert _count("api_failed") == before + 1


class TestToolContract:
    """The model calls the tool on any delivery-cost question (R18): the code, not
    the model, says what is missing — the model asked «що саме замовити?» instead
    of the tariff line when nothing was required to be known (goldset 2026-10-28)."""

    def test_nothing_is_required_up_front(self) -> None:
        schema = next(t for t in ALL_TOOLS if t["name"] == dq.DELIVERY_COST_TOOL)
        assert schema["input_schema"]["required"] == []
        assert "ЗАВЖДИ" in schema["description"]

    def test_a_bare_call_is_the_tariff_line(self, np_key: None) -> None:
        (res,), _ = asyncio.run(_drive([("get_delivery_cost", {})]))
        assert res["status"] == "need_more"
        assert res["phrase"].startswith(TARIFF)
