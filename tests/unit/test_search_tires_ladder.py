"""search_tires: tyre-type allowlist, network ranking, relaxation ladder, axles.

Wave 4-J (orders-consult-networks 2026-09-28).

- D1 (FINDINGS I §4): wheels (``type_id='566'``) leaked into tyre search with
  no size, by a wheel brand (``Techline`` → 5/5 wheels) and through
  ``check_availability(query=<brand>)`` → ``items[0]``. Every catalog lookup
  answering "which tyre" carries ``m.type_id IN TIRE_SEARCH_TYPES``.
- Ranking: network ``brand_priority`` first, then a price spread; at most
  ``recommend_count`` (2–3) in-stock variants, one per model.
- Ladder: nothing matches → a filter is dropped and the result says so
  (``relaxed`` + ``caveat_key``) — a code marker, not a prompt rule.
- ``get_vehicle_tire_sizes``: front/rear pairs by ``(kit_id, axle_group)``;
  non-factory sizes flagged ``specialist_only``.

The DB is a small fake engine that records SQL + bind params and answers
through a responder; no bare AsyncMock stands in for an API.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import create_autospec, patch

import pytest

from src.agent.network_policy import NetworkPolicy
from src.core.call_session import CallSession
from src.main import _build_tool_router
from src.store_client import client as client_mod
from src.store_client.catalog_types import TIRE_SEARCH_TYPES, WHEEL
from src.store_client.client import StoreClient

# ── fake DB ──────────────────────────────────────────────────────────────


class _Result:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def mappings(self) -> _Result:
        return self

    def all(self) -> list[dict[str, Any]]:
        return list(self._rows)

    def first(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    def __iter__(self) -> Any:
        return iter(tuple(r.values()) for r in self._rows)


class _Conn:
    def __init__(self, engine: _Engine) -> None:
        self._engine = engine

    async def __aenter__(self) -> _Conn:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def execute(self, query: Any, params: dict[str, Any] | None = None) -> _Result:
        sql = str(query)
        params = dict(params or {})
        self._engine.calls.append((sql, params))
        return _Result(self._engine.responder(sql, params))


class _Engine:
    def __init__(self, responder: Any) -> None:
        self.responder = responder
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def connect(self) -> _Conn:
        return _Conn(self)


def _row(
    brand: str,
    model: str,
    price: int,
    *,
    sku: str | None = None,
    qty: int = 4,
    size: str = "205/55R16",
    season: str = "winter",
) -> dict[str, Any]:
    return {
        "id": sku or f"{brand}-{model}-{size}",
        "brand": brand,
        "model": model,
        "size": size,
        "season": season,
        "price": price,
        "stock_quantity": qty,
    }


def _client(responder: Any) -> tuple[StoreClient, _Engine]:
    engine = _Engine(responder)
    return StoreClient(base_url="http://x", api_key="k", db_engine=engine), engine


def _allowlist_in(sql: str, params: dict[str, Any]) -> bool:
    """The SQL filters ``m.type_id`` to exactly the allowlist."""
    bound = {v for k, v in params.items() if k.startswith("tire_type_")}
    return "m.type_id IN (" in sql and bound == set(TIRE_SEARCH_TYPES)


def _tire_sql_calls(engine: _Engine) -> list[tuple[str, dict[str, Any]]]:
    return [(s, p) for s, p in engine.calls if "FROM tire_products" in s]


# ── D1: tyre-type allowlist ──────────────────────────────────────────────


class TestTireTypeAllowlist:
    def test_allowlist_is_passenger_only(self) -> None:
        assert WHEEL not in TIRE_SEARCH_TYPES

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "params",
        [
            {},  # no size — FINDINGS: 2 of 5 cards were wheels
            {"brand": "Techline"},  # wheel brand — 5 of 5
            {"diameter": 16},
            {"width": 205, "profile": 55, "diameter": 16, "season": "winter"},
        ],
    )
    async def test_every_search_query_carries_the_allowlist(self, params: dict[str, Any]) -> None:
        client, engine = _client(lambda sql, p: [])
        await client.search_tires(network="ProKoleso", **params)
        calls = _tire_sql_calls(engine)
        assert calls, "search did not reach the catalog"
        for sql, bound in calls:
            assert _allowlist_in(sql, bound), sql

    @pytest.mark.asyncio
    async def test_legacy_search_without_policy_carries_the_allowlist(self) -> None:
        client, engine = _client(lambda sql, p: [])
        await client._search_tires_db(network="ProKoleso", brand="Techline")
        ((sql, bound),) = _tire_sql_calls(engine)
        assert _allowlist_in(sql, bound)

    @pytest.mark.asyncio
    async def test_check_availability_by_brand_query_carries_the_allowlist(self) -> None:
        def responder(sql: str, p: dict[str, Any]) -> list[dict[str, Any]]:
            if "FROM tire_products" in sql:
                return [{"id": "00000012345"}] if _allowlist_in(sql, p) else []
            if "FROM tire_stock" in sql:
                return [
                    {
                        "price": 1000,
                        "stock_quantity": 4,
                        "country": "UA",
                        "year_issue": "2025",
                        "trading_network": "ProKoleso",
                    }
                ]
            return []

        client, engine = _client(responder)
        result = await client.check_availability(query="Techline", network="ProKoleso")
        lookups = _tire_sql_calls(engine)
        assert len(lookups) == 1
        assert _allowlist_in(*lookups[0])
        assert result["available"] is True

    @pytest.mark.asyncio
    async def test_check_availability_wheel_brand_is_not_found(self) -> None:
        """Only wheels match the brand → the allowlisted lookup is empty."""

        def responder(sql: str, p: dict[str, Any]) -> list[dict[str, Any]]:
            if "FROM tire_products" in sql and not _allowlist_in(sql, p):
                return [{"id": "00000036319"}]  # a wheel SKU
            return []

        client, _ = _client(responder)
        result = await client.check_availability(query="Techline", network="ProKoleso")
        assert result["available"] is False


# ── filters ──────────────────────────────────────────────────────────────


class TestFilters:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("value", "positive", "negative"),
        [(True, True, False), (False, False, True), (None, False, False)],
    )
    async def test_studded(self, value: Any, positive: bool, negative: bool) -> None:
        client, engine = _client(lambda sql, p: [_row("Nokian", "Hakka", 3000)])
        await client.search_tires(network="ProKoleso", studded=value, width=205)
        sql = _tire_sql_calls(engine)[0][0]
        assert (f"NOT {client_mod._STUDDED_SQL}" in sql) is negative
        assert (client_mod._STUDDED_SQL in sql) is (positive or negative)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("value", "present", "negated"),
        [(True, True, False), (False, True, True), (None, False, False)],
    )
    async def test_runflat(self, value: Any, present: bool, negated: bool) -> None:
        client, engine = _client(lambda sql, p: [_row("Pirelli", "P7", 3000)])
        await client.search_tires(network="ProKoleso", runflat=value, width=205)
        sql = _tire_sql_calls(engine)[0][0]
        assert (client_mod._RUNFLAT_SQL in sql) is present
        assert (f"NOT ({client_mod._RUNFLAT_SQL})" in sql) is negated

    @pytest.mark.asyncio
    async def test_non_bool_studded_does_not_filter(self) -> None:
        client, engine = _client(lambda sql, p: [_row("Nokian", "Hakka", 3000)])
        await client.search_tires(network="ProKoleso", studded="yes", width=205)
        assert client_mod._STUDDED_SQL not in _tire_sql_calls(engine)[0][0]

    def test_schema_has_optional_filters(self) -> None:
        from src.agent.tools import ALL_TOOLS

        schema = next(t for t in ALL_TOOLS if t["name"] == "search_tires")["input_schema"]
        for key in ("studded", "runflat"):
            assert schema["properties"][key]["type"] == "boolean"
            assert key not in schema["required"]


# ── ranking ──────────────────────────────────────────────────────────────

_PRIORITY = ("bridgestone", "firestone", "laufenn")


def _market() -> list[dict[str, Any]]:
    """Price-ordered rows like the SQL returns (cheapest first)."""
    rows = [
        _row("Doublestar", "DH03", 900),
        _row("Laufenn", "I Fit", 1500),
        _row("Laufenn", "I Fit", 1600, sku="laufenn-dup"),
        _row("Kumho", "WP72", 2000),
        _row("Firestone", "Winterhawk", 2500),
        _row("Nokian", "Hakka R5", 4000),
        _row("Michelin", "Alpin 7", 5500),
        _row("Bridgestone", "Blizzak LM005", 6000),
        _row("Bridgestone", "Blizzak 6", 6500),
        _row("Pirelli", "Sottozero", 7000),
    ]
    return sorted(rows, key=lambda r: r["price"])


class TestRanking:
    def test_priority_brands_first_in_network_order(self) -> None:
        picked = StoreClient._rank_tires(_market(), _PRIORITY, 3)
        assert [r["brand"].lower() for r in picked] == list(_PRIORITY)

    def test_priority_is_skipped_when_out_of_stock(self) -> None:
        rows = [{**r, "stock_quantity": 0} if r["brand"] == "Bridgestone" else r for r in _market()]
        picked = StoreClient._rank_tires(rows, _PRIORITY, 3)
        assert "Bridgestone" not in {r["brand"] for r in picked}
        assert all(r["stock_quantity"] > 0 for r in picked)
        assert [r["brand"] for r in picked][:2] == ["Firestone", "Laufenn"]

    def test_empty_priority_spreads_over_price(self) -> None:
        rows = _market()
        picked = StoreClient._rank_tires(rows, (), 3)
        prices = sorted(r["price"] for r in rows)
        assert len(picked) == 3
        assert min(r["price"] for r in picked) == prices[0]
        assert max(r["price"] for r in picked) == prices[-1]

    @pytest.mark.parametrize("count", [0, 1, 2, 3, 4, 10, -5])
    def test_count_is_clamped_to_policy_range(self, count: int) -> None:
        picked = StoreClient._rank_tires(_market(), _PRIORITY, count)
        assert 2 <= len(picked) <= 3

    def test_one_model_one_variant(self) -> None:
        # Distinct SKUs of one model (load/speed index variants) — as in 1C.
        rows = [
            _row("Bridgestone", m, p, sku=f"sku-{p}")
            for m, p in (("A", 1), ("A", 2), ("B", 3), ("A", 4))
        ]
        picked = StoreClient._rank_tires(rows, _PRIORITY, 3)
        models = [(r["brand"], r["model"]) for r in picked]
        assert len(models) == len(set(models))

    def test_one_brand_one_variant_when_brands_differ(self) -> None:
        picked = StoreClient._rank_tires(_market(), (), 3)
        brands = [r["brand"] for r in picked]
        assert len(brands) == len(set(brands))

    def test_named_brand_offers_its_models(self) -> None:
        rows = [_row("Michelin", m, p) for m, p in (("X", 5000), ("Y", 5200), ("Z", 5400))]
        picked = StoreClient._rank_tires(rows, _PRIORITY, 3)
        assert len(picked) == 3

    def test_out_of_stock_rows_are_never_offered(self) -> None:
        rows = [_row("Nokian", "A", 3000, qty=0), _row("Kumho", "B", 2000)]
        picked = StoreClient._rank_tires(rows, _PRIORITY, 3)
        assert all(r["stock_quantity"] > 0 for r in picked)


def _limited_catalog(rows: list[dict[str, Any]]) -> Any:
    """Honours ORDER BY (network brands first when the SQL asks) and LIMIT."""

    def responder(sql: str, p: dict[str, Any]) -> list[dict[str, Any]]:
        if "FROM tire_products" not in sql:
            return []
        prio = {v for k, v in p.items() if k.startswith("prio_")}
        ordered = sorted(
            rows,
            key=lambda r: (
                0
                if "CASE WHEN LOWER(m.manufacturer) IN" in sql and r["brand"].lower() in prio
                else 1,
                r["price"],
            ),
        )
        return ordered[: p["result_limit"]]

    return responder


class TestPriorityBeyondTheCheapRows:
    @pytest.mark.asyncio
    async def test_expensive_network_brand_is_not_cut_by_limit(self) -> None:
        """Prod 205/55 R16 winter: no Bridgestone among the 50 cheapest rows.

        More cheap rows than any window (a search without size spans the
        whole catalog) — only the SQL ordering keeps the network brand in.
        """
        cheap = [_row(f"Budget{i}", "M", 1000 + i) for i in range(1000)]
        rows = [*cheap, _row("Bridgestone", "Blizzak", 9000)]
        client, _ = _client(_limited_catalog(rows))
        result = await client.search_tires(
            network="ProKoleso",
            width=205,
            brand_priority=_PRIORITY,
            recommend_count=3,
        )
        assert result["items"][0]["brand"] == "Bridgestone"

    @pytest.mark.asyncio
    async def test_price_spread_reaches_the_premium_end(self) -> None:
        """The spread covers the whole size, not only the 50 cheapest rows."""
        rows = [_row(f"Brand{i}", "M", 1000 + 10 * i) for i in range(120)]
        client, _ = _client(_limited_catalog(rows))
        result = await client.search_tires(network="ProKoleso", width=205, recommend_count=3)
        prices = [i["price"] for i in result["items"]]
        assert max(prices) == max(r["price"] for r in rows)


# ── ladder ───────────────────────────────────────────────────────────────


def _catalog(studded_rows: list[dict[str, Any]], friction_rows: list[dict[str, Any]]) -> Any:
    """Responder honouring the studded predicate and the brand filter."""

    def responder(sql: str, p: dict[str, Any]) -> list[dict[str, Any]]:
        if "FROM tire_products" not in sql:
            return []
        if f"NOT {client_mod._STUDDED_SQL}" in sql:
            rows = friction_rows
        elif client_mod._STUDDED_SQL in sql:
            rows = studded_rows
        else:
            rows = studded_rows + friction_rows
        if "brand" in p:
            rows = [r for r in rows if r["brand"].lower() == p["brand"].lower()]
        return sorted(rows, key=lambda r: r["price"])

    return responder


class TestLadder:
    @pytest.mark.asyncio
    async def test_no_studded_in_size_offers_friction_with_marker(self) -> None:
        client, _ = _client(_catalog([], [_row("Nokian", "R5", 4000), _row("Kumho", "A", 2000)]))
        result = await client.search_tires(
            network="ProKoleso", width=205, studded=True, recommend_count=3
        )
        assert result["items"]
        assert result["relaxed"] == ["studded"]
        assert result["caveat_key"] == "no_studded_offer_friction"

    @pytest.mark.asyncio
    async def test_studded_found_carries_no_marker(self) -> None:
        client, _ = _client(_catalog([_row("Nokian", "Hakka 10", 5000)], []))
        result = await client.search_tires(
            network="ProKoleso", width=205, studded=True, recommend_count=3
        )
        assert result["items"]
        assert "relaxed" not in result and "caveat_key" not in result

    @pytest.mark.asyncio
    async def test_missing_brand_offers_alternatives_with_marker(self) -> None:
        client, _ = _client(_catalog([], [_row("Kumho", "A", 2000), _row("Laufenn", "B", 1500)]))
        result = await client.search_tires(
            network="ProKoleso", width=205, brand="Michelin", recommend_count=3
        )
        assert result["items"]
        assert result["relaxed"] == ["brand"]
        assert result["caveat_key"] == "brand_unavailable_alternatives"
        assert all(i["brand"] != "Michelin" for i in result["items"])

    @pytest.mark.asyncio
    async def test_brand_without_studs_keeps_studs_first(self) -> None:
        client, _ = _client(
            _catalog([_row("Nokian", "Hakka 10", 5000)], [_row("Michelin", "X-Ice", 6000)])
        )
        result = await client.search_tires(
            network="ProKoleso", width=205, brand="Michelin", studded=True, recommend_count=3
        )
        assert result["relaxed"] == ["brand"]
        assert {i["brand"] for i in result["items"]} == {"Nokian"}

    @pytest.mark.asyncio
    async def test_nothing_at_all_stays_empty_without_marker(self) -> None:
        client, _ = _client(_catalog([], []))
        result = await client.search_tires(
            network="ProKoleso", width=205, brand="Michelin", studded=True, recommend_count=3
        )
        assert result["items"] == []
        assert "relaxed" not in result

    @pytest.mark.asyncio
    async def test_check_availability_never_relaxes_the_brand(self) -> None:
        client, _ = _client(_catalog([], [_row("Kumho", "A", 2000)]))
        result = await client.check_availability(query="Michelin", network="ProKoleso")
        assert result["available"] is False


# ── staggered search ─────────────────────────────────────────────────────


class TestStaggeredSearch:
    @pytest.mark.asyncio
    async def test_one_model_in_both_sizes(self) -> None:
        front = [
            _row("Bridgestone", "S001", 5000, size="245/40R18"),
            _row("Kumho", "PS71", 3000, size="245/40R18"),
        ]
        rear = [
            _row("Bridgestone", "S001", 5600, size="275/35R18"),
            _row("Pirelli", "PZ4", 7000, size="275/35R18"),
        ]

        def responder(sql: str, p: dict[str, Any]) -> list[dict[str, Any]]:
            return rear if p.get("width") == 275 else front

        client, _ = _client(responder)
        result = await client.search_tires(
            network="ProKoleso",
            width=245,
            profile=40,
            diameter=18,
            season="summer",
            rear_width=275,
            rear_profile=35,
            rear_diameter=18,
            recommend_count=3,
        )
        assert [(i["brand"], i["model"]) for i in result["items"]] == [("Bridgestone", "S001")]
        assert result["items"][0]["rear_size"] == "275/35R18"


# ── get_vehicle_tire_sizes: axles ────────────────────────────────────────


def _vehicle_responder(size_rows: list[dict[str, Any]]) -> Any:
    def responder(sql: str, p: dict[str, Any]) -> list[dict[str, Any]]:
        if "FROM vehicle_brands" in sql:
            return [{"id": 1, "name": "BMW"}]
        if "FROM vehicle_models" in sql:
            return [{"id": 2, "name": "5 Series"}]
        if "SELECT DISTINCT k.year" in sql:
            return [{"year": 2020}]
        if "FROM vehicle_tire_sizes" in sql:
            return size_rows
        return []

    return responder


def _vts(w: int, h: int, d: float, type_: int, axle: int, kit: int, grp: int) -> dict[str, Any]:
    return {
        "width": w,
        "height": h,
        "diameter": d,
        "type": type_,
        "axle": axle,
        "kit_id": kit,
        "axle_group": grp,
    }


class TestVehicleAxles:
    @pytest.mark.asyncio
    async def test_staggered_pair_and_specialist_only(self) -> None:
        rows = [
            _vts(245, 40, 18.0, 1, 1, 7, 0),
            _vts(275, 35, 18.0, 1, 2, 7, 0),
            _vts(245, 40, 18.0, 1, 1, 8, 0),  # same pair from another kit
            _vts(275, 35, 18.0, 1, 2, 8, 0),
            _vts(225, 45, 18.0, 2, 1, 7, 1),  # non-factory pair
            _vts(255, 40, 18.0, 2, 2, 7, 1),
        ]
        client, _ = _client(_vehicle_responder(rows))
        result = await client.get_vehicle_tire_sizes(brand="BMW", model="5 Series")
        assert result["staggered_pairs"] == [{"front": "245/40 R18", "rear": "275/35 R18"}]
        assert result["stock_sizes"] == ["245/40 R18 (перед)", "275/35 R18 (зад)"]
        assert result["acceptable_sizes_policy"] == "specialist_only"

    @pytest.mark.asyncio
    async def test_same_size_axles_have_no_pairs_key(self) -> None:
        rows = [_vts(205, 55, 16.0, 1, 0, 7, 0)]
        client, _ = _client(_vehicle_responder(rows))
        result = await client.get_vehicle_tire_sizes(brand="BMW", model="5 Series")
        assert "staggered_pairs" not in result
        assert "acceptable_sizes_policy" not in result

    def test_ambiguous_group_is_not_paired(self) -> None:
        rows = [
            _vts(245, 40, 18.0, 1, 1, 7, 0),
            _vts(235, 40, 18.0, 1, 1, 7, 0),
            _vts(275, 35, 18.0, 1, 2, 7, 0),
        ]
        assert client_mod._staggered_pairs(rows) is None


# ── wiring: handle_call's policy reaches the catalog ─────────────────────


class TestWiring:
    @pytest.mark.asyncio
    async def test_router_passes_network_policy_ranking(self) -> None:
        store = create_autospec(StoreClient, instance=True)
        store.search_tires.return_value = {"total": 0, "items": []}
        policy = NetworkPolicy(brand_priority=_PRIORITY, recommend_count=2)
        router = _build_tool_router(
            CallSession(uuid.uuid4()), store_client=store, network_policy=policy
        )
        await router.execute(
            "search_tires", {"width": 205, "brand_priority": ["evil"], "recommend_count": 9}
        )
        kwargs = store.search_tires.await_args.kwargs
        assert kwargs["brand_priority"] == _PRIORITY
        assert kwargs["recommend_count"] == 2

    @pytest.mark.asyncio
    async def test_priority_order_end_to_end(self) -> None:
        """Real StoreClient behind the real router: the network's brands lead."""
        client, _ = _client(lambda sql, p: _market() if "FROM tire_products" in sql else [])
        policy = NetworkPolicy(brand_priority=_PRIORITY, recommend_count=3)
        router = _build_tool_router(
            CallSession(uuid.uuid4()), store_client=client, network_policy=policy
        )
        with patch("src.main._call_logger", None):
            result = await router.execute("search_tires", {"width": 205, "season": "winter"})
        assert [i["brand"].lower() for i in result["items"]] == list(_PRIORITY)

    def test_handle_call_passes_policy_to_the_router(self) -> None:
        import inspect

        import src.main as main_module

        src = inspect.getsource(main_module.handle_call)
        call = src[src.index("_build_tool_router(") :].split(")")[0]
        assert "network_policy=network_policy" in call
