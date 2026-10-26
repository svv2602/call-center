"""Wave 4-F — a named need orders the tyre offer (port of tshina `NeedsMatcher`).

What is pinned here:

* the caller's words → ``quiet`` / ``wet_grip`` / ``fuel`` / ``highway``
  (UA + RU, oblique cases); «шум не важливий» is not a need and drops one
  said before — through `merge_tire_query`, turn by turn;
* the step of a catalogue row per need (tshina thresholds), no data → 0;
* `StoreClient._rank_tires`: the need first, ``brand_priority`` only between
  equally fitting tyres, a short step topped up from below; without needs the
  offer is exactly the pre-4-F one (pinned SKU lists computed with ``5d5bca5``);
* the ``search_tires`` handler: needs from ``session.tire_query`` only, never
  from the LLM's args, only under sales;
* «Підбір шин: прогрес» shows the need — rendered through the pipeline's
  builder from session state.

Rows have the shape of `StoreClient._query_tire_rows`. The DB is a small fake
engine; the store in handler-argument tests is ``create_autospec``.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import create_autospec, patch

import pytest

from src.agent import prompts
from src.agent.network_policy import NetworkPolicy
from src.agent.parsers.tire_query import parse_need_refusals, parse_needs
from src.agent.tire_needs import need_score, need_tier, row_speed_index
from src.core.call_session import CallSession
from src.core.pipeline import merge_tire_query
from src.main import _build_tool_router
from src.store_client.client import StoreClient
from tests.unit.test_pipeline_fsm_wire import Harness

SALES_ON = NetworkPolicy(sales_enabled=True, recommend_count=3, brand_priority=())

# ── rows in the shape of `_query_tire_rows` ─────────────────────────────


def _row(
    brand: str,
    model: str,
    price: int,
    *,
    noise: int | None = None,
    wet: str | None = None,
    fuel: str | None = None,
    speed: str | None = None,
    qty: int = 4,
    size: str = "205/55 R16",
    season: str = "winter",
    sku: str | None = None,
) -> dict[str, Any]:
    return {
        "id": sku or f"{brand}-{model}-{size}".replace(" ", "_"),
        "brand": brand,
        "model": model,
        "size": size,
        "season": season,
        "price": price,
        "stock_quantity": qty,
        "runflat": False,
        "eu_fuel": fuel,
        "eu_wet": wet,
        "eu_noise_db": noise,
        "attr_studded": None,
        "attr_xl": None,
        "load_index": None,
        "speed_index": speed,
        "desc_studded": False,
        "desc_studdable": False,
        "desc_xl": False,
    }


_PRIORITY = ("bridgestone", "firestone", "laufenn")


def _market() -> list[dict[str, Any]]:
    """Winter 205/55 R16, price-ordered like the SQL (cheapest first)."""
    rows = [
        _row("Doublestar", "DH03", 900, noise=73, wet="C", fuel="E", speed="T"),
        _row("Laufenn", "I Fit", 1500, noise=72, wet="B", fuel="C", speed="H"),
        _row("Laufenn", "I Fit", 1600, noise=70, wet="A", sku="laufenn-dup"),
        _row("Kumho", "WP72", 2000, noise=70, wet="C", fuel="C", speed="H"),
        _row("Firestone", "Winterhawk", 2500, noise=71, wet="B", fuel="C", speed="T"),
        _row("Nokian", "Hakka R5", 4000, noise=68, wet="A", fuel="B", speed="V"),
        _row("Michelin", "Alpin 7", 5500, noise=69, wet="A", fuel="A", speed="H"),
        _row("Bridgestone", "Blizzak LM005", 6000, noise=72, wet="B", fuel="C", speed="H"),
        _row("Bridgestone", "Blizzak 6", 6500, noise=73, wet="C", fuel="C", speed="T"),
        _row("Pirelli", "Sottozero", 7000),
        _row("Continental", "WinterContact", 3000, noise=67, wet="A", qty=0),
    ]
    return sorted(rows, key=lambda r: r["price"])


def _one_brand() -> list[dict[str, Any]]:
    return [
        _row("Michelin", m, p, noise=n)
        for m, p, n in (("X", 5000, 73), ("Y", 5200, 68), ("Z", 5400, 71), ("W", 5600, None))
    ]


#: name → (rows, brand_priority, recommend_count). Expected SKUs below were
#: computed with `_rank_tires` of ``5d5bca5`` (before wave 4-F).
FIXTURES: dict[str, tuple[list[dict[str, Any]], tuple[str, ...], int]] = {
    "market_priority": (_market(), _PRIORITY, 3),
    "market_no_priority": (_market(), (), 3),
    "market_two": (_market(), ("michelin",), 2),
    "one_brand": (_one_brand(), _PRIORITY, 3),
}
PRE_4F_OFFER: dict[str, list[str]] = {}  # filled below


# ── parser ───────────────────────────────────────────────────────────────


class TestParseNeeds:
    @pytest.mark.parametrize(
        ("text", "needs"),
        [
            ("тихі", ["quiet"]),
            ("щоб не шуміли", ["quiet"]),
            ("хочу тиху гуму", ["quiet"]),
            ("нужны тихие", ["quiet"]),
            ("хочу тихую", ["quiet"]),
            ("чтобы не шумели", ["quiet"]),
            ("щоб шуму менше", ["quiet"]),
            ("на мокрому щоб тримали", ["wet_grip"]),
            ("чтобы на мокрой не скользили", ["wet_grip"]),
            ("важливе зчеплення", ["wet_grip"]),
            ("сцепление в дождь", ["wet_grip"]),
            ("економні по пальному", ["fuel"]),
            ("экономичные", ["fuel"]),
            ("щоб менше палива їли", ["fuel"]),
            ("для траси", ["highway"]),
            ("для трассы", ["highway"]),
            ("багато їжджу трасою", ["highway"]),
            ("тихі і для траси", ["quiet", "highway"]),
        ],
    )
    def test_need_named(self, text: str, needs: list[str]) -> None:
        assert parse_needs(text) == needs

    @pytest.mark.parametrize(
        "text",
        [
            "з тих, що ви назвали, яка дешевша?",  # «тих» = «those»
            "зимові 205/55 R16",
            "мішлен",
            "скільки коштує",
            "шумні не треба",  # ambiguous, not a refusal either
        ],
    )
    def test_no_need(self, text: str) -> None:
        assert parse_needs(text) is None
        assert parse_need_refusals(text) is None

    @pytest.mark.parametrize(
        ("text", "refused"),
        [
            ("шум не важливий", ["quiet"]),
            ("неважно, как шумят", ["quiet"]),
            ("неважно шумят", ["quiet"]),
            ("тиша не принципова", ["quiet"]),
            ("по пальному мені все одно", ["fuel"]),
            ("не по трасі, по місту", ["highway"]),
            ("по трассе почти не езжу", ["highway"]),
        ],
    )
    def test_not_caring_is_not_a_need(self, text: str, refused: list[str]) -> None:
        assert parse_needs(text) is None
        assert parse_need_refusals(text) == refused

    def test_refusal_and_need_in_one_utterance(self) -> None:
        text = "тиша не важлива, а от на мокрому щоб тримали"
        assert parse_needs(text) == ["wet_grip"]
        assert parse_need_refusals(text) == ["quiet"]

    def test_indifference_about_something_else_keeps_the_need(self) -> None:
        assert parse_needs("тихі, а бренд все одно") == ["quiet"]
        assert parse_needs("мені все одно який бренд головне щоб тихі") == ["quiet"]


# ── merge: needs through the conversation ────────────────────────────────


def _talk(*lines: str) -> list[dict[str, Any]]:
    states, current = [], {}
    for line in lines:
        current = merge_tire_query(current, line)
        states.append(current)
    return states


class TestMergeNeeds:
    def test_needs_accumulate_and_a_refusal_drops_one(self) -> None:
        states = _talk(
            "потрібні зимові 205/55 R16",
            "щоб тихі були",
            "і на мокрому щоб тримали, тихі",
            "а шум взагалі не важливий",
            "ні, все-таки для траси",
        )
        assert "needs" not in states[0]
        assert states[1]["needs"] == ["quiet"]
        assert states[2]["needs"] == ["quiet", "wet_grip"]
        assert states[3]["needs"] == ["wet_grip"]
        assert states[4]["needs"] == ["wet_grip", "highway"]
        assert states[4]["sizes"] == ["205/55 R16"] and states[4]["season"] == "winter"

    def test_last_refused_need_leaves_no_key(self) -> None:
        states = _talk("нужны зимние 205/55 R16, чтобы не шумели", "а, неважно шумят")
        assert states[0]["needs"] == ["quiet"]
        assert "needs" not in states[1]

    def test_unrelated_turn_keeps_the_needs(self) -> None:
        states = _talk("зимові 205/55 R16 тихі", "комплект")
        assert states[1]["needs"] == ["quiet"]


# ── tiers ────────────────────────────────────────────────────────────────


class TestTiers:
    @pytest.mark.parametrize(
        ("noise", "tier"), [(60, 2), (69, 2), (70, 1), (71, 1), (72, 0), (75, 0), (None, 0)]
    )
    def test_noise(self, noise: int | None, tier: int) -> None:
        assert need_tier(_row("A", "B", 1, noise=noise), "quiet") == tier

    @pytest.mark.parametrize(("grade", "tier"), [("A", 2), ("B", 1), ("C", 0), ("E", 0), (None, 0)])
    def test_wet(self, grade: str | None, tier: int) -> None:
        assert need_tier(_row("A", "B", 1, wet=grade), "wet_grip") == tier

    @pytest.mark.parametrize(
        ("grade", "tier"), [("A", 2), ("b", 2), ("C", 1), ("D", 0), (None, 0), ("", 0)]
    )
    def test_fuel(self, grade: str | None, tier: int) -> None:
        assert need_tier(_row("A", "B", 1, fuel=grade), "fuel") == tier

    @pytest.mark.parametrize(
        ("speed", "tier"),
        [("V", 2), ("W", 2), ("Y", 2), ("(Y)", 2), ("ZR", 2), ("H", 1), ("T", 0), ("Q", 0)],
    )
    def test_speed(self, speed: str, tier: int) -> None:
        assert need_tier(_row("A", "B", 1, speed=speed), "highway") == tier

    def test_speed_from_the_size_when_no_attribute(self) -> None:
        row = _row("A", "B", 1, size="225/50 R17 98W")
        assert row_speed_index(row) == "W"
        assert need_tier(row, "highway") == 2
        assert row_speed_index(_row("A", "B", 1, size="215/65 R16C")) is None
        assert need_tier(_row("A", "B", 1), "highway") == 0

    def test_score_is_the_sum(self) -> None:
        row = _row("A", "B", 1, noise=70, wet="A", speed="H")
        assert need_score(row, ["quiet", "wet_grip", "highway"]) == 4
        assert need_score(row, []) == 0


# ── ranking ──────────────────────────────────────────────────────────────


def _ids(rows: list[Any]) -> list[str]:
    return [r["id"] for r in rows]


PRE_4F_OFFER.update(
    {
        "market_priority": [
            "Bridgestone-Blizzak_LM005-205/55_R16",
            "Firestone-Winterhawk-205/55_R16",
            "Laufenn-I_Fit-205/55_R16",
        ],
        "market_no_priority": [
            "Doublestar-DH03-205/55_R16",
            "Nokian-Hakka_R5-205/55_R16",
            "Pirelli-Sottozero-205/55_R16",
        ],
        "market_two": ["Michelin-Alpin_7-205/55_R16", "Firestone-Winterhawk-205/55_R16"],
        "one_brand": ["Michelin-X-205/55_R16", "Michelin-Z-205/55_R16", "Michelin-W-205/55_R16"],
    }
)


class TestRankingWithoutNeeds:
    @pytest.mark.parametrize("name", list(FIXTURES))
    @pytest.mark.parametrize("needs", [(), [], None])
    def test_offer_is_the_pre_4f_one(self, name: str, needs: Any) -> None:
        rows, prio, count = FIXTURES[name]
        assert _ids(StoreClient._rank_tires(rows, prio, count, needs or ())) == PRE_4F_OFFER[name]
        assert _ids(StoreClient._rank_tires(rows, prio, count)) == PRE_4F_OFFER[name]


class TestRankingWithNeeds:
    def test_quietest_first_even_outside_the_network_brands(self) -> None:
        picked = StoreClient._rank_tires(_market(), _PRIORITY, 3, ["quiet"])
        # Step 2 (≤ 69 dB): Nokian 68, Michelin 69 — none is a network brand.
        assert _ids(picked)[:2] == ["Nokian-Hakka_R5-205/55_R16", "Michelin-Alpin_7-205/55_R16"]
        # Step 1 (70–71 dB): Kumho, Firestone → the network's Firestone breaks the tie.
        assert picked[2]["brand"] == "Firestone"

    def test_equal_step_keeps_the_network_order(self) -> None:
        rows = [
            _row("Kumho", "A", 2000, wet="A"),
            _row("Nokian", "B", 4000, wet="A"),
            _row("Bridgestone", "C", 6000, wet="A"),
        ]
        picked = StoreClient._rank_tires(rows, _PRIORITY, 3, ["wet_grip"])
        assert picked[0]["brand"] == "Bridgestone"
        assert _ids(picked) == _ids(StoreClient._rank_tires(rows, _PRIORITY, 3))

    def test_short_step_is_topped_up_from_below(self) -> None:
        rows = [
            _row("Kumho", "A", 2000),  # no label
            _row("Nokian", "B", 4000, noise=68),
            _row("Hankook", "C", 3000),  # no label
            _row("Laufenn", "D", 1500, noise=74),
        ]
        picked = StoreClient._rank_tires(rows, (), 3, ["quiet"])
        assert len(picked) == 3
        assert picked[0]["brand"] == "Nokian"
        assert {r["brand"] for r in picked[1:]} <= {"Kumho", "Hankook", "Laufenn"}

    def test_highway_prefers_the_speed_index(self) -> None:
        rows = [
            _row("Kumho", "A", 2000, speed="T"),
            _row("Hankook", "B", 3000, speed="H"),
            _row("Nokian", "C", 4000, size="205/55 R16 94V"),
        ]
        picked = StoreClient._rank_tires(rows, (), 2, ["highway"])
        assert [r["brand"] for r in picked] == ["Nokian", "Hankook"]

    def test_out_of_stock_never_offered(self) -> None:
        picked = StoreClient._rank_tires(_market(), (), 3, ["quiet"])
        assert all(r["stock_quantity"] > 0 for r in picked)
        assert "Continental" not in {r["brand"] for r in picked}

    def test_steps_are_logged(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level("INFO", logger="src.store_client.client"):
            StoreClient._rank_tires(_market(), _PRIORITY, 3, ["quiet"])
        line = next(r.getMessage() for r in caplog.records if "rank_tires" in r.getMessage())
        assert "needs=['quiet']" in line and "{2: 2, 1: 2, 0: 5}" in line

    def test_no_needs_takes_the_old_path(self, caplog: pytest.LogCaptureFixture) -> None:
        """No need named → the pre-4-F code path, not a one-step need ranking."""
        with caplog.at_level("INFO", logger="src.store_client.client"):
            StoreClient._rank_tires(_market(), _PRIORITY, 3)
        assert not [r for r in caplog.records if "rank_tires" in r.getMessage()]


# ── store: search paths (fake engine) ────────────────────────────────────


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
        return iter(())


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
        if "FROM tire_products" not in sql:
            return _Result([])
        return _Result(self._engine.responder(dict(params or {})))


class _Engine:
    def __init__(self, responder: Any) -> None:
        self.responder = responder
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def connect(self) -> _Conn:
        return _Conn(self)


def _store(rows: list[dict[str, Any]]) -> StoreClient:
    return StoreClient(base_url="http://x", api_key="k", db_engine=_Engine(lambda p: rows))


def _staggered_store() -> StoreClient:
    front = [
        _row("Kumho", "PS71", 3000, size="245/40 R18", noise=73),
        _row("Bridgestone", "S001", 5000, size="245/40 R18", noise=72),
        _row("Nokian", "Powerproof", 4500, size="245/40 R18", noise=68),
        _row("Hankook", "S1", 3500, size="245/40 R18", noise=70),
    ]
    rear = [
        {**r, "id": f"rear-{r['brand']}", "size": "275/35 R18", "price": r["price"] + 600}
        for r in front
    ]
    return StoreClient(
        base_url="http://x",
        api_key="k",
        db_engine=_Engine(lambda p: rear if p.get("width") == 275 else front),
    )


_STAGGERED = {
    "width": 245,
    "profile": 40,
    "diameter": 18,
    "season": "summer",
    "rear_width": 275,
    "rear_profile": 35,
    "rear_diameter": 18,
    "recommend_count": 2,
    "brand_priority": ("bridgestone",),
}


class TestStoreSearch:
    @pytest.mark.asyncio
    async def test_needs_reach_the_ranking_and_never_the_sql(self) -> None:
        store = _store(_market())
        result = await store.search_tires(
            network="ProKoleso",
            width=205,
            recommend_count=3,
            brand_priority=_PRIORITY,
            needs=["quiet"],
        )
        assert result["items"][0]["brand"] == "Nokian"
        engine = store._db_engine
        assert all("needs" not in p for _, p in engine.calls)

    @pytest.mark.asyncio
    async def test_no_needs_is_the_pre_4f_offer(self) -> None:
        store = _store(_market())
        result = await store.search_tires(
            network="ProKoleso", width=205, recommend_count=3, brand_priority=_PRIORITY
        )
        assert [i["id"] for i in result["items"]] == PRE_4F_OFFER["market_priority"]

    @pytest.mark.asyncio
    async def test_staggered_pair_is_ranked_by_the_need(self) -> None:
        plain = await _staggered_store().search_tires(network="N", **_STAGGERED)
        assert [i["brand"] for i in plain["items"]] == ["Bridgestone", "Hankook"]  # pre-4-F
        quiet = await _staggered_store().search_tires(network="N", **_STAGGERED, needs=["quiet"])
        assert [i["brand"] for i in quiet["items"]] == ["Nokian", "Hankook"]
        assert quiet["staggered"] is True and quiet["items"][0]["rear_size"] == "275/35 R18"


# ── handler: needs from the session, sales only ─────────────────────────


def _session(*lines: str) -> CallSession:
    session = CallSession(uuid.uuid4())
    for line in lines:
        session.tire_query = merge_tire_query(session.tire_query, line)
    return session


def _autospec_router(session: CallSession, policy: NetworkPolicy) -> tuple[Any, Any]:
    store = create_autospec(StoreClient, instance=True)
    store.search_tires.return_value = {"total": 0, "items": []}
    return _build_tool_router(session, store_client=store, network_policy=policy), store


async def _search(router: Any, args: dict[str, Any]) -> Any:
    with patch("src.main._call_logger", None):
        return await router.execute("search_tires", args)


_ARGS = {"width": 205, "profile": 55, "diameter": 16, "season": "winter"}


class TestHandler:
    @pytest.mark.asyncio
    async def test_session_needs_are_passed_under_sales(self) -> None:
        session = _session("зимові 205/55 R16", "щоб тихі були")
        router, store = _autospec_router(session, SALES_ON)
        await _search(router, dict(_ARGS))
        assert store.search_tires.call_args.kwargs["needs"] == ["quiet"]

    @pytest.mark.asyncio
    async def test_llm_needs_are_ignored(self) -> None:
        session = _session("зимові 205/55 R16", "для траси")
        router, store = _autospec_router(session, SALES_ON)
        await _search(router, {**_ARGS, "needs": ["quiet", "fuel"]})
        assert store.search_tires.call_args.kwargs["needs"] == ["highway"]

    @pytest.mark.asyncio
    async def test_llm_needs_without_session_needs_pass_nothing(self) -> None:
        session = _session("зимові 205/55 R16")
        router, store = _autospec_router(session, SALES_ON)
        await _search(router, {**_ARGS, "needs": ["quiet"]})
        assert "needs" not in store.search_tires.call_args.kwargs

    @pytest.mark.asyncio
    @pytest.mark.parametrize("policy", [NetworkPolicy(sales_enabled=False), None])
    async def test_sales_off_passes_no_needs(self, policy: NetworkPolicy | None) -> None:
        session = _session("зимові 205/55 R16")
        session.tire_query = {**session.tire_query, "needs": ["quiet"]}
        router, store = _autospec_router(session, policy)  # type: ignore[arg-type]
        await _search(router, {**_ARGS, "needs": ["quiet"]})
        assert "needs" not in store.search_tires.call_args.kwargs

    @pytest.mark.asyncio
    async def test_said_need_reorders_the_offer_end_to_end(self) -> None:
        policy = NetworkPolicy(sales_enabled=True, recommend_count=3, brand_priority=_PRIORITY)
        plain = _session("зимові 205/55 R16")
        router = _build_tool_router(plain, store_client=_store(_market()), network_policy=policy)
        before = await _search(router, dict(_ARGS))
        assert [i["id"] for i in before["items"]] == PRE_4F_OFFER["market_priority"]

        quiet = _session("зимові 205/55 R16", "чтобы не шумели")
        router = _build_tool_router(quiet, store_client=_store(_market()), network_policy=policy)
        after = await _search(router, dict(_ARGS))
        assert after["items"][0]["brand"] == "Nokian"
        assert after["items"][0]["eu_label"]["noise_db"] == 68


# ── «Підбір шин: прогрес» from session state ─────────────────────────────


def _prompt(*lines: str) -> str:
    h = Harness()
    h.streaming_loop._network_policy = NetworkPolicy(sales_enabled=True)
    for line in lines:
        h.session.tire_query = merge_tire_query(h.session.tire_query, line)
    return prompts.build_system_prompt_with_context(
        "BASE", is_modular=False, tire_progress=h.pipeline._build_tire_progress()
    )


class TestProgressBlock:
    def test_need_is_shown(self) -> None:
        prompt = _prompt("зимові 205/55 R16", "щоб тихі", "і для трассы")
        assert "- Потреба: ✅ тихі, для траси (високий індекс швидкості)" in prompt

    def test_every_need_has_a_label(self) -> None:
        prompt = _prompt("зимові 205/55 R16 тихі, на мокрому тримали, економні, для траси")
        line = next(x for x in prompt.splitlines() if x.startswith("- Потреба:"))
        for label in ("тихі", "зчеплення на мокрій дорозі", "економія пального", "для траси"):
            assert label in line
        assert "quiet" not in line and "wet_grip" not in line

    def test_refused_need_is_not_shown(self) -> None:
        prompt = _prompt("зимові 205/55 R16 тихі", "шум не важливий")
        assert "Потреба:" not in prompt
