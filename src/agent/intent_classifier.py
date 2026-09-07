"""Intent classifier для колл-центра шиномонтажа (Wave 13 FSM refactor, T2).

Отдельный модуль, вызывающий LLM (через LLMRouter) для классификации
customer_text в один из 5 интентов: BOOK, PRICE, CANCEL, RESCHEDULE, TRANSFER.

Поддерживает:
- Compound intents (например, «шиномонтаж і ціну» → primary=PRICE, secondary=[BOOK]).
- Priority-based reorder: TRANSFER > CANCEL > RESCHEDULE > PRICE > BOOK.
- Ambiguous handling: confidence<0.6 или неоднозначная фраза → requires_clarification.
- Fallback: timeout / JSON error / any exception → primary=BOOK + confidence=0.0
  (downstream ориентируется на confidence=0.0 как маркер «использовать старый агент»).

Модель по умолчанию — gpt-4.1-mini (см. memory: gpt-5-mini для voice не подходит).
Переопределяется через env INTENT_CLASSIFIER_PROVIDER (значение из
DEFAULT_ROUTING_CONFIG.providers, например "openai-gpt41-mini").
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from src.llm.router import LLMRouter

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Типы и dataclasses (публичный контракт модуля)
# ---------------------------------------------------------------------------

Intent = Literal["BOOK", "PRICE", "CANCEL", "RESCHEDULE", "TRANSFER"]

_ALLOWED_INTENTS: tuple[str, ...] = ("BOOK", "PRICE", "CANCEL", "RESCHEDULE", "TRANSFER")

# Приоритет для выбора primary среди compound intents.
# TRANSFER — эскалация → сразу выходим.
# CANCEL/RESCHEDULE — операции над существующими бронями.
# PRICE — быстро ответить и продолжить BOOK.
_PRIORITY_ORDER: tuple[str, ...] = ("TRANSFER", "CANCEL", "RESCHEDULE", "PRICE", "BOOK")

# Порог доверия — ниже него отдаём в clarification (если primary не эскалация).
_CONFIDENCE_THRESHOLD: float = 0.6

# Ceiling для diameter (типовые R13-R24 для легковых).
_DIAMETER_MIN: int = 13
_DIAMETER_MAX: int = 24


@dataclass
class ExtractedFields:
    """Извлечённые из фразы клиента структурированные поля.

    Все опциональны — LLM возвращает то, что смогло уверенно найти.
    """

    city: str | None = None
    diameter: int | None = None  # 13-24
    station_hint: str | None = None  # район/ландмарк/название СТО
    date_hint: str | None = None  # weekday или raw date


@dataclass
class IntentResult:
    """Результат классификации.

    Fallback-маркер: confidence == 0.0 → downstream должен использовать
    старый агент (это соглашение с pipeline, Wave 2-A).
    """

    primary_intent: Intent
    secondary_intents: list[Intent] = field(default_factory=list)  # max 2
    extracted_fields: ExtractedFields = field(default_factory=ExtractedFields)
    confidence: float = 0.0
    requires_clarification: bool = False
    clarification_question: str | None = None


# ---------------------------------------------------------------------------
# System prompt (<2K chars)
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """Ти — класифікатор інтентів колл-центру шиномонтажу (UA/RU, STT з огріхами).

INTENTS (1 primary + до 2 secondary):
- BOOK: нова броня. «хочу записатися», «переобутися», «записатися на завтра».
- PRICE: вартість. «скільки коштує», «яка ціна», STT: «не песка жить в артист монтажу», «весной сварки».
- CANCEL: скасувати бронь. «скасувати запис», «відмінити», STT: «в кассоватый запуск».
- RESCHEDULE: перенести бронь. «перенести», «змінити час», «перепризначити» (часто CANCEL+BOOK).
- TRANSFER: оператор/менеджер/людина.

COMPOUND: якщо кілька — поверни всі. Пріоритет для primary (downstream сортує): TRANSFER > CANCEL > RESCHEDULE > PRICE > BOOK.

EXTRACTED FIELDS:
- city: Київ/Дніпро/Харків/Одеса/Львів/Запоріжжя… (називний відмінок).
- diameter: R13..R24 як число («R17»/«на 17»/«сімнадцять» → 17).
- station_hint: район/ландмарк/назва СТО (Караван, Запорізьке шосе, Печерськ…).
- date_hint: «завтра», «понеділок», «7 вересня».

AMBIGUOUS:
- STT занадто пошкоджений, жоден keyword не тригерить → requires_clarification=true.
- 2+ інтенти з рівною ймовірністю (напр. «шиномонтаж на Запорізьке шосе» — BOOK чи PRICE?) → confidence<0.6 + requires_clarification=true.
- Питання коротке UA: «Хочете записатися чи дізнатися вартість?».

OUTPUT — тільки JSON:
{"primary_intent":"BOOK|PRICE|CANCEL|RESCHEDULE|TRANSFER","secondary_intents":["..."],"extracted_fields":{"city":null,"diameter":null,"station_hint":null,"date_hint":null},"confidence":0.0-1.0,"requires_clarification":true|false,"clarification_question":null}
"""

# ---------------------------------------------------------------------------
# Keyword helpers для fallback-эвристики (не заменяют LLM, страхуют края)
# ---------------------------------------------------------------------------

# Keyword-триггеры, покрывающие STT-мутации из waves 3-12.
# Используются ТОЛЬКО для эвристики «низкая уверенность + нет keyword-триггеров».
_KEYWORD_TRIGGERS: dict[str, tuple[str, ...]] = {
    "BOOK": (
        "записа",
        "запиш",
        "переобу",
        "монтаж",
        "шином",
        "монтажу",
        "запуск",
        "запис",
    ),
    "PRICE": (
        "ціна",
        "ціну",
        "коштує",
        "вартість",
        "скільки",
        "почём",
        "почем",
        "цена",
        "стоит",
    ),
    "CANCEL": (
        "скасу",
        "відмін",
        "отмен",
        "прибра",
        "убра",
        "касова",
        "кассова",
    ),
    "RESCHEDULE": (
        "перенес",
        "перенос",
        "змінит",
        "змінить",
        "перепризнач",
        "переназнач",
        "перенест",
    ),
    "TRANSFER": (
        "оператор",
        "менедж",
        "людин",
        "человек",
        "переклю",
        "перекл",
    ),
}


def _has_keyword_trigger(text: str) -> bool:
    """True, если во фразе есть хоть один keyword любого интента."""
    low = text.lower()
    for triggers in _KEYWORD_TRIGGERS.values():
        for kw in triggers:
            if kw in low:
                return True
    return False


# ---------------------------------------------------------------------------
# Публичный API
# ---------------------------------------------------------------------------


async def classify_intent(
    customer_text: str,
    session_context: dict[str, Any],
    llm_router: LLMRouter,
) -> IntentResult:
    """Классифицирует customer_text и извлекает поля через LLM.

    Args:
        customer_text: Реплика клиента (после STT).
        session_context: Диалог-контекст:
            - current_step: str | None — текущий FSM-step ("CITY", "DATE", ...) или None.
            - filled_fields: dict — уже собранные поля.
            - dialog_history_tail: list[str] — последние 3-5 реплик (перемешаны bot/user).
            - tenant: str — например "tvoya-shina".
        llm_router: инстанс `src.llm.router.LLMRouter`.

    Returns:
        IntentResult. Если LLM упал/timeout/JSON error → IntentResult с
        primary_intent="BOOK" и confidence=0.0 (fallback-маркер).
    """
    text = (customer_text or "").strip()
    if not text:
        # Пустая фраза — сразу fallback (без LLM).
        logger.warning("intent_classifier: empty customer_text, fallback to BOOK")
        return _fallback_result()

    user_prompt = _build_user_prompt(text, session_context)
    messages = [{"role": "user", "content": user_prompt}]

    provider_override = os.environ.get("INTENT_CLASSIFIER_PROVIDER") or None
    if provider_override is None:
        # Дефолт из памяти: gpt-4.1-mini для voice.
        provider_override = "openai-gpt41-mini"

    start_mono = time.monotonic()
    # Ленивый импорт LLMTask — избегаем session-level импорта `src.llm`,
    # который в свою очередь тянет тяжёлые SDK (anthropic и др.).
    # В тестах mock_llm_router принимает любой task.
    try:
        from src.llm.models import LLMTask

        task_value: Any = LLMTask.AGENT
    except Exception:
        # Fallback для окружений без установленных LLM SDK — router-мок примет строку.
        task_value = "agent"

    try:
        response = await llm_router.complete(
            task=task_value,
            messages=messages,
            system=_SYSTEM_PROMPT,
            max_tokens=400,
            provider_override=provider_override,
        )
    except TimeoutError:
        latency_ms = int((time.monotonic() - start_mono) * 1000)
        logger.warning("intent_classifier: LLM timeout after %dms, fallback to BOOK", latency_ms)
        return _fallback_result()
    except Exception:
        latency_ms = int((time.monotonic() - start_mono) * 1000)
        logger.warning(
            "intent_classifier: LLM raised after %dms, fallback to BOOK",
            latency_ms,
            exc_info=True,
        )
        return _fallback_result()

    latency_ms = int((time.monotonic() - start_mono) * 1000)

    # Провайдер-специфический hint: если LLM положил ответ не в .text, а иначе,
    # унифицированный LLMResponse.text приходит из провайдера.
    raw = (getattr(response, "text", "") or "").strip()
    parsed = _extract_json(raw)
    if parsed is None:
        logger.warning(
            "intent_classifier: JSON parse failed (latency=%dms), raw=%r → fallback to BOOK",
            latency_ms,
            raw[:200],
        )
        return _fallback_result()

    try:
        result = _build_result(parsed, text)
    except _InvalidPayloadError as exc:
        logger.warning(
            "intent_classifier: invalid payload (latency=%dms): %s → fallback to BOOK",
            latency_ms,
            exc,
        )
        return _fallback_result()

    logger.info(
        "intent_classifier: primary=%s confidence=%.2f secondary=%s clarify=%s "
        "intent_classifier_latency_ms=%d",
        result.primary_intent,
        result.confidence,
        result.secondary_intents,
        result.requires_clarification,
        latency_ms,
    )
    return result


# ---------------------------------------------------------------------------
# Внутренние утилиты
# ---------------------------------------------------------------------------


class _InvalidPayloadError(ValueError):
    """LLM вернул JSON, но с невалидным содержимым (нет обязательных полей)."""


def _fallback_result() -> IntentResult:
    """Fallback: primary=BOOK + confidence=0.0 — маркер для downstream."""
    return IntentResult(
        primary_intent="BOOK",
        secondary_intents=[],
        extracted_fields=ExtractedFields(),
        confidence=0.0,
        requires_clarification=False,
        clarification_question=None,
    )


def _build_user_prompt(customer_text: str, ctx: dict[str, Any]) -> str:
    """Компактная user-часть promt'а: минимум контекста, чтобы уложиться в latency budget."""
    current_step = ctx.get("current_step") or "—"
    filled = ctx.get("filled_fields") or {}
    tail = ctx.get("dialog_history_tail") or []
    tenant = ctx.get("tenant") or "—"

    # Обрезаем history tail до последних 5 реплик, чтобы не раздувать prompt.
    tail = list(tail)[-5:]
    tail_str = "\n".join(f"  - {line}" for line in tail) if tail else "  —"

    filled_str = json.dumps(filled, ensure_ascii=False, sort_keys=True) if filled else "{}"

    return (
        f"CUSTOMER: {customer_text}\n\n"
        f"CONTEXT:\n"
        f"  tenant: {tenant}\n"
        f"  current_step: {current_step}\n"
        f"  filled_fields: {filled_str}\n"
        f"  dialog_history_tail:\n{tail_str}\n\n"
        f"Класифікуй CUSTOMER та поверни JSON (див. system)."
    )


_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def _extract_json(raw: str) -> dict[str, Any] | None:
    """Пытаемся выдернуть JSON из ответа LLM.

    Модель может вернуть чистый JSON, или обрамить его текстом/markdown. Пробуем:
    1) json.loads напрямую;
    2) выделить первый top-level `{...}` через regex.
    """
    if not raw:
        return None

    try:
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else None
    except (json.JSONDecodeError, TypeError):
        pass

    match = _JSON_OBJECT_RE.search(raw)
    if not match:
        return None

    try:
        obj = json.loads(match.group(0))
        return obj if isinstance(obj, dict) else None
    except (json.JSONDecodeError, TypeError):
        return None


def _coerce_intent(value: Any) -> Intent | None:
    if not isinstance(value, str):
        return None
    upper = value.strip().upper()
    if upper in _ALLOWED_INTENTS:
        return upper  # type: ignore[return-value]
    return None


def _coerce_intent_list(value: Any) -> list[Intent]:
    if not isinstance(value, list):
        return []
    out: list[Intent] = []
    for item in value:
        intent = _coerce_intent(item)
        if intent is not None and intent not in out:
            out.append(intent)
    return out


def _coerce_extracted_fields(value: Any) -> ExtractedFields:
    if not isinstance(value, dict):
        return ExtractedFields()

    city_raw = value.get("city")
    diameter_raw = value.get("diameter")
    station_raw = value.get("station_hint")
    date_raw = value.get("date_hint")

    city = city_raw.strip() if isinstance(city_raw, str) and city_raw.strip() else None
    station = station_raw.strip() if isinstance(station_raw, str) and station_raw.strip() else None
    date_hint = date_raw.strip() if isinstance(date_raw, str) and date_raw.strip() else None

    diameter: int | None = None
    if isinstance(diameter_raw, bool):
        # bool is subclass of int — но нам не нужно
        diameter = None
    elif isinstance(diameter_raw, int):
        diameter = diameter_raw
    elif isinstance(diameter_raw, str):
        digits = re.findall(r"\d+", diameter_raw)
        if digits:
            try:
                diameter = int(digits[0])
            except ValueError:
                diameter = None
    if diameter is not None and not (_DIAMETER_MIN <= diameter <= _DIAMETER_MAX):
        diameter = None

    return ExtractedFields(
        city=city,
        diameter=diameter,
        station_hint=station,
        date_hint=date_hint,
    )


def _build_result(payload: dict[str, Any], customer_text: str) -> IntentResult:
    """Строит IntentResult из JSON-payload с валидацией и compound-reorder."""
    primary = _coerce_intent(payload.get("primary_intent"))
    if primary is None:
        raise _InvalidPayloadError(
            f"primary_intent missing or invalid: {payload.get('primary_intent')!r}"
        )

    secondary = _coerce_intent_list(payload.get("secondary_intents"))
    # secondary не должен дублировать primary
    secondary = [i for i in secondary if i != primary][:2]

    # Compound reorder: собираем all_intents и выбираем primary по _PRIORITY_ORDER.
    all_intents: list[Intent] = [primary, *secondary]
    reordered = _reorder_by_priority(all_intents)
    new_primary = reordered[0]
    new_secondary = reordered[1:]

    confidence_raw = payload.get("confidence", 0.0)
    try:
        confidence = float(confidence_raw)
    except (TypeError, ValueError):
        confidence = 0.0
    # clamp
    confidence = max(0.0, min(1.0, confidence))

    requires_clarification_raw = payload.get("requires_clarification", False)
    requires_clarification = bool(requires_clarification_raw)

    clarification_question_raw = payload.get("clarification_question")
    clarification_question: str | None = None
    if isinstance(clarification_question_raw, str):
        stripped = clarification_question_raw.strip()
        clarification_question = stripped or None

    extracted = _coerce_extracted_fields(payload.get("extracted_fields"))

    # Ambiguous-эвристика:
    # confidence < 0.6 AND нет keyword-триггеров ни для одного интента → clarification.
    if (
        confidence < _CONFIDENCE_THRESHOLD
        and new_primary != "TRANSFER"  # TRANSFER = escalation, не переспрашиваем
        and not _has_keyword_trigger(customer_text)
    ):
        requires_clarification = True

    # Если требуется clarification, но модель не сгенерила вопрос — подставим дефолт.
    if requires_clarification and not clarification_question:
        clarification_question = _default_clarification_question(new_primary, new_secondary)

    # Если clarification не требуется — гасим clarification_question,
    # чтобы downstream не путался.
    if not requires_clarification:
        clarification_question = None

    return IntentResult(
        primary_intent=new_primary,
        secondary_intents=new_secondary,
        extracted_fields=extracted,
        confidence=confidence,
        requires_clarification=requires_clarification,
        clarification_question=clarification_question,
    )


def _reorder_by_priority(intents: list[Intent]) -> list[Intent]:
    """Сортирует intents по _PRIORITY_ORDER, дедуплицируя."""
    seen: set[str] = set()
    out: list[Intent] = []
    for candidate in _PRIORITY_ORDER:
        if candidate in intents and candidate not in seen:
            out.append(candidate)  # type: ignore[arg-type]
            seen.add(candidate)
    # На случай, если primary был вне priority (не должно быть, но защита) — добавим хвост.
    for i in intents:
        if i not in seen:
            out.append(i)
            seen.add(i)
    return out


def _default_clarification_question(primary: Intent, secondary: list[Intent]) -> str:
    """Fallback-вопрос, если модель не сгенерировала свой clarification_question."""
    intents_set = {primary, *secondary}
    if "BOOK" in intents_set and "PRICE" in intents_set:
        return "Хочете записатися чи дізнатися вартість?"
    if "CANCEL" in intents_set and "RESCHEDULE" in intents_set:
        return "Ви хочете скасувати запис чи перенести на іншу дату?"
    if "TRANSFER" in intents_set:
        return "Уточніть, будь ласка: вас з'єднати з оператором?"
    return "Не зовсім зрозуміло — уточніть, будь ласка, що саме вас цікавить?"


# ---------------------------------------------------------------------------
# Debug helper (не для прод) — сериализация в dict.
# ---------------------------------------------------------------------------


def result_to_dict(result: IntentResult) -> dict[str, Any]:
    """Сериализация IntentResult → dict (для логов/тестов)."""
    return asdict(result)


__all__ = [
    "ExtractedFields",
    "Intent",
    "IntentResult",
    "classify_intent",
    "result_to_dict",
]
