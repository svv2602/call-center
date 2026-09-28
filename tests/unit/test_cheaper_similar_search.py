"""«А дешевше?» / «схожі за ціною» — search_tires(price_mode) from the last offer.

Wave 4-H (sales-structural 2026-09-28), after tshina_new ``e4abe9f8f`` (a turn
without a size while the car is known does not go back to choosing the size),
``cc06d65ac`` and ``f7f5418b8`` (the price corridor and ``budget_source``).

- The ``search_tires`` wrapper (sales only) remembers the offer the caller
  heard: ``session.last_tire_offer`` — per-tyre prices, ids and the
  size/season/needs of the search. It survives the Redis round trip.
- ``price_mode="cheaper"`` → strictly below the cheapest shown tyre;
  ``"similar"`` → median of the shown ± 10 %, empty → ± 20 %. The size and
  season the model left out come from the last offer (not asked again).
- ``budget_source``: ``caller`` only when the caller named a budget
  (``tire_query.budget``), ``price_corridor`` for a price-mode search without
  one — only ``caller`` allows «в межах вашого бюджету».

The DB is a small fake engine that applies the SQL's bind params to a fixed
market (price bounds, excluded ids), so the invariants are checked over what
a real query would return; no bare AsyncMock stands in for an API.
"""

from __future__ import annotations

import json
import uuid
from typing import Any
from unittest.mock import create_autospec, patch

import pytest

from src.agent.network_policy import NetworkPolicy
from src.agent.prompts import _MOD_TIRE_SEARCH, _MOD_TIRE_SEARCH_SALES
from src.agent.tool_result_compressor import compress_tool_result
from src.agent.tools import ALL_TOOLS
from src.core.call_session import CallSession
from src.main import _build_tool_router
from src.store_client.client import (
    PRICE_CORRIDOR_STEPS,
    StoreClient,
    price_filter,
    shown_tire_offer,
    within_price_filter,
)

# ── fake DB: a market filtered by the query's bind params ────────────────


class _Result:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def mappings(self) -> _Result:
        return self

    def all(self) -> list[dict[str, Any]]:
        return list(self._rows)


class _Conn:
    def __init__(self, engine: _Engine) -> None:
        self._engine = engine

    async def __aenter__(self) -> _Conn:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def execute(self, query: Any, params: dict[str, Any] | None = None) -> _Result:
        sql = str(query)
        self._engine.calls.append((sql, dict(params or {})))
        return _Result(self._engine.answer(sql, dict(params or {})))


class _Engine:
    """Rows of ``market`` that a query with these bind params would return."""

    def __init__(self, market: list[dict[str, Any]]) -> None:
        self.market = market
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def connect(self) -> _Conn:
        return _Conn(self)

    def answer(self, sql: str, p: dict[str, Any]) -> list[dict[str, Any]]:
        if "FROM tire_products" not in sql:
            return []
        rows = [r for r in self.market if r["width"] == p.get("width", r["width"])]
        if "price_below" in p:
            rows = [r for r in rows if 0 < r["price"] < p["price_below"]]
        if "price_from" in p:
            rows = [r for r in rows if p["price_from"] <= r["price"] <= p["price_to"]]
        excluded = {v for k, v in p.items() if k.startswith("exclude_")}
        rows = [r for r in rows if r["id"] not in excluded]
        return sorted(rows, key=lambda r: r["price"])

    def tire_params(self) -> list[dict[str, Any]]:
        return [p for s, p in self.calls if "FROM tire_products" in s]


def _row(brand: str, price: int, *, width: int = 205) -> dict[str, Any]:
    return {
        "id": f"{brand}-{width}",
        "brand": brand,
        "model": f"{brand} M",
        "size": f"{width}/55R16",
        "season": "summer",
        "price": price,
        "stock_quantity": 4,
        "runflat": False,
        "width": width,
    }


#: The network's brands are the premium end, so the first offer leaves cheaper
#: and similar tyres in the market.
_PRIORITY = ("Michelin", "Continental", "Bridgestone")
_MARKET = [
    _row("Michelin", 5200),
    _row("Continental", 5000),
    _row("Bridgestone", 4800),
    _row("Goodyear", 4600),
    _row("Nokian", 5500),
    _row("Hankook", 3100),
    _row("Kumho", 2900),
    _row("Rosava", 2000),
    _row("Michelin", 9000, width=225),
]
_SIZE = {"width": 205, "profile": 55, "diameter": 16, "season": "summer"}


def _session(budget: dict[str, Any] | None = None) -> CallSession:
    session = CallSession(uuid.uuid4())
    session.tire_query = {"season": "summer"}
    if budget is not None:
        session.tire_query["budget"] = budget
    return session


def _setup(
    *, sales: bool = True, market: list[dict[str, Any]] | None = None, budget: Any = None
) -> tuple[Any, CallSession, _Engine]:
    engine = _Engine(list(market or _MARKET))
    store = StoreClient(base_url="http://x", api_key="k", db_engine=engine)
    session = _session(budget)
    policy = NetworkPolicy(sales_enabled=sales, recommend_count=3, brand_priority=_PRIORITY)
    return _build_tool_router(session, store_client=store, network_policy=policy), session, engine


async def _run(router: Any, args: dict[str, Any]) -> dict[str, Any]:
    with patch("src.main._call_logger", None):
        return await router.execute("search_tires", args)


def _prices(result: dict[str, Any]) -> list[float]:
    return [float(i["price"]) for i in result["items"]]


# ── pure: bounds ─────────────────────────────────────────────────────────


class TestPriceFilter:
    def test_cheaper_is_below_the_cheapest_shown(self) -> None:
        prices = [5000.0, 4200.0, 6100.0]
        bounds = price_filter("cheaper", prices)
        assert bounds["price_below"] == min(prices)
        assert not within_price_filter({"price": min(prices)}, bounds)
        assert within_price_filter({"price": min(prices) - 1}, bounds)

    @pytest.mark.parametrize("step", PRICE_CORRIDOR_STEPS)
    def test_similar_is_the_median_corridor(self, step: float) -> None:
        prices = [3000.0, 4000.0, 9000.0]
        bounds = price_filter("similar", prices, step)
        median = sorted(prices)[1]
        assert bounds["price_from"] == pytest.approx(median * (1 - step))
        assert bounds["price_to"] == pytest.approx(median * (1 + step))
        assert within_price_filter({"price": median}, bounds)
        assert not within_price_filter({"price": median * (1 + step) + 1}, bounds)

    def test_steps_widen(self) -> None:
        assert list(PRICE_CORRIDOR_STEPS) == sorted(PRICE_CORRIDOR_STEPS)
        assert PRICE_CORRIDOR_STEPS[0] < PRICE_CORRIDOR_STEPS[-1]

    @pytest.mark.parametrize("mode", ["", "dearer", None])
    def test_unknown_mode_or_no_prices_is_no_bound(self, mode: Any) -> None:
        assert price_filter(mode, [1000.0]) == {}
        assert price_filter("cheaper", []) == {}

    def test_item_without_a_price_is_denied(self) -> None:
        bounds = price_filter("cheaper", [3000.0])
        assert not within_price_filter({"price": 0}, bounds)
        assert not within_price_filter({}, bounds)

    def test_shown_offer_is_what_the_caller_heard(self) -> None:
        items = [{"id": str(n), "price": 1000 + n} for n in range(5)]
        prices, ids = shown_tire_offer({"items": items})
        assert ids == ["0", "1", "2"]
        assert prices == [1000.0, 1001.0, 1002.0]


# ── SQL: the bounds reach the query ──────────────────────────────────────


class TestQuery:
    @pytest.mark.asyncio
    async def test_price_bounds_and_excluded_ids_are_bound(self) -> None:
        engine = _Engine(list(_MARKET))
        store = StoreClient(base_url="http://x", api_key="k", db_engine=engine)
        await store.search_tires(
            network="N", **_SIZE, price_from=1.0, price_to=2.0, exclude_ids=["a", "b"]
        )
        sql, params = next((s, p) for s, p in engine.calls if "FROM tire_products" in s)
        assert "s.price BETWEEN :price_from AND :price_to" in sql
        assert "p.sku NOT IN" in sql
        assert {params["exclude_0"], params["exclude_1"]} == {"a", "b"}

    @pytest.mark.asyncio
    async def test_cheaper_bound_is_strict_in_sql(self) -> None:
        engine = _Engine(list(_MARKET))
        store = StoreClient(base_url="http://x", api_key="k", db_engine=engine)
        await store.search_tires(network="N", **_SIZE, price_below=3000.0)
        sql, params = next((s, p) for s, p in engine.calls if "FROM tire_products" in s)
        assert "s.price < :price_below" in sql
        assert "<= :price_below" not in sql
        assert params["price_below"] == 3000.0

    @pytest.mark.asyncio
    async def test_staggered_rear_is_not_price_bound(self) -> None:
        engine = _Engine(list(_MARKET))
        store = StoreClient(base_url="http://x", api_key="k", db_engine=engine)
        await store.search_tires(
            network="N",
            **_SIZE,
            rear_width=225,
            rear_profile=50,
            rear_diameter=16,
            price_below=3000.0,
            recommend_count=3,
        )
        tire = engine.tire_params()
        assert len(tire) >= 2
        assert "price_below" in tire[0]
        assert "price_below" not in tire[1]


# ── wiring: the wrapper remembers the offer and searches from it ─────────


class TestWiring:
    @pytest.mark.asyncio
    async def test_search_writes_the_shown_offer(self) -> None:
        router, session, _ = _setup()
        first = await _run(router, dict(_SIZE))
        offer = session.last_tire_offer
        assert offer is not None
        assert offer["prices"] == [float(p) for p in _prices(first)[:3]]
        assert offer["ids"] == [i["id"] for i in first["items"][:3]]
        assert offer["params"]["width"] == _SIZE["width"]
        assert offer["params"]["season"] == _SIZE["season"]
        assert "brand_priority" not in offer["params"]

    @pytest.mark.asyncio
    async def test_cheaper_is_strictly_below_the_cheapest_shown(self) -> None:
        router, _, _ = _setup()
        first = await _run(router, dict(_SIZE))
        cheapest = min(_prices(first))
        result = await _run(router, {"price_mode": "cheaper"})
        assert result["items"], "the market has cheaper tyres of the size"
        assert all(p < cheapest for p in _prices(result))
        assert result["price_mode"] == "cheaper"

    @pytest.mark.asyncio
    async def test_price_mode_keeps_the_size_of_the_offer(self) -> None:
        router, _, engine = _setup()
        await _run(router, dict(_SIZE))
        await _run(router, {"price_mode": "cheaper", "width": 0})
        last = engine.tire_params()[-1]
        assert last["width"] == _SIZE["width"]
        assert last["diameter"] == _SIZE["diameter"]
        assert last["season"] == _SIZE["season"]

    @pytest.mark.asyncio
    async def test_cheaper_again_goes_lower_still(self) -> None:
        router, _, _ = _setup()
        await _run(router, dict(_SIZE))
        second = await _run(router, {"price_mode": "cheaper"})
        third = await _run(router, {"price_mode": "cheaper"})
        assert all(p < min(_prices(second)) for p in _prices(third))

    @pytest.mark.asyncio
    async def test_nothing_cheaper_says_so_and_keeps_the_offer(self) -> None:
        market = [_row("Michelin", 5000), _row("Continental", 5100), _row("Bridgestone", 5200)]
        router, session, _ = _setup(market=market)
        await _run(router, dict(_SIZE))
        before = dict(session.last_tire_offer or {})
        result = await _run(router, {"price_mode": "cheaper"})
        assert result["items"] == []
        assert result["message"]
        assert session.last_tire_offer == before

    @pytest.mark.asyncio
    async def test_similar_is_within_ten_percent_of_the_median(self) -> None:
        router, _, _ = _setup()
        first = await _run(router, dict(_SIZE))
        shown = sorted(_prices(first))
        median = shown[len(shown) // 2]
        result = await _run(router, {"price_mode": "similar"})
        assert result["items"]
        step = PRICE_CORRIDOR_STEPS[0]
        assert all(median * (1 - step) <= p <= median * (1 + step) for p in _prices(result))
        assert not {i["id"] for i in result["items"]} & {i["id"] for i in first["items"]}
        assert "price_corridor_widened" not in result

    @pytest.mark.asyncio
    async def test_empty_corridor_widens_once(self) -> None:
        market = [
            _row("Michelin", 5000),
            _row("Continental", 5000),
            _row("Bridgestone", 5000),
            _row("Kumho", 4150),
            _row("Rosava", 1000),
        ]
        router, _, _ = _setup(market=market)
        await _run(router, dict(_SIZE))
        result = await _run(router, {"price_mode": "similar"})
        wide = PRICE_CORRIDOR_STEPS[-1]
        assert result["price_corridor_widened"] is True
        assert [i["brand"] for i in result["items"]] == ["Kumho"]
        assert all(5000 * (1 - wide) <= p <= 5000 * (1 + wide) for p in _prices(result))

    @pytest.mark.asyncio
    async def test_no_shown_offer_is_a_plain_search(self) -> None:
        router, _, engine = _setup()
        result = await _run(router, {**_SIZE, "price_mode": "cheaper"})
        assert result["items"]
        assert "price_mode" not in result
        assert "budget_source" not in result
        assert all("price_below" not in p for p in engine.tire_params())

    @pytest.mark.asyncio
    async def test_model_cannot_pass_its_own_bounds(self) -> None:
        router, _, engine = _setup()
        await _run(router, {**_SIZE, "price_below": 10**6, "exclude_ids": ["x"]})
        assert all("price_below" not in p and "exclude_0" not in p for p in engine.tire_params())

    @pytest.mark.asyncio
    async def test_wrapper_denies_items_outside_the_bounds(self) -> None:
        """Whatever engine answered (HTTP fallback knows no price bounds)."""
        store = create_autospec(StoreClient, instance=True)
        store.search_tires.return_value = {
            "total": 2,
            "items": [{"id": "a", "price": 4000}, {"id": "b", "price": 2000}],
        }
        session = _session()
        session.last_tire_offer = {"prices": [3000.0], "ids": ["z"], "params": dict(_SIZE)}
        policy = NetworkPolicy(sales_enabled=True, recommend_count=3)
        router = _build_tool_router(session, store_client=store, network_policy=policy)
        result = await _run(router, {"price_mode": "cheaper"})
        assert [i["id"] for i in result["items"]] == ["b"]
        kwargs = store.search_tires.await_args.kwargs
        assert kwargs["price_below"] == 3000.0
        assert kwargs["width"] == _SIZE["width"]

    @pytest.mark.asyncio
    async def test_similar_never_offers_a_shown_tyre_again(self) -> None:
        """An engine that ignores ``exclude_ids`` (HTTP fallback) still cannot."""
        store = create_autospec(StoreClient, instance=True)
        store.search_tires.return_value = {
            "total": 2,
            "items": [{"id": "shown", "price": 3000}, {"id": "new", "price": 3050}],
        }
        session = _session()
        session.last_tire_offer = {"prices": [3000.0], "ids": ["shown"], "params": dict(_SIZE)}
        policy = NetworkPolicy(sales_enabled=True, recommend_count=3)
        router = _build_tool_router(session, store_client=store, network_policy=policy)
        result = await _run(router, {"price_mode": "similar"})
        assert [i["id"] for i in result["items"]] == ["new"]
        assert store.search_tires.await_args.kwargs["exclude_ids"] == ["shown"]


# ── budget_source ────────────────────────────────────────────────────────


class TestBudgetSource:
    @pytest.mark.asyncio
    async def test_corridor_without_the_callers_budget(self) -> None:
        router, _, _ = _setup()
        await _run(router, dict(_SIZE))
        result = await _run(router, {"price_mode": "cheaper"})
        assert result["budget_source"] == "price_corridor"
        out = json.loads(compress_tool_result("search_tires", result, sales_enabled=True))
        assert out["budget_source"] == "price_corridor"
        assert "бюджет" in out["budget_note"]

    @pytest.mark.asyncio
    async def test_caller_budget_is_caller(self) -> None:
        router, _, _ = _setup(budget={"amount": 3000, "scope": "per_tire", "is_cap": True})
        first = await _run(router, dict(_SIZE))
        assert first["budget_source"] == "caller"
        result = await _run(router, {"price_mode": "cheaper"})
        assert result["budget_source"] == "caller"
        out = json.loads(compress_tool_result("search_tires", result, sales_enabled=True))
        assert out["budget_source"] == "caller"
        assert "budget_note" not in out

    @pytest.mark.asyncio
    async def test_plain_search_without_budget_claims_no_budget(self) -> None:
        router, _, _ = _setup()
        result = await _run(router, dict(_SIZE))
        assert "budget_source" not in result


# ── sales off: nothing of this ───────────────────────────────────────────


class TestSalesOff:
    @pytest.mark.asyncio
    async def test_off_ignores_price_mode_and_remembers_nothing(self) -> None:
        router, session, engine = _setup(sales=False)
        await _run(router, dict(_SIZE))
        result = await _run(router, {**_SIZE, "price_mode": "cheaper"})
        assert session.last_tire_offer is None
        assert "price_mode" not in result and "budget_source" not in result
        assert all("price_below" not in p for p in engine.tire_params())

    def test_compressor_off_is_unchanged(self) -> None:
        plain = {"total": 1, "items": [{"brand": "B", "price": 1}]}
        marked = {**plain, "price_mode": "cheaper", "budget_source": "price_corridor"}
        assert compress_tool_result("search_tires", marked) == compress_tool_result(
            "search_tires", plain
        )


# ── session: the offer survives Redis ────────────────────────────────────


class TestSession:
    def test_round_trip(self) -> None:
        session = _session()
        session.last_tire_offer = {
            "prices": [3000.0, 4000.0],
            "ids": ["a", "b"],
            "params": dict(_SIZE),
        }
        restored = CallSession.deserialize(session.serialize())
        assert restored.last_tire_offer == session.last_tire_offer

    def test_empty_lists_restore_as_none(self) -> None:
        session = _session()
        session.last_tire_offer = {"prices": [], "ids": [], "params": dict(_SIZE)}
        restored = CallSession.deserialize(session.serialize())
        assert restored.last_tire_offer is not None
        assert restored.last_tire_offer["prices"] is None
        assert restored.last_tire_offer["ids"] is None

    def test_default_is_none(self) -> None:
        restored = CallSession.deserialize(_session().serialize())
        assert restored.last_tire_offer is None

    @pytest.mark.asyncio
    async def test_price_mode_after_a_redis_round_trip(self) -> None:
        """A turn on another Call Processor still knows what was shown."""
        router, session, _ = _setup()
        first = await _run(router, dict(_SIZE))
        restored = CallSession.deserialize(session.serialize())
        store = StoreClient(base_url="http://x", api_key="k", db_engine=_Engine(list(_MARKET)))
        policy = NetworkPolicy(sales_enabled=True, recommend_count=3, brand_priority=_PRIORITY)
        router2 = _build_tool_router(restored, store_client=store, network_policy=policy)
        result = await _run(router2, {"price_mode": "cheaper"})
        assert result["items"]
        assert all(p < min(_prices(first)) for p in _prices(result))


# ── schema and prompt ────────────────────────────────────────────────────


class TestSchemaAndPrompt:
    def test_schema_has_price_mode(self) -> None:
        tool = next(t for t in ALL_TOOLS if t["name"] == "search_tires")
        prop = tool["input_schema"]["properties"]["price_mode"]
        assert set(prop["enum"]) == {"cheaper", "similar"}
        assert "price_mode" not in tool["input_schema"]["required"]
        assert not any(ch.isdigit() for ch in prop["description"])

    def test_sales_module_routes_the_phrases(self) -> None:
        line = next(ln for ln in _MOD_TIRE_SEARCH_SALES.splitlines() if "price_mode" in ln)
        assert 'search_tires(price_mode="cheaper")' in line
        assert 'price_mode="similar"' in line
        assert 'budget_source="caller"' in line
        assert not any(ch.isdigit() for ch in line)

    def test_fitting_module_untouched(self) -> None:
        assert "price_mode" not in _MOD_TIRE_SEARCH
