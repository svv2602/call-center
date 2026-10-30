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
    budget_filter,
    heard_tires,
    merge_price_bounds,
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
        if p.get("brand"):
            rows = [r for r in rows if r["brand"].lower() == str(p["brand"]).lower()]
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


# ── the caller's budget bounds the search (call 06311ba8, 2026-10-29) ─────


class TestBudgetFilter:
    def test_cap_is_the_upper_bound(self) -> None:
        assert budget_filter({"amount": 4000, "is_cap": True}) == {
            "price_from": 1.0,
            "price_to": 4000.0,
        }

    def test_range(self) -> None:
        assert budget_filter({"amount": 4500, "floor": 3500, "is_cap": True}) == {
            "price_from": 3500.0,
            "price_to": 4500.0,
        }

    def test_per_set_is_split_over_the_count(self) -> None:
        budget = {"amount": 16000, "scope": "per_set", "is_cap": True}
        assert budget_filter(budget)["price_to"] == 4000.0
        assert budget_filter(budget, 2)["price_to"] == 8000.0

    def test_target_allows_some_over(self) -> None:
        assert budget_filter({"amount": 4000, "is_cap": False})["price_to"] == 4400.0

    @pytest.mark.parametrize("budget", [None, {}, {"amount": 0}, {"amount": "x"}, "4000"])
    def test_nothing_usable(self, budget: Any) -> None:
        assert budget_filter(budget) == {}

    def test_merge_keeps_both(self) -> None:
        merged = merge_price_bounds(
            {"price_from": 1.0, "price_to": 4000.0}, {"price_below": 5346.0}
        )
        assert merged == {"price_from": 1.0, "price_to": 4000.0, "price_below": 5346.0}

    def test_merge_intersects_corridors(self) -> None:
        merged = merge_price_bounds(
            {"price_from": 1.0, "price_to": 4000.0}, {"price_from": 3500.0, "price_to": 4500.0}
        )
        assert merged == {"price_from": 3500.0, "price_to": 4000.0}


class TestBudgetSearch:
    @pytest.mark.asyncio
    async def test_cap_bounds_the_search(self) -> None:
        router, _, _ = _setup(budget={"amount": 3200, "scope": None, "is_cap": True})
        result = await _run(router, dict(_SIZE))
        assert result["items"]
        assert all(p <= 3200 for p in _prices(result))
        assert result["budget_source"] == "caller"

    @pytest.mark.asyncio
    async def test_nothing_within_the_budget_offers_the_nearest_over_it(self) -> None:
        router, _, _ = _setup(budget={"amount": 1500, "scope": None, "is_cap": True})
        result = await _run(router, dict(_SIZE))
        # The nearest window over 1500 that has tyres: ×1.5 → Rosava at 2000.
        assert _prices(result) == [2000.0]
        assert result["over_budget"]["budget_to"] == 1500
        assert "budget_source" not in result
        out = json.loads(compress_tool_result("search_tires", result, sales_enabled=True))
        assert "1500" in out["over_budget"]["say"]

    @pytest.mark.asyncio
    async def test_far_over_the_budget_still_offers_something(self) -> None:
        market = [_row("Michelin", 5000), _row("Continental", 5100)]
        router, _, _ = _setup(market=market, budget={"amount": 1000, "scope": None, "is_cap": True})
        result = await _run(router, dict(_SIZE))
        assert _prices(result) == [5000.0, 5100.0]
        assert result["over_budget"]["budget_to"] == 1000

    @pytest.mark.asyncio
    async def test_cheaper_stays_within_the_budget(self) -> None:
        router, session, _ = _setup()
        await _run(router, dict(_SIZE))
        session.tire_query["budget"] = {"amount": 3000, "scope": None, "is_cap": True}
        result = await _run(router, {"price_mode": "cheaper"})
        assert result["items"]
        assert all(p <= 3000 for p in _prices(result))


class TestHeardTyres:
    @pytest.mark.asyncio
    async def test_cheaper_never_offers_a_tyre_heard_earlier(self) -> None:
        """Call 06311ba8: «дешевше» after a brand search repeated the first offer."""
        router, _, _ = _setup()
        first = await _run(router, dict(_SIZE))
        heard = {i["id"] for i in first["items"][:3]}
        nokian = await _run(router, {**_SIZE, "brand": "Nokian"})
        assert [i["brand"] for i in nokian["items"]] == ["Nokian"]
        result = await _run(router, {"price_mode": "cheaper"})
        assert result["items"]
        assert not {i["id"] for i in result["items"]} & heard

    @pytest.mark.asyncio
    async def test_a_new_size_starts_the_list_anew(self) -> None:
        router, session, _ = _setup()
        await _run(router, dict(_SIZE))
        await _run(router, {**_SIZE, "width": 225})
        heard = (session.last_tire_offer or {}).get("heard") or []
        assert [h["id"] for h in heard] == ["Michelin-225"]

    @pytest.mark.asyncio
    async def test_nothing_cheaper_names_the_cheapest_heard(self) -> None:
        market = [_row("Michelin", 5000), _row("Continental", 5100), _row("Bridgestone", 5200)]
        router, _, _ = _setup(market=market)
        await _run(router, dict(_SIZE))
        result = await _run(router, {"price_mode": "cheaper"})
        assert result["cheapest_shown"]["id"] == "Michelin-205"
        assert "5000" in result["message"]
        out = json.loads(compress_tool_result("search_tires", result, sales_enabled=True))
        assert out["cheapest_shown"]["price"] == 5000.0
        assert "Michelin" in out["message"]

    def test_heard_keeps_the_order_and_no_duplicates(self) -> None:
        params = {"width": 205, "profile": 55, "diameter": 16, "season": "summer"}
        offer = {"params": params, "heard": [{"id": "a", "price": 1.0}]}
        result = {"items": [{"id": "a", "price": 1}, {"id": "b", "price": 2, "brand": "B"}]}
        assert [h["id"] for h in heard_tires(offer, result, params)] == ["a", "b"]


# ── «аналоги?» → similar, by code (wave 6-M, R17) ─────────────────────────


async def _turn(router: Any, session: CallSession, line: str, args: dict[str, Any]) -> Any:
    """The streaming path: the caller's line is in the session before the tool runs."""
    session.add_user_turn(line)
    return await _run(router, args)


class TestAnalogsByCode:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "line",
        ["а аналоги є?", "щось схоже по ціні", "аналоги матадора?", "что-то похожее"],
    )
    async def test_analogs_after_an_offer_search_the_corridor(self, line: str) -> None:
        router, session, _ = _setup()
        first = await _turn(router, session, "зимові 205/55 R16", dict(_SIZE))
        shown = sorted(_prices(first))
        median = shown[len(shown) // 2]
        result = await _turn(router, session, line, {})
        assert result["price_mode"] == "similar"
        assert result["items"]
        step = PRICE_CORRIDOR_STEPS[0]
        assert all(median * (1 - step) <= p <= median * (1 + step) for p in _prices(result))
        assert not {i["id"] for i in result["items"]} & {i["id"] for i in first["items"]}

    @pytest.mark.asyncio
    async def test_the_models_price_mode_wins(self) -> None:
        router, session, _ = _setup()
        first = await _turn(router, session, "205/55 R16", dict(_SIZE))
        result = await _turn(router, session, "а аналоги є?", {"price_mode": "cheaper"})
        assert result["price_mode"] == "cheaper"
        assert all(p < min(_prices(first)) for p in _prices(result))

    @pytest.mark.asyncio
    async def test_cheaper_line_is_not_analogs(self) -> None:
        router, session, engine = _setup()
        await _turn(router, session, "205/55 R16", dict(_SIZE))
        result = await _turn(router, session, "а дешевше?", dict(_SIZE))
        assert "price_mode" not in result
        assert all("price_from" not in p for p in engine.tire_params())

    @pytest.mark.asyncio
    async def test_no_shown_offer_is_a_plain_search(self, caplog: pytest.LogCaptureFixture) -> None:
        router, session, engine = _setup()
        with caplog.at_level("INFO", logger="src.main"):
            result = await _turn(router, session, "а аналоги матадора є?", dict(_SIZE))
        assert result["items"]
        assert "price_mode" not in result
        assert all("price_from" not in p for p in engine.tire_params())
        # The search would fall back by itself; the log must not claim a switch.
        assert not any("by code" in r.getMessage() for r in caplog.records)

    @pytest.mark.asyncio
    async def test_last_turn_is_the_bots_nothing_is_read(self) -> None:
        # The blocking path adds the caller's line after the LLM: the last
        # turn is then the bot's, and a line of an earlier turn is not this one.
        router, session, engine = _setup()
        await _turn(router, session, "а аналоги є?", dict(_SIZE))
        session.add_assistant_turn("Є Michelin за 5200 гривень.")
        result = await _run(router, dict(_SIZE))
        assert "price_mode" not in result
        assert all("price_from" not in p for p in engine.tire_params())

    @pytest.mark.asyncio
    async def test_the_bots_own_analog_words_are_not_the_callers(self) -> None:
        # Only the caller's line asks for analogs: the bot offering «аналоги»
        # in the last turn is not a request (blocking path, speaker check).
        router, session, engine = _setup()
        await _turn(router, session, "205/55 R16", dict(_SIZE))
        session.add_assistant_turn("Можу підібрати аналоги Michelin, схожі за ціною.")
        result = await _run(router, dict(_SIZE))
        assert "price_mode" not in result
        assert all("price_from" not in p for p in engine.tire_params())

    @pytest.mark.asyncio
    async def test_analogs_of_x_search_without_x(self) -> None:
        router, session, engine = _setup()
        await _turn(router, session, "205/55 R16", dict(_SIZE))
        await _turn(router, session, "аналоги матадора?", {"brand": "Matador"})
        assert "brand" not in engine.tire_params()[-1]

    @pytest.mark.asyncio
    async def test_a_brand_not_excluded_is_kept(self) -> None:
        router, session, engine = _setup()
        await _turn(router, session, "205/55 R16", dict(_SIZE))
        await _turn(router, session, "аналоги матадора?", {"brand": "Michelin"})
        # the turn's first query (an empty brand result relaxes the brand after it)
        assert engine.tire_params()[1]["brand"] == "Michelin"

    @pytest.mark.asyncio
    async def test_logged(self, caplog: pytest.LogCaptureFixture) -> None:
        router, session, _ = _setup()
        await _turn(router, session, "205/55 R16", dict(_SIZE))
        with caplog.at_level("INFO", logger="src.main"):
            await _turn(router, session, "а подібні є?", {})
        assert any("price_mode=similar by code" in r.getMessage() for r in caplog.records)

    @pytest.mark.asyncio
    async def test_sales_off_reads_no_line(self) -> None:
        router, session, engine = _setup(sales=False)
        await _turn(router, session, "205/55 R16", dict(_SIZE))
        session.last_tire_offer = {"prices": [5000.0], "ids": ["x"], "params": {}}
        result = await _turn(
            router, session, "а аналоги матадора є?", {**_SIZE, "brand": "Matador"}
        )
        assert "price_mode" not in result
        assert all("price_from" not in p for p in engine.tire_params())
        assert engine.tire_params()[1]["brand"] == "Matador"


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
            "heard": [{"id": "a", "brand": "A", "model": "M", "price": 3000.0}],
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
