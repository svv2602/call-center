"""search_tires: XL / RunFlat / C filters, their ladder, the runflat_required warning.

Wave 3-E (sales-structural 2026-09-28), after tshina_new ``b5ab212cb`` (XL
facet with a fallback), ``ac3ce7687`` (the «RunFlat у розмірі … немає» caveat)
and ``2232038ce`` (RunflatMissing: every BMW, Mercedes GLE/GLS/G).

- ``xl`` / ``commercial``: SQL filters over ``description`` / the
  ``commercial`` column; only a real bool filters.
- XL / RunFlat missing in the size → regular tyres of the size with
  ``relaxed`` + ``caveat_key`` — never an empty answer with no reason.
- ``warning: runflat_required``: the car of the call (the caller's words) is on
  `RUNFLAT_REQUIRED_VEHICLES` and the offer has a non-RunFlat tyre.
- The phrases are spoken by the loop (`tire_caveat_phrase`); the compressor
  keeps the markers for the LLM.

The DB is a small fake engine that records SQL + bind params and answers
through a responder; no bare AsyncMock stands in for an API.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any
from unittest.mock import create_autospec, patch

import pytest

from src.agent.network_policy import NetworkPolicy
from src.agent.tool_result_compressor import compress_tool_result, tire_caveat_phrase
from src.agent.tools import ALL_TOOLS
from src.core.call_session import CallSession
from src.main import _build_tool_router
from src.store_client import client as client_mod
from src.store_client.client import (
    RUNFLAT_REQUIRED_VEHICLES,
    StoreClient,
    runflat_required,
    runflat_warning,
)

# ── fake DB ──────────────────────────────────────────────────────────────


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
        return _Result(self._engine.responder(sql, dict(params or {})))


class _Engine:
    def __init__(self, responder: Any) -> None:
        self.responder = responder
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def connect(self) -> _Conn:
        return _Conn(self)


def _row(brand: str, model: str, price: int, *, runflat: bool = False) -> dict[str, Any]:
    return {
        "id": f"{brand}-{model}",
        "brand": brand,
        "model": model,
        "size": "245/45R18",
        "season": "summer",
        "price": price,
        "stock_quantity": 4,
        "runflat": runflat,
    }


_MARKET = [_row("Michelin", "PS5", 5000), _row("Hankook", "K135", 3000)]


def _client(responder: Any) -> tuple[StoreClient, _Engine]:
    engine = _Engine(responder)
    return StoreClient(base_url="http://x", api_key="k", db_engine=engine), engine


def _tire_sql(engine: _Engine) -> list[str]:
    return [s for s, _ in engine.calls if "FROM tire_products" in s]


def _only_without(fragment: str) -> Any:
    """Rows only for a query that does not filter by ``fragment``."""

    def responder(sql: str, p: dict[str, Any]) -> list[dict[str, Any]]:
        if "FROM tire_products" not in sql:
            return []
        where = sql.split("WHERE", 1)[1]
        return [] if fragment in where else list(_MARKET)

    return responder


_SIZE = {"width": 245, "profile": 45, "diameter": 18, "season": "summer"}


# ── filters ──────────────────────────────────────────────────────────────


class TestFilters:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("value", "present", "negated"),
        [(True, True, False), (False, True, True), (None, False, False), ("yes", False, False)],
    )
    async def test_xl(self, value: Any, present: bool, negated: bool) -> None:
        client, engine = _client(lambda sql, p: list(_MARKET))
        await client.search_tires(network="ProKoleso", xl=value, **_SIZE)
        sql = _tire_sql(engine)[0]
        assert (client_mod._XL_SQL in sql) is present
        assert (f"NOT ({client_mod._XL_SQL})" in sql) is negated

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("value", "fragment"),
        [
            (True, "p.commercial IS TRUE"),
            (False, "p.commercial IS NOT TRUE"),
            (None, None),
            ("yes", None),
        ],
    )
    async def test_commercial(self, value: Any, fragment: str | None) -> None:
        client, engine = _client(lambda sql, p: list(_MARKET))
        await client.search_tires(network="ProKoleso", commercial=value, **_SIZE)
        sql = _tire_sql(engine)[0]
        if fragment is None:
            assert "p.commercial" not in sql
        else:
            assert fragment in sql

    def test_schema_has_optional_xl_and_commercial(self) -> None:
        tool = next(t for t in ALL_TOOLS if t["name"] == "search_tires")
        props = tool["input_schema"]["properties"]
        for key in ("xl", "commercial"):
            assert props[key]["type"] == "boolean"
            assert key not in tool["input_schema"]["required"]
            # No literal numbers: they become tool-call arguments.
            assert not re.search(r"\d", props[key]["description"])

    @pytest.mark.asyncio
    async def test_item_says_runflat_only_when_the_row_is(self) -> None:
        rows = [_row("Pirelli", "P7 RFT", 6000, runflat=True), _row("Hankook", "K135", 3000)]
        client, _ = _client(lambda sql, p: rows if "FROM tire_products" in sql else [])
        result = await client.search_tires(network="ProKoleso", **_SIZE)
        by_model = {i["model"]: i for i in result["items"]}
        assert by_model["P7 RFT"]["runflat"] is True
        assert "runflat" not in by_model["K135"]


# ── ladder ───────────────────────────────────────────────────────────────


class TestLadder:
    @pytest.mark.asyncio
    async def test_xl_found_carries_no_marker(self) -> None:
        client, _ = _client(lambda sql, p: list(_MARKET) if "FROM tire_products" in sql else [])
        result = await client.search_tires(network="ProKoleso", xl=True, **_SIZE)
        assert result["items"]
        assert "caveat_key" not in result and "relaxed" not in result

    @pytest.mark.asyncio
    async def test_no_xl_in_size_offers_regular_with_marker(self) -> None:
        client, engine = _client(_only_without(client_mod._XL_SQL))
        result = await client.search_tires(network="ProKoleso", xl=True, **_SIZE)
        assert result["items"], "no XL in the size must not end in an empty answer"
        assert result["relaxed"] == ["xl"]
        assert result["caveat_key"] == client_mod.CAVEAT_XL_NONE
        # The size and season stay: only the XL filter was dropped.
        last = [p for s, p in engine.calls if "FROM tire_products" in s][-1]
        assert (last["width"], last["profile"], last["diameter"]) == (245, 45, 18)

    @pytest.mark.asyncio
    async def test_no_runflat_in_size_offers_regular_with_marker(self) -> None:
        client, _ = _client(_only_without(client_mod._RUNFLAT_SQL))
        result = await client.search_tires(network="ProKoleso", runflat=True, **_SIZE)
        assert result["items"]
        assert result["relaxed"] == ["runflat"]
        assert result["caveat_key"] == client_mod.CAVEAT_RUNFLAT_NONE

    @pytest.mark.asyncio
    async def test_both_missing_drops_both_and_says_runflat(self) -> None:
        def responder(sql: str, p: dict[str, Any]) -> list[dict[str, Any]]:
            if "FROM tire_products" not in sql:
                return []
            where = sql.split("WHERE", 1)[1]
            if client_mod._XL_SQL in where or client_mod._RUNFLAT_SQL in where:
                return []
            return list(_MARKET)

        client, _ = _client(responder)
        result = await client.search_tires(network="ProKoleso", runflat=True, xl=True, **_SIZE)
        assert result["items"]
        assert set(result["relaxed"]) == {"xl", "runflat"}
        assert result["caveat_key"] == client_mod.CAVEAT_RUNFLAT_NONE

    @pytest.mark.asyncio
    async def test_commercial_is_never_relaxed(self) -> None:
        """A passenger tyre on a van is not an alternative: empty stays empty."""
        client, _ = _client(_only_without("p.commercial IS TRUE"))
        result = await client.search_tires(network="ProKoleso", commercial=True, **_SIZE)
        assert result["items"] == []
        assert "caveat_key" not in result

    @pytest.mark.asyncio
    async def test_refused_runflat_is_not_relaxed(self) -> None:
        client, _ = _client(_only_without(f"NOT ({client_mod._RUNFLAT_SQL})"))
        result = await client.search_tires(network="ProKoleso", runflat=False, **_SIZE)
        assert result["items"] == []
        assert "caveat_key" not in result


# ── runflat_required ─────────────────────────────────────────────────────


class TestRunflatRequired:
    @pytest.mark.parametrize(
        "vehicle",
        [
            {"brand": "BMW"},
            {"brand": "BMW", "model": "X5"},
            {"brand": "bmw", "model": "3 Series"},
            {"brand": "Mercedes", "model": "GLE-Class (W166)"},
            {"brand": "Mercedes", "model": "GLE AMG"},
            {"brand": "Mercedes", "model": "GLS-Class"},
            {"brand": "Mercedes", "model": "G-Class (W463)"},
        ],
    )
    def test_listed(self, vehicle: dict[str, Any]) -> None:
        assert runflat_required(vehicle) is True

    @pytest.mark.parametrize(
        "vehicle",
        [
            None,
            {},
            {"brand": "Mercedes"},  # model unknown — the list names families
            {"brand": "Mercedes", "model": "GL-Class"},
            {"brand": "Mercedes", "model": "GLA-Class"},
            {"brand": "Mercedes", "model": "GLC-Class Coupe"},
            {"brand": "Mercedes", "model": "E-Class"},
            {"brand": "Audi", "model": "Q7"},
            {"brand": "Skoda", "model": "Octavia"},
        ],
    )
    def test_not_listed(self, vehicle: Any) -> None:
        assert runflat_required(vehicle) is False

    def test_the_list_is_one_constant(self) -> None:
        assert set(RUNFLAT_REQUIRED_VEHICLES) == {"bmw", "mercedes"}

    def test_plain_offer_for_bmw_warns(self) -> None:
        result = {"items": [{"brand": "Hankook", "model": "K135"}]}
        assert runflat_warning(result, {"brand": "BMW"}, {}) == "runflat_required"

    def test_mixed_offer_for_bmw_warns(self) -> None:
        result = {"items": [{"model": "P7", "runflat": True}, {"model": "K135"}]}
        assert runflat_warning(result, {"brand": "BMW"}, {}) == "runflat_required"

    @pytest.mark.parametrize(
        ("result", "params"),
        [
            ({"items": [{"model": "P7", "runflat": True}]}, {}),  # all RunFlat
            ({"items": [{"model": "K135"}]}, {"runflat": False}),  # caller refused
            ({"items": [{"model": "K135"}], "relaxed": ["runflat"]}, {"runflat": True}),
            ({"items": []}, {}),
            ({"error": True}, {}),
        ],
    )
    def test_silent(self, result: dict[str, Any], params: dict[str, Any]) -> None:
        assert runflat_warning(result, {"brand": "BMW"}, params) is None

    def test_silent_for_other_cars(self) -> None:
        result = {"items": [{"model": "K135"}]}
        assert runflat_warning(result, {"brand": "Skoda", "model": "Octavia"}, {}) is None
        assert runflat_warning(result, None, {}) is None


# ── phrases + compressor ─────────────────────────────────────────────────


def _digits_outside_size(phrase: str, size: str) -> str:
    return re.sub(r"\D", "", phrase.replace(size, ""))


_ITEMS = [{"brand": "Hankook", "model": "K135", "size": "245/45R18", "price": 3000}]


class TestPhrases:
    @pytest.mark.parametrize(
        ("key", "words"),
        [("xl_none_offer_regular", ("XL", "звичайні")), ("runflat_none", ("RunFlat", "звичайні"))],
    )
    def test_relaxed_phrase_names_the_size(self, key: str, words: tuple[str, ...]) -> None:
        result = {"items": _ITEMS, "relaxed": [key.split("_")[0]], "caveat_key": key}
        phrase = tire_caveat_phrase(result, {})
        assert phrase is not None
        assert "245/45R18" in phrase
        for w in words:
            assert w in phrase
        assert _digits_outside_size(phrase, "245/45R18") == ""

    def test_runflat_required_phrase(self) -> None:
        phrase = tire_caveat_phrase({"items": _ITEMS, "warning": "runflat_required"}, {})
        assert phrase is not None and "RunFlat" in phrase
        assert not re.search(r"\d", phrase)

    def test_warning_follows_the_caveat(self) -> None:
        result = {
            "items": _ITEMS,
            "relaxed": ["xl"],
            "caveat_key": "xl_none_offer_regular",
            "warning": "runflat_required",
        }
        phrase = tire_caveat_phrase(result, {})
        assert phrase is not None
        assert phrase.index("XL") < phrase.index("RunFlat")

    def test_unknown_marker_says_nothing(self) -> None:
        assert tire_caveat_phrase({"items": _ITEMS, "warning": "whatever"}, {}) is None
        assert tire_caveat_phrase({"items": _ITEMS, "caveat_key": "whatever"}, {}) is None
        assert tire_caveat_phrase({"items": [], "warning": "runflat_required"}, {}) is None

    def test_compressor_keeps_markers_for_the_llm(self) -> None:
        result = {
            "total": 1,
            "items": [{**_ITEMS[0], "runflat": True}, _ITEMS[0]],
            "relaxed": ["xl"],
            "caveat_key": "xl_none_offer_regular",
            "warning": "runflat_required",
        }
        out = json.loads(compress_tool_result("search_tires", result, sales_enabled=True, args={}))
        assert out["warning"] == "runflat_required"
        assert out["caveat_key"] == "xl_none_offer_regular"
        assert out["items"][0]["runflat"] is True
        assert "caveat_already_said" in out

    def test_compressor_off_is_unchanged(self) -> None:
        plain = {"total": 1, "items": _ITEMS}
        marked = {**plain, "items": [{**_ITEMS[0], "runflat": True}], "warning": "runflat_required"}
        assert compress_tool_result("search_tires", marked) == compress_tool_result(
            "search_tires", plain
        )


# ── wiring: the car of the call reaches the warning ──────────────────────


def _router(store: Any, *, sales: bool, lines: list[str]) -> Any:
    session = CallSession(uuid.uuid4())
    session.tire_query = {"season": "summer"}
    for line in lines:
        session.add_user_turn(line)
        session.add_assistant_turn("Добре.")
    policy = NetworkPolicy(sales_enabled=sales, recommend_count=3)
    return _build_tool_router(session, store_client=store, network_policy=policy)


def _store(vehicles: dict[str, Any]) -> Any:
    store = create_autospec(StoreClient, instance=True)
    store.search_tires.return_value = {"total": 1, "items": [dict(_ITEMS[0])]}
    store.resolve_vehicle_text.side_effect = lambda text: vehicles.get(text)
    return store


class TestWiring:
    @pytest.mark.asyncio
    async def test_bmw_named_by_the_caller_gets_the_warning(self) -> None:
        store = _store({"на бмв х5": {"brand": "BMW", "model": "X5"}})
        router = _router(store, sales=True, lines=["на бмв х5", "літні"])
        with patch("src.main._call_logger", None):
            result = await router.execute("search_tires", dict(_SIZE))
        assert result["warning"] == "runflat_required"

    @pytest.mark.asyncio
    async def test_latest_car_wins(self) -> None:
        store = _store({"бмв": {"brand": "BMW"}, "ні, шкода октавія": {"brand": "Skoda"}})
        router = _router(store, sales=True, lines=["бмв", "ні, шкода октавія"])
        with patch("src.main._call_logger", None):
            result = await router.execute("search_tires", dict(_SIZE))
        assert "warning" not in result

    @pytest.mark.asyncio
    async def test_each_line_is_looked_up_once(self) -> None:
        store = _store({})
        router = _router(store, sales=True, lines=["добрий день", "літні"])
        with patch("src.main._call_logger", None):
            await router.execute("search_tires", dict(_SIZE))
            await router.execute("search_tires", dict(_SIZE))
        assert store.resolve_vehicle_text.await_count == 2

    @pytest.mark.asyncio
    async def test_sales_off_never_looks_for_the_car(self) -> None:
        store = _store({"бмв": {"brand": "BMW"}})
        router = _router(store, sales=False, lines=["бмв"])
        with patch("src.main._call_logger", None):
            result = await router.execute("search_tires", dict(_SIZE))
        assert "warning" not in result
        store.resolve_vehicle_text.assert_not_awaited()
