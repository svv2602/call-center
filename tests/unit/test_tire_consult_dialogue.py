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
from typing import Any, ClassVar
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
        out = json.loads(
            compress_tool_result(
                "search_tires", RELAXED_STUDDED, sales_enabled=True, args={"studded": True}
            )
        )
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

    @pytest.mark.parametrize(
        ("text", "budget"),
        [
            ("а є гума до шести тисяч", {"amount": 6000, "scope": None, "is_cap": True}),
            ("гуму до 6000", {"amount": 6000, "scope": None, "is_cap": True}),
            ("коліс до 5000 гривень", {"amount": 5000, "scope": None, "is_cap": True}),
            ("колеса до 5000 за комплект", {"amount": 5000, "scope": "per_set", "is_cap": True}),
            ("колеса 3000 за колесо", {"amount": 3000, "scope": "per_tire", "is_cap": False}),
        ],
    )
    def test_first_line_budget_with_a_tyre_word_starts(
        self, text: str, budget: dict[str, Any]
    ) -> None:
        # R18 (tshina `762168d31`): «ціною до N» is a budget, even as the first
        # line. These carry no sales word — only the budget start reads them.
        from src.agent.intent_classifier import sales_intents

        assert sales_intents(text) == ()
        assert merge_tire_query({}, text)["budget"] == budget

    def test_first_line_tyres_up_to_n_control(self) -> None:
        # «шини» is already a sales word: started before this change too.
        assert merge_tire_query({}, "а є шини до 6000?") == {
            "budget": {"amount": 6000, "scope": None, "is_cap": True}
        }

    @pytest.mark.parametrize(
        "text",
        [
            "до 6000",
            "ціною до 6000",
            "а є до 6000?",
            "шиномонтаж до 600 гривень",
            "запис до 12",
            "гума 6000",
            "колеса до 12",
            "колеса за 600",
        ],
    )
    def test_first_line_without_a_tyre_word_and_a_capped_budget_does_not_start(
        self, text: str
    ) -> None:
        assert merge_tire_query({}, text) == {}

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


def _talk(*lines: str, state: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """``session.tire_query`` after each caller line, merged turn by turn."""
    states: list[dict[str, Any]] = []
    current = dict(state or {})
    for line in lines:
        current = merge_tire_query(current, line)
        states.append(current)
    return states


class TestBrandQuestionIsNotAFilter:
    """Talk b1e72568 (tshina `0cb563847`): a question or «аналоги X» is not a choice of X."""

    def test_the_talk(self) -> None:
        states = _talk(
            "зимові 235/55 R18",
            "розкажіть детальніше про матадор",
            "а аналоги матадора є?",
            "а ціною до 6000 ще є варіанти?",
        )
        assert [s.get("brands") for s in states] == [None, None, None, None]
        assert states[-1] == {
            "sizes": ["235/55 R18"],
            "season": "winter",
            "budget": {"amount": 6000, "scope": None, "is_cap": True},
        }

    def test_analogs_drop_the_brand_chosen_before(self) -> None:
        states = _talk("зимові 235/55 R18", "хочу матадор", "а аналоги матадора є?")
        assert states[1]["brands"] == ["matador"]
        assert "brands" not in states[2]

    @pytest.mark.parametrize(
        "line",
        [
            "подробнее про матадор",
            "расскажите о матадоре",
            "что за Матадор?",
            "розкажіть про мішлен",
            "що скажете про Michelin?",
        ],
    )
    def test_a_question_keeps_the_brands_collected(self, line: str) -> None:
        start = {"sizes": ["235/55 R18"], "season": "winter", "brands": ["nokian"]}
        assert _talk(line, state=start)[-1]["brands"] == ["nokian"]

    @pytest.mark.parametrize(
        ("line", "slug"),
        [
            ("хочу Matador", "matador"),
            ("давайте Michelin", "michelin"),
            ("Nokian є?", "nokian"),
            ("а давайте мішлен", "michelin"),
            ("возьму Мишлен", "michelin"),
            ("хочу матадор, розкажіть про нього", "matador"),
        ],
    )
    def test_a_choice_is_written_as_before(self, line: str, slug: str) -> None:
        states = _talk("зимові 235/55 R18", line)
        assert states[-1]["brands"] == [slug]

    def test_bare_brand_answering_the_brand_question(self) -> None:
        state = merge_tire_query(
            {"sizes": ["235/55 R18"], "season": "winter"},
            "Michelin",
            last_bot_text="Яку марку розглядаєте?",
        )
        assert state["brands"] == ["michelin"]

    def test_analogs_of_one_of_two(self) -> None:
        start = {"sizes": ["235/55 R18"], "season": "winter"}
        states = _talk("Матадор або Мішлен", "аналоги Matador", state=start)
        assert states[0]["brands"] == ["matador", "michelin"]
        assert states[1]["brands"] == ["michelin"]

    @pytest.mark.parametrize(
        "line",
        [
            "не матадор",
            "без матадора",
            "крім матадора",
            "щось схоже на матадор",
            "кроме матадора",
            "вместо матадора",
            "матадора не хочу",
            "похожие на матадор есть?",
        ],
    )
    def test_negation_and_ru_forms_drop_the_brand(self, line: str) -> None:
        start = {"sizes": ["235/55 R18"], "season": "winter", "brands": ["matador"]}
        assert "brands" not in _talk(line, state=start)[-1]

    def test_instead_of_x_picks_y(self) -> None:
        start = {"sizes": ["235/55 R18"], "season": "winter", "brands": ["matador"]}
        assert _talk("давайте мішлен замість матадора", state=start)[-1]["brands"] == ["michelin"]


class TestForcedSearchAfterAnalogs:
    """``tire_search_gate`` reads the same merge: «аналоги матадора» searches no Matador."""

    _TOOLS: ClassVar[list[dict[str, Any]]] = [{"name": "search_tires"}]

    def _plan(self, *lines: str) -> dict[str, Any] | None:
        from src.agent.tire_search_gate import ForcedTireSearch, accumulate_query

        query: dict[str, Any] = {}
        history: list[dict[str, Any]] = []
        for line in lines:
            query = accumulate_query(query, line, history, ())
            history.append({"role": "user", "content": line})
        return ForcedTireSearch(sales_enabled=True, tools=self._TOOLS).plan(
            query, lines[-1], history
        )

    def test_control_a_chosen_brand_is_searched(self) -> None:
        args = self._plan("зимові шиповані 235/55 R18", "хочу матадор")
        assert args is not None and args["brand"] == "matador"

    @pytest.mark.parametrize(
        "lines",
        [
            ("зимові шиповані 235/55 R18", "хочу матадор", "а аналоги матадора, шиповані?"),
            (
                "зимові шиповані 235/55 R18",
                "розкажіть про матадор",
                "а аналоги матадора, шиповані?",
            ),
            ("зимові шиповані 235/55 R18", "хочу матадор", "а аналоги матадора є?"),
        ],
    )
    def test_no_brand_after_analogs(self, lines: tuple[str, ...]) -> None:
        args = self._plan(*lines)
        assert args is None or "brand" not in args

    def test_analogs_with_a_tyre_word_search_without_the_brand(self) -> None:
        args = self._plan(
            "зимові шиповані 235/55 R18", "хочу матадор", "а аналоги матадора, шиповані?"
        )
        assert args is not None
        assert "brand" not in args


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


# ── load index requirement (wave 7-Q) ─────────────────────────────────

LOAD_INDEX_LINE = "гума 205/65 R16 літня, індекс навантаження 102-105, бренд будь-який"


def _tyre_row(sku: str, brand: str, price: int, **over: Any) -> dict[str, Any]:
    """A row as ``StoreClient._query_tire_rows`` selects it."""
    row: dict[str, Any] = {
        "id": sku,
        "brand": brand,
        "model": "M",
        "size": "205/65 R16",
        "season": "summer",
        "price": price,
        "stock_quantity": 4,
        "runflat": False,
        "eu_fuel": None,
        "eu_wet": None,
        "eu_noise_db": None,
        "attr_studded": None,
        "attr_xl": None,
        "load_index": None,
        "speed_index": None,
        "desc_studded": False,
        "desc_studdable": False,
        "desc_xl": False,
    }
    row.update(over)
    return row


def _offer(*rows: dict[str, Any]) -> dict[str, Any]:
    items = [StoreClient._tire_item(r) for r in rows]
    return {"total": len(items), "items": items}


#: Bridgestone 95 and Firestone 99 — the goldset case `tc_load_index_range_not_met`.
ALL_BELOW = (
    _tyre_row("l1", "Bridgestone", 3900, load_index="95", speed_index="H"),
    _tyre_row("l2", "Firestone", 3300, load_index="99", speed_index="H", attr_xl=True),
)
#: 95 (field), 99 (size tail only), unknown, 104 (field), 109/107 (dual).
MIXED = (
    _tyre_row("m1", "Bridgestone", 3900, load_index="95"),
    _tyre_row("m2", "Firestone", 3300, size="205/65 R16 99H"),
    _tyre_row("m3", "Kumho", 3000),
    _tyre_row("m4", "Michelin", 5200, load_index="104", speed_index="V"),
    _tyre_row("m5", "Nokian", 4800, load_index="109/107"),
)

SEARCH_ARGS = {"width": 205, "profile": 65, "diameter": 16, "season": "summer"}


def _load_router(
    session: CallSession, policy: NetworkPolicy, answer: dict[str, Any]
) -> tuple[Any, Any]:
    from src.main import _build_tool_router

    store = create_autospec(StoreClient, instance=True)
    store.search_tires.return_value = answer
    store.resolve_vehicle_text.return_value = None
    return _build_tool_router(session, store_client=store, network_policy=policy), store


async def _search(session: CallSession, policy: NetworkPolicy, answer: dict[str, Any]) -> Any:
    router, _ = _load_router(session, policy, answer)
    with patch("src.main._call_logger", None):
        return await router.execute("search_tires", dict(SEARCH_ARGS))


class TestLoadIndexMerge:
    def test_requirement_is_collected_without_a_budget(self) -> None:
        state = merge_tire_query({}, LOAD_INDEX_LINE)
        assert state["load_index_min"] == 102
        assert state["sizes"] == ["205/65 R16"] and state["season"] == "summer"
        state = merge_tire_query(state, "індекс навантаження від 102 до 105")
        assert "budget" not in state

    def test_budget_beside_the_index_is_kept(self) -> None:
        state = merge_tire_query({"season": "summer"}, "індекс навантаження 102, до 5000 грн")
        assert state["load_index_min"] == 102
        assert state["budget"] == {"amount": 5000, "scope": None, "is_cap": True}

    def test_newer_value_overwrites(self) -> None:
        state = merge_tire_query({"season": "summer", "load_index_min": 102}, "нагрузка не ниже 98")
        assert state["load_index_min"] == 98

    def test_not_important_drops_it(self) -> None:
        state = merge_tire_query({"season": "summer", "load_index_min": 102}, "індекс не важливий")
        assert "load_index_min" not in state

    def test_bare_number_is_not_a_requirement(self) -> None:
        state = merge_tire_query({"season": "summer"}, "102")
        assert "load_index_min" not in state

    @pytest.mark.asyncio
    async def test_sales_off_turn_collects_nothing(self) -> None:
        h = _pipeline_harness(SALES_OFF)
        with fsm_flags(enabled=False):
            await h.run(LOAD_INDEX_LINE)
        assert h.session.tire_query == {}
        assert "tire_progress" not in h.llm_kwargs[-1]


class TestLoadIndexProgressBlock:
    def test_row_rendered_from_session_state(self) -> None:
        h = _pipeline_harness(SALES_ON)
        h.session.tire_query = merge_tire_query({}, LOAD_INDEX_LINE)
        assert "- Індекс навантаження: ✅ від 102" in _prompt_from_session(h)

    def test_no_row_without_requirement(self) -> None:
        h = _pipeline_harness(SALES_ON)
        h.session.tire_query = merge_tire_query({}, "гума 205/65 R16 літня")
        assert "Індекс навантаження" not in _prompt_from_session(h)

    @pytest.mark.asyncio
    async def test_turn_collects_and_hands_it_to_the_llm(self) -> None:
        h = _pipeline_harness(SALES_ON)
        with fsm_flags(enabled=False):
            await h.run(LOAD_INDEX_LINE)
        assert h.session.tire_query["load_index_min"] == 102
        assert h.llm_kwargs[-1]["tire_progress"]["load_index_min"] == 102


class TestLoadIndexFilter:
    @pytest.mark.asyncio
    async def test_below_dropped_unknown_kept(self) -> None:
        session = CallSession(uuid.uuid4())
        session.tire_query = {"season": "summer", "load_index_min": 102}
        result = await _search(session, SALES_ON, _offer(*MIXED))
        assert [i["id"] for i in result["items"]] == ["m3", "m4", "m5"]
        assert result["total"] == 3
        assert "load_index_below" not in result
        # The caller heard the filtered offer — that is what is remembered.
        assert session.last_tire_offer["ids"] == ["m3", "m4", "m5"]

    @pytest.mark.asyncio
    async def test_unknown_index_alone_is_offered(self) -> None:
        session = CallSession(uuid.uuid4())
        session.tire_query = {"season": "summer", "load_index_min": 102}
        result = await _search(session, SALES_ON, _offer(MIXED[0], MIXED[2]))
        assert [i["id"] for i in result["items"]] == ["m3"]
        assert "load_index_below" not in result

    @pytest.mark.asyncio
    async def test_none_meets_it_keeps_the_offer_and_flags(self) -> None:
        session = CallSession(uuid.uuid4())
        session.tire_query = {"season": "summer", "load_index_min": 102}
        result = await _search(session, SALES_ON, _offer(*ALL_BELOW))
        assert [i["id"] for i in result["items"]] == ["l1", "l2"]
        assert result["load_index_below"] == {"required": 102, "shown": [95, 99]}
        said = json.loads(compress_tool_result("search_tires", result, sales_enabled=True))
        assert said["load_index_below"]["required"] == 102
        assert "від 102" in said["load_index_below"]["say"]
        assert "95–99" in said["load_index_below"]["say"]

    @pytest.mark.asyncio
    async def test_end_to_end_from_the_callers_words(self) -> None:
        session = CallSession(uuid.uuid4())
        session.tire_query = merge_tire_query({}, LOAD_INDEX_LINE)
        result = await _search(session, SALES_ON, _offer(*ALL_BELOW, MIXED[3]))
        assert [i["id"] for i in result["items"]] == ["m4"]

    @pytest.mark.asyncio
    async def test_without_requirement_byte_identical(self) -> None:
        session = CallSession(uuid.uuid4())
        session.tire_query = {"season": "summer"}
        answer = _offer(*MIXED)
        before = json.dumps(answer, ensure_ascii=False, sort_keys=True)
        result = await _search(session, SALES_ON, answer)
        assert json.dumps(result, ensure_ascii=False, sort_keys=True) == before

    @pytest.mark.asyncio
    async def test_sales_off_byte_identical(self) -> None:
        session = CallSession(uuid.uuid4())
        session.tire_query = {"season": "summer", "load_index_min": 102}
        answer = _offer(*ALL_BELOW, MIXED[3])
        before = json.dumps(answer, ensure_ascii=False, sort_keys=True)
        result = await _search(session, SALES_OFF, answer)
        assert json.dumps(result, ensure_ascii=False, sort_keys=True) == before
