"""Wave 5-M — tyre consultation by voice (sales scope only).

What is pinned here:

* the compressor keeps what the consultation needs (``relaxed``,
  ``caveat_key``, the rear axle, ``staggered_pairs``, the non-stock policy)
  under sales, and is byte-identical to the fitting-only output off;
* the caveat of a relaxed search is spoken by the streaming loop itself, before
  the LLM's variants, and the model is told it has been said;
* parser → ``session.tire_query`` (Redis round trip) → «Підбір шин: прогрес»,
  every collected key visible to the LLM; the call site in the pipeline is
  exercised through a real turn;
* the season guard on ``search_tires``: default-deny on the caller's season,
  once per call;
* the sales variants of the tyre / consultation / objections modules; the
  fitting-only prompt unchanged.

Mocks are ``MagicMock(spec=[...])`` / ``create_autospec`` only.
"""

from __future__ import annotations

import json
import uuid
from typing import Any
from unittest.mock import create_autospec, patch

import pytest

from src.agent import prompts
from src.agent.network_policy import NetworkPolicy
from src.agent.tool_result_compressor import compress_tool_result, tire_caveat_phrase
from src.core.call_session import CallSession
from src.core.pipeline import (
    FSM_VOICE_STATES,
    merge_tire_query,
    parse_tire_season,
    tire_season_refusal,
)
from src.store_client.client import StoreClient
from tests.unit.mocks.mock_tts import MockTTSEngine
from tests.unit.test_pipeline_fsm_wire import Harness, fsm_flags
from tests.unit.test_streaming_loop import _build_loop, _text_stream, _tool_stream

SALES_ON = NetworkPolicy(sales_enabled=True)
SALES_OFF = NetworkPolicy(sales_enabled=False)

NO_STUDDED_PHRASE = (
    "Шипованих у цьому розмірі зараз немає — можу запропонувати фрикційні (липучку)."
)

#: A relaxed answer of `StoreClient._search_tires_ladder`: studded asked, none in
#: stock, friction tyres returned.
RELAXED_STUDDED: dict[str, Any] = {
    "total": 1,
    "items": [
        {
            "id": "w1",
            "brand": "Bridgestone",
            "model": "Blizzak 6",
            "size": "235/55 R19",
            "season": "winter",
            "price": 6900,
            "in_stock": True,
        }
    ],
    "relaxed": ["studded"],
    "caveat_key": "no_studded_offer_friction",
}

STAGGERED: dict[str, Any] = {
    "total": 1,
    "staggered": True,
    "items": [
        {
            "id": "f1",
            "brand": "Michelin",
            "model": "Pilot Sport 5",
            "size": "245/40 R19",
            "season": "summer",
            "price": 8000,
            "in_stock": True,
            "rear_id": "r1",
            "rear_size": "275/35 R19",
            "rear_price": 9000,
        }
    ],
}

VEHICLE: dict[str, Any] = {
    "found": True,
    "brand": "BMW",
    "model": "3",
    "years": [2019, 2020],
    "stock_sizes": ["225/45 R18", "245/40 R18"],
    "staggered_pairs": [{"front": "225/45 R18", "rear": "255/40 R18"}],
    "acceptable_sizes": ["225/40 R19"],
    "acceptable_sizes_policy": "specialist_only",
}


# ── compressor ─────────────────────────────────────────────────────────


class TestCompressorSalesOff:
    """Off: the exact output of `a0cdaed` (literal snapshot, taken before the edit)."""

    def test_search_tires_is_byte_identical(self) -> None:
        assert compress_tool_result("search_tires", RELAXED_STUDDED) == (
            '{"total":1,"items":[{"brand":"Bridgestone","model":"Blizzak 6",'
            '"size":"235/55 R19","price":6900,"in_stock":true}]}'
        )

    def test_vehicle_sizes_is_byte_identical(self) -> None:
        assert compress_tool_result("get_vehicle_tire_sizes", VEHICLE) == (
            '{"found":true,"brand":"BMW","model":"3","stock_sizes":["225/45 R18",'
            '"245/40 R18"],"acceptable_sizes":["225/40 R19"],"years":[2019,2020]}'
        )


class TestCompressorSalesOn:
    def test_relaxation_marks_reach_the_llm(self) -> None:
        out = json.loads(compress_tool_result("search_tires", RELAXED_STUDDED, sales_enabled=True))
        assert out["relaxed"] == ["studded"]
        assert out["caveat_key"] == "no_studded_offer_friction"
        assert NO_STUDDED_PHRASE in out["caveat_already_said"]

    def test_rear_axle_kept_without_its_id(self) -> None:
        out = json.loads(compress_tool_result("search_tires", STAGGERED, sales_enabled=True))
        item = out["items"][0]
        assert item["rear_size"] == "275/35 R19" and item["rear_price"] == 9000
        assert "rear_id" not in item and "id" not in item
        assert out["staggered"] is True

    def test_vehicle_pairs_kept_and_non_stock_list_withheld(self) -> None:
        out = json.loads(
            compress_tool_result("get_vehicle_tire_sizes", VEHICLE, sales_enabled=True)
        )
        assert out["staggered_pairs"] == VEHICLE["staggered_pairs"]
        assert "спеціаліст" in out["acceptable_sizes_policy"]
        # the model cannot offer a size it cannot see
        assert "225/40 R19" not in json.dumps(out, ensure_ascii=False)

    def test_plain_result_has_no_caveat(self) -> None:
        out = json.loads(compress_tool_result("search_tires", STAGGERED, sales_enabled=True))
        assert "caveat_key" not in out and "caveat_already_said" not in out


class TestCaveatPhrase:
    def test_no_studded(self) -> None:
        assert tire_caveat_phrase(RELAXED_STUDDED, {"studded": True}) == NO_STUDDED_PHRASE

    def test_brand_named_from_the_args(self) -> None:
        result = {
            **RELAXED_STUDDED,
            "relaxed": ["brand"],
            "caveat_key": "brand_unavailable_alternatives",
        }
        assert tire_caveat_phrase(result, {"brand": "Michelin"}) == (
            "Michelin зараз немає в наявності, ось альтернативи."
        )

    def test_studs_and_brand_both_dropped(self) -> None:
        result = {**RELAXED_STUDDED, "relaxed": ["studded", "brand"]}
        phrase = tire_caveat_phrase(result, {"brand": "Nokian", "studded": True})
        assert phrase is not None
        assert phrase.startswith("Nokian зараз немає") and NO_STUDDED_PHRASE in phrase

    @pytest.mark.parametrize(
        "result",
        [
            {**RELAXED_STUDDED, "items": []},
            {**RELAXED_STUDDED, "caveat_key": "something_new"},
            STAGGERED,
            "not a dict",
        ],
    )
    def test_nothing_invented(self, result: Any) -> None:
        assert tire_caveat_phrase(result, {}) is None


# ── the caveat is spoken by the loop ──────────────────────────────────


class _RecordingTTS(MockTTSEngine):
    def __init__(self) -> None:
        super().__init__()
        self.texts: list[str] = []

    async def synthesize(self, text: str) -> bytes:
        self.texts.append(text)
        return await super().synthesize(text)


def _search_turn(policy: NetworkPolicy) -> tuple[Any, _RecordingTTS]:
    loop, _, _tools, _ = _build_loop(
        [
            _tool_stream("", "tc1", "search_tires", {"width": 235, "studded": True}),
            _text_stream("Є Bridgestone Blizzak 6 за 6900 гривень за шину. Цікавить?"),
        ],
        {"search_tires": RELAXED_STUDDED},
    )
    tts = _RecordingTTS()
    loop._tts_initial = tts
    loop._network_policy = policy
    loop._sales_enabled = policy.sales_enabled
    return loop, tts


@pytest.fixture(autouse=False)
def no_global_tts():
    """`StreamingAgentLoop._tts` prefers the hot-reloaded global engine."""
    with patch("src.tts.get_engine", return_value=None):
        yield


@pytest.mark.usefixtures("no_global_tts")
class TestCaveatSpokenByTheLoop:
    @pytest.mark.asyncio
    async def test_spoken_before_the_variants(self) -> None:
        loop, tts = _search_turn(SALES_ON)
        history: list[dict[str, Any]] = []
        result = await loop.run_turn("потрібні шиповані 235/55 R19", history)

        assert NO_STUDDED_PHRASE in tts.texts
        variants = next(i for i, t in enumerate(tts.texts) if "Blizzak" in t)
        assert tts.texts.index(NO_STUDDED_PHRASE) < variants
        assert result.spoken_text.index(NO_STUDDED_PHRASE) < result.spoken_text.index("Blizzak")

    @pytest.mark.asyncio
    async def test_model_is_told_it_was_said(self) -> None:
        loop, _ = _search_turn(SALES_ON)
        history: list[dict[str, Any]] = []
        await loop.run_turn("потрібні шиповані 235/55 R19", history)
        tool_results = [
            part["content"]
            for msg in history
            if isinstance(msg.get("content"), list)
            for part in msg["content"]
            if part.get("type") == "tool_result"
        ]
        assert any("caveat_already_said" in c for c in tool_results)

    @pytest.mark.asyncio
    async def test_silent_while_sales_are_off(self) -> None:
        loop, tts = _search_turn(SALES_OFF)
        await loop.run_turn("потрібні шиповані 235/55 R19", [])
        assert not any("Шипованих" in t for t in tts.texts)


# ── parser → session ──────────────────────────────────────────────────


class TestMergeTireQuery:
    def test_studded_implies_winter(self) -> None:
        state = merge_tire_query({}, "потрібні шиповані 235/55 R19")
        assert state == {"sizes": ["235/55 R19"], "season": "winter", "nail": "studded"}

    def test_bare_diameter_needs_the_bots_size_question(self) -> None:
        # finding F: the tail of a phone number reads as R16
        started = {"season": "winter"}
        assert "diameter" not in merge_tire_query(started, "на шістнадцять")
        assert "diameter" not in merge_tire_query(
            started, "на шістнадцять", last_bot_text="Як до вас звертатися?"
        )
        assert (
            merge_tire_query(started, "на шістнадцять", last_bot_text="Який розмір шин?")[
                "diameter"
            ]
            == 16
        )

    def test_fitting_request_does_not_start_a_consultation(self) -> None:
        assert merge_tire_query({}, "хочу записатись на шиномонтаж, зимову резину") == {}

    def test_every_field(self) -> None:
        state = merge_tire_query({}, "мені мішлен комплект до шести тисяч за шину ранфлет літні")
        assert state["season"] == "summer"
        assert state["quantity"] == 4
        assert state["budget"] == {"amount": 6000, "scope": "per_tire", "is_cap": True}
        assert state["brands"] == ["michelin"]
        assert state["tech"] == ["runflat"]

    def test_no_empty_values_stored(self) -> None:
        state = merge_tire_query({"sizes": ["205/55 R16"]}, "добре")
        assert all(v not in (None, "", [], {}) for v in state.values())

    def test_a_question_is_not_a_season(self) -> None:
        assert parse_tire_season("літні чи зимові?") is None

    def test_indifference_only_answers_a_season_question(self) -> None:
        assert parse_tire_season("все одно") is None
        assert parse_tire_season("все одно", bot_asked_season=True) == "any"
        state = merge_tire_query(
            {"sizes": ["205/55 R16"]}, "без різниці", last_bot_text="Вам літні чи зимові?"
        )
        assert state["season"] == "any"

    @pytest.mark.parametrize(
        ("text", "season"),
        [
            ("зимові", "winter"),
            ("на зиму", "winter"),
            ("летние", "summer"),
            ("літню гуму", "summer"),
            ("всесезонні", "all_season"),
            ("всесезонку", "all_season"),
        ],
    )
    def test_ua_and_ru_forms(self, text: str, season: str) -> None:
        assert parse_tire_season(text) == season


class TestSessionRoundTrip:
    def test_tire_query_survives_redis(self) -> None:
        session = CallSession(uuid.uuid4())
        session.tire_query = merge_tire_query({}, "потрібні шиповані 235/55 R19")
        session.tire_season_guard_fired = True
        restored = CallSession.from_dict(json.loads(json.dumps(session.to_dict())))
        assert restored.tire_query == session.tire_query
        assert restored.tire_season_guard_fired is True


# ── session → «Підбір шин: прогрес» ─────────────────────────────────


def _pipeline_harness(policy: NetworkPolicy) -> Harness:
    h = Harness()
    h.streaming_loop._network_policy = policy
    return h


def _prompt_from_session(h: Harness) -> str:
    return prompts.build_system_prompt_with_context(
        "BASE", is_modular=False, tire_progress=h.pipeline._build_tire_progress()
    )


class TestProgressBlockVisibility:
    """Rendered through the builder from session state — never a hand-made dict."""

    @pytest.mark.parametrize(
        ("text", "visible"),
        [
            ("потрібні шиповані 235/55 R19", ["235/55 R19", "зимові", "шиповані"]),
            ("липучка 205/55 R16", ["без шипів (липучка)"]),
            ("під шип 205/55 R16", ["під шип"]),
            (
                "мішлен комплект до шести тисяч за шину ранфлет літні",
                ["Michelin", "4 шт.", "до 6000 грн за шину", "RunFlat", "літні"],
            ),
        ],
    )
    def test_every_collected_field_is_rendered(self, text: str, visible: list[str]) -> None:
        h = _pipeline_harness(SALES_ON)
        h.session.tire_query = merge_tire_query({}, text)
        prompt = _prompt_from_session(h)
        assert "Підбір шин: прогрес" in prompt
        for fragment in visible:
            assert fragment in prompt

    def test_unknown_season_is_pointed_at(self) -> None:
        h = _pipeline_harness(SALES_ON)
        h.session.tire_query = merge_tire_query({}, "потрібні шини 205/55 R16")
        assert "Сезон: ⏳" in _prompt_from_session(h)

    def test_sales_off_no_block_even_with_state(self) -> None:
        h = _pipeline_harness(SALES_OFF)
        h.session.tire_query = {"sizes": ["205/55 R16"]}
        assert h.pipeline._build_tire_progress() is None
        assert _prompt_from_session(h) == prompts.build_system_prompt_with_context(
            "BASE", is_modular=False
        )


class TestPipelineWiring:
    """The call site in `_transcript_processor_loop`, through a real turn."""

    @pytest.mark.asyncio
    async def test_turn_collects_and_hands_the_block_to_the_llm(self) -> None:
        h = _pipeline_harness(SALES_ON)
        with fsm_flags(enabled=False):
            await h.run("потрібні шиповані 235/55 R19")
        assert h.session.tire_query["nail"] == "studded"
        assert h.llm_kwargs[-1]["tire_progress"]["season"] == "winter"

    @pytest.mark.asyncio
    async def test_sales_off_turn_is_untouched(self) -> None:
        h = _pipeline_harness(SALES_OFF)
        with fsm_flags(enabled=False):
            await h.run("потрібні шиповані 235/55 R19")
        assert h.session.tire_query == {}
        assert "tire_progress" not in h.llm_kwargs[-1]

    def test_parser_is_not_inside_the_fsm_step(self) -> None:
        import inspect

        from src.core.pipeline import CallPipeline

        src = inspect.getsource(CallPipeline._run_fsm_deterministic_step)
        assert "merge_tire_query" not in src and "_run_tire_query_step" not in src
        assert not FSM_VOICE_STATES


# ── season guard ───────────────────────────────────────────────────────


def _router(session: CallSession, policy: NetworkPolicy) -> tuple[Any, Any]:
    from src.main import _build_tool_router

    store = create_autospec(StoreClient, instance=True)
    store.search_tires.return_value = {"total": 0, "items": []}
    return _build_tool_router(session, store_client=store, network_policy=policy), store


ARGS = {"width": 205, "profile": 55, "diameter": 16, "season": "winter"}


class TestSeasonGuard:
    @pytest.mark.asyncio
    async def test_season_the_llm_filled_in_is_refused(self) -> None:
        session = CallSession(uuid.uuid4())
        session.tire_query = {"sizes": ["205/55 R16"]}
        router, store = _router(session, SALES_ON)
        with patch("src.main._call_logger", None):
            result = await router.execute("search_tires", dict(ARGS))
        assert result["error"] is True and result["reason"] == "season_unknown"
        store.search_tires.assert_not_awaited()

    def test_empty_season_is_not_a_season(self) -> None:
        assert tire_season_refusal({"season": ""}, guard_fired=False) is not None
        assert tire_season_refusal({"season": "  "}, guard_fired=False) is not None

    @pytest.mark.parametrize("season", ["winter", "summer", "all_season", "any"])
    def test_callers_season_passes(self, season: str) -> None:
        assert tire_season_refusal({"season": season}, guard_fired=False) is None

    @pytest.mark.asyncio
    async def test_refuses_once_per_call(self) -> None:
        session = CallSession(uuid.uuid4())
        router, store = _router(session, SALES_ON)
        with patch("src.main._call_logger", None):
            first = await router.execute("search_tires", dict(ARGS))
            second = await router.execute("search_tires", dict(ARGS))
        assert first.get("error") is True
        assert session.tire_season_guard_fired is True
        assert second == {"total": 0, "items": []}
        store.search_tires.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_callers_season_goes_through(self) -> None:
        session = CallSession(uuid.uuid4())
        session.tire_query = {"season": "winter"}
        router, store = _router(session, SALES_ON)
        with patch("src.main._call_logger", None):
            await router.execute("search_tires", dict(ARGS))
        store.search_tires.assert_awaited_once()
        assert session.tire_season_guard_fired is False

    @pytest.mark.asyncio
    async def test_no_guard_while_sales_are_off(self) -> None:
        session = CallSession(uuid.uuid4())
        router, store = _router(session, SALES_OFF)
        with patch("src.main._call_logger", None):
            await router.execute("search_tires", dict(ARGS))
        store.search_tires.assert_awaited_once()
        assert session.tire_season_guard_fired is False


# ── prompt modules ─────────────────────────────────────────────────────


class TestPromptModules:
    def test_fitting_only_prompt_unchanged(self) -> None:
        prompt = prompts.assemble_prompt(scenario="tire_search", network_policy=SALES_OFF)
        for mod in (prompts._MOD_TIRE_SEARCH, prompts._MOD_CONSULTATION, prompts._MOD_OBJECTIONS):
            assert mod in prompt
        assert prompts._MOD_TIRE_SEARCH_SALES not in prompt

    def test_sales_prompt_uses_the_variants(self) -> None:
        prompt = prompts.assemble_prompt(scenario="sales", network_policy=SALES_ON)
        assert prompts._MOD_TIRE_SEARCH_SALES in prompt
        assert prompts._MOD_TIRE_SEARCH not in prompt
        assert prompts._MOD_OBJECTIONS_SALES in prompt
        assert prompts._MOD_CONSULTATION_SALES in prompt

    def test_non_stock_size_goes_to_a_specialist(self) -> None:
        assert "підбирає спеціаліст" in prompts._MOD_TIRE_SEARCH_SALES
        assert "альт. профілем" not in prompts._MOD_TIRE_SEARCH_SALES
        assert "за 1 шину" in prompts._MOD_TIRE_SEARCH_SALES

    def test_no_fitting_offer_after_an_order(self) -> None:
        assert "записатися на шиномонтаж" in prompts._MOD_OBJECTIONS
        assert "записатися на шиномонтаж" not in prompts._MOD_OBJECTIONS_SALES

    def test_wheels_and_fitting_categories(self) -> None:
        from src.knowledge.categories import CATEGORY_VALUES

        for category in ("wheels", "fitting"):
            assert category in CATEGORY_VALUES
            assert f'category="{category}"' in prompts._MOD_CONSULTATION_SALES

    def test_expansion_does_not_bring_back_the_original(self) -> None:
        extra = prompts.infer_expanded_modules("fitting", {"search_tires"}, SALES_ON) or []
        assert prompts._MOD_TIRE_SEARCH not in extra
        assert prompts._MOD_TIRE_SEARCH_SALES in extra
