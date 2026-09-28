"""The wheel fit verdict is said by the code; «підходять» without data is dropped.

Goldset №4 (2026-09-28): 3 of 3 wheel runs with a car — the bot called
`search_disks` but did not say the verdict, or said «підходять» although
every wheel came back ``cannot_confirm``. Now the verdict (``fit.text`` of
the result) is spoken by each loop, on the model's own call and on the
substitution road alike, and the model's compatibility claim is dropped when
nothing could be confirmed.

The result shapes are the goldset mocks (`consult.yaml`), which are the live
shape of `StoreClient.search_disks`; `fit` is checked against
`FitVerdict.as_dict` so the two cannot drift apart.
"""

from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, create_autospec

import pytest

from scripts.configure_tenants import TVOYA_SHINA_CONFIG_PATCH
from src.agent import disk_fitment as fit
from src.agent.agent import LLMAgent, ToolRouter
from src.agent.disk_fit_claim_guard import (
    DiskFitClaimState,
    drop_fit_claims,
    drop_fit_claims_text,
    is_fit_claim,
)
from src.agent.network_policy import NetworkPolicy
from src.agent.streaming_loop import StreamingAgentLoop
from src.agent.tool_result_compressor import (
    DISK_NO_CAR_DATA_PHRASE,
    _compact,
    compress_tool_result,
    disk_caveat_phrase,
)
from src.core.sentence_buffer import SentenceReady
from src.llm.models import (
    LLMResponse,
    StreamDone,
    TextDelta,
    ToolCall,
    ToolCallDelta,
    ToolCallEnd,
    ToolCallStart,
    Usage,
)
from src.llm.router import LLMRouter
from tests.unit.mocks.mock_audio_socket import MockAudioSocketConnection
from tests.unit.mocks.mock_llm_router import MockLLMRouter
from tests.unit.mocks.mock_tts import MockTTSEngine

_CONSULT = Path(__file__).resolve().parents[1] / "goldset" / "cases" / "consult.yaml"


def _item(id_: str, brand: str, model: str, size: str, price: int, status: str) -> dict[str, Any]:
    width, pcd, et, dia = size.split()
    return {
        "id": id_,
        "brand": brand,
        "model": model,
        "size": size,
        "color": "silver",
        "diameter": int(width.split("x")[1]),
        "width": width.split("x")[0],
        "pcd": pcd,
        "et": et.removeprefix("ET"),
        "dia": dia.removeprefix("DIA"),
        "price": price,
        "in_stock": True,
        "fit": fit.FitVerdict(status).as_dict(),
    }


#: `disk_fit_by_car_verdict` of the goldset.
FITS: dict[str, Any] = {
    "total": 2,
    "vehicle": {"found": True, "brand": "Skoda", "model": "Octavia", "year": 2018},
    "fit_policy": "no_spacers_no_redrilling",
    "items": [
        _item("d1", "Replay", "VV150", "6.5x16 5x112 ET46 DIA57.1", 4200, fit.FITS),
        _item("d2", "Kosei", "K1", "7x16 5x112 ET45 DIA66.6", 3600, fit.FITS_WITH_RINGS),
    ],
}
#: `disk_no_car_data_cannot_confirm` of the goldset.
NO_DATA: dict[str, Any] = {
    "total": 2,
    "vehicle": {"found": False, "brand": "ЗАЗ", "model": "Таврія"},
    "items": [
        _item("d7", "Replay", "LF25", "5x13 4x98 ET35 DIA58.6", 2100, fit.CANNOT_CONFIRM),
        _item("d8", "Kosei", "K8", "5.5x13 4x98 ET38 DIA58.6", 1900, fit.CANNOT_CONFIRM),
    ],
}
AMBIGUOUS = {
    "total": 0,
    "items": [],
    "vehicle": {"found": True, "brand": "VW", "model": "Passat", "status": fit.AMBIGUOUS_CAR},
    "need": "vehicle_year_or_modification",
}

FITS_PHRASE = (
    "Щодо сумісності з вашим авто: Replay VV150 — підходить; "
    "Kosei K1 — підходить з центрувальними кільцями."
)
CLAIM = "Підходять для Таврії."
OFFER = "Є Replay LF25 за 2100 гривень."


def _policy(sales: bool) -> NetworkPolicy:
    return NetworkPolicy.from_tenant_config({**TVOYA_SHINA_CONFIG_PATCH, "sales_enabled": sales})


# ── the goldset shapes are the live shapes ─────────────────────────────


@pytest.mark.parametrize(
    ("case_id", "shape"),
    [("disk_fit_by_car_verdict", FITS), ("disk_no_car_data_cannot_confirm", NO_DATA)],
)
def test_shapes_are_the_goldset_mocks(case_id: str, shape: dict[str, Any]) -> None:
    yaml = pytest.importorskip("yaml")
    cases = yaml.safe_load(_CONSULT.read_text(encoding="utf-8"))
    mock = next(c for c in cases if c["id"] == case_id)["mocks"]["search_disks"]
    for got, want in zip(mock["items"], shape["items"], strict=True):
        assert got["fit"]["status"] == want["fit"]["status"]
        assert got["fit"]["text"] == want["fit"]["text"]
        assert (got["brand"], got["model"]) == (want["brand"], want["model"])
    assert mock["vehicle"] == shape["vehicle"]


# ── the phrase ─────────────────────────────────────────────────────────


class TestDiskCaveatPhrase:
    def test_fits_and_rings_per_item_from_the_result(self) -> None:
        assert disk_caveat_phrase(FITS) == FITS_PHRASE

    def test_text_is_taken_from_the_result_not_composed(self) -> None:
        result = copy.deepcopy(FITS)
        result["items"][0]["fit"]["text"] = "ТЕКСТ-З-РЕЗУЛЬТАТУ"
        phrase = disk_caveat_phrase(result)
        assert phrase is not None
        assert "Replay VV150 — ТЕКСТ-З-РЕЗУЛЬТАТУ" in phrase

    def test_all_cannot_confirm(self) -> None:
        assert disk_caveat_phrase(NO_DATA) == DISK_NO_CAR_DATA_PHRASE
        assert "не можу підтвердити" in DISK_NO_CAR_DATA_PHRASE

    def test_car_not_found_wins_over_item_verdicts(self) -> None:
        result = copy.deepcopy(FITS)
        result["vehicle"]["found"] = False
        assert disk_caveat_phrase(result) == DISK_NO_CAR_DATA_PHRASE

    def test_mixed_verdicts_are_listed_per_item(self) -> None:
        result = copy.deepcopy(FITS)
        result["items"][1]["fit"] = fit.FitVerdict(fit.CANNOT_CONFIRM).as_dict()
        phrase = disk_caveat_phrase(result)
        assert phrase is not None
        assert "Replay VV150 — підходить;" in phrase
        assert f"Kosei K1 — {fit.VERDICT_TEXT_UK[fit.CANNOT_CONFIRM]}." in phrase

    def test_ambiguous_car_asks_once(self) -> None:
        phrase = disk_caveat_phrase(AMBIGUOUS)
        assert phrase == "Уточніть рік або модифікацію авто."

    def test_no_car_in_the_call_says_nothing(self) -> None:
        result = copy.deepcopy(FITS)
        del result["vehicle"]
        for item in result["items"]:
            del item["fit"]
        assert disk_caveat_phrase(result) is None

    @pytest.mark.parametrize(
        "result",
        [None, "text", {"error": "diameter_required", "items": []}, {"vehicle": {}, "items": []}],
    )
    def test_nothing_to_say(self, result: Any) -> None:
        assert disk_caveat_phrase(result) is None


class TestCompressor:
    def test_sales_on_tells_the_model_it_was_said(self) -> None:
        content = compress_tool_result("search_disks", FITS, sales_enabled=True)
        assert "caveat_already_said" in content
        assert FITS_PHRASE in content
        assert "Replay" in content

    def test_sales_off_is_incumbent(self) -> None:
        assert compress_tool_result("search_disks", FITS, sales_enabled=False) == _compact(FITS)

    def test_no_phrase_no_mark(self) -> None:
        plain = {"total": 0, "items": []}
        assert compress_tool_result("search_disks", plain, sales_enabled=True) == _compact(plain)


# ── the claim guard ─────────────────────────────────────────────────────


class TestIsFitClaim:
    @pytest.mark.parametrize(
        "sentence",
        [
            "Підходять для Таврії.",
            "Ці диски вам підійдуть.",
            "Replay LF25 підходить на ваше авто.",
            "Эти диски подойдут на Таврию.",
        ],
    )
    def test_claims(self, sentence: str) -> None:
        assert is_fit_claim(sentence)

    @pytest.mark.parametrize(
        "sentence",
        [
            "Не можу підтвердити, що підходять.",
            "Цей диск не підходить.",
            "Чи підійдуть вони — перевіримо на шиномонтажі.",
            "Хочете дізнатись, чи підходять?",
            "Підійдуть вам такі за ціною?",
            "Є Replay LF25 за 2100 гривень.",
        ],
    )
    def test_not_claims(self, sentence: str) -> None:
        assert not is_fit_claim(sentence)


def _state(result: Any) -> DiskFitClaimState:
    state = DiskFitClaimState()
    state.note(result)
    return state


class TestState:
    def test_all_cannot_confirm_is_active(self) -> None:
        assert _state(NO_DATA).active

    def test_any_confirmed_verdict_is_not(self) -> None:
        state = _state(NO_DATA)
        state.note(FITS)
        assert not state.active

    def test_nothing_noted_is_not(self) -> None:
        assert not DiskFitClaimState().active
        assert not _state(AMBIGUOUS).active


class TestDropText:
    def test_drops_the_claim_keeps_the_rest(self) -> None:
        out = drop_fit_claims_text(f"{CLAIM} {OFFER}", _state(NO_DATA))
        assert out == OFFER

    def test_keeps_disclaimer_and_negation(self) -> None:
        text = "Не можу підтвердити, що підходять. Цей диск не підходить."
        assert drop_fit_claims_text(text, _state(NO_DATA)) == text

    def test_inactive_leaves_the_claim(self) -> None:
        text = f"{CLAIM} {OFFER}"
        assert drop_fit_claims_text(text, _state(FITS)) == text


async def _collect(events: list[Any], state: DiskFitClaimState) -> list[str]:
    async def _gen():
        for e in events:
            yield e

    return [e.text async for e in drop_fit_claims(_gen(), state) if isinstance(e, SentenceReady)]


class TestDropStream:
    def test_drops_a_claim_sentence(self) -> None:
        events = [SentenceReady(CLAIM), SentenceReady(OFFER)]
        assert asyncio.run(_collect(events, _state(NO_DATA))) == [OFFER]

    def test_fragments_judged_as_one_sentence(self) -> None:
        events = [SentenceReady("Не можу підтвердити,"), SentenceReady("що підходять.")]
        out = asyncio.run(_collect(events, _state(NO_DATA)))
        assert out == ["Не можу підтвердити,", "що підходять."]

    def test_inactive_passes_through(self) -> None:
        events = [SentenceReady(CLAIM)]
        assert asyncio.run(_collect(events, _state(FITS))) == [CLAIM]


# ── wiring: both loops, both roads ──────────────────────────────────────

TAVRIA = "диски тринадцятий радіус на Таврію є?"
_TOOL_NAMES = ("search_disks", "search_tires", "get_vehicle_tire_sizes", "search_knowledge_base")
_TOOLS = [{"name": n, "description": "", "input_schema": {"type": "object"}} for n in _TOOL_NAMES]
_OWN_CALL = ("search_disks", {"diameter": 13, "vehicle": {"brand": "ЗАЗ", "model": "Таврія"}})
_SUBSTITUTED = ("search_tires", {"diameter": 13})


def _router(disks: dict[str, Any]) -> tuple[ToolRouter, list[str]]:
    router = ToolRouter()
    ran: list[str] = []
    for name in _TOOL_NAMES:

        async def _run(_name: str = name, **_: Any) -> dict[str, Any]:
            ran.append(_name)
            return copy.deepcopy(disks) if _name == "search_disks" else {"items": []}

        router.register(name, _run)
    return router, ran


def _stream(call: tuple[str, dict[str, Any]], disks: dict[str, Any], *, sales: bool = True):
    name, args = call
    rounds = [
        [
            ToolCallStart(id="s0", name=name),
            ToolCallDelta(id="s0", arguments_chunk=json.dumps(args, ensure_ascii=False)),
            ToolCallEnd(id="s0"),
            StreamDone(stop_reason="tool_use", usage=Usage(1, 1)),
        ],
        [
            TextDelta(text=f"{CLAIM} {OFFER}"),
            StreamDone(stop_reason="end_turn", usage=Usage(1, 1)),
        ],
    ]
    router, ran = _router(disks)
    loop = StreamingAgentLoop(
        llm_router=MockLLMRouter(rounds),
        tool_router=router,
        tts=MockTTSEngine(),
        conn=MockAudioSocketConnection(),
        barge_in_event=asyncio.Event(),
        tools=_TOOLS,
        system_prompt="Test system prompt",
        network_policy=_policy(sales),
    )
    result = asyncio.run(loop.run_turn(TAVRIA, []))
    return result.spoken_text, ran


def _text(call: tuple[str, dict[str, Any]], disks: dict[str, Any], *, sales: bool = True):
    name, args = call
    llm_router = create_autospec(LLMRouter, instance=True)
    llm_router.complete = AsyncMock(
        side_effect=[
            LLMResponse(
                text="",
                tool_calls=[ToolCall(id="t0", name=name, arguments=args)],
                stop_reason="tool_use",
                usage=Usage(1, 1),
                provider="test",
            ),
            LLMResponse(text=f"{CLAIM} {OFFER}", usage=Usage(1, 1), provider="test"),
        ]
    )
    router, ran = _router(disks)
    agent = LLMAgent(
        api_key="test-key",
        system_prompt="base",
        llm_router=llm_router,
        tool_router=router,
        tools=_TOOLS,
        network_policy=_policy(sales),
    )
    text, _ = asyncio.run(agent.process_message(TAVRIA, []))
    return text, ran


LOOPS = pytest.mark.parametrize("run", [_stream, _text], ids=["streaming", "text"])
ROADS = pytest.mark.parametrize("call", [_OWN_CALL, _SUBSTITUTED], ids=["own_call", "substituted"])


@LOOPS
@ROADS
def test_no_data_verdict_said_and_claim_dropped(run: Any, call: Any) -> None:
    said, ran = run(call, NO_DATA)
    assert ran == ["search_disks"]
    assert said.startswith(DISK_NO_CAR_DATA_PHRASE)
    assert CLAIM not in said
    assert OFFER in said


@LOOPS
@ROADS
def test_fit_verdict_said_per_item(run: Any, call: Any) -> None:
    said, _ = run(call, FITS)
    assert said.startswith(FITS_PHRASE)
    # Confirmed verdicts: the guard stays out of the model's words.
    assert CLAIM in said


@LOOPS
def test_sales_off_says_nothing_new(run: Any) -> None:
    said, _ = run(_OWN_CALL, NO_DATA, sales=False)
    assert DISK_NO_CAR_DATA_PHRASE not in said
    assert CLAIM in said
