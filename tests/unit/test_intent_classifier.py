"""Unit tests for src.agent.intent_classifier (Wave 13, T3).

Все LLM вызовы замоканы через AsyncMock — реальный провайдер не бьётся.

Классы тестов:
- TestPrimaryIntents         — 15+ «чистых» фраз на каждый intent.
- TestSTTGarbage             — 10+ мутаций из waves 3-12 + сегодняшних anchor calls.
- TestCompoundIntents        — BOOK+PRICE, RESCHEDULE, CANCEL+переспрос.
- TestExtractedFields        — city/diameter/station/date вместе с intent.
- TestAmbiguousClarification — requires_clarification=True + question.
- TestFallback               — LLM timeout / JSON error / invalid payload.
"""

from __future__ import annotations

import asyncio
import json
import logging
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.agent.intent_classifier import (
    ExtractedFields,
    IntentResult,
    classify_intent,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _mk_router(payload: dict[str, Any] | str | None = None) -> AsyncMock:
    """AsyncMock, эмулирующий LLMRouter.complete.

    Возвращает объект с .text = JSON-строкой из payload (или готовую строку).
    """
    router = AsyncMock()
    if isinstance(payload, dict):
        text = json.dumps(payload, ensure_ascii=False)
    elif isinstance(payload, str):
        text = payload
    else:
        text = "{}"

    async def _complete(**kwargs: Any) -> Any:
        return SimpleNamespace(text=text)

    router.complete = AsyncMock(side_effect=_complete)
    return router


def _mk_payload(
    *,
    primary: str = "BOOK",
    secondary: list[str] | None = None,
    city: str | None = None,
    diameter: int | None = None,
    station: str | None = None,
    date: str | None = None,
    confidence: float = 0.9,
    requires_clarification: bool = False,
    question: str | None = None,
) -> dict[str, Any]:
    return {
        "primary_intent": primary,
        "secondary_intents": secondary or [],
        "extracted_fields": {
            "city": city,
            "diameter": diameter,
            "station_hint": station,
            "date_hint": date,
        },
        "confidence": confidence,
        "requires_clarification": requires_clarification,
        "clarification_question": question,
    }


def _ctx(
    *,
    step: str | None = None,
    filled: dict[str, Any] | None = None,
    tail: list[str] | None = None,
    tenant: str = "tvoya-shina",
) -> dict[str, Any]:
    return {
        "current_step": step,
        "filled_fields": filled or {},
        "dialog_history_tail": tail or [],
        "tenant": tenant,
    }


@pytest.fixture
def mock_llm_router() -> AsyncMock:
    """Дефолтный мок — вернёт BOOK с confidence=0.9."""
    return _mk_router(_mk_payload(primary="BOOK", confidence=0.9))


# ---------------------------------------------------------------------------
# TestPrimaryIntents — 15+ тестов, 3+ на каждый intent
# ---------------------------------------------------------------------------


class TestPrimaryIntents:
    """Чистые фразы, LLM возвращает каноничный intent с confidence≥0.9."""

    # BOOK ---------------------------------------------------------------

    async def test_book_hochu_zapysatysya(self) -> None:
        router = _mk_router(_mk_payload(primary="BOOK", confidence=0.95))
        result = await classify_intent("хочу записатися на шиномонтаж", _ctx(), router)
        assert result.primary_intent == "BOOK"
        assert result.confidence == pytest.approx(0.95)
        assert result.requires_clarification is False

    async def test_book_pereobutysya(self) -> None:
        router = _mk_router(_mk_payload(primary="BOOK", confidence=0.9))
        result = await classify_intent("мені треба переобутися", _ctx(), router)
        assert result.primary_intent == "BOOK"

    async def test_book_na_zavtra(self) -> None:
        router = _mk_router(_mk_payload(primary="BOOK", confidence=0.92, date="завтра"))
        result = await classify_intent("запишіть на завтра", _ctx(), router)
        assert result.primary_intent == "BOOK"
        assert result.extracted_fields.date_hint == "завтра"

    # PRICE --------------------------------------------------------------

    async def test_price_skilky_koshtue(self) -> None:
        router = _mk_router(_mk_payload(primary="PRICE", confidence=0.93))
        result = await classify_intent("скільки коштує шиномонтаж", _ctx(), router)
        assert result.primary_intent == "PRICE"

    async def test_price_pidkazhit_vartist(self) -> None:
        router = _mk_router(_mk_payload(primary="PRICE", confidence=0.9))
        result = await classify_intent("підкажіть вартість", _ctx(), router)
        assert result.primary_intent == "PRICE"

    async def test_price_yaka_tsina_r17(self) -> None:
        router = _mk_router(_mk_payload(primary="PRICE", confidence=0.94, diameter=17))
        result = await classify_intent("яка ціна монтажу R17", _ctx(), router)
        assert result.primary_intent == "PRICE"
        assert result.extracted_fields.diameter == 17

    # CANCEL -------------------------------------------------------------

    async def test_cancel_skasuvaty(self) -> None:
        router = _mk_router(_mk_payload(primary="CANCEL", confidence=0.95))
        result = await classify_intent("хочу скасувати запис", _ctx(), router)
        assert result.primary_intent == "CANCEL"

    async def test_cancel_vidminyty(self) -> None:
        router = _mk_router(_mk_payload(primary="CANCEL", confidence=0.9))
        result = await classify_intent("відмінити", _ctx(), router)
        assert result.primary_intent == "CANCEL"

    async def test_cancel_prybraty_bron(self) -> None:
        router = _mk_router(_mk_payload(primary="CANCEL", confidence=0.88))
        result = await classify_intent("прибрати мою бронь", _ctx(), router)
        assert result.primary_intent == "CANCEL"

    # RESCHEDULE ---------------------------------------------------------

    async def test_reschedule_perenesti(self) -> None:
        router = _mk_router(_mk_payload(primary="RESCHEDULE", confidence=0.92))
        result = await classify_intent("перенести запис", _ctx(), router)
        assert result.primary_intent == "RESCHEDULE"

    async def test_reschedule_zminyty_chas(self) -> None:
        router = _mk_router(_mk_payload(primary="RESCHEDULE", confidence=0.9))
        result = await classify_intent("змінити час", _ctx(), router)
        assert result.primary_intent == "RESCHEDULE"

    async def test_reschedule_pereprusnachyty(self) -> None:
        router = _mk_router(_mk_payload(primary="RESCHEDULE", confidence=0.9))
        result = await classify_intent("перепризначити на завтра", _ctx(), router)
        assert result.primary_intent == "RESCHEDULE"

    # TRANSFER -----------------------------------------------------------

    async def test_transfer_operator(self) -> None:
        router = _mk_router(_mk_payload(primary="TRANSFER", confidence=0.97))
        result = await classify_intent("дайте оператора", _ctx(), router)
        assert result.primary_intent == "TRANSFER"

    async def test_transfer_z_lyudynoyu(self) -> None:
        router = _mk_router(_mk_payload(primary="TRANSFER", confidence=0.9))
        result = await classify_intent("з людиною поспілкуватися", _ctx(), router)
        assert result.primary_intent == "TRANSFER"

    async def test_transfer_menedzher(self) -> None:
        router = _mk_router(_mk_payload(primary="TRANSFER", confidence=0.9))
        result = await classify_intent("менеджер", _ctx(), router)
        assert result.primary_intent == "TRANSFER"


# ---------------------------------------------------------------------------
# TestSTTGarbage — 10+ мутированных фраз из waves 3-5 + anchor calls 2026-09-07
# ---------------------------------------------------------------------------


class TestSTTGarbage:
    """STT-огрызки — LLM должна распарсить, модуль — пробросить."""

    async def test_selsky_korzh_shinomontazh(self) -> None:
        """anchor: «сельский Корж шиномонтаж на Запорізьке шосе» → BOOK/PRICE ambiguous."""
        router = _mk_router(
            _mk_payload(
                primary="BOOK",
                secondary=["PRICE"],
                station="Запорізьке шосе",
                confidence=0.55,  # намеренно ниже порога → clarification
                requires_clarification=True,
                question="Хочете записатися чи дізнатися вартість?",
            )
        )
        result = await classify_intent(
            "сельский Корж шиномонтаж на Запорізьке шосе", _ctx(), router
        )
        # Primary после reorder: PRICE > BOOK → PRICE
        assert result.primary_intent == "PRICE"
        assert "BOOK" in result.secondary_intents
        assert result.extracted_fields.station_hint == "Запорізьке шосе"
        assert result.requires_clarification is True

    async def test_ne_peska_zhyt_v_artyst_montazhu(self) -> None:
        """anchor: «не песка жить в артист монтажу На Харьковский» → PRICE."""
        router = _mk_router(_mk_payload(primary="PRICE", confidence=0.7, station="Харківське шосе"))
        result = await classify_intent(
            "не песка жить в артист монтажу На Харьковский", _ctx(), router
        )
        assert result.primary_intent == "PRICE"

    async def test_v_kasovatyj_zapusk(self) -> None:
        """anchor: «в кассоватый запуск» → CANCEL."""
        router = _mk_router(_mk_payload(primary="CANCEL", confidence=0.75))
        result = await classify_intent("в кассоватый запуск", _ctx(), router)
        assert result.primary_intent == "CANCEL"

    async def test_yakoby_est_montazhu_ervy_17_u_dnepre(self) -> None:
        """anchor: «якобы есть монтажу эрви 17 у Днепре» → BOOK+PRICE compound."""
        router = _mk_router(
            _mk_payload(
                primary="BOOK",
                secondary=["PRICE"],
                city="Дніпро",
                diameter=17,
                confidence=0.8,
            )
        )
        result = await classify_intent("якобы есть монтажу эрви 17 у Днепре", _ctx(), router)
        # Primary после reorder: PRICE > BOOK
        assert result.primary_intent == "PRICE"
        assert "BOOK" in result.secondary_intents
        assert result.extracted_fields.city == "Дніпро"
        assert result.extracted_fields.diameter == 17

    async def test_belyak_karavanu_momentu(self) -> None:
        """anchor: «беляк каравану моменту зашлифовывает» → BOOK + station_hint=Караван."""
        router = _mk_router(_mk_payload(primary="BOOK", confidence=0.72, station="Караван"))
        result = await classify_intent("беляк каравану моменту зашлифовывает", _ctx(), router)
        assert result.primary_intent == "BOOK"
        assert result.extracted_fields.station_hint == "Караван"

    async def test_mne_potrebnosty_s_montazhu(self) -> None:
        """anchor: «мне потребности с монтажу» → requires_clarification."""
        router = _mk_router(
            _mk_payload(
                primary="BOOK",
                confidence=0.4,
                requires_clarification=True,
                question="Уточніть, будь ласка: записатися чи дізнатися вартість?",
            )
        )
        result = await classify_intent("мне потребности с монтажу", _ctx(), router)
        assert result.requires_clarification is True
        assert result.clarification_question is not None
        assert len(result.clarification_question) > 0

    async def test_trogat_nadpys_na_montazh_v_kyevi(self) -> None:
        """anchor: «трогать надпис на монтаж в Києві» → BOOK + city=Київ."""
        router = _mk_router(_mk_payload(primary="BOOK", confidence=0.7, city="Київ"))
        result = await classify_intent("трогать надпис на монтаж в Києві", _ctx(), router)
        assert result.primary_intent == "BOOK"
        assert result.extracted_fields.city == "Київ"

    async def test_vesnoy_svarky_v_dnipri(self) -> None:
        """anchor: «весной сварки в Дніпрі» → PRICE."""
        router = _mk_router(_mk_payload(primary="PRICE", confidence=0.65, city="Дніпро"))
        result = await classify_intent("весной сварки в Дніпрі", _ctx(), router)
        assert result.primary_intent == "PRICE"
        assert result.extracted_fields.city == "Дніпро"

    async def test_my_ne_trogat_nadpys_tynu(self) -> None:
        """anchor: «мы не трогать надпись Тину на монтаж» → BOOK."""
        router = _mk_router(_mk_payload(primary="BOOK", confidence=0.7))
        result = await classify_intent("мы не трогать надпись Тину на монтаж", _ctx(), router)
        assert result.primary_intent == "BOOK"

    async def test_perenos_z_ponedilka_na_seredu(self) -> None:
        """STT-friendly: «перенос з понеділка на середу» → RESCHEDULE."""
        router = _mk_router(_mk_payload(primary="RESCHEDULE", confidence=0.85))
        result = await classify_intent("перенос з понеділка на середу", _ctx(), router)
        assert result.primary_intent == "RESCHEDULE"


# ---------------------------------------------------------------------------
# TestCompoundIntents — 5+ тестов
# ---------------------------------------------------------------------------


class TestCompoundIntents:
    """Compound: несколько intent'ов в одной фразе, priority reorder."""

    async def test_shynomontazh_i_tsinu(self) -> None:
        """«шиномонтаж і ціну» → primary=PRICE, secondary=[BOOK]."""
        router = _mk_router(_mk_payload(primary="BOOK", secondary=["PRICE"], confidence=0.85))
        result = await classify_intent("шиномонтаж і ціну", _ctx(), router)
        assert result.primary_intent == "PRICE"
        assert result.secondary_intents == ["BOOK"]

    async def test_reschedule_wins_over_book(self) -> None:
        """RESCHEDULE + BOOK → primary=RESCHEDULE."""
        router = _mk_router(_mk_payload(primary="BOOK", secondary=["RESCHEDULE"], confidence=0.85))
        result = await classify_intent("перенести з понеділка на середу", _ctx(), router)
        assert result.primary_intent == "RESCHEDULE"
        assert result.secondary_intents == ["BOOK"]

    async def test_cancel_wins_over_book(self) -> None:
        """«скасувати запис і записати на іншу дату» → primary=CANCEL, secondary=[BOOK]."""
        router = _mk_router(_mk_payload(primary="BOOK", secondary=["CANCEL"], confidence=0.85))
        result = await classify_intent("скасувати запис і записати на іншу дату", _ctx(), router)
        assert result.primary_intent == "CANCEL"
        assert "BOOK" in result.secondary_intents

    async def test_transfer_wins_over_everything(self) -> None:
        """TRANSFER всегда primary (эскалация)."""
        router = _mk_router(
            _mk_payload(primary="BOOK", secondary=["PRICE", "TRANSFER"], confidence=0.9)
        )
        result = await classify_intent("хочу шиномонтаж і ціну, а краще оператора", _ctx(), router)
        assert result.primary_intent == "TRANSFER"

    async def test_secondary_deduped_and_capped(self) -> None:
        """secondary не должен дублировать primary + max 2."""
        router = _mk_router(
            _mk_payload(
                primary="BOOK",
                secondary=["BOOK", "PRICE", "PRICE"],  # мусор от LLM
                confidence=0.9,
            )
        )
        result = await classify_intent("щось нейтральне", _ctx(), router)
        # PRICE > BOOK по приоритету
        assert result.primary_intent == "PRICE"
        assert result.secondary_intents == ["BOOK"]
        assert len(result.secondary_intents) <= 2


# ---------------------------------------------------------------------------
# TestExtractedFields — 5+ тестов
# ---------------------------------------------------------------------------


class TestExtractedFields:
    """Проверяем извлечение city / diameter / station / date."""

    async def test_book_r18_v_dnipri(self) -> None:
        """«шиномонтаж R18 в Дніпрі» → BOOK + city=Дніпро + diameter=18."""
        router = _mk_router(
            _mk_payload(primary="BOOK", city="Дніпро", diameter=18, confidence=0.95)
        )
        result = await classify_intent("шиномонтаж R18 в Дніпрі", _ctx(), router)
        assert result.primary_intent == "BOOK"
        assert result.extracted_fields.city == "Дніпро"
        assert result.extracted_fields.diameter == 18

    async def test_price_r17_v_kyevi(self) -> None:
        """«скільки коштує R17 в Києві» → PRICE + city=Київ + diameter=17."""
        router = _mk_router(_mk_payload(primary="PRICE", city="Київ", diameter=17, confidence=0.94))
        result = await classify_intent("скільки коштує R17 в Києві", _ctx(), router)
        assert result.primary_intent == "PRICE"
        assert result.extracted_fields.city == "Київ"
        assert result.extracted_fields.diameter == 17

    async def test_diameter_from_string(self) -> None:
        """LLM возвращает diameter как строку 'R21' → парсим в 21."""
        router = _mk_router(
            _mk_payload(primary="BOOK", diameter="R21", confidence=0.9)  # type: ignore[arg-type]
        )
        result = await classify_intent("на R21 хочу", _ctx(), router)
        assert result.extracted_fields.diameter == 21

    async def test_diameter_out_of_range_dropped(self) -> None:
        """diameter=99 → None (вне 13..24)."""
        router = _mk_router(_mk_payload(primary="BOOK", diameter=99, confidence=0.9))
        result = await classify_intent("хочу монтаж", _ctx(), router)
        assert result.extracted_fields.diameter is None

    async def test_all_fields_together(self) -> None:
        """city + diameter + station + date одновременно."""
        router = _mk_router(
            _mk_payload(
                primary="BOOK",
                city="Одеса",
                diameter=16,
                station="Караван",
                date="понеділок",
                confidence=0.9,
            )
        )
        result = await classify_intent(
            "хочу монтаж R16 в Одесі на Караван у понеділок", _ctx(), router
        )
        fields = result.extracted_fields
        assert fields.city == "Одеса"
        assert fields.diameter == 16
        assert fields.station_hint == "Караван"
        assert fields.date_hint == "понеділок"

    async def test_diameter_none_ok(self) -> None:
        """Без diameter — поле остаётся None."""
        router = _mk_router(_mk_payload(primary="BOOK", confidence=0.9))
        result = await classify_intent("хочу записатися", _ctx(), router)
        assert result.extracted_fields.diameter is None


# ---------------------------------------------------------------------------
# TestAmbiguousClarification — 5+ тестов
# ---------------------------------------------------------------------------


class TestAmbiguousClarification:
    """requires_clarification=True + не-пустой clarification_question."""

    async def test_llm_marks_ambiguous(self) -> None:
        """LLM явно вернула requires_clarification=True."""
        router = _mk_router(
            _mk_payload(
                primary="BOOK",
                confidence=0.5,
                requires_clarification=True,
                question="Хочете записатися чи дізнатися вартість?",
            )
        )
        result = await classify_intent("щось з монтажем", _ctx(), router)
        assert result.requires_clarification is True
        assert result.clarification_question == "Хочете записатися чи дізнатися вартість?"

    async def test_low_confidence_no_keywords_triggers_clarification(self) -> None:
        """confidence<0.6 + нет keyword-триггеров → requires_clarification=True."""
        # Фраза «абракадабра» — ни один keyword не триггерит.
        router = _mk_router(
            _mk_payload(
                primary="BOOK",
                confidence=0.4,
                requires_clarification=False,  # LLM не пометила
            )
        )
        result = await classify_intent("абракадабра", _ctx(), router)
        # Модуль-эвристика: сам поднимает флаг
        assert result.requires_clarification is True
        assert result.clarification_question is not None

    async def test_low_confidence_with_keyword_no_forced_clarification(self) -> None:
        """confidence<0.6, но keyword есть → эвристика НЕ добавляет clarification."""
        router = _mk_router(
            _mk_payload(primary="BOOK", confidence=0.4, requires_clarification=False)
        )
        # «шиномонтаж» — keyword для BOOK
        result = await classify_intent("шиномонтаж будь ласка", _ctx(), router)
        assert result.requires_clarification is False

    async def test_default_question_generated_when_llm_returns_none(self) -> None:
        """Если модель requires_clarification=True + question=None → модуль подставит дефолт."""
        router = _mk_router(
            _mk_payload(
                primary="BOOK",
                secondary=["PRICE"],
                confidence=0.55,
                requires_clarification=True,
                question=None,
            )
        )
        result = await classify_intent("шиномонтаж і щось про ціну", _ctx(), router)
        assert result.requires_clarification is True
        # BOOK + PRICE → должен быть дефолт про запис/вартість
        assert result.clarification_question is not None
        assert "запис" in result.clarification_question.lower()

    async def test_transfer_low_confidence_no_clarification(self) -> None:
        """TRANSFER — эскалация, не переспрашиваем даже при низком confidence."""
        router = _mk_router(
            _mk_payload(primary="TRANSFER", confidence=0.4)  # низкий confidence
        )
        # Даже без keyword-триггеров для TRANSFER эвристика должна пропустить
        result = await classify_intent("будь ласка допомога", _ctx(), router)
        assert result.primary_intent == "TRANSFER"
        assert result.requires_clarification is False

    async def test_clarification_field_nullified_when_not_required(self) -> None:
        """Если requires_clarification=False → clarification_question=None (даже если LLM прислала)."""
        router = _mk_router(
            _mk_payload(
                primary="BOOK",
                confidence=0.95,
                requires_clarification=False,
                question="лишний вопрос",
            )
        )
        result = await classify_intent("хочу записатися", _ctx(), router)
        assert result.requires_clarification is False
        assert result.clarification_question is None


# ---------------------------------------------------------------------------
# TestFallback — LLM timeout / JSON error / invalid payload
# ---------------------------------------------------------------------------


class TestFallback:
    """Все fallback-пути → primary=BOOK + confidence=0.0."""

    async def test_llm_timeout(self, caplog: pytest.LogCaptureFixture) -> None:
        router = AsyncMock()
        router.complete = AsyncMock(side_effect=TimeoutError())
        caplog.set_level(logging.WARNING, logger="src.agent.intent_classifier")

        result = await classify_intent("хочу записатися", _ctx(), router)

        assert isinstance(result, IntentResult)
        assert result.primary_intent == "BOOK"
        assert result.confidence == 0.0
        assert result.requires_clarification is False
        assert any("timeout" in r.message.lower() for r in caplog.records)

    async def test_llm_returns_non_json(self, caplog: pytest.LogCaptureFixture) -> None:
        router = _mk_router("not a json at all")
        caplog.set_level(logging.WARNING, logger="src.agent.intent_classifier")

        result = await classify_intent("хочу записатися", _ctx(), router)

        assert result.primary_intent == "BOOK"
        assert result.confidence == 0.0
        assert any("json parse" in r.message.lower() for r in caplog.records)

    async def test_llm_returns_json_missing_primary(self, caplog: pytest.LogCaptureFixture) -> None:
        """JSON без primary_intent → invalid payload → fallback."""
        router = _mk_router({"foo": "bar", "confidence": 0.9})
        caplog.set_level(logging.WARNING, logger="src.agent.intent_classifier")

        result = await classify_intent("хочу записатися", _ctx(), router)

        assert result.primary_intent == "BOOK"
        assert result.confidence == 0.0
        assert any("invalid payload" in r.message.lower() for r in caplog.records)

    async def test_llm_raises_generic_exception(self, caplog: pytest.LogCaptureFixture) -> None:
        router = AsyncMock()
        router.complete = AsyncMock(side_effect=RuntimeError("provider blew up"))
        caplog.set_level(logging.WARNING, logger="src.agent.intent_classifier")

        result = await classify_intent("хочу записатися", _ctx(), router)

        assert result.primary_intent == "BOOK"
        assert result.confidence == 0.0
        assert any("raised" in r.message.lower() for r in caplog.records)

    async def test_empty_customer_text_fallback(self, caplog: pytest.LogCaptureFixture) -> None:
        """Пустой customer_text → fallback без вызова LLM."""
        router = AsyncMock()
        router.complete = AsyncMock()
        caplog.set_level(logging.WARNING, logger="src.agent.intent_classifier")

        result = await classify_intent("   ", _ctx(), router)

        assert result.primary_intent == "BOOK"
        assert result.confidence == 0.0
        # LLM НЕ должна была быть вызвана
        router.complete.assert_not_awaited()

    async def test_fallback_result_has_default_extracted_fields(self) -> None:
        """У fallback ExtractedFields — все None."""
        router = _mk_router("bad")
        result = await classify_intent("хочу", _ctx(), router)
        assert result.extracted_fields == ExtractedFields()
        assert result.extracted_fields.city is None
        assert result.extracted_fields.diameter is None


# ---------------------------------------------------------------------------
# Sanity: system prompt fits under 2K chars (per contract)
# ---------------------------------------------------------------------------


def test_system_prompt_under_2k_chars() -> None:
    from src.agent.intent_classifier import _SYSTEM_PROMPT

    assert len(_SYSTEM_PROMPT) < 2000, (
        f"System prompt too long: {len(_SYSTEM_PROMPT)} chars (limit 2000)"
    )


def test_classify_intent_awaits_router_complete(
    mock_llm_router: AsyncMock,
) -> None:
    """Sanity: LLM зовётся через complete() ровно 1 раз для валидной фразы."""

    async def run() -> None:
        await classify_intent("хочу записатися", _ctx(), mock_llm_router)
        mock_llm_router.complete.assert_awaited_once()

    asyncio.run(run())
