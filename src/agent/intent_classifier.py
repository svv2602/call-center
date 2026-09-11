"""Intent classifier для колл-центра шиномонтажа (FSM refactor, Волна 3-A / T2).

Отдельный модуль: классифицирует реплику клиента (после STT) в один из 5 интентов
BOOK / PRICE / CANCEL / RESCHEDULE / TRANSFER, извлекает структурированные поля и
отдаёт результат FSM-движку (`src/agent/fitting_fsm.py`, состояние `INTENT`).

Ключевое требование волны — **контекстная чувствительность**. Первая версия модуля
(`96879a6`, откачена `c8c6601`) отдавала `PRICE conf=0.95` на реплики «так», «17»,
«мені», «не про», потому что контекст диалога до промпта модели не доходил. Здесь
две линии обороны:

1. `fsm_state` + `dialog_history_tail` всегда рендерятся в user-prompt (см.
   `_build_user_prompt`) — это проверяется тестом на тело LLM-запроса.
2. Backend-guard `_apply_context_guard`: при непустом `fsm_state` короткая или
   подтверждающая реплика без явного лексического свидетельства смены интента
   пинится к интенту текущего состояния, а confidence опускается ниже порога.
   Промпт — не единственная защита (память проекта: «Backend guard beats prompt
   anti-patterns»).

Fallback-контракт: любая ошибка (timeout / JSON / провайдер) → `primary_intent="BOOK"`
и `confidence=0.0`. Ноль confidence — маркер для downstream «классификатору верить
нельзя, работаем старым агентом».

Модель по умолчанию — gpt-4.1-mini (память проекта: gpt-5-mini для голоса не годится).
Переопределяется env-переменными `INTENT_CLASSIFIER_PROVIDER` / `INTENT_CLASSIFIER_MODEL`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:  # pragma: no cover - тайп-хинты, в рантайме src.llm не импортируем
    from src.llm.router import LLMRouter

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Публичный контракт
# ---------------------------------------------------------------------------

Intent = Literal["BOOK", "PRICE", "CANCEL", "RESCHEDULE", "TRANSFER"]

_ALLOWED_INTENTS: tuple[str, ...] = ("BOOK", "PRICE", "CANCEL", "RESCHEDULE", "TRANSFER")

#: Приоритет выбора primary среди compound-интентов.
#: TRANSFER — эскалация, выходим сразу. CANCEL/RESCHEDULE — операции над уже
#: существующей бронью. PRICE дешевле ответить и вернуться к BOOK, чем наоборот.
_PRIORITY_ORDER: tuple[str, ...] = ("TRANSFER", "CANCEL", "RESCHEDULE", "PRICE", "BOOK")

#: Ниже этого порога результат считается неуверенным.
_CONFIDENCE_THRESHOLD: float = 0.6

#: Confidence, до которого guard опускает «смену интента» без свидетельств.
#: Не 0.0 — ноль зарезервирован под fallback-маркер.
_GUARDED_CONFIDENCE: float = 0.4

_DIAMETER_MIN: int = 13
_DIAMETER_MAX: int = 24

_DEFAULT_PROVIDER: str = "openai-gpt41-mini"
_DEFAULT_MODEL: str = "gpt-4.1-mini"

#: Бюджет на LLM-вызов (README: <300ms). Превышение логируем, но результат берём.
_LATENCY_BUDGET_MS: int = 300
#: Жёсткий таймаут: лучше fallback на старого агента, чем зависший звонок.
_LLM_TIMEOUT_SEC: float = 2.0

_MAX_TOKENS: int = 400
_MAX_SECONDARY: int = 2
_MAX_HISTORY_TAIL: int = 5


@dataclass
class ExtractedFields:
    """Структурированные поля, извлечённые из реплики вместе с интентом."""

    city: str | None = None
    diameter: int | None = None  # 13-24
    station_hint: str | None = None  # район / ландмарк / название точки
    date_hint: str | None = None  # weekday или сырая дата


@dataclass
class IntentResult:
    """Результат классификации.

    `confidence == 0.0` — fallback-маркер: LLM недоступен либо ответил мусором,
    downstream обязан откатиться на старый агент.
    """

    primary_intent: Intent
    secondary_intents: list[Intent] = field(default_factory=list)  # max 2
    extracted_fields: ExtractedFields = field(default_factory=ExtractedFields)
    confidence: float = 0.0
    requires_clarification: bool = False
    clarification_question: str | None = None


# ---------------------------------------------------------------------------
# System prompt (<2000 chars — проверяется тестом)
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """Ти — класифікатор інтентів колл-центру шиномонтажу (UA/RU, текст із STT, часто спотворений).

INTENTS (1 primary + до 2 secondary):
- BOOK: новий запис. «хочу записатися», «переобутися», «запишіть на завтра».
- PRICE: вартість. «скільки коштує», «яка ціна монтажу R17», STT: «не песка жить в артист монтажу».
- CANCEL: скасувати наявний запис. «скасувати», «відмінити», STT: «в кассоватый запуск».
- RESCHEDULE: перенести наявний запис. «перенести», «змінити час» (це CANCEL+BOOK, але одна мітка).
- TRANSFER: оператор / менеджер / жива людина.

COMPOUND: повертай усі знайдені мітки. Primary обирається за пріоритетом TRANSFER > CANCEL > RESCHEDULE > PRICE > BOOK.

КОНТЕКСТ — ГОЛОВНЕ ПРАВИЛО:
Якщо у CONTEXT є fsm_state (діалог уже триває), коротка або підтверджувальна репліка («так», «ні», «мені», «не про», голе число, одне слово) — це ВІДПОВІДЬ на питання поточного кроку, а не новий інтент. Голе число під час збору діаметра/часу — це значення поля, НЕ запит ціни. Змінюй інтент лише за явним лексичним свідченням у словах клієнта («скасуйте», «скільки коштує», «оператора»). Сумніваєшся — залишай інтент поточного кроку.

FIELDS:
- city: Київ/Дніпро/Харків/Одеса/Львів/Запоріжжя (називний відмінок).
- diameter: R13..R24 як число («R17», «на 17», «сімнадцять» → 17).
- station_hint: район, ландмарк або назва точки (Караван, Запорізьке шосе, Печерськ).
- date_hint: «завтра», «понеділок», «7 вересня».

AMBIGUOUS: STT пошкоджений і жоден keyword не тригерить, або дві мітки рівноймовірні → confidence<0.6 і requires_clarification=true, clarification_question українською, коротко («Хочете записатися чи дізнатися вартість?»).

OUTPUT — лише JSON, без пояснень:
{"primary_intent":"BOOK|PRICE|CANCEL|RESCHEDULE|TRANSFER","secondary_intents":[],"extracted_fields":{"city":null,"diameter":null,"station_hint":null,"date_hint":null},"confidence":0.0,"requires_clarification":false,"clarification_question":null}
"""


# ---------------------------------------------------------------------------
# Знание об FSM (src/agent/fitting_fsm.py) — нужно guard'у
# ---------------------------------------------------------------------------

#: Интент, который «уже идёт», когда FSM стоит в этом состоянии.
#: Всё, чего нет в мапе, — это main flow записи, то есть BOOK.
_STATE_INTENT: dict[str, Intent] = {
    "PRICE_INTERRUPT": "PRICE",
    "CANCEL_INTERRUPT": "CANCEL",
    "TRANSFER": "TRANSFER",
}

#: Состояния, где короткая реплика ещё НЕ является ответом на вопрос поля:
#: WELCOME/INTENT — это как раз момент, когда интент только определяется.
_PIN_EXEMPT_STATES: frozenset[str] = frozenset({"WELCOME", "INTENT"})

#: Поле, которое собирает состояние (`StateConfig.field_name`). Дублируется здесь
#: намеренно: импорт `fitting_fsm` тянет `CallSession` и метрики, а классификатору
#: нужен только plain-string state — модуль обязан оставаться дешёвым на импорт.
_STATE_FIELD: dict[str, str] = {
    "INTENT": "intent",
    "CITY": "city",
    "STATION": "station_id",
    "STORAGE": "storage_choice",
    "DATE": "date",
    "TIME": "time",
    "COLOR": "color",
    "BRAND": "brand",
    "CONFIRM": "confirmed",
    "PRICE_INTERRUPT": "diameter",
    "CANCEL_INTERRUPT": "booking_id",
}

#: Состояния, где голое число — значение поля, а не новый интент.
_NUMERIC_FIELD_STATES: frozenset[str] = frozenset(
    {"TIME", "DATE", "PRICE_INTERRUPT", "CANCEL_INTERRUPT"}
)


# ---------------------------------------------------------------------------
# Лексика: keyword-триггеры и «короткий ответ»
# ---------------------------------------------------------------------------


def _words(raw: str) -> tuple[str, ...]:
    """Компактный литерал словаря: «а б в» → ("а", "б", "в")."""
    return tuple(raw.split())


#: Триггеры покрывают STT-мутации из anchor calls волн 3-12. Используются только
#: как эвристика (есть ли вообще лексическое свидетельство), не как классификатор.
_KEYWORD_TRIGGERS: dict[str, tuple[str, ...]] = {
    "BOOK": _words("записа запиш запис переобу монтаж шином запуск надпис надпись"),
    "PRICE": _words(
        "ціна ціну ціни коштує вартіст скільки почем почём цена цену стоит сварки прайс"
    ),
    "CANCEL": _words("скасу відмін отмен прибра убра касова кассова анулю"),
    "RESCHEDULE": _words("перенес перенест перенос змінит змінить перепризнач переназнач"),
    # TRANSFER — объединение с `_OPERATOR_KEYWORDS` (`streaming_loop.py:156`) плюс
    # «з'єдна» / «соедин» / «сполуч». Литералы СКОПИРОВАНЫ, а не импортированы,
    # по той же причине, что и у `_STATE_FIELD` выше: `streaming_loop` тянет за
    # собой пол-агента, а классификатор обязан оставаться дешёвым на импорт.
    # Не «чинить» дубликат импортом — сведение двух словарей в один модуль это
    # отдельная задача, и она не делается на файле, который правит соседняя волна.
    #
    # Длинные варианты не нужны: проверка идёт подстрокой, «людин» ловит и
    # «людина», и «жива людина», и «живою людиною»; «менедж» — «менеджера».
    # Три написания «з'єдна» — это форма входных данных, а не избыточность:
    # украинский апостроф приходит из STT то как U+0027, то как «`», то никак.
    "TRANSFER": _words(
        "оператор operator менедж manager консультант людин человек "
        "живий живой живого живому перекл з'єдна зєдна з`єдна соедин сполуч"
    ),
}

#: Слова, которые сами по себе ничего не значат вне контекста вопроса бота.
_FILLER_WORDS: frozenset[str] = frozenset(
    _words(
        # подтверждение / отрицание
        "так да ага угу ок окей добре гаразд звісно звичайно авжеж аякже ясно зрозуміло "
        "ні нi нет не неа ніт нєа "
        # местоимения и служебные
        "я мені мене мій моя моє ми нам воно це то про що як там тут ну а і й та "
        # вежливость / филлеры звонка
        "будь ласка дякую алло ало хвилинку почекайте секунду"
    )
)

_TOKEN_RE = re.compile(r"[\w'’\-]+", re.UNICODE)
_BARE_NUMBER_RE = re.compile(r"^\d{1,4}$")

#: Порог «короткой» реплики. Две лексемы / 16 символов покрывают «так», «17»,
#: «не про», «мені» — ровно те anchor-огрызки, на которых сломалась первая версия.
_SHORT_MAX_TOKENS: int = 2
_SHORT_MAX_CHARS: int = 16


def _tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def _is_bare_number(text: str) -> bool:
    """True, если реплика состоит только из цифр («17», «17 30»)."""
    toks = _tokens(text)
    return bool(toks) and all(_BARE_NUMBER_RE.match(t) for t in toks)


def _is_short_answer(text: str) -> bool:
    """True для реплик, которые вне контекста вопроса бота ничего не значат."""
    stripped = text.strip()
    toks = _tokens(stripped)
    if not toks:
        return True
    if _is_bare_number(stripped):
        return True
    if all(t in _FILLER_WORDS for t in toks):
        return True
    return len(toks) <= _SHORT_MAX_TOKENS and len(stripped) <= _SHORT_MAX_CHARS


def _triggered_intents(text: str) -> set[str]:
    """Множество интентов, для которых во фразе есть keyword-свидетельство."""
    low = text.lower()
    return {
        intent
        for intent, triggers in _KEYWORD_TRIGGERS.items()
        if any(kw in low for kw in triggers)
    }


def _has_keyword_trigger(text: str) -> bool:
    return bool(_triggered_intents(text))


def _has_transfer_evidence(text: str) -> bool:
    """True, если в словах САМОГО клиента есть признак просьбы о человеке.

    Предикат сформулирован в своих терминах и намеренно НЕ пересказывает
    `_should_block_false_transfer` (`streaming_loop.py:215`): тот судит по всей
    истории звонка и по аргументу `reason` инструмента, этот — только по текущей
    реплике, потому что классификатор вызван именно на ней. Разделение
    обязанностей: клиент, попросивший оператора три хода назад и ответивший
    сейчас «так», получает человека через инструмент, а не через вердикт на
    слове «так».

    Держать формулировки раздельно важно ещё и потому, что прод в логе называет
    только ПЕРВЫЙ сработавший гард: пересказ соседнего маскирует и отсутствующий
    гард, и сломанный.
    """
    low = text.lower()
    return any(kw in low for kw in _KEYWORD_TRIGGERS["TRANSFER"])


# ---------------------------------------------------------------------------
# Публичный API
# ---------------------------------------------------------------------------


async def classify_intent(
    customer_text: str,
    session_context: dict[str, Any],
    llm_router: LLMRouter,
) -> IntentResult:
    """Классифицирует реплику клиента и извлекает поля через LLM.

    Args:
        customer_text: Реплика клиента после STT.
        session_context: Контекст диалога:
            - ``fsm_state``: текущее состояние FSM или None;
            - ``current_step``: «CITY» / «DATE» / … или None (используется, если
              ``fsm_state`` не передан);
            - ``filled_fields``: уже собранные поля сессии;
            - ``dialog_history_tail``: последние 3-5 реплик (bot+user вперемешку);
            - ``tenant``: например «tvoya-shina».
        llm_router: инстанс `src.llm.router.LLMRouter` (или совместимый объект).

    Returns:
        IntentResult. При любой ошибке — primary_intent="BOOK", confidence=0.0.
    """
    text = (customer_text or "").strip()
    context = session_context or {}

    if not text:
        logger.warning("intent_classifier: empty customer_text → fallback")
        return _fallback_result()

    user_prompt = _build_user_prompt(text, context)

    start = time.monotonic()
    try:
        raw = await asyncio.wait_for(
            _invoke_llm(llm_router, user_prompt),
            timeout=_LLM_TIMEOUT_SEC,
        )
    except TimeoutError:  # asyncio.TimeoutError is an alias since 3.11
        logger.warning(
            "intent_classifier: LLM timeout, intent_classifier_latency_ms=%d → fallback",
            _elapsed_ms(start),
        )
        return _fallback_result()
    except Exception:
        logger.warning(
            "intent_classifier: LLM call failed, intent_classifier_latency_ms=%d → fallback",
            _elapsed_ms(start),
            exc_info=True,
        )
        return _fallback_result()

    latency_ms = _elapsed_ms(start)

    payload = _extract_json(raw)
    if payload is None:
        logger.warning(
            "intent_classifier: JSON parse failed, intent_classifier_latency_ms=%d raw=%r → fallback",
            latency_ms,
            raw[:200],
        )
        return _fallback_result()

    try:
        result = _build_result(payload, text)
    except _InvalidPayloadError as exc:
        logger.warning(
            "intent_classifier: invalid payload (%s), intent_classifier_latency_ms=%d → fallback",
            exc,
            latency_ms,
        )
        return _fallback_result()

    result = _apply_context_guard(result, text, context)

    if latency_ms > _LATENCY_BUDGET_MS:
        logger.warning(
            "intent_classifier: over budget, intent_classifier_latency_ms=%d (budget=%dms)",
            latency_ms,
            _LATENCY_BUDGET_MS,
        )
    logger.info(
        "intent_classifier: primary=%s secondary=%s confidence=%.2f clarify=%s "
        "fsm_state=%s intent_classifier_latency_ms=%d",
        result.primary_intent,
        result.secondary_intents,
        result.confidence,
        result.requires_clarification,
        _effective_state(context),
        latency_ms,
    )
    return result


# ---------------------------------------------------------------------------
# LLM-вызов
# ---------------------------------------------------------------------------


class _InvalidPayloadError(ValueError):
    """LLM отдал JSON, но без обязательных полей / с мусором в них."""


def _elapsed_ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)


def _provider_key() -> str:
    return os.environ.get("INTENT_CLASSIFIER_PROVIDER") or _DEFAULT_PROVIDER


def _model_name() -> str:
    return os.environ.get("INTENT_CLASSIFIER_MODEL") or _DEFAULT_MODEL


async def _invoke_llm(llm_router: Any, user_prompt: str) -> str:
    """Вызывает роутер и возвращает сырой текст ответа.

    `LLMRouter.complete()` не принимает ``response_format``, поэтому строгость
    JSON держится на system-prompt + `_extract_json`.
    """
    # Ленивый импорт: `src.llm` тянет тяжёлые SDK, модуль должен импортироваться дёшево.
    try:
        from src.llm.models import LLMTask

        task: Any = LLMTask.AGENT
    except Exception:  # pragma: no cover - окружение без LLM SDK
        task = "agent"

    response = await llm_router.complete(
        task=task,
        messages=[{"role": "user", "content": user_prompt}],
        system=_SYSTEM_PROMPT,
        max_tokens=_MAX_TOKENS,
        provider_override=_provider_key(),
    )
    return _response_text(response)


def _response_text(response: Any) -> str:
    """Достаёт текст из LLMResponse."""
    text = getattr(response, "text", None)
    return text.strip() if isinstance(text, str) else ""


# ---------------------------------------------------------------------------
# Prompt-рендеринг
# ---------------------------------------------------------------------------


def _normalize_state(raw: Any) -> str | None:
    """Приводит fsm_state (str | FsmState | None) к UPPERCASE-строке."""
    if raw is None:
        return None
    value = str(raw).strip().upper()
    return value or None


def _effective_state(context: dict[str, Any]) -> str | None:
    """`fsm_state`, а если его не передали — `current_step`."""
    return _normalize_state(context.get("fsm_state")) or _normalize_state(
        context.get("current_step")
    )


def _build_user_prompt(customer_text: str, context: dict[str, Any]) -> str:
    """Собирает user-часть промпта.

    `fsm_state` и `dialog_history_tail` — несущие поля, а не декорация: именно их
    отсутствие в теле запроса дало `PRICE conf=0.95` на «так» в откаченной версии.
    Формат нарочно плоский и предсказуемый, чтобы тест мог проверить сам текст.
    """
    fsm_state = _normalize_state(context.get("fsm_state"))
    current_step = _normalize_state(context.get("current_step"))
    tenant = context.get("tenant") or "—"
    filled = context.get("filled_fields") or {}
    tail = list(context.get("dialog_history_tail") or [])[-_MAX_HISTORY_TAIL:]

    lines = [f"CUSTOMER: {customer_text}", "", "CONTEXT:", f"  tenant: {tenant}"]
    lines.append(f"  fsm_state: {fsm_state if fsm_state else 'null'}")
    lines.append(f"  current_step: {current_step if current_step else 'null'}")

    state_for_field = fsm_state or current_step
    if state_for_field and state_for_field in _STATE_FIELD:
        lines.append(f"  collecting_field: {_STATE_FIELD[state_for_field]}")

    lines.append(
        f"  filled_fields: {json.dumps(filled, ensure_ascii=False, sort_keys=True) if filled else '{}'}"
    )

    lines.append("  dialog_history_tail:")
    if tail:
        lines.extend(f"    - {line}" for line in tail)
    else:
        lines.append("    (порожньо)")

    if state_for_field and state_for_field not in _PIN_EXEMPT_STATES:
        field_name = _STATE_FIELD.get(state_for_field, "—")
        lines.extend(
            [
                "",
                f"УВАГА: діалог уже триває, стан {state_for_field} (бот щойно питав про «{field_name}»). "
                "Коротка чи підтверджувальна репліка — це відповідь на це питання, а не новий інтент. "
                "Змінюй інтент лише за явним лексичним свідченням.",
            ]
        )

    lines.extend(["", "Класифікуй CUSTOMER і поверни JSON (формат — у system)."])
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Разбор ответа
# ---------------------------------------------------------------------------

_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def _extract_json(raw: str) -> dict[str, Any] | None:
    """Достаёт JSON-объект из ответа: чистый, либо обёрнутый текстом/```json."""
    if not raw:
        return None
    try:
        obj = json.loads(raw)
        if isinstance(obj, dict):
            return obj
    except (json.JSONDecodeError, TypeError):
        pass

    match = _JSON_OBJECT_RE.search(raw)
    if not match:
        return None
    try:
        obj = json.loads(match.group(0))
    except (json.JSONDecodeError, TypeError):
        return None
    return obj if isinstance(obj, dict) else None


def _coerce_intent(value: Any) -> Intent | None:
    if not isinstance(value, str):
        return None
    upper = value.strip().upper()
    return upper if upper in _ALLOWED_INTENTS else None  # type: ignore[return-value]


def _coerce_intent_list(value: Any) -> list[Intent]:
    if not isinstance(value, list):
        return []
    out: list[Intent] = []
    for item in value:
        intent = _coerce_intent(item)
        if intent is not None and intent not in out:
            out.append(intent)
    return out


def _coerce_diameter(raw: Any) -> int | None:
    if isinstance(raw, bool):  # bool — подкласс int, но диаметром быть не может
        return None
    value: int | None = None
    if isinstance(raw, int):
        value = raw
    elif isinstance(raw, float):
        value = int(raw)
    elif isinstance(raw, str):
        digits = re.findall(r"\d+", raw)
        if digits:
            value = int(digits[0])
    if value is None or not (_DIAMETER_MIN <= value <= _DIAMETER_MAX):
        return None
    return value


def _coerce_extracted_fields(value: Any) -> ExtractedFields:
    if not isinstance(value, dict):
        return ExtractedFields()

    def _str_or_none(key: str) -> str | None:
        raw = value.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
        return None

    return ExtractedFields(
        city=_str_or_none("city"),
        diameter=_coerce_diameter(value.get("diameter")),
        station_hint=_str_or_none("station_hint"),
        date_hint=_str_or_none("date_hint"),
    )


def _reorder_by_priority(intents: list[Intent]) -> list[Intent]:
    """Упорядочивает интенты по _PRIORITY_ORDER с дедупликацией."""
    ordered: list[Intent] = [i for i in _PRIORITY_ORDER if i in intents]  # type: ignore[misc]
    ordered.extend(i for i in intents if i not in ordered)
    return ordered


def _default_clarification_question(primary: Intent, secondary: list[Intent]) -> str:
    intents = {primary, *secondary}
    if {"BOOK", "PRICE"} <= intents:
        return "Хочете записатися чи дізнатися вартість?"
    if {"CANCEL", "RESCHEDULE"} <= intents:
        return "Ви хочете скасувати запис чи перенести його на іншу дату?"
    if "TRANSFER" in intents:
        return "Уточніть, будь ласка: вас з'єднати з оператором?"
    return "Не зовсім зрозуміло — уточніть, будь ласка, що саме вас цікавить?"


def _build_result(payload: dict[str, Any], customer_text: str) -> IntentResult:
    """JSON-payload → IntentResult, с валидацией и compound-reorder."""
    primary = _coerce_intent(payload.get("primary_intent"))
    if primary is None:
        raise _InvalidPayloadError(
            f"primary_intent missing or invalid: {payload.get('primary_intent')!r}"
        )

    secondary = [i for i in _coerce_intent_list(payload.get("secondary_intents")) if i != primary]
    reordered = _reorder_by_priority([primary, *secondary])
    primary, secondary = reordered[0], reordered[1:][:_MAX_SECONDARY]

    try:
        confidence = float(payload.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))

    requires_clarification = bool(payload.get("requires_clarification", False))

    question_raw = payload.get("clarification_question")
    question = question_raw.strip() or None if isinstance(question_raw, str) else None

    extracted = _coerce_extracted_fields(payload.get("extracted_fields"))

    # Неуверенный результат без единого keyword-триггера — переспрашиваем.
    # TRANSFER не переспрашиваем: эскалацию лучше сделать лишний раз.
    if (
        confidence < _CONFIDENCE_THRESHOLD
        and primary != "TRANSFER"
        and not _has_keyword_trigger(customer_text)
    ):
        requires_clarification = True

    if requires_clarification and not question:
        question = _default_clarification_question(primary, secondary)
    if not requires_clarification:
        question = None

    return IntentResult(
        primary_intent=primary,
        secondary_intents=secondary,
        extracted_fields=extracted,
        confidence=confidence,
        requires_clarification=requires_clarification,
        clarification_question=question,
    )


# ---------------------------------------------------------------------------
# Backend guard (вторая линия обороны)
# ---------------------------------------------------------------------------


def _apply_context_guard(
    result: IntentResult,
    customer_text: str,
    context: dict[str, Any],
) -> IntentResult:
    """Не даёт короткой реплике сменить интент посреди уже идущего сценария.

    Причина существования — anchor calls 2026-09-07: «так» / «17» / «мені» /
    «не про» приходили как `PRICE conf=0.95` и сносили запись в консультацию по
    ценам. Промпт эту проблему уже адресует, но по памяти проекта («Backend guard
    beats prompt anti-patterns») правило дублируется в коде.

    Поля (`extracted_fields`) guard не трогает: его дело — интент, а значение
    поля разберёт парсер текущего состояния FSM.
    """
    # Default-deny на вердикте TRANSFER. Он единственный обрывает звонок
    # (`pipeline.py:2228` — `mark_transfer`, ход LLM не случается вовсе), поэтому
    # обязан опираться на слова клиента, а не только на мнение модели. Замер
    # 2026-09-11: 15 звонков переведены с `transfer_reason=intent_classifier_transfer`,
    # 0 записей, и ни в одной из 14 ложных реплик нет ни одного признака просьбы
    # о человеке.
    #
    # Клауза стоит ДО проверки длины сознательно: всё, что ниже, работает только
    # на короткой реплике, а до перевода дожили ровно длинные.
    #
    # Отказ не отнимает у клиента человека, а стоит ему одного хода: интент
    # остаётся TRANSFER, ход уходит обычному LLM, у которого есть
    # `transfer_to_operator` со своим тестом на свидетельство и своим
    # loop-breaker'ом.
    if result.primary_intent == "TRANSFER" and not _has_transfer_evidence(customer_text):
        capped = min(result.confidence, _GUARDED_CONFIDENCE)
        logger.info(
            "intent_classifier: TRANSFER verdict on %r, but the caller's own words carry "
            "no request for a human → confidence %.2f→%.2f (below the pipeline floor, "
            "so the turn goes to the normal LLM; transfer_to_operator stays available)",
            customer_text,
            result.confidence,
            capped,
        )
        # `min`, а не присваивание: `confidence == 0.0` — fallback-маркер
        # «LLM недоступен», downstream по нему откатывается на старого агента.
        result.confidence = capped

    if not _is_short_answer(customer_text):
        return result

    state = _effective_state(context)
    triggered = _triggered_intents(customer_text)

    # Нет активного состояния (или мы как раз в момент определения интента) —
    # короткая реплика без единого keyword'а не может быть уверенным интентом.
    if state is None or state in _PIN_EXEMPT_STATES:
        # Кап безусловный. Порог `>= _CONFIDENCE_THRESHOLD` (0.6) стоял здесь
        # раньше и выкидывал окно [0.5, 0.6): `FSM_INTERRUPT_CONFIDENCE_FLOOR`
        # в `pipeline.py` — 0.5, то есть вердикт с confidence 0.55 гард не трогал,
        # а pipeline уже действовал по нему. Две константы не сведены, и сводить
        # их эта волна не берётся — она снимает лишний порог перед капом.
        if not triggered:
            capped = min(result.confidence, _GUARDED_CONFIDENCE)
            # Уточняющий вопрос — только если confidence реально понизилась.
            # Иначе `confidence = 0.2` начал бы получать переспрос там, где
            # раньше просто уходил на LLM-ход, а `0.0` (fallback-маркер) —
            # там, где downstream обязан молча откатиться на старого агента.
            if capped < result.confidence:
                logger.info(
                    "intent_classifier: short reply %r without lexical evidence and no fsm_state "
                    "→ capping confidence %.2f→%.2f, asking for clarification",
                    customer_text,
                    result.confidence,
                    capped,
                )
                result.confidence = capped
                result.requires_clarification = True
                if not result.clarification_question:
                    result.clarification_question = _default_clarification_question(
                        result.primary_intent, result.secondary_intents
                    )
        return result

    expected: Intent = _STATE_INTENT.get(state, "BOOK")
    if result.primary_intent == expected:
        return result

    # Смена интента разрешена только при явном лексическом свидетельстве
    # в пользу другого интента («скасуйте», «скільки коштує», «оператора»).
    if triggered - {expected}:
        return result

    logger.info(
        "intent_classifier: context guard — short reply %r in fsm_state=%s, "
        "LLM said %s (conf=%.2f) without lexical evidence → pinned to %s (conf=%.2f)",
        customer_text,
        state,
        result.primary_intent,
        result.confidence,
        expected,
        _GUARDED_CONFIDENCE,
    )
    result.primary_intent = expected
    result.secondary_intents = []
    result.confidence = min(result.confidence, _GUARDED_CONFIDENCE)
    # Переспрашивать нечего: реплика — ответ на вопрос текущего шага.
    result.requires_clarification = False
    result.clarification_question = None

    # Голое число в состоянии, которое собирает число, — это значение поля.
    if (
        state in _NUMERIC_FIELD_STATES
        and _is_bare_number(customer_text)
        and _STATE_FIELD.get(state) == "diameter"
        and result.extracted_fields.diameter is None
    ):
        result.extracted_fields.diameter = _coerce_diameter(customer_text.strip())

    return result


def _fallback_result() -> IntentResult:
    """primary=BOOK + confidence=0.0 — маркер «downstream, работай старым агентом»."""
    return IntentResult(
        primary_intent="BOOK",
        secondary_intents=[],
        extracted_fields=ExtractedFields(),
        confidence=0.0,
        requires_clarification=False,
        clarification_question=None,
    )


__all__ = [
    "ExtractedFields",
    "Intent",
    "IntentResult",
    "classify_intent",
]
