"""Wave 3-F: a network promotion reaches the prompt only on a turn it concerns.

tshina `0813efc4e`: a live promotion is a source only for a relevant request.
Relevant = the caller asks about promotions (discounts, a programme, free
delivery, a damage warranty — ua + ru), or names a brand of the promotion in
this reply or earlier in the selection (the session's ``tire_query``). A
promotion without brands is relevant to the question only. Everything else
default-denies: no block on the turn.

The guard's exemptions (``promo_overrides``) stay built from *every* live
promotion — relevance filters what the model reads, not what it may say.

With sales off, the agents keep the old static ``promotions_context`` string
byte for byte.
"""

from __future__ import annotations

import ast
import asyncio
from datetime import date
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from src.agent import agent as agent_mod
from src.agent import streaming_loop as loop_mod
from src.agent.agent import LLMAgent, ToolRouter
from src.agent.network_policy import NetworkPolicy
from src.agent.promotions import (
    ActivePromotion,
    asks_about_promotions,
    format_promotions_block,
    promo_overrides,
    relevant_promotions,
    turn_promotions_block,
)
from src.agent.streaming_loop import StreamingAgentLoop
from tests.unit.mocks.mock_audio_socket import MockAudioSocketConnection
from tests.unit.mocks.mock_llm_router import MockLLMRouter
from tests.unit.mocks.mock_tts import MockTTSEngine

_REPO = Path(__file__).resolve().parents[2]
_END = date(2026, 12, 31)

DOUBLESTAR = ActivePromotion(
    title="Безкоштовна доставка шин Doublestar та Rydanz",
    bot_text="Безкоштовна доставка на шини Doublestar та Rydanz.",
    valid_to=_END,
    overrides={"free_delivery": True},
    mention_brands=("doublestar", "rydanz"),
)
MATADOR = ActivePromotion(
    title="Подовжена гарантія Matador",
    bot_text="Гарантія від пошкоджень на шини Matador.",
    valid_to=_END,
    overrides={"extended_warranty_brands": ["matador"]},
    mention_brands=("matador",),
)
MICHELIN = ActivePromotion(
    title="Сервісна програма MICHELIN",
    bot_text="Заміна пошкодженої шини MICHELIN.",
    valid_to=_END,
    overrides={"extended_warranty_brands": ["michelin"]},
    mention_brands=("michelin",),
)
PARTNER = ActivePromotion(
    title="Ексклюзивне сервісне обслуговування",
    bot_text="Знижка 25% на шиномонтаж у партнера.",
    valid_to=_END,
    overrides={"discount": True, "partner_service": {"service": "fitting"}},
)
ALL = [DOUBLESTAR, MATADOR, MICHELIN, PARTNER]


def _titles(promos: list[ActivePromotion]) -> set[str]:
    return {p.title for p in promos}


# ── the predicate ─────────────────────────────────────────────────────────


class TestAsksAboutPromotions:
    @pytest.mark.parametrize(
        "text",
        [
            "які у вас зараз акції?",
            "а є якась акція?",
            "какие у вас акции?",
            "есть скидки?",
            "а скидку можно?",
            "чи є знижка на комплект?",
            "знижок немає?",
            "у вас є програма лояльності?",
            "есть какая-то программа?",
            "чи є безкоштовна доставка?",
            "доставка бесплатная?",
            "а доставка безплатна буде?",
            "чи є гарантія від пошкоджень?",
            "есть гарантия от повреждений?",
            "якщо я пошкоджу шину, гарантія діє?",
            "чи є розширена гарантія?",
        ],
    )
    def test_ua_and_ru_asks(self, text: str) -> None:
        assert asks_about_promotions(text) is True

    @pytest.mark.parametrize(
        "text",
        [
            "",
            None,
            "мені потрібні шини двісті п'ять п'ятдесят п'ять шістнадцять",
            "скільки коштує доставка?",
            "яка гарантія на шини?",
            "чи можна у вас змонтувати шини?",
            "нужны зимние шины на Шкоду",
        ],
    )
    def test_ordinary_turns_are_not_asks(self, text: str | None) -> None:
        assert asks_about_promotions(text) is False


class TestRelevantPromotions:
    def test_unrelated_brand_brings_no_promotion(self) -> None:
        # goldset promo_pk_unrelated_brand_not_mentioned
        assert relevant_promotions(ALL, "мені потрібні шини Bridgestone") == []

    def test_ordinary_turn_brings_no_promotion(self) -> None:
        assert relevant_promotions(ALL, "а на п'ятнадцятий диск що є?") == []

    def test_latin_brand_brings_only_its_promotion(self) -> None:
        got = relevant_promotions(ALL, "скільки коштує доставка шин Doublestar?")
        assert got == [DOUBLESTAR]

    @pytest.mark.parametrize(
        ("text", "promo"),
        [
            ("а Мішлен є в наявності?", MICHELIN),
            ("хочу мішлена", MICHELIN),
            ("а що по Мишлену?", MICHELIN),
            ("Матадор є?", MATADOR),
            ("нет ли матадора?", MATADOR),
        ],
    )
    def test_cyrillic_brand_and_its_case_forms(self, text: str, promo: ActivePromotion) -> None:
        assert relevant_promotions(ALL, text) == [promo]

    def test_brand_unknown_to_the_parser_as_the_promotion_spells_it(self) -> None:
        assert relevant_promotions(ALL, "а Rydanz є?") == [DOUBLESTAR]

    def test_brand_from_the_session_tire_query(self) -> None:
        # Brand named two turns ago; this turn is «а доставка?».
        got = relevant_promotions(ALL, "а доставка скільки?", {"brands": ["matador"]})
        assert got == [MATADOR]

    def test_tire_query_without_brands_brings_nothing(self) -> None:
        tq = {"width": 205, "profile": 55, "diameter": 16, "season": "winter"}
        assert relevant_promotions(ALL, "а доставка скільки?", tq) == []

    def test_question_about_promotions_brings_every_live_one(self) -> None:
        assert relevant_promotions(ALL, "які у вас є акції?") == ALL
        assert relevant_promotions(ALL, "есть какие-то скидки?") == ALL

    def test_brandless_promotion_only_on_the_question(self) -> None:
        # A fitting question brings the partner fitting promotion (runner fix).
        assert PARTNER in relevant_promotions(ALL, "чи можна у вас змонтувати шини?")
        assert PARTNER not in relevant_promotions(ALL, "Doublestar є?", {"brands": ["matador"]})
        assert PARTNER in relevant_promotions(ALL, "а знижки на монтаж є?")

    def test_no_promotions(self) -> None:
        assert relevant_promotions([], "які акції?") == []
        assert relevant_promotions(None, "які акції?") == []

    def test_turn_block_is_none_when_nothing_is_relevant(self) -> None:
        assert turn_promotions_block(ALL, "мені потрібні шини Bridgestone") is None

    def test_turn_block_holds_only_the_relevant_one(self) -> None:
        block = turn_promotions_block(ALL, "Matador є?")
        assert block == format_promotions_block([MATADOR])
        assert "Doublestar" not in block
        assert "MICHELIN" not in block

    def test_guard_exemptions_still_cover_every_live_promotion(self) -> None:
        # Relevance never narrows what the claim guard lets through.
        assert promo_overrides(ALL).free_delivery_brand_scopes == (
            frozenset({"doublestar", "rydanz"}),
        )
        assert promo_overrides(ALL).extended_warranty_brands == frozenset({"matador", "michelin"})


# ── the wiring: each agent builds the block per turn ─────────────────────


class _StopError(Exception):
    pass


def _capture(monkeypatch: pytest.MonkeyPatch, module: Any) -> list[Any]:
    seen: list[Any] = []

    def _build(*_a: Any, **kwargs: Any) -> str:
        seen.append(kwargs.get("promotions_context"))
        raise _StopError

    monkeypatch.setattr(module, "build_system_prompt_with_context", _build)
    return seen


def _sales_policy() -> NetworkPolicy:
    return NetworkPolicy.from_tenant_config({"sales_enabled": True})


def _llm_agent(**kwargs: Any) -> LLMAgent:
    return LLMAgent(api_key="test", tool_router=ToolRouter(), **kwargs)


def _loop(**kwargs: Any) -> StreamingAgentLoop:
    return StreamingAgentLoop(
        llm_router=MockLLMRouter([]),
        tool_router=ToolRouter(),
        tts=MockTTSEngine(),
        conn=MockAudioSocketConnection(),
        barge_in_event=asyncio.Event(),
        system_prompt="Test",
        pii_vault=MagicMock(spec=["mask"], mask=lambda s: s),
        **kwargs,
    )


def _turns_via_agent(monkeypatch: pytest.MonkeyPatch, texts: list[str], **kw: Any) -> list[Any]:
    seen = _capture(monkeypatch, agent_mod)
    agent = _llm_agent(**kw)
    for text in texts:
        with pytest.raises(_StopError):
            asyncio.run(agent.process_message(text, []))
    return seen


def _turns_via_loop(
    monkeypatch: pytest.MonkeyPatch,
    turns: list[tuple[str, dict[str, Any] | None]],
    **kw: Any,
) -> list[Any]:
    seen = _capture(monkeypatch, loop_mod)
    loop = _loop(**kw)
    for text, tq in turns:
        with pytest.raises(_StopError):
            asyncio.run(loop.run_turn(text, [], tire_progress=tq))
    return seen


class TestLLMAgentWiring:
    def test_block_changes_with_the_turn(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen = _turns_via_agent(
            monkeypatch,
            ["мені потрібні шини Bridgestone", "а Матадор є?", "які акції?"],
            promotions=ALL,
            network_policy=_sales_policy(),
        )
        assert seen == [
            None,
            format_promotions_block([MATADOR]),
            format_promotions_block(ALL),
        ]

    def test_sales_off_passes_the_static_string(self, monkeypatch: pytest.MonkeyPatch) -> None:
        static = "\n## Акції (старий шлях)\nтекст"
        seen = _turns_via_agent(
            monkeypatch,
            ["мені потрібні шини Bridgestone", "які акції?"],
            promotions_context=static,
        )
        assert seen == [static, static]


class TestStreamingLoopWiring:
    def test_block_changes_with_the_turn(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen = _turns_via_loop(
            monkeypatch,
            [
                ("мені потрібні шини Bridgestone", None),
                ("а доставка скільки?", {"brands": ["doublestar"]}),
                ("а мішлен є?", None),
            ],
            promotions=ALL,
            network_policy=_sales_policy(),
        )
        assert seen == [
            None,
            format_promotions_block([DOUBLESTAR]),
            format_promotions_block([MICHELIN]),
        ]

    def test_sales_off_passes_the_static_string(self, monkeypatch: pytest.MonkeyPatch) -> None:
        static = "\n## Акції (старий шлях)\nтекст"
        seen = _turns_via_loop(
            monkeypatch,
            [("мені потрібні шини Bridgestone", None), ("які акції?", None)],
            promotions_context=static,
        )
        assert seen == [static, static]


# ── main.py::handle_call hands the list to both agents (AST) ─────────────


def _handle_call() -> ast.AsyncFunctionDef:
    tree = ast.parse((_REPO / "src/main.py").read_text(encoding="utf-8"))
    funcs = [
        n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "handle_call"
    ]
    assert len(funcs) == 1
    return funcs[0]


class TestMainWiring:
    def test_both_agents_get_the_live_list(self) -> None:
        calls = [
            n
            for n in ast.walk(_handle_call())
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id in {"LLMAgent", "StreamingAgentLoop"}
        ]
        assert {c.func.id for c in calls} == {"LLMAgent", "StreamingAgentLoop"}
        for c in calls:
            kw = {k.arg: k.value for k in c.keywords}
            assert isinstance(kw.get("promotions"), ast.Name)
            assert kw["promotions"].id == "live_promotions"

    def test_live_list_only_with_sales(self) -> None:
        assigns = [
            n
            for n in ast.walk(_handle_call())
            if isinstance(n, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "live_promotions" for t in n.targets)
        ]
        assert len(assigns) == 1
        value = assigns[0].value
        assert isinstance(value, ast.IfExp)
        assert isinstance(value.test, ast.Attribute) and value.test.attr == "sales_enabled"
        assert isinstance(value.orelse, ast.Constant) and value.orelse.value is None


class TestPartnerPromotionOnServiceQuestion:
    """Про Колесо's partner promotion (fitting at Твоя Шина) reaches a turn
    asking about fitting — «не надаємо» alone would hide the offer."""

    def _partner(self) -> Any:
        from datetime import date

        from src.agent.promotions import ActivePromotion

        return ActivePromotion(
            title="Ексклюзивне сервісне обслуговування",
            bot_text="Знижка на шиномонтаж у мережі-партнері.",
            valid_to=date(2026, 12, 31),
            overrides={
                "discount": True,
                "partner_service": {"service": "fitting", "network_label": "Твоя Шина"},
            },
            mention_brands=(),
        )

    def test_fitting_question_brings_the_partner_promotion(self) -> None:
        from src.agent.promotions import relevant_promotions

        promo = self._partner()
        assert relevant_promotions([promo], "а можна у вас змонтувати шини?") == [promo]

    def test_unrelated_turn_does_not(self) -> None:
        from src.agent.promotions import relevant_promotions

        assert relevant_promotions([self._partner()], "потрібні літні шини 205/55 R16") == []
