"""Вердикт `TRANSFER` без свидетельства в словах клиента не обрывает звонок (Волна 1-A).

Замер прод-БД 2026-09-11, окно 30 дней: 15 звонков с
`transfer_reason = 'intent_classifier_transfer'`, **0 записей**. В 14 из 15 клиент
отвечал ровно на заданный вопрос бота, и ни в одной из этих 14 реплик нет ни одного
признака просьбы о человеке. Пятнадцатый (`fdb4a7d4`, «з'єднайте з магазином») —
верный перевод, он обязан остаться переводом.

**Что здесь проверяется и чего здесь НЕ проверяется.** Гард `_apply_context_guard`
существует с `1b2417f` (2026-09-08) и сам по себе подавляет 10 из 15 — через пин
короткой реплики к интенту состояния. Поэтому корпус ниже зелёный и без правки
волны на этих десяти, и сам по себе он ничего не доказывает. Доказывают его
мутации: клауза default-deny отвечает ровно за те **4** звонка, где
`_is_short_answer` = False (`8abd8557`, `81f2eeac`, `6ccbea3d`, `a83655c5`),
и за `fdb4a7d4`, который должен выжить. Выигрыш волны — 4 звонка, не 9.

Тесты зовут `_apply_context_guard` напрямую: он синхронный и без I/O, поэтому
моки коллабораторов здесь не нужны вовсе (а `AsyncMock` без `spec` умеет отвечать
на любой атрибут и красит зелёным путь, которого в проде нет).
"""

from __future__ import annotations

import logging

import pytest

from src.agent.intent_classifier import (
    _GUARDED_CONFIDENCE,
    IntentResult,
    _apply_context_guard,
    _is_short_answer,
)

# Порог берётся из своего модуля, а не вписывается числом: тест, который пишет
# «0.5» руками, переживёт правку порога в pipeline и промолчит.
from src.core.pipeline import FSM_INTERRUPT_CONFIDENCE_FLOOR

_CLASSIFIER_LOGGER = "src.agent.intent_classifier"

#: Строка лога клаузы default-deny (задача 2.2).
_EVIDENCE_GUARD_LOG = "no request for a human"
#: Строка лога капа в exempt-ветке (задача 2.3).
_EXEMPT_CAP_LOG = "without lexical evidence and no fsm_state"


def _survives_to_transfer(result: IntentResult) -> bool:
    """Доживает ли вердикт до `mark_transfer` (`pipeline.py:2228-2232`)."""
    return (
        result.primary_intent == "TRANSFER" and result.confidence >= FSM_INTERRUPT_CONFIDENCE_FLOOR
    )


def _guard(
    text: str,
    state: str | None,
    *,
    intent: str = "TRANSFER",
    confidence: float = 0.9,
) -> IntentResult:
    result = IntentResult(primary_intent=intent, confidence=confidence)  # type: ignore[arg-type]
    return _apply_context_guard(result, text, {"fsm_state": state})


# ---------------------------------------------------------------------------
# Корпус замера
# ---------------------------------------------------------------------------

#: (реплика клиента, состояние FSM, доживает ли до перевода).
#:
#: Состояние выведено из текста последнего вопроса бота — колонки `fsm_state`
#: у таблицы `calls` нет, восстановить его точно задним числом нельзя. Это
#: оценка, и на неё опирается только маршрут внутри гарда, не сам вердикт.
_MEASURED_CORPUS: list[tuple[str, str, str, bool]] = [
    ("fe1857ba", "так", "STORAGE", False),
    ("cf43d623", "свои свои", "STORAGE", False),
    ("f71046f0", "свої", "STORAGE", False),
    ("bba035ff", "900", "TIME", False),
    ("39469f9f", "19", "PRICE_INTERRUPT", False),
    ("431e60fb", "так", "CONFIRM", False),
    ("78475ed1", "Ой извините", "STATION", False),
    ("8abd8557", "еще раз временно зовите я хотел на два часа дня", "TIME", False),
    ("30dd42fa", "нет", "CONFIRM", False),
    ("2a349173", "Алло", "CONFIRM", False),
    ("81f2eeac", "Добрый день мы не требуйте знать", "WELCOME", False),
    ("6ccbea3d", "Марина ти де", "CONFIRM", False),
    ("380a280d", "є запитання", "CONFIRM", False),
    ("a83655c5", "Я хочу выйти из надписью запись успешный", "CONFIRM", False),
    # Единственный верный перевод замера. Если он покраснеет — волна отняла у
    # клиента человека, и это стоп, а не «поправить ожидание».
    ("fdb4a7d4", "з'єднайте з магазином", "INTENT", True),
]

#: Реплики замера, которые существующий пин не достаёт. Условие берётся у самого
#: `_is_short_answer`, а не переписывается «длиной больше 16»: своя копия правила
#: проверяла бы копию (на «Марина ти де» — 12 символов, но 3 лексемы — она уже
#: расходится с оригиналом).
#: Ровно за эти пять реплик отвечает клауза default-deny, на них и меряется выигрыш.
_LONG_REPLIES: list[tuple[str, str, str]] = [
    (call_id, text, state)
    for call_id, text, state, _ in _MEASURED_CORPUS
    if not _is_short_answer(text)
]


class TestMeasuredCorpus:
    @pytest.mark.parametrize(
        ("text", "state", "expected"),
        [
            pytest.param(text, state, expected, id=call_id)
            for call_id, text, state, expected in _MEASURED_CORPUS
        ],
    )
    def test_verdict_survives_only_with_evidence(
        self, text: str, state: str, expected: bool
    ) -> None:
        assert _survives_to_transfer(_guard(text, state)) is expected

    def test_exactly_one_of_fifteen_survives(self) -> None:
        """Агрегат: попарное сравнение с инкумбентом, а не абсолютный счётчик.

        Инкумбент (`7ae79bd`) на этом же наборе доводит до перевода 5 из 15.
        """
        survivors = [
            call_id
            for call_id, text, state, _ in _MEASURED_CORPUS
            if _survives_to_transfer(_guard(text, state))
        ]
        assert survivors == ["fdb4a7d4"]


# ---------------------------------------------------------------------------
# Пороги
# ---------------------------------------------------------------------------


class TestThresholdInvariant:
    def test_guarded_confidence_stays_below_the_pipeline_floor(self) -> None:
        # Утверждается ОТНОШЕНИЕ, а не два числа: дыру волны создало как раз
        # расхождение 0.5 в pipeline против 0.6 в классификаторе. Тест обязан
        # ловить класс дрейфа, а не его сегодняшнее значение.
        assert _GUARDED_CONFIDENCE < FSM_INTERRUPT_CONFIDENCE_FLOOR

    def test_transfer_in_the_open_window_is_refused_by_the_evidence_clause(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """`confidence = 0.55`, `fsm_state` пустой — окно `[0.5, 0.6)`.

        Проверяется ЧЕЙ это отказ: прод в логе называет только первый
        сработавший гард, поэтому тест утверждает строку клаузы default-deny,
        а не просто «звонок не переведён».
        """
        with caplog.at_level(logging.INFO, logger=_CLASSIFIER_LOGGER):
            out = _guard("так", None, confidence=0.55)

        assert out.confidence < FSM_INTERRUPT_CONFIDENCE_FLOOR
        assert not _survives_to_transfer(out)
        assert any(_EVIDENCE_GUARD_LOG in r.getMessage() for r in caplog.records)

    def test_non_transfer_verdict_in_the_open_window_is_refused_by_the_exempt_cap(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Та же дыра для `PRICE`/`CANCEL` — её закрывает безусловный кап, не клауза.

        `pipeline.py:2261` читает тот же `FSM_INTERRUPT_CONFIDENCE_FLOOR` для
        всех вердиктов, поэтому `PRICE conf=0.55` на реплике «19» без состояния
        запускал PRICE-интеррапт. Вердикт здесь нарочно НЕ `TRANSFER`: клауза
        2.2 накрыла бы его собой, мутация порога выжила бы, а лог в проде
        назвал бы чужой гард.
        """
        with caplog.at_level(logging.INFO, logger=_CLASSIFIER_LOGGER):
            out = _guard("19", None, intent="PRICE", confidence=0.55)

        assert out.confidence < FSM_INTERRUPT_CONFIDENCE_FLOOR
        assert any(_EXEMPT_CAP_LOG in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# Чего правило ломать не имеет права
# ---------------------------------------------------------------------------


class TestRuleDoesNotBreak:
    @pytest.mark.parametrize(
        "text",
        [
            "дайте оператора",
            "з'єднайте з магазином",
            "мені потрібен консультант",
            "покличте живу людину",
            "переключіть на менеджера",
        ],
    )
    def test_a_request_for_a_human_still_reaches_the_transfer(self, text: str) -> None:
        """Расширенный словарь 2.1 — живой, а не мёртвые литералы.

        «консультант» и «з'єдна» не знал ни один из двух словарей, живших до
        волны, а «з'єднайте з магазином» — это верный перевод замера.
        """
        assert _survives_to_transfer(_guard(text, "INTENT"))

    @pytest.mark.parametrize(
        "intent",
        ["PRICE", "CANCEL", "BOOK", "RESCHEDULE"],
    )
    @pytest.mark.parametrize(
        ("text", "state"),
        [pytest.param(text, state, id=call_id) for call_id, text, state in _LONG_REPLIES],
    )
    def test_other_verdicts_are_untouched_by_the_evidence_clause(
        self, intent: str, text: str, state: str
    ) -> None:
        """Клауза судит ТОЛЬКО вердикт `TRANSFER`.

        Она снимает право обрывать звонок, а это право есть лишь у `TRANSFER`
        (`pipeline.py:2228`). Гасить ею `PRICE` означало бы чинить не тот дефект.
        """
        out = _guard(text, state, intent=intent, confidence=0.9)
        assert out.primary_intent == intent
        assert out.confidence == pytest.approx(0.9)

    def test_fallback_marker_survives_the_evidence_clause(self) -> None:
        """`confidence == 0.0` — маркер «LLM недоступен, работай старым агентом».

        Превратись он в 0.4, downstream перестанет откатываться, и сделает это
        молча.
        """
        out = _guard("Алло", "CONFIRM", confidence=0.0)
        assert out.confidence == 0.0

    def test_fallback_marker_survives_the_exempt_cap(self) -> None:
        out = _guard("Алло", None, intent="BOOK", confidence=0.0)
        assert out.confidence == 0.0
        assert out.requires_clarification is False

    def test_intent_label_is_not_rewritten_by_the_evidence_clause(self) -> None:
        """Гард снимает право обрывать звонок, а не переписывает сказанное.

        LLM-ход обязан увидеть вердикт как есть — иначе подмена метки станет
        вторым источником правды о том, что сказал клиент.
        """
        out = _guard("Марина ти де", "CONFIRM")
        assert out.primary_intent == "TRANSFER"
        assert out.confidence == pytest.approx(_GUARDED_CONFIDENCE)


# ---------------------------------------------------------------------------
# Осознанный размен
# ---------------------------------------------------------------------------


class TestKnownTradeOff:
    def test_a_human_asked_for_outside_the_dictionary_costs_one_turn(self) -> None:
        """Формулировка не из словаря — клиент теряет ход, а не возможность.

        Записано тестом намеренно: это не баг, а цена default-deny. Инструмент
        `transfer_to_operator` остаётся у LLM, а его собственный loop-breaker
        (`_MAX_BLOCKS_PER_CALL = 2`, `streaming_loop.py:187`) пропускает перевод
        после двух блокировок. Если этот тест однажды покраснеет, значит словарь
        расширили — проверь, что расширение не задело корпус замера.
        """
        out = _guard("покличте когось із відділу продажів", "INTENT")
        assert not _survives_to_transfer(out)
        assert out.primary_intent == "TRANSFER"
