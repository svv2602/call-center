"""Unit-тесты intent classifier'а (Волна 3-A / T3).

Первая версия модуля (`96879a6`) прошла 50/50 тестов и всё равно сломала прод:
ни один тест не смотрел на **тело** LLM-запроса, поэтому потеря `fsm_state` и
`dialog_history_tail` по дороге к модели осталась незамеченной. Отсюда группа
`TestContextSensitivity` и тест `test_llm_request_body_carries_fsm_state_and_history`,
который читает `mock_llm_router.complete.call_args`.

Все LLM-вызовы замоканы, в реального провайдера тесты не ходят.
"""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agent.intent_classifier import (
    _CONFIDENCE_THRESHOLD,
    _SYSTEM_PROMPT,
    ExtractedFields,
    IntentResult,
    classify_intent,
)

# ---------------------------------------------------------------------------
# Хелперы
# ---------------------------------------------------------------------------


def llm_json(
    primary: str = "BOOK",
    secondary: list[str] | None = None,
    confidence: float = 0.9,
    city: str | None = None,
    diameter: Any = None,
    station_hint: str | None = None,
    date_hint: str | None = None,
    requires_clarification: bool = False,
    clarification_question: str | None = None,
) -> str:
    """Канонический JSON-ответ LLM в формате, который ожидает классификатор."""
    return json.dumps(
        {
            "primary_intent": primary,
            "secondary_intents": secondary or [],
            "extracted_fields": {
                "city": city,
                "diameter": diameter,
                "station_hint": station_hint,
                "date_hint": date_hint,
            },
            "confidence": confidence,
            "requires_clarification": requires_clarification,
            "clarification_question": clarification_question,
        },
        ensure_ascii=False,
    )


def ctx(
    fsm_state: str | None = None,
    current_step: str | None = None,
    filled_fields: dict[str, Any] | None = None,
    dialog_history_tail: list[str] | None = None,
    tenant: str = "tvoya-shina",
) -> dict[str, Any]:
    """session_context в формате из README волны."""
    return {
        "fsm_state": fsm_state,
        "current_step": current_step,
        "filled_fields": filled_fields or {},
        "dialog_history_tail": dialog_history_tail or [],
        "tenant": tenant,
    }


@pytest.fixture
def mock_llm_router() -> MagicMock:
    """Мок реального `LLMRouter`.

    `spec=["complete"]` — намеренно. Голый `AsyncMock` отвечает на любой атрибут,
    поэтому классификатор, зовущий несуществующий метод, прошёл бы все тесты и
    сломался только в проде. `complete()` — единственный метод роутера, которым
    ходят в LLM (`src/llm/router.py`); `chat_completion` в репозитории нет.
    """
    router = MagicMock(spec=["complete"])
    router.complete = AsyncMock(return_value=SimpleNamespace(text=llm_json()))
    return router


async def classify(
    router: MagicMock,
    text: str,
    raw_response: str | None = None,
    context: dict[str, Any] | None = None,
) -> IntentResult:
    """Прогоняет classify_intent с заданным ответом провайдера."""
    if raw_response is not None:
        router.complete.return_value = SimpleNamespace(text=raw_response)
    return await classify_intent(text, context if context is not None else ctx(), router)


def sent_prompt(router: MagicMock) -> str:
    """User-часть последнего запроса к LLM."""
    messages = router.complete.call_args.kwargs["messages"]
    return next(m["content"] for m in messages if m["role"] == "user")


def _real_router_complete_params() -> set[str]:
    """Имена аргументов `LLMRouter.complete` — из исходника, без импорта.

    Импортировать `src.llm` нельзя: он тянет провайдерские SDK (`anthropic` и др.),
    которых в тестовом окружении может не быть. AST-разбор даёт ту же сверку
    бесплатно.
    """
    import ast
    import pathlib

    source = pathlib.Path("src/llm/router.py").read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "complete":
            args = node.args
            return {a.arg for a in [*args.posonlyargs, *args.args, *args.kwonlyargs]} - {"self"}
    raise AssertionError("LLMRouter.complete не найден в src/llm/router.py")


# ---------------------------------------------------------------------------
# TestContextSensitivity — регрессии на баги, из-за которых волну откатили.
# ---------------------------------------------------------------------------


class TestContextSensitivity:
    """Короткая реплика внутри идущего сценария не может сменить интент.

    Anchor calls 2026-09-07: «так» / «17» / «мені» / «не про» приходили как
    `PRICE conf=0.95` и сносили запись в консультацию по ценам.
    """

    async def test_yes_during_booking_flow_is_not_price(self, mock_llm_router: AsyncMock) -> None:
        result = await classify(
            mock_llm_router,
            "так",
            llm_json(primary="PRICE", confidence=0.95),
            ctx(
                fsm_state="DATE",
                dialog_history_tail=["Бот: На яку дату записуємо?", "Клієнт: так"],
            ),
        )
        assert result.primary_intent == "BOOK"
        assert result.confidence < _CONFIDENCE_THRESHOLD
        # Переспрашивать нечего — это ответ на вопрос текущего шага.
        assert result.requires_clarification is False

    async def test_bare_number_in_booking_flow_is_field_value_not_price(
        self, mock_llm_router: AsyncMock
    ) -> None:
        """Голое «17» → BOOK + diameter=17, а не PRICE (README, anchor #2)."""
        result = await classify(
            mock_llm_router,
            "17",
            llm_json(primary="PRICE", confidence=0.95, diameter=17),
            ctx(
                fsm_state="TIME",
                filled_fields={"city": "Дніпро", "date": "2026-09-09"},
                dialog_history_tail=["Бот: На 9 вересня вільно: 10:00, 17:00. Який час зручніше?"],
            ),
        )
        assert result.primary_intent == "BOOK"
        assert result.extracted_fields.diameter == 17
        assert result.confidence < _CONFIDENCE_THRESHOLD

    async def test_meni_during_city_step_is_not_price(self, mock_llm_router: AsyncMock) -> None:
        result = await classify(
            mock_llm_router,
            "мені",
            llm_json(primary="PRICE", confidence=0.92),
            ctx(fsm_state="CITY", dialog_history_tail=["Бот: У якому місті вам зручніше?"]),
        )
        assert result.primary_intent == "BOOK"
        assert result.primary_intent != "PRICE"

    async def test_ne_pro_during_storage_step_is_not_price(
        self, mock_llm_router: AsyncMock
    ) -> None:
        result = await classify(
            mock_llm_router,
            "не про",
            llm_json(primary="PRICE", confidence=0.95),
            ctx(
                fsm_state="STORAGE",
                dialog_history_tail=["Бот: Шини свої з собою чи ті, що у нас на зберіганні?"],
            ),
        )
        assert result.primary_intent == "BOOK"
        assert result.confidence < _CONFIDENCE_THRESHOLD

    async def test_llm_request_body_carries_fsm_state_and_history(
        self, mock_llm_router: AsyncMock
    ) -> None:
        """Главный тест волны: контекст физически уходит в LLM.

        В откаченной версии `fsm_state` и `dialog_history_tail` были в сигнатуре,
        но до промпта не доходили — и все 50 тестов это пропустили, потому что
        проверяли только разбор ответа.
        """
        tail = [
            "Бот: На яку дату записуємо?",
            "Клієнт: а скільки це коштує",
            "Бот: Залежить від діаметра. Який у вас?",
        ]
        await classify(
            mock_llm_router,
            "17",
            llm_json(primary="PRICE", confidence=0.9),
            ctx(fsm_state="DATE", filled_fields={"city": "Дніпро"}, dialog_history_tail=tail),
        )

        mock_llm_router.complete.assert_awaited_once()
        prompt = sent_prompt(mock_llm_router)

        assert "fsm_state" in prompt
        assert "DATE" in prompt
        assert "dialog_history_tail" in prompt
        for line in tail:
            assert line in prompt, f"реплика {line!r} не дошла до промпта"
        assert "Дніпро" in prompt
        assert "17" in prompt

    async def test_llm_call_matches_real_router_signature(
        self, mock_llm_router: MagicMock
    ) -> None:
        """Вызов обязан быть совместим с настоящим `LLMRouter.complete`.

        Моки отвечают на что угодно, поэтому «зелёные тесты» сами по себе не
        доказывают, что модуль зовёт существующий метод с существующими
        аргументами. Здесь запрос сверяется с живой сигнатурой из
        `src/llm/router.py` — если её переименуют или сменят kwargs, тест упадёт
        здесь, а не на проде.
        """
        await classify(mock_llm_router, "хочу записатися на шиномонтаж")

        kwargs = mock_llm_router.complete.call_args.kwargs
        assert kwargs["system"] is _SYSTEM_PROMPT
        assert kwargs["messages"][0]["role"] == "user"

        # Сигнатура читается из исходника через AST, а не импортом: `src.llm`
        # тянет провайдерские SDK, которых в тестовом окружении может не быть.
        assert set(kwargs) <= _real_router_complete_params()

    async def test_yes_without_fsm_state_is_ambiguous_not_confident_price(
        self, mock_llm_router: AsyncMock
    ) -> None:
        """«так» вне сценария: ambiguous допустимо, `PRICE conf>0.9` — нет."""
        result = await classify(
            mock_llm_router,
            "так",
            llm_json(primary="PRICE", confidence=0.95),
            ctx(fsm_state=None, current_step=None),
        )
        assert not (result.primary_intent == "PRICE" and result.confidence > 0.9)
        assert result.confidence < _CONFIDENCE_THRESHOLD
        assert result.requires_clarification is True
        assert result.clarification_question

    async def test_intent_switch_without_lexical_evidence_is_rejected(
        self, mock_llm_router: AsyncMock
    ) -> None:
        result = await classify(
            mock_llm_router,
            "ага",
            llm_json(primary="CANCEL", secondary=["PRICE"], confidence=0.9),
            ctx(fsm_state="BRAND", dialog_history_tail=["Бот: Яка марка вашого авто?"]),
        )
        assert result.primary_intent == "BOOK"
        assert result.secondary_intents == []

    async def test_intent_switch_with_lexical_evidence_passes(
        self, mock_llm_router: AsyncMock
    ) -> None:
        """Guard не глушит настоящие переключения — иначе прервать запись нельзя."""
        result = await classify(
            mock_llm_router,
            "скасуйте запис",
            llm_json(primary="CANCEL", confidence=0.93),
            ctx(fsm_state="DATE"),
        )
        assert result.primary_intent == "CANCEL"
        assert result.confidence == pytest.approx(0.93)

    async def test_bare_number_while_collecting_diameter_stays_in_price_flow(
        self, mock_llm_router: AsyncMock
    ) -> None:
        """В PRICE_INTERRUPT «17» — это значение diameter, а не возврат к записи."""
        result = await classify(
            mock_llm_router,
            "17",
            llm_json(primary="BOOK", confidence=0.8),
            ctx(fsm_state="PRICE_INTERRUPT", dialog_history_tail=["Бот: Який діаметр коліс?"]),
        )
        assert result.primary_intent == "PRICE"
        assert result.extracted_fields.diameter == 17

    async def test_long_utterance_is_not_pinned_by_guard(self, mock_llm_router: AsyncMock) -> None:
        """Развёрнутая фраза внутри сценария guard'ом не трогается."""
        result = await classify(
            mock_llm_router,
            "а скільки взагалі коштує перевзути чотири колеса",
            llm_json(primary="PRICE", confidence=0.88),
            ctx(fsm_state="DATE"),
        )
        assert result.primary_intent == "PRICE"
        assert result.confidence == pytest.approx(0.88)


# ---------------------------------------------------------------------------
# TestPrimaryIntents
# ---------------------------------------------------------------------------


class TestPrimaryIntents:
    """Чистые фразы без контекста — классификатор отдаёт метку LLM как есть."""

    @pytest.mark.parametrize(
        "text",
        [
            "хочу записатися на шиномонтаж",
            "мені треба переобутися",
            "запишіть на завтра",
        ],
    )
    async def test_book(self, mock_llm_router: AsyncMock, text: str) -> None:
        result = await classify(mock_llm_router, text, llm_json(primary="BOOK", confidence=0.93))
        assert result.primary_intent == "BOOK"
        assert result.requires_clarification is False

    @pytest.mark.parametrize(
        "text",
        [
            "скільки коштує шиномонтаж",
            "підкажіть вартість",
            "яка ціна монтажу R17",
        ],
    )
    async def test_price(self, mock_llm_router: AsyncMock, text: str) -> None:
        result = await classify(mock_llm_router, text, llm_json(primary="PRICE", confidence=0.91))
        assert result.primary_intent == "PRICE"

    @pytest.mark.parametrize(
        "text",
        [
            "хочу скасувати запис",
            "відмінити",
            "прибрати мою бронь",
        ],
    )
    async def test_cancel(self, mock_llm_router: AsyncMock, text: str) -> None:
        result = await classify(mock_llm_router, text, llm_json(primary="CANCEL", confidence=0.9))
        assert result.primary_intent == "CANCEL"

    @pytest.mark.parametrize(
        "text",
        [
            "перенести запис",
            "змінити час",
            "перепризначити на завтра",
        ],
    )
    async def test_reschedule(self, mock_llm_router: AsyncMock, text: str) -> None:
        result = await classify(
            mock_llm_router, text, llm_json(primary="RESCHEDULE", confidence=0.88)
        )
        assert result.primary_intent == "RESCHEDULE"

    @pytest.mark.parametrize(
        "text",
        [
            "дайте оператора",
            "з людиною поспілкуватися",
            "менеджер",
        ],
    )
    async def test_transfer(self, mock_llm_router: AsyncMock, text: str) -> None:
        result = await classify(
            mock_llm_router, text, llm_json(primary="TRANSFER", confidence=0.96)
        )
        assert result.primary_intent == "TRANSFER"


# ---------------------------------------------------------------------------
# TestSTTGarbage — anchor calls волн 3-5 и жалобы 2026-09-07.
# ---------------------------------------------------------------------------


class TestSTTGarbage:
    """Огрызки STT: важно, что модуль не ломается и уважает метку/уверенность LLM."""

    async def test_selskiy_korzh_shinomontazh_is_ambiguous(
        self, mock_llm_router: AsyncMock
    ) -> None:
        result = await classify(
            mock_llm_router,
            "сельский Корж шиномонтаж на Запорізьке шосе",
            llm_json(
                primary="BOOK",
                confidence=0.5,
                station_hint="Запорізьке шосе",
                requires_clarification=True,
                clarification_question="Хочете записатися чи дізнатися вартість?",
            ),
        )
        assert result.primary_intent in {"BOOK", "PRICE"}
        assert result.requires_clarification is True
        assert result.extracted_fields.station_hint == "Запорізьке шосе"

    async def test_ne_peska_zhit_is_price(self, mock_llm_router: AsyncMock) -> None:
        result = await classify(
            mock_llm_router,
            "не песка жить в артист монтажу На Харьковский",
            llm_json(primary="PRICE", confidence=0.7, station_hint="Харківське шосе"),
        )
        assert result.primary_intent == "PRICE"

    async def test_kassovatyy_zapusk_is_cancel(self, mock_llm_router: AsyncMock) -> None:
        result = await classify(
            mock_llm_router,
            "в кассоватый запуск",
            llm_json(primary="CANCEL", confidence=0.65),
        )
        assert result.primary_intent == "CANCEL"

    async def test_yakoby_est_montazhu_ervi_17_is_book_plus_price(
        self, mock_llm_router: AsyncMock
    ) -> None:
        result = await classify(
            mock_llm_router,
            "якобы есть монтажу эрви 17 у Днепре",
            llm_json(
                primary="BOOK",
                secondary=["PRICE"],
                confidence=0.72,
                city="Дніпро",
                diameter=17,
            ),
        )
        # Priority order: PRICE выше BOOK, поэтому primary переупорядочивается.
        assert result.primary_intent == "PRICE"
        assert result.secondary_intents == ["BOOK"]
        assert result.extracted_fields.diameter == 17

    async def test_belyak_karavanu_is_book(self, mock_llm_router: AsyncMock) -> None:
        result = await classify(
            mock_llm_router,
            "беляк каравану моменту зашлифовывает",
            llm_json(primary="BOOK", confidence=0.63, station_hint="Караван"),
        )
        assert result.primary_intent == "BOOK"
        assert result.extracted_fields.station_hint == "Караван"

    async def test_mne_potrebnosti_s_montazhu_asks_clarification(
        self, mock_llm_router: AsyncMock
    ) -> None:
        result = await classify(
            mock_llm_router,
            "мне потребности с монтажу",
            llm_json(primary="BOOK", confidence=0.42, requires_clarification=True),
        )
        assert result.requires_clarification is True
        assert result.clarification_question

    async def test_trogat_nadpis_na_montazh_is_book(self, mock_llm_router: AsyncMock) -> None:
        result = await classify(
            mock_llm_router,
            "трогать надпис на монтаж в Києві",
            llm_json(primary="BOOK", confidence=0.68, city="Київ"),
        )
        assert result.primary_intent == "BOOK"
        assert result.extracted_fields.city == "Київ"

    async def test_vesnoy_svarki_is_price(self, mock_llm_router: AsyncMock) -> None:
        result = await classify(
            mock_llm_router,
            "весной сварки в Дніпрі",
            llm_json(primary="PRICE", confidence=0.62, city="Дніпро"),
        )
        assert result.primary_intent == "PRICE"
        assert result.extracted_fields.city == "Дніпро"

    async def test_my_ne_trogat_nadpis_tinu_is_book(self, mock_llm_router: AsyncMock) -> None:
        result = await classify(
            mock_llm_router,
            "мы не трогать надпись Тину на монтаж",
            llm_json(primary="BOOK", confidence=0.6),
        )
        assert result.primary_intent == "BOOK"

    async def test_unintelligible_fragment_without_context_is_ambiguous(
        self, mock_llm_router: AsyncMock
    ) -> None:
        result = await classify(
            mock_llm_router,
            "не про",
            llm_json(primary="PRICE", confidence=0.95),
            ctx(fsm_state=None),
        )
        assert result.requires_clarification is True
        assert result.confidence < _CONFIDENCE_THRESHOLD


# ---------------------------------------------------------------------------
# TestCompoundIntents
# ---------------------------------------------------------------------------


class TestCompoundIntents:
    async def test_book_and_price_promotes_price(self, mock_llm_router: AsyncMock) -> None:
        result = await classify(
            mock_llm_router,
            "шиномонтаж і ціну",
            llm_json(primary="BOOK", secondary=["PRICE"], confidence=0.8),
        )
        assert result.primary_intent == "PRICE"
        assert result.secondary_intents == ["BOOK"]

    async def test_reschedule_from_monday_to_wednesday(self, mock_llm_router: AsyncMock) -> None:
        result = await classify(
            mock_llm_router,
            "перенести з понеділка на середу",
            llm_json(primary="RESCHEDULE", secondary=["BOOK"], confidence=0.87, date_hint="середа"),
        )
        assert result.primary_intent == "RESCHEDULE"
        assert result.extracted_fields.date_hint == "середа"

    async def test_cancel_and_rebook_labelled_reschedule(self, mock_llm_router: AsyncMock) -> None:
        result = await classify(
            mock_llm_router,
            "скасувати запис і записати на іншу дату",
            llm_json(primary="RESCHEDULE", secondary=["BOOK"], confidence=0.84),
        )
        assert result.primary_intent == "RESCHEDULE"
        assert "BOOK" in result.secondary_intents

    async def test_transfer_wins_over_everything(self, mock_llm_router: AsyncMock) -> None:
        result = await classify(
            mock_llm_router,
            "скільки коштує і дайте оператора",
            llm_json(primary="PRICE", secondary=["TRANSFER"], confidence=0.9),
        )
        assert result.primary_intent == "TRANSFER"
        assert result.secondary_intents == ["PRICE"]

    async def test_secondary_is_capped_at_two_and_deduplicated(
        self, mock_llm_router: AsyncMock
    ) -> None:
        result = await classify(
            mock_llm_router,
            "хочу скасувати запис, дізнатись ціну і поговорити з оператором",
            llm_json(
                primary="BOOK",
                secondary=["PRICE", "CANCEL", "TRANSFER", "BOOK"],
                confidence=0.75,
            ),
        )
        assert result.primary_intent == "TRANSFER"
        assert len(result.secondary_intents) <= 2
        assert result.secondary_intents == ["CANCEL", "PRICE"]

    async def test_priority_applied_even_when_llm_order_differs(
        self, mock_llm_router: AsyncMock
    ) -> None:
        result = await classify(
            mock_llm_router,
            "запишіть мене, а стару бронь скасуйте",
            llm_json(primary="BOOK", secondary=["CANCEL"], confidence=0.82),
        )
        assert result.primary_intent == "CANCEL"
        assert result.secondary_intents == ["BOOK"]


# ---------------------------------------------------------------------------
# TestExtractedFields
# ---------------------------------------------------------------------------


class TestExtractedFields:
    async def test_book_with_city_and_diameter(self, mock_llm_router: AsyncMock) -> None:
        result = await classify(
            mock_llm_router,
            "шиномонтаж R18 в Дніпрі",
            llm_json(primary="BOOK", confidence=0.9, city="Дніпро", diameter=18),
        )
        assert result.primary_intent == "BOOK"
        assert result.extracted_fields.city == "Дніпро"
        assert result.extracted_fields.diameter == 18

    async def test_price_with_city_and_diameter(self, mock_llm_router: AsyncMock) -> None:
        result = await classify(
            mock_llm_router,
            "скільки коштує R17 в Києві",
            llm_json(primary="PRICE", confidence=0.94, city="Київ", diameter=17),
        )
        assert result.primary_intent == "PRICE"
        assert result.extracted_fields == ExtractedFields(city="Київ", diameter=17)

    async def test_diameter_out_of_range_is_dropped(self, mock_llm_router: AsyncMock) -> None:
        result = await classify(
            mock_llm_router,
            "скільки коштує R30",
            llm_json(primary="PRICE", confidence=0.8, diameter=30),
        )
        assert result.extracted_fields.diameter is None

    async def test_diameter_as_string_is_parsed(self, mock_llm_router: AsyncMock) -> None:
        result = await classify(
            mock_llm_router,
            "монтаж R21",
            llm_json(primary="BOOK", confidence=0.85, diameter="R21"),
        )
        assert result.extracted_fields.diameter == 21

    async def test_station_and_date_hints(self, mock_llm_router: AsyncMock) -> None:
        result = await classify(
            mock_llm_router,
            "запишіть на п'ятницю на Караван",
            llm_json(
                primary="BOOK",
                confidence=0.89,
                station_hint="Караван",
                date_hint="п'ятниця",
            ),
        )
        assert result.extracted_fields.station_hint == "Караван"
        assert result.extracted_fields.date_hint == "п'ятниця"

    async def test_malformed_extracted_fields_degrade_to_empty(
        self, mock_llm_router: AsyncMock
    ) -> None:
        raw = json.dumps(
            {
                "primary_intent": "BOOK",
                "secondary_intents": [],
                "extracted_fields": "нічого",
                "confidence": 0.9,
            },
            ensure_ascii=False,
        )
        result = await classify(mock_llm_router, "хочу записатися на шиномонтаж", raw)
        assert result.extracted_fields == ExtractedFields()
        assert result.primary_intent == "BOOK"


# ---------------------------------------------------------------------------
# TestAmbiguousClarification
# ---------------------------------------------------------------------------


class TestAmbiguousClarification:
    async def test_low_confidence_without_keywords_forces_clarification(
        self, mock_llm_router: AsyncMock
    ) -> None:
        result = await classify(
            mock_llm_router,
            "ееее ммм",
            llm_json(primary="BOOK", confidence=0.35),
        )
        assert result.requires_clarification is True
        assert result.clarification_question

    async def test_model_question_is_preserved(self, mock_llm_router: AsyncMock) -> None:
        result = await classify(
            mock_llm_router,
            "монтаж чи ціна",
            llm_json(
                primary="BOOK",
                secondary=["PRICE"],
                confidence=0.5,
                requires_clarification=True,
                clarification_question="Уточніть: запис чи вартість?",
            ),
        )
        assert result.clarification_question == "Уточніть: запис чи вартість?"

    async def test_default_question_for_book_price_pair(self, mock_llm_router: AsyncMock) -> None:
        result = await classify(
            mock_llm_router,
            "ааа ееее",
            llm_json(primary="BOOK", secondary=["PRICE"], confidence=0.4),
        )
        assert result.requires_clarification is True
        assert result.clarification_question == "Хочете записатися чи дізнатися вартість?"

    async def test_question_is_cleared_when_clarification_not_needed(
        self, mock_llm_router: AsyncMock
    ) -> None:
        result = await classify(
            mock_llm_router,
            "хочу записатися на шиномонтаж",
            llm_json(
                primary="BOOK",
                confidence=0.95,
                requires_clarification=False,
                clarification_question="залишковий текст",
            ),
        )
        assert result.requires_clarification is False
        assert result.clarification_question is None

    async def test_low_confidence_with_keyword_is_not_forced(
        self, mock_llm_router: AsyncMock
    ) -> None:
        """Keyword есть → модель имеет опору, не переспрашиваем через силу."""
        result = await classify(
            mock_llm_router,
            "скільки коштує перевзуття",
            llm_json(primary="PRICE", confidence=0.55),
        )
        assert result.requires_clarification is False

    async def test_transfer_is_never_downgraded_to_clarification(
        self, mock_llm_router: AsyncMock
    ) -> None:
        """Эскалацию лучше сделать лишний раз, чем переспрашивать раздражённого клиента."""
        result = await classify(
            mock_llm_router,
            "ааааа шшшш",
            llm_json(primary="TRANSFER", confidence=0.3),
        )
        assert result.primary_intent == "TRANSFER"
        assert result.requires_clarification is False


# ---------------------------------------------------------------------------
# TestFallback — confidence=0.0 как маркер «downstream, работай старым агентом».
# ---------------------------------------------------------------------------


class TestFallback:
    async def test_timeout_returns_fallback(self, mock_llm_router: AsyncMock) -> None:
        mock_llm_router.complete.side_effect = TimeoutError()
        result = await classify_intent("хочу записатися", ctx(), mock_llm_router)
        assert result.primary_intent == "BOOK"
        assert result.confidence == 0.0
        assert result.requires_clarification is False

    async def test_provider_exception_returns_fallback(self, mock_llm_router: AsyncMock) -> None:
        mock_llm_router.complete.side_effect = RuntimeError("all providers failed")
        result = await classify_intent("хочу записатися", ctx(), mock_llm_router)
        assert result.confidence == 0.0
        assert result.primary_intent == "BOOK"

    async def test_non_json_response_returns_fallback(
        self, mock_llm_router: AsyncMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.WARNING)
        result = await classify(mock_llm_router, "хочу записатися", "вибачте, не зрозуміла")
        assert result.confidence == 0.0
        assert "JSON parse failed" in caplog.text

    async def test_missing_required_field_returns_fallback(
        self, mock_llm_router: AsyncMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.WARNING)
        result = await classify(mock_llm_router, "хочу записатися", json.dumps({"confidence": 0.9}))
        assert result.confidence == 0.0
        assert result.primary_intent == "BOOK"
        assert "invalid payload" in caplog.text

    async def test_unknown_intent_label_returns_fallback(self, mock_llm_router: AsyncMock) -> None:
        result = await classify(mock_llm_router, "хочу записатися", llm_json(primary="ORDER_TIRES"))
        assert result.confidence == 0.0

    async def test_empty_text_short_circuits_without_llm_call(
        self, mock_llm_router: AsyncMock
    ) -> None:
        result = await classify_intent("   ", ctx(), mock_llm_router)
        assert result.confidence == 0.0
        mock_llm_router.complete.assert_not_awaited()

    async def test_json_wrapped_in_markdown_is_still_parsed(
        self, mock_llm_router: AsyncMock
    ) -> None:
        """Не fallback: ```json-обёртку разбираем, иначе теряем валидный ответ."""
        result = await classify(
            mock_llm_router,
            "хочу записатися на шиномонтаж",
            f"```json\n{llm_json(primary='BOOK', confidence=0.9)}\n```",
        )
        assert result.primary_intent == "BOOK"
        assert result.confidence == pytest.approx(0.9)


# ---------------------------------------------------------------------------
# TestRequestShape — форма запроса к провайдеру.
# ---------------------------------------------------------------------------


class TestRequestShape:
    def test_system_prompt_under_2k_chars(self) -> None:
        assert len(_SYSTEM_PROMPT) < 2000

    async def test_system_prompt_is_sent_via_system_kwarg(
        self, mock_llm_router: MagicMock
    ) -> None:
        """`LLMRouter.complete` берёт system отдельным аргументом, не сообщением."""
        await classify(mock_llm_router, "хочу записатися на шиномонтаж")
        kwargs = mock_llm_router.complete.call_args.kwargs
        assert kwargs["system"] is _SYSTEM_PROMPT
        assert [m["role"] for m in kwargs["messages"]] == ["user"]

    async def test_provider_override_pins_the_cheap_model(
        self, mock_llm_router: MagicMock
    ) -> None:
        """Классификатор не должен уезжать на дорогую модель агента."""
        await classify(mock_llm_router, "хочу записатися на шиномонтаж")
        kwargs = mock_llm_router.complete.call_args.kwargs
        assert kwargs["provider_override"] == "openai-gpt41-mini"

    async def test_history_tail_is_truncated_to_last_five_turns(
        self, mock_llm_router: AsyncMock
    ) -> None:
        tail = [f"Репліка {i}" for i in range(8)]
        await classify(
            mock_llm_router,
            "хочу записатися на шиномонтаж",
            context=ctx(fsm_state="CITY", dialog_history_tail=tail),
        )
        prompt = sent_prompt(mock_llm_router)
        assert "Репліка 0" not in prompt
        assert "Репліка 7" in prompt

    async def test_latency_is_logged(
        self, mock_llm_router: AsyncMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.INFO)
        await classify(mock_llm_router, "хочу записатися на шиномонтаж")
        assert "intent_classifier_latency_ms" in caplog.text
