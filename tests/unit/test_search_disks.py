"""search_disks: wheel catalog query, fitment verdict per variant, wiring.

Wave 5-N (orders-consult-networks 2026-09-28). Tables from migration 062
(``disk_products``, ``vehicle_disk_sizes``) are not applied anywhere yet —
the DB is a small fake engine that records SQL + bind params and answers
through a responder; no bare AsyncMock stands in for an API.
"""

from __future__ import annotations

import ast
import pathlib
import re
import uuid
from decimal import Decimal
from typing import Any
from unittest.mock import create_autospec, patch

import pytest

from src.agent import disk_fitment as fit
from src.agent.network_policy import NetworkPolicy
from src.agent.tools import ALL_TOOLS
from src.core.call_session import CallSession
from src.main import _SCENARIO_TOOLS, _build_tool_router, _scenario_tool_names
from src.store_client.catalog_types import WHEEL
from src.store_client.client import StoreClient

D = Decimal
_REPO = pathlib.Path(__file__).resolve().parents[2]

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
        return iter(self._rows)


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


def _wheel(
    brand: str,
    model: str,
    price: int,
    *,
    bolts: int = 5,
    pcd: str = "114.3",
    pcd_alt: str | None = None,
    et: str = "45",
    dia: str = "60.1",
    width: str = "7",
    diameter: int = 17,
    qty: int = 4,
) -> dict[str, Any]:
    return {
        "id": f"{brand}-{model}-{pcd}-{et}-{dia}",
        "brand": brand,
        "model": model,
        "size": f"{diameter} {bolts}/{pcd}x{width} ET{et} DIA{dia}",
        "diameter": diameter,
        "width_j": D(width),
        "bolt_count": bolts,
        "pcd": D(pcd),
        "pcd_alt": D(pcd_alt) if pcd_alt else None,
        "et": D(et),
        "dia": D(dia),
        "color": "SILVER",
        "price": price,
        "stock_quantity": qty,
    }


_CAMRY_KITS = [{"bolt_count": 5, "pcd": D("114.30"), "dia": D("60.10")}]
_CAMRY_SIZES = [{"width": D("7.00"), "diameter": D("17.0"), "et": D("45.0")}]


def _responder(
    wheels: list[dict[str, Any]],
    *,
    kits: list[dict[str, Any]] | None = None,
    sizes: list[dict[str, Any]] | None = None,
    car_found: bool = True,
) -> Any:
    def respond(sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        if "FROM disk_products" in sql:
            return wheels
        if "FROM vehicle_brands" in sql:
            return [{"id": 1, "name": "Toyota"}] if car_found else []
        if "FROM vehicle_models" in sql:
            return [{"id": 2, "name": "Camry"}]
        if "FROM vehicle_disk_sizes" in sql:
            return sizes if sizes is not None else _CAMRY_SIZES
        if "SELECT k.id FROM vehicle_kits" in sql:
            return [{"id": 9}]
        if "FROM vehicle_kits" in sql:
            return kits if kits is not None else _CAMRY_KITS
        return []

    return respond


def _client(responder: Any) -> tuple[StoreClient, _Engine]:
    engine = _Engine(responder)
    return StoreClient(base_url="http://x", api_key="k", db_engine=engine), engine


def _disk_sql(engine: _Engine) -> list[tuple[str, dict[str, Any]]]:
    return [(s, p) for s, p in engine.calls if "FROM disk_products" in s]


_CAR = {"brand": "Toyota", "model": "Camry", "year": 2018}


# ── catalog query ────────────────────────────────────────────────────────


class TestQuery:
    @pytest.mark.asyncio
    async def test_wheels_only_parsed_in_stock_of_the_network(self) -> None:
        client, engine = _client(_responder([]))
        await client.search_disks(diameter=17, network="Tshina")
        [(sql, bound)] = _disk_sql(engine)
        assert "m.type_id = :wheel_type" in sql and bound["wheel_type"] == WHEEL
        assert "d.parse_ok" in sql
        assert "s.stock_quantity > 0" in sql
        assert bound["network"] == "Tshina"
        assert bound["diameter"] == 17

    @pytest.mark.asyncio
    async def test_diameter_required(self) -> None:
        client, engine = _client(_responder([]))
        result = await client.search_disks()
        assert result["items"] == [] and result["error"] == "diameter_required"
        assert engine.calls == []

    @pytest.mark.asyncio
    async def test_bad_pcd_is_refused_not_ignored(self) -> None:
        client, engine = _client(_responder([]))
        result = await client.search_disks(diameter=17, pcd="сто")
        assert result["error"] == "pcd_format"
        assert engine.calls == []

    @pytest.mark.asyncio
    async def test_pcd_filter_is_exact_after_sql(self) -> None:
        rows = [
            _wheel("A", "a", 100, pcd="100"),
            _wheel("B", "b", 200, pcd="100", pcd_alt="112"),
            _wheel("C", "c", 300, bolts=4, pcd="100"),  # bolt count differs
            _wheel("E", "e", 400, pcd="112", pcd_alt="100"),
        ]
        client, engine = _client(_responder(rows))
        result = await client.search_disks(diameter=17, pcd="5x100")
        assert {i["brand"] for i in result["items"]} == {"A", "B", "E"}
        [(_, bound)] = _disk_sql(engine)
        assert bound["bolts"] == 5 and bound["pcd"] == D("100")

    @pytest.mark.asyncio
    async def test_double_drilled_wheel_found_by_4_bolt_pattern(self) -> None:
        rows = [_wheel("A", "a", 100, bolts=8, pcd="100", pcd_alt="114.3")]
        client, engine = _client(_responder(rows))
        result = await client.search_disks(diameter=17, pcd="4x114.3")
        assert [i["pcd"] for i in result["items"]] == ["4x100/4x114.3"]
        [(_, bound)] = _disk_sql(engine)
        assert bound["bolts_double"] == 8


# ── offer size ───────────────────────────────────────────────────────────


class TestRecommendCount:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("count", [2, 3])
    async def test_at_most_recommend_count_one_per_model(self, count: int) -> None:
        rows = [_wheel(f"B{i}", f"m{i}", 100 * (i + 1)) for i in range(8)]
        rows += [_wheel("B0", "m0", 50, et="44")]  # same model twice
        client, _ = _client(_responder(rows))
        result = await client.search_disks(diameter=17, recommend_count=count)
        assert len(result["items"]) == count
        models = [(i["brand"], i["model"]) for i in result["items"]]
        assert len(set(models)) == len(models)


# ── fitment ──────────────────────────────────────────────────────────────


class TestFitment:
    @pytest.mark.asyncio
    async def test_every_offered_wheel_carries_a_verdict(self) -> None:
        rows = [
            _wheel("Fit", "a", 300),
            _wheel("Rings", "b", 100, dia="67.1"),
            _wheel("Small", "c", 50, dia="57.1"),  # smaller bore → not_fit
            _wheel("FarEt", "d", 60, et="20"),  # 25 mm → not_recommended
        ]
        client, _ = _client(_responder(rows))
        result = await client.search_disks(diameter=17, vehicle=_CAR, recommend_count=3)
        statuses = {i["brand"]: i["fit"]["status"] for i in result["items"]}
        assert statuses == {"Fit": fit.FITS, "Rings": fit.FITS_WITH_RINGS}
        assert result["excluded_not_fitting"] == 2
        assert result["vehicle"]["found"] is True
        assert result["fit_policy"] == "no_spacers_no_redrilling"
        for item in result["items"]:
            assert item["fit"]["status"] in fit.OFFERABLE

    @pytest.mark.asyncio
    async def test_better_verdict_goes_first_even_if_dearer(self) -> None:
        rows = [_wheel("Rings", "b", 100, dia="67.1"), _wheel("Fit", "a", 900)]
        client, _ = _client(_responder(rows))
        result = await client.search_disks(diameter=17, vehicle=_CAR, recommend_count=2)
        assert [i["fit"]["status"] for i in result["items"]] == [fit.FITS, fit.FITS_WITH_RINGS]

    @pytest.mark.asyncio
    async def test_car_pcd_narrows_the_query(self) -> None:
        client, engine = _client(_responder([]))
        await client.search_disks(diameter=17, vehicle=_CAR)
        [(_, bound)] = _disk_sql(engine)
        assert bound["bolts"] == 5 and bound["pcd"] == D("114.30")

    @pytest.mark.asyncio
    async def test_ambiguous_car_gets_no_offer(self) -> None:
        kits = [*_CAMRY_KITS, {"bolt_count": 5, "pcd": D("100"), "dia": D("54.1")}]
        client, engine = _client(_responder([_wheel("A", "a", 1)], kits=kits))
        result = await client.search_disks(diameter=17, vehicle=_CAR)
        assert result["items"] == []
        assert result["vehicle"]["status"] == fit.AMBIGUOUS_CAR
        assert result["need"] == "vehicle_year_or_modification"
        assert _disk_sql(engine) == []

    @pytest.mark.asyncio
    async def test_car_without_pcd_never_fits(self) -> None:
        kits = [{"bolt_count": None, "pcd": None, "dia": D("60.1")}]
        client, _ = _client(_responder([_wheel("A", "a", 1)], kits=kits))
        result = await client.search_disks(diameter=17, vehicle=_CAR)
        assert result["items"], "a car without PCD still sees wheels, with a caveat"
        for item in result["items"]:
            assert item["fit"]["status"] == fit.CANNOT_CONFIRM

    @pytest.mark.asyncio
    async def test_unknown_car_never_fits(self) -> None:
        client, _ = _client(_responder([_wheel("A", "a", 1)], car_found=False))
        result = await client.search_disks(diameter=17, vehicle={"brand": "Nope", "model": "X"})
        assert result["vehicle"]["found"] is False
        assert [i["fit"]["status"] for i in result["items"]] == [fit.CANNOT_CONFIRM]

    @pytest.mark.asyncio
    async def test_no_vehicle_no_verdict(self) -> None:
        client, _ = _client(_responder([_wheel("A", "a", 1)]))
        result = await client.search_disks(diameter=17)
        assert "fit" not in result["items"][0]
        assert "vehicle" not in result


# ── wiring ───────────────────────────────────────────────────────────────


class TestWiring:
    @pytest.mark.asyncio
    async def test_router_registers_and_passes_policy_count(self) -> None:
        store = create_autospec(StoreClient, instance=True)
        store.search_disks.return_value = {"total": 0, "items": []}
        session = CallSession(uuid.uuid4())
        session.network_id = "Tshina"
        policy = NetworkPolicy(recommend_count=2)
        router = _build_tool_router(session, store_client=store, network_policy=policy)
        with patch("src.main._call_logger", None):
            await router.execute(
                "search_disks", {"diameter": 17, "recommend_count": 9, "network": "evil"}
            )
        kwargs = store.search_disks.await_args.kwargs
        assert kwargs["recommend_count"] == 2
        assert kwargs["network"] == "Tshina"
        assert kwargs["diameter"] == 17

    @pytest.mark.asyncio
    async def test_end_to_end_policy_caps_the_offer(self) -> None:
        rows = [_wheel(f"B{i}", f"m{i}", 100 * (i + 1)) for i in range(6)]
        client, _ = _client(_responder(rows))
        policy = NetworkPolicy(recommend_count=2)
        router = _build_tool_router(
            CallSession(uuid.uuid4()), store_client=client, network_policy=policy
        )
        with patch("src.main._call_logger", None):
            result = await router.execute("search_disks", {"diameter": 17})
        assert len(result["items"]) == 2

    def test_tool_schema_in_all_tools(self) -> None:
        names = [t["name"] for t in ALL_TOOLS]
        assert names.count("search_disks") == 1

    def test_only_the_sales_scenario_offers_it(self) -> None:
        assert "search_disks" in _SCENARIO_TOOLS["sales"]
        on = NetworkPolicy(sales_enabled=True)
        assert "search_disks" in (_scenario_tool_names("sales", on) or set())
        for scenario, tools in _SCENARIO_TOOLS.items():
            if scenario != "sales":
                assert "search_disks" not in tools, scenario

    def test_schema_has_no_literal_numbers(self) -> None:
        """Example values in a tool description become call arguments."""
        [tool] = [t for t in ALL_TOOLS if t["name"] == "search_disks"]
        texts = [tool["description"]]

        def walk(node: Any) -> None:
            if isinstance(node, dict):
                for k, v in node.items():
                    if k == "description" and isinstance(v, str):
                        texts.append(v)
                    else:
                        walk(v)

        walk(tool["input_schema"])
        for text in texts:
            assert not re.search(r"\d", text), text
        assert tool["input_schema"]["required"] == ["diameter"]

    def test_canonical_tool_lists_name_it(self) -> None:
        overview = (_REPO / "doc/development/00-overview.md").read_text(encoding="utf-8")
        claude_md = (_REPO / "CLAUDE.md").read_text(encoding="utf-8")
        assert "| `search_disks` |" in overview
        section = claude_md[claude_md.index("## Canonical Tool Names") :]
        assert "`search_disks`" in section.split("\n## ")[0]


# ── knowledge base ───────────────────────────────────────────────────────


class TestKnowledge:
    def test_scraper_sends_wheel_articles_to_wheels(self) -> None:
        # bs4 is not importable in the local venv — read the literal map.
        src = (_REPO / "src/knowledge/scraper.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        [node] = [
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.AnnAssign)
            and isinstance(n.target, ast.Name)
            and n.target.id == "_SLUG_CATEGORY_MAP"
        ]
        mapping = ast.literal_eval(node.value)
        assert mapping["vse-o-diskah"] == "wheels"

    def test_tshina_disk_topics_seeded_into_wheels(self) -> None:
        from scripts.import_tshina_faq import DEFAULT_SOURCE, TOPICS, build

        for slug in ("disk-replica", "wheel-fasteners-tpms"):
            assert TOPICS[slug][1].startswith("wheels/")
        if not DEFAULT_SOURCE.exists():
            pytest.skip("tshina_new sources not on this host")
        files = build(DEFAULT_SOURCE)
        for slug in ("disk-replica", "wheel-fasteners-tpms"):
            rel = TOPICS[slug][1]
            assert (_REPO / "knowledge_seed" / rel).read_text(encoding="utf-8") == files[rel]


def test_sales_scope_sends_wheel_selection_to_search_disks() -> None:
    """With search_disks live, the sales frame no longer hands wheel picks to a manager."""
    from scripts.configure_tenants import PROKOLESO_CONFIG_PATCH, TVOYA_SHINA_CONFIG_PATCH
    from src.agent.network_policy import NetworkPolicy
    from src.agent.prompts import render_sales_scope

    for cfg in (TVOYA_SHINA_CONFIG_PATCH, PROKOLESO_CONFIG_PATCH):
        policy = NetworkPolicy.from_tenant_config({**cfg, "sales_enabled": True})
        frame = render_sales_scope(policy)
        assert "search_disks" in frame
        assert "Підбір дисків робить менеджер" not in frame
