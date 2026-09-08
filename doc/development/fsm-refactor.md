# FSM Refactor for Fitting Flow — Design Document

**Дата:** 2026-09-07
**Автор:** Wave 13 FSM refactor team (Wave 1-A: T1 — design intent classifier)
**Версия:** v1
**Статус:** Phase 1 задизайнен; Phase 2 и Phase 3 — placeholder'ы (напишут T9 / Wave 3-B и
T18 / Wave 5-B соответственно).

## Preamble — зачем это делаем

Fitting-flow вырос до 4 сценариев (запис, скасування, перенесення, консультація по вартості).
Все они сейчас управляются гигантским LLM prompt-модулем (`_MOD_CORE` + `_MOD_FITTING`
в `src/agent/prompts.py`), где склеены:
- keyword-детект интента (секция «🚨 ЖОРСТКИЙ ПРАВИЛО KEYWORD-FIRST», ~2500 chars),
- пошаговый чеклист Кроків 0-8,
- инлайн-парсеры (діаметр, дата, час, колір, місто),
- десятки anti-pattern'ов с call_id-anchor'ами waves 4-12.

Модуль превысил 62K chars (см. memory `project_fitting_color_and_price.md` Wave 2 note).
Симптомы attention dilution наблюдаются в проде:
- LLM хроническими даёт `transfer_to_operator(reason="cannot_help")` там, где активный шаг
  требует переспросить (waves 3-5, anchor `dd3dd368`).
- Регрессии keyword-first правила несмотря на явные inline антипаттерны
  (см. `feedback_prompt_escape_hatch_regresses`).
- Confabulation даты/адреса на Кроці 8 несмотря на verbatim guard (Wave 3 #5).

**Wave 13 FSM refactor** разделяет монолит на 3 независимых компонента:
1. **Phase 1 — Intent Classifier** (этот документ, ниже) — отдельный LLM-шаг между STT
   и agent, классифицирует реплику в 5 меток + вытягивает поля.
2. **Phase 2 — FSM Engine** — детерминированный state-machine, ведёт клиента по чеклисту
   без LLM-свободы в переходах (placeholder ниже, писать в Wave 3-B / T9).
3. **Phase 3 — Field Parsers** — вынести inline-парсеры в отдельные функции с тестами
   (placeholder ниже, писать в Wave 5-B / T18).

Wave 13 checklist root: `development-checklists/fsm-refactor-2026-09-07/`.
Wave 1-A checklist: `.../wave-1-A-design-intent-classifier/`.

---

## Phase 1: Intent Classifier

Отдельный backend-шаг, вклинивающийся между STT-корректором и основным LLM agent'ом.
Реализация: `src/agent/intent_classifier.py` (Wave 1-B / T2, уже написан параллельно).
Тесты: `tests/unit/test_intent_classifier.py` (Wave 1-B / T3).
Интеграция в pipeline: Wave 2-A / T6.

### 1.1. Список меток (5)

Классификатор возвращает одну `primary_intent` из следующих 5 меток, плюс до 2 `secondary_intents`
для compound-случаев (см. §1.4).

#### BOOK — новая бронь на шиномонтаж

**Определение:** клиент хочет записаться на шиномонтаж (первичный запис, ещё нет активной брони,
о которой идёт речь).

**Чистые примеры (UA/RU):**
- «Хочу записатися на шиномонтаж»
- «Треба переобутися»
- «Запишіть мене на завтра»
- «Хочу поміняти шини на зимові»
- «Записаться на монтаж»
- «Мне на переобувку»
- «Перевзути коліс»
- «На шиномонтаж у Києві»
- «Запис на шиномонтаж на Правому березі»
- «Треба перевзути авто на всесезонні»

**STT-огрызки (из waves 3-12):**
- «запах на шиномонтаж місто Черкаси» — STT «запис» → «запах»
  (Wave 4 #6, call anchor указан в memory `project_fitting_color_and_price.md`).
- «Bed ласка на шиномонтаж на за парижском шоссе» — STT-шум + landmark
  (call `91a100c0` 2026-08-14).
- «монет рыба в Києве» — STT «мені треба» → «монет рыба»
  (2026-08-31 batch).
- «скотину на монтаж в Києве» — STT-мусор + keyword `монтаж` (call `dbc81d5d` 2026-08-18).
- «мы не трогать надпись Тину на монтаж в Києве» — STT «хотіли б записать шину» → «трогать
  надпись Тину» (call `c9ab41f8` 2026-09-01).
- «traba запасалась она монтажкой жить якобы есть монтажу эрви 17 у Днепре»
  — сегодняшняя жалоба #1 2026-09-07, compound с PRICE.

#### PRICE — вартість шиномонтажа

**Определение:** клиент спрашивает про стоимость шиномонтажа (услуги!), не про цену самих
шин. Соответствует `get_fitting_price` tool'у.

**Чистые примеры (UA/RU):**
- «Скільки коштує шиномонтаж?»
- «Ціна шиномонтажу»
- «Вартість перевзувки»
- «Скільки коштує перевзути 4 колеса?»
- «Тариф на монтаж R17»
- «Прайс на шиномонтаж у Києві»
- «Почому монтаж на R18?»
- «Ціну на монтаж»
- «Сколько стоит шиномонтаж?»
- «Комплексний шиномонтаж, ціна?»

**STT-огрызки:**
- «весной сварки» + монтаж → «вартість» (call `b5b48797`, Wave 2 batch 2026-09-02).
- «якабуду ватре» + монтаж → «скільки буде коштувати» (Wave 2 batch).
- «Со скольки кошка монтаж» → «скільки коштує монтаж» (call 2026-08-28).
- «скидки будут А что вот шиномонтажу Харькове» → «скільки буде коштувати шиномонтаж»
  (call 2026-08-31).
- «артист монтажу в Дніпре» — STT «цікавить» / «ціна» → «артист» (call `7afbc049` 2026-09-01).
- «надпис на монтаж» — STT «цікавить/підкажіть ціну» → «надпис» (call `c9ab41f8` вариация).
- «что в этой монтаж там» — mid-flow PRICE, сегодняшняя жалоба #2 2026-09-07.
- «беляк каравану моменту зашлифовывает» — STT-каша про Караван (Дніпро, ЖМ Караван),
  сегодняшняя жалоба #2.

#### CANCEL — скасувати бронь

**Определение:** клиент хочет отменить существующую бронь. Активирует `cancel_fitting`
(с обязательным prior вызовом `get_customer_bookings` — см. Wave 5 P2 fix, call `dd3dd368`).

**Чистые примеры (UA/RU):**
- «Скасуйте мій запис»
- «Хочу скасувати бронювання»
- «Відмініть запис на шиномонтаж»
- «Прибрати мій запис на завтра»
- «Отменить запись»
- «Відмова від запису»
- «Не поїду, скасуйте»
- «Прибери мою бронь»
- «Скинути запис»
- «Отменити броню на понеділок»

**STT-огрызки:**
- «в кассоватый запуск» → «скасувати запис» (сегодняшняя жалоба #3 2026-09-07).
- «касова запуск» — вариация той же STT-мутации.

#### RESCHEDULE — перенести бронь

**Определение:** клиент хочет перенести существующую бронь на другую дату/время.
Часто эквивалентно CANCEL+BOOK, но обрабатывается атомарно.

**Чистые примеры (UA/RU):**
- «Перенесіть мій запис на четвер»
- «Можна перенести на завтра?»
- «Змінити час запису»
- «Перепризначте на 15:00»
- «Хочу перенести бронь»
- «Перенести шиномонтаж на іншу дату»
- «Пересуньте запис на понеділок»
- «Перенос на 10 вересня»
- «Змінить дату брони»
- «Перепланувати на середу»

**STT-огрызки:** в проде на 2026-09-07 не наблюдалось (RESCHEDULE — редкий intent).
Классификатор использует базовые keyword-триггеры (`перенес*`, `змінит*`, `перепризнач*`,
`переназнач*` — см. `_KEYWORD_TRIGGERS["RESCHEDULE"]` в `src/agent/intent_classifier.py`).

#### TRANSFER — оператор

**Определение:** клиент явно попросил оператора / человека. Немедленный `transfer_to_operator`,
FSM не вовлекается.

**Чистые примеры (UA/RU):**
- «З'єднайте з оператором»
- «Хочу говорити з менеджером»
- «Мені потрібна людина»
- «Переключіть на живого»
- «Оператор, будь ласка»
- «Дайте оператора»
- «Соедините с человеком»
- «Менеджера»
- «Не хочу з ботом, давайте оператора»
- «Переключитися»

**STT-огрызки:** обычно распознаётся чисто (короткие ключевые слова).

⚠️ **Важно:** «Хочу поговорити» / «мені треба ще спитати» — **НЕ** TRANSFER. См.
call `21f61d17` 2026-09-01 antipattern: LLM интерпретировал ответ на вопрос про имя как
`transfer_to_operator(reason="customer_request")` — WRONG. Классификатор
не должен путать generic escalation-подобные фразы с явным запросом оператора.

### 1.2. Input contract

```python
{
    "customer_text": str,                    # последняя реплика после STT + corrections
    "session_context": {
        "current_step": str | None,          # текущий FSM-step ("CITY"/"DATE"/…) или None
        "filled_fields": dict,               # уже собранные поля из FSM
        "dialog_history_tail": list[str],    # последние 3-5 реплик (bot+user перемешаны)
        "tenant": str,                       # например "tvoya-shina"
    },
}
```

**Примечания:**
- `customer_text` — уже прошёл через STT-corrections (Redis `stt:corrections`), не raw ASR.
- `current_step` = `None` при первой реплике звонка (до входа в FSM).
- `dialog_history_tail` обрезается до последних 5 элементов внутри классификатора
  (см. `_build_user_prompt` в `src/agent/intent_classifier.py:310`).
- `tenant` используется в будущем для multi-tenant разграничения (например, если сеть B
  не имеет CANCEL сценария).

### 1.3. Output contract

```python
{
    "primary_intent": "BOOK" | "PRICE" | "CANCEL" | "RESCHEDULE" | "TRANSFER",
    "secondary_intents": list[str],          # max 2 элемента, не дублируют primary
    "extracted_fields": {
        "city": str | None,                  # называется в номинативе: "Київ", "Дніпро"
        "diameter": int | None,              # 13..24 (clamp вне диапазона → None)
        "station_hint": str | None,          # район / ландмарк / название СТО
        "date_hint": str | None,             # weekday / raw date («завтра», «понеділок», «7 вересня»)
    },
    "confidence": float,                     # 0.0..1.0
    "requires_clarification": bool,          # True если ambiguous
    "clarification_question": str | None,    # UA-текст вопроса, если requires_clarification=True
}
```

**Fallback marker:** `confidence == 0.0` → downstream должен использовать старый LLM-агент
(соглашение с pipeline, Wave 2-A). Не путать с `confidence < 0.6` (это clarification-случай).

**Инварианты:**
- `secondary_intents` не содержит `primary_intent`.
- Compound-reorder происходит внутри классификатора (см. `_reorder_by_priority`,
  `src/agent/intent_classifier.py:486`) — вызывающему возвращается уже отсортированный
  список по приоритету.
- `diameter` clamp'ится в [13, 24]; всё вне диапазона → `None`.
- `clarification_question` = `None`, если `requires_clarification=False`.

### 1.4. Compound intent rules

Клиент может выразить два интента в одной фразе. Классификатор возвращает `primary_intent`
и до 2 `secondary_intents`. Priority order для выбора `primary`:

```
TRANSFER > CANCEL > RESCHEDULE > PRICE > BOOK
```

**Правила:**

1. **BOOK + PRICE → primary=PRICE, secondary=[BOOK].**
   Ответить цену сразу, потом продолжить в booking flow.
   Пример: «шиномонтаж R18 в Дніпрі — скільки коштує?» → PRICE(city=Дніпро, diameter=18)
   + BOOK как продолжение.
   Anchor: сегодняшняя жалоба #1 (compound BOOK+PRICE с city+diameter).

2. **CANCEL + RESCHEDULE → primary=RESCHEDULE.**
   Перенос = отмена + новая запись; RESCHEDULE побеждает как более специфичный интент.
   Пример: «скасуйте запис на четвер і перенесіть на п'ятницю» → RESCHEDULE.

3. **BOOK + CANCEL → clarification.**
   Двусмысленно: клиент отменяет старую бронь и хочет новую, или что-то одно?
   `requires_clarification=True`, вопрос: «Ви хочете скасувати запис чи перенести на іншу дату?»
   (default из `_default_clarification_question` для {CANCEL, RESCHEDULE} — уточнить в T3
   если {BOOK, CANCEL} даёт другой вопрос).

4. **TRANSFER + anything → primary=TRANSFER.**
   Escalation всегда побеждает. Downstream немедленно вызывает `transfer_to_operator`.

5. **PRICE mid-flow** (клиент в booking-flow задал вопрос про цену) → primary=PRICE,
   FSM сохраняет state, после ответа возвращается на текущий шаг.
   Anchor: сегодняшняя жалоба #2 2026-09-07 («что в этой монтаж там» посреди booking).

6. **Reorder гарантия.** Если LLM выдал `primary=BOOK, secondary=[TRANSFER]`, классификатор
   принудительно переставит на `primary=TRANSFER, secondary=[BOOK]` (см.
   `_reorder_by_priority`, `src/agent/intent_classifier.py:486`).

### 1.5. Ambiguous cases → clarification

Условия для `requires_clarification=True`:

- **confidence < 0.6 AND нет keyword-триггеров ни для одного интента** → эвристика
  «STT слишком повреждён» → переспросить (см. `_has_keyword_trigger` +
  `_CONFIDENCE_THRESHOLD` в `src/agent/intent_classifier.py:171-178`).
- **2+ интента с примерно равной вероятностью** — LLM сам ставит флаг.
- **`primary_intent == "TRANSFER"`** — clarification НЕ триггерится (эскалация приоритетнее
  переспроса).

**Известные ambiguous паттерны (для тестов и prompt):**

1. **«мне потребности с монтажу на Харьковском шоссе»** — сегодняшняя жалоба #4 2026-09-07.
   Двусмысленно BOOK vs PRICE. Плюс geo-hint: «Харківське шосе» → Київ (см. memory
   `project_fitting_color_and_price.md`, batch 2026-08-31 landmark rule).
   Ожидаемый output: `primary=BOOK, requires_clarification=True, station_hint="Харківське шосе"`,
   question: «Хочете записатися чи дізнатися вартість?».
2. **«мне нужно с монтажом»** — та же двусмысленность без geo.
3. **«шиномонтаж на Запорізьке шосе»** — есть монтаж + landmark, но нет глагола
   (запис/цена?). Классифицируем как BOOK по умолчанию (правило 3 «ЗВЕРХУ ВНИЗ» из
   старого `_MOD_CORE`), но confidence 0.5-0.6 → clarification.
4. **«то ли записать то ли узнать»** — прямая двусмысленность в самом тексте.
5. **Голое «скільки коштує?» без слова «монтаж/шиномонтаж»** — цена шин или услуги?
   `requires_clarification=True`, вопрос: «Вартість шиномонтажу чи ціна самих шин?».
6. **Голая STT-каша без keyword-триггеров** — fallback-эвристика через `_has_keyword_trigger`.

**Default clarification questions** (см. `_default_clarification_question`,
`src/agent/intent_classifier.py:502`):
- `{BOOK, PRICE}` → «Хочете записатися чи дізнатися вартість?»
- `{CANCEL, RESCHEDULE}` → «Ви хочете скасувати запис чи перенести на іншу дату?»
- `{TRANSFER}` (edge case когда clarification всё же нужен) → «Уточніть, будь ласка: вас з'єднати з оператором?»
- Иначе → «Не зовсім зрозуміло — уточніть, будь ласка, що саме вас цікавить?»

### 1.6. LLM prompt sketch

**Модель:** `gpt-4.1-mini` (см. memory `project_gpt5_mini_not_for_voice.md` — gpt-5-mini не
подошёл для voice, оставляем 4.1-mini). Переопределяется через env
`INTENT_CLASSIFIER_PROVIDER` (значение из `DEFAULT_ROUTING_CONFIG.providers`, например
`"openai-gpt41-mini"`).

**Task:** `LLMTask.AGENT` (см. `src/llm/models.py:10`).

**Structured output:** JSON-object через system-prompt + regex-fallback парсинг ответа
(`_extract_json` в `src/agent/intent_classifier.py:337`). НЕ используется OpenAI-специфичный
`response_format={"type":"json_object"}` — LLMRouter должен работать с любым провайдером
из 9 доступных, structured output не у всех.

**System prompt (<2K chars, UA):** описание 5 меток + STT-мутаций + priority order +
extracted fields spec + ambiguous-правила + жёсткий JSON-only output.
Полный текст — в `_SYSTEM_PROMPT` (`src/agent/intent_classifier.py:88-112`), 1.5K chars.

**User prompt:** компактный, включает `CUSTOMER: <text>`, `tenant`, `current_step`,
`filled_fields` (JSON-serialized), `dialog_history_tail` (last 5 lines, bullet-formatted).
См. `_build_user_prompt` (`src/agent/intent_classifier.py:310`).

**Latency budget:** <300ms end-to-end (это hot-path перед основным agent turn).
Метрика логается как `intent_classifier_latency_ms=%d` (INFO log в `classify_intent`).
Мониторинг — через Prometheus histogram (добавить в Wave 2-A / T6 при интеграции в pipeline).

**max_tokens:** 400 (JSON ответ короткий, но нужна страховка на длинные
`clarification_question`).

### 1.7. Fallback behaviour

Классификатор НИКОГДА не должен блокировать основной flow. Все ошибки → graceful fallback.

| Условие | Действие | Log level |
|---|---|---|
| Пустой `customer_text` | `IntentResult(primary=BOOK, confidence=0.0)` без вызова LLM | WARNING |
| `LLMRouter.complete` бросил `TimeoutError` | `IntentResult(primary=BOOK, confidence=0.0)` | WARNING + latency_ms |
| `LLMRouter.complete` бросил любой exception | `IntentResult(primary=BOOK, confidence=0.0)` | WARNING + traceback + latency_ms |
| Ответ LLM не парсится в JSON | `IntentResult(primary=BOOK, confidence=0.0)` | WARNING + raw[:200] |
| JSON без обязательного `primary_intent` | `IntentResult(primary=BOOK, confidence=0.0)` | WARNING |
| `confidence < 0.6` AND нет keyword-триггеров | `requires_clarification=True` | INFO |
| `primary_intent == "TRANSFER"` | Downstream вызывает `transfer_to_operator` сразу, минуя FSM | INFO |

**Downstream contract (для Wave 2-A / T6):**
- `confidence == 0.0` → маркер fallback, использовать старый LLM-agent (полный
  `_MOD_CORE`+`_MOD_FITTING` prompt).
- `confidence >= 0.6` AND `primary_intent != "TRANSFER"` → передать в FSM
  (Phase 2, будущий Wave 3-B).
- `primary_intent == "TRANSFER"` → сразу `transfer_to_operator` без FSM.
- `requires_clarification=True` → бот отвечает `clarification_question`, FSM state
  не двигается (или откатывается на «pending clarification»).

Реализация: `_fallback_result()` в `src/agent/intent_classifier.py:298`.

### 1.8. Test anchors для T3

Тесты пишутся в Wave 1-B / T3 (`tests/unit/test_intent_classifier.py`) с моком LLMRouter.

**Обязательные anchor cases (все 6 сегодняшних жалоб 2026-09-07):**

1. **compound BOOK+PRICE + STT + fields extraction** —
   `«traba запасалась она монтажкой жить якобы есть монтажу эрви 17 у Днепре»`
   → assert `primary_intent="PRICE"`, `secondary_intents=["BOOK"]`,
   `extracted_fields.city="Дніпро"`, `extracted_fields.diameter=17`.

2. **PRICE mid-flow** —
   `«что в этой монтаж там»` при `current_step="STATION"` (booking-flow активен)
   → assert `primary_intent="PRICE"`, FSM state сохраняется downstream.
   Дополнительно: `«беляк каравану моменту зашлифовывает»` → `station_hint="Караван"`.

3. **CANCEL STT-мутация** —
   `«в кассоватый запуск»` → assert `primary_intent="CANCEL"`.

4. **AMBIGUOUS BOOK vs PRICE + landmark** —
   `«потребности с монтажу на Харьковском шоссе»` → assert `requires_clarification=True`,
   `clarification_question` содержит «записатися ... вартість»,
   `extracted_fields.station_hint="Харківське шосе"` (city=Київ подставит downstream mapping,
   не классификатор).

5. **FSM concern — auto-pick без запроса даты (Sep 8)** — этот anchor **НЕ** для intent
   classifier, а для Phase 2 (FSM). Упомянут для явной трассировки: не забыть добавить в
   тесты FSM engine (Wave 3-B / T9).

6. **FSM concern — 14:20 не в списке (state loss)** — аналогично, для Phase 2 (FSM).
   Не для intent classifier.

**Anchor calls из waves 3-12:**

- **`b5b48797`** — PRICE STT «весной сварки» + монтаж →
  assert `primary_intent="PRICE"`.
- **`7afbc049`** — PRICE STT «артист монтажу в Дніпре» →
  assert `primary_intent="PRICE"`, `extracted_fields.city="Дніпро"`.
- **`c9ab41f8`** — BOOK STT «трогать надпис Тину на монтаж в Києве» →
  assert `primary_intent="BOOK"`, `extracted_fields.city="Київ"`.
- **`dd3dd368`** — batch anchor из 7 багов Wave 5, использовать для регрессионных
  проверок: `«не feat Fiat»` НЕ должно давать `TRANSFER(reason="cannot_help")` без
  прямого escalation-keyword'а.

**Отдельно — compound + full field extraction:**

- **«шиномонтаж R18 в Дніпрі»** → `primary=BOOK`, `city=Дніпро`, `diameter=18`,
  `secondary=[]`, `confidence>=0.8`.
- **«шиномонтаж R18 в Дніпрі, скільки коштує?»** → `primary=PRICE`,
  `secondary=[BOOK]`, `city=Дніпро`, `diameter=18`.

**Fallback tests:**

- Empty string → `primary=BOOK, confidence=0.0` без вызова LLMRouter.
- LLMRouter throws `TimeoutError` → `primary=BOOK, confidence=0.0`.
- LLMRouter returns non-JSON string → `primary=BOOK, confidence=0.0`.
- LLMRouter returns JSON без `primary_intent` → `primary=BOOK, confidence=0.0`.

**Compound reorder tests:**

- LLM отдал `primary=BOOK, secondary=[TRANSFER]` → assert реально
  `primary=TRANSFER, secondary=[BOOK]` (priority order enforce).
- LLM отдал `primary=BOOK, secondary=[PRICE, CANCEL]` → assert реально
  `primary=CANCEL, secondary=[PRICE, BOOK]`.

---

## Phase 2: FSM Engine

Детерминированный state-machine для fitting booking flow. Заменяет монолитный `_MOD_FITTING`
prompt (Krok 0..8 free-form инструкции для LLM) на явный набор состояний, переходов и
per-state конфигов. LLM продолжает генерировать реплики, но **что спрашивать и куда идти
после ответа** решает FSM engine на бэкенде.

Цель: убрать attention-dilution regressions (Waves 4C→12 показали, что даже жёсткие prompt
guardrails ломаются на длинных диалогах) через код-level детерминизм. Backend guards из
Waves 4C→12 остаются как страховка — см. §2.8 «Backward compatibility».

### 2.1. State enum

12 основных состояний + 3 side-state (interrupts и терминал).

```python
class FSMState(enum.StrEnum):
    # === Main flow (linear order, gated by field_filled events) ===
    WELCOME = "welcome"        # Greeting + await first user turn
    INTENT  = "intent"         # Route by intent_classifier (Wave 1)
    CITY    = "city"           # Krok 1 part 1 — определить/подтвердить город
    STATION = "station"        # Krok 1 part 2 — get_fitting_stations, выбор точки
    STORAGE = "storage"        # Krok 2 — свои с собой (own) vs зі зберігання (contract)
    DATE    = "date"           # Krok 3 — дата (учёт +3 роб.дн. при contract)
    TIME    = "time"           # Krok 4 — get_fitting_slots → выбор слота
    COLOR   = "color"          # Krok 5 — колір авто (замінив держномер, 2026-08-18)
    BRAND   = "brand"          # Krok 6 — марка авто (з STT-guard рідкісних)
    CONFIRM = "confirm"        # Krok 8 — «Перевіримо: … Підтверджуєте?»
    BOOK    = "book"           # Tool call book_fitting + result parse
    DONE    = "done"           # Krok 9 success farewell → terminal

    # === Side-states (freeze main state, resume after) ===
    PRICE_INTERRUPT  = "price_interrupt"   # Сценарий 4 — консультация ціни
    CANCEL_INTERRUPT = "cancel_interrupt"  # get_customer_bookings + cancel_fitting
    TRANSFER         = "transfer"          # transfer_to_operator → terminal
```

**Rationale по составу:**
- **WELCOME + INTENT** отделены от CITY, чтобы Wave 1 intent_classifier ветвил flow до входа
  в booking-конкретику (fitting / cancel / price / knowledge / operator).
- **CITY и STATION разделены** — по promptu Krok 1 состоит из двух суб-шагов (подтверждение
  города из профиля / из реплики → потом get_fitting_stations + опционально query по району).
- **PHONE отдельного state НЕТ** — CallerID есть в 99% случаев, `session.caller_phone`
  auto-fill. Крок 7 в prompt существует, но реально запускается только если CallerID
  пусто; в FSM это side-branch внутри BOOK precondition, а не отдельный state.
- **NAME отдельного state НЕТ** — Krok 0 (ім'я) обрабатывается либо через профиль
  (`fitting_customer_name` из get_customer_profile), либо через `name_detect` auto-persist
  на первом же turn (Wave 6). Это field_filled event до входа в CITY, не отдельная стадия.
- **PRICE_INTERRUPT** — самая частая mid-flow ветка. Клиент может спросить цену на любом
  Krok 2..8; после ответа возвращаемся в замороженный main state.
- **CANCEL_INTERRUPT** — отдельная ветка, потому что cancel-flow тоже может конвертнуться
  обратно в booking («Скасовано. Записати на інший час?» → RESCHEDULE = cancel + BOOK).
- **TRANSFER** терминальный — после `transfer_to_operator` мы больше не ведём диалог.

### 2.2. Transition table

Формат: `(from_state, event) → to_state, action`. Events кодифицированы как enum:

```python
class FSMEvent(enum.StrEnum):
    # Field-level (parser вернул non-None)
    FIELD_FILLED       = "field_filled"        # name/city/station/date/... — с payload
    PARSER_NULL        = "parser_null"         # parser не смог извлечь поле
    # Confirmation-level
    CONFIRM_YES        = "confirm_yes"         # «так»/«підтверджую»/«ок»/…
    CONFIRM_NO         = "confirm_no"          # «ні»/«не підтверджую»/«потрібно змінити»
    # Interrupt-level (fired by pipeline pre-FSM step)
    INTERRUPT_PRICE    = "interrupt_price"     # клиент вставил цінову фразу
    INTERRUPT_CANCEL   = "interrupt_cancel"    # клиент попросил отменить существующий
    INTERRUPT_RESCHED  = "interrupt_resched"   # клиент попросил перенести
    RESUME             = "resume"              # side-state завершён, вернуться в main
    # Ambient
    TIMEOUT            = "timeout"             # silence — SILENCE_TIMEOUT_SEC=18
    ESCALATE           = "escalate"            # 3× timeout / 3× empty response / hard fail
    TOOL_ERROR         = "tool_error"          # tool вернул {"error": True, …}
    TOOL_SUCCESS       = "tool_success"        # tool вернул валидный payload
```

| # | From              | Event                             | To                | Action                                                                                              |
|---|-------------------|-----------------------------------|-------------------|-----------------------------------------------------------------------------------------------------|
| 1 | WELCOME           | FIELD_FILLED(intent)              | INTENT            | сохранить intent из Wave 1 classifier                                                              |
| 2 | INTENT            | FIELD_FILLED(intent=fitting)      | CITY              | route_to_booking_flow                                                                              |
| 3 | INTENT            | FIELD_FILLED(intent=price)        | PRICE_INTERRUPT   | freeze=None (пусто), enter price flow                                                              |
| 4 | INTENT            | FIELD_FILLED(intent=cancel_fitting) | CANCEL_INTERRUPT | freeze=None, get_customer_bookings                                                                 |
| 5 | INTENT            | FIELD_FILLED(intent=other)        | TRANSFER          | transfer_to_operator(reason=out_of_scope)                                                          |
| 6 | CITY              | FIELD_FILLED(city)                | STATION           | сохранить city, вызвать get_fitting_stations(city)                                                 |
| 7 | CITY              | PARSER_NULL                       | CITY              | re-ask с fallback-списком (Київ/Дніпро/Запоріжжя/Харків/Черкаси)                                    |
| 8 | STATION           | FIELD_FILLED(station_id)          | STORAGE           | pin `session.last_fitting_station_id`, save fitting_stations_seen                                  |
| 9 | STATION           | PARSER_NULL (ambiguous 2+ stations) | STATION         | ask "У якому районі?" (district_options)                                                           |
| 10| STORAGE           | FIELD_FILLED(storage_choice=own)  | DATE              | fitting_storage_choice="own", storage_contract=""                                                  |
| 11| STORAGE           | FIELD_FILLED(storage_choice=contract) | DATE          | вызвать find_storage, pin fitting_storage_contract, min_date = today+3 роб.дн.                     |
| 12| STORAGE           | PARSER_NULL                       | STORAGE           | re-ask одной фразой "Свої з собою чи зі зберігання?"; anti-loop guard: 3-я попытка → default=own   |
| 13| DATE              | FIELD_FILLED(date)                | TIME              | validate: tomorrow ≤ date ≤ today+21; contract → +3 роб.дн.; вызвать get_fitting_slots             |
| 14| DATE              | PARSER_NULL                       | DATE              | re-ask "На яку дату?" (без дефолта!)                                                               |
| 15| TIME              | FIELD_FILLED(time)                | COLOR             | validate: time ∈ fitting_slots_offered; pin selected_fitting_date + selected_fitting_time          |
| 16| TIME              | TOOL_ERROR(no_slots, suggested_date) | DATE           | offer suggested_date; on CONFIRM_YES — вернуться в DATE с новой датой                              |
| 17| TIME              | PARSER_NULL                       | TIME              | re-ask из офера: перечислить slots заново                                                          |
| 18| COLOR             | FIELD_FILLED(color)               | BRAND             | сохранить в fitting_plate (историч. имя поля, содержит COLOR с 2026-08-18)                         |
| 19| COLOR             | FIELD_FILLED(color=«не назвали»)  | BRAND             | ТОЛЬКО если pipeline детектит forget-keyword в 3 last turns (Wave 5 escape-hatch guard)            |
| 20| COLOR             | PARSER_NULL (2× подряд)           | COLOR             | targeted SILENCE_COLOR_REPROMPT_TEXT (Wave 5); третий null → сохранить "колір не розчула"          |
| 21| BRAND             | FIELD_FILLED(brand)               | CONFIRM           | сохранить fitting_vehicle_brand                                                                    |
| 22| BRAND             | PARSER_NULL (rare brand STT)      | BRAND             | ask "Ви маєте на увазі X?" (Krok 6 STT-guard: Zeekr/BYD/NIO/Xpeng/Polestar/…)                      |
| 23| BRAND             | PARSER_NULL (2× подряд)           | BRAND             | Wave 6 type-fallback: спросить тип авто (легкове/SUV/…), сохранить в vehicle_info                  |
| 24| CONFIRM           | CONFIRM_YES                       | BOOK              | вызвать book_fitting(…all pinned fields…)                                                          |
| 25| CONFIRM           | CONFIRM_NO                        | DATE              | вернуть на первый ⏳ / указанный клиентом field для правки                                          |
| 26| CONFIRM           | PARSER_NULL («алло»/«що?»)        | CONFIRM           | повторить фразу "Перевіримо: …" — не трактовать как YES (Wave 5 emergency banner)                  |
| 27| BOOK              | TOOL_SUCCESS                      | DONE              | play success TTS + persist calls.fitting_booking_id via shield (Wave 7)                            |
| 28| BOOK              | TOOL_ERROR(krok3_4_guard)         | TIME              | Wave 7 guard: date/slots не запинены → повторить TIME                                              |
| 29| BOOK              | TOOL_ERROR(escape_hatch)          | COLOR             | Wave 5 guard: color «не назвали» без forget-keyword → пере-спросить                                |
| 30| BOOK              | TOOL_ERROR(type_as_brand)         | BRAND             | Wave 7 guard: vehicle_info = тип без type-fallback вопроса → спросить марку                        |
| 31| BOOK              | TOOL_ERROR(storage_contract_missing) | STORAGE        | Wave 5/6 guard: find_storage matched, но storage_contract="" → пере-спросить (once)                |
| 32| BOOK              | TOOL_ERROR(cross_city / past_krok_2) | STATION         | Wave 9/10 regression_guards: LLM попытался вернуть в CITY → блок + подсказка вернуться в TIME       |
| 33| BOOK              | TOOL_ERROR(weekday_mismatch)      | DATE              | Wave 8/12 weekday guard: date не совпадает с requested weekday → указать правильную дату           |
| 34| BOOK              | TOOL_ERROR(1c_network) 1st        | BOOK              | retry с теми же параметрами один раз                                                               |
| 35| BOOK              | TOOL_ERROR(1c_network) 2nd        | TRANSFER          | transfer_to_operator(reason=fitting_service_unavailable), keep session data                        |
| 36| any(CITY..CONFIRM)| INTERRUPT_PRICE                   | PRICE_INTERRUPT   | fsm_prev_state = current; enter Krok Ц-1..Ц-5                                                       |
| 37| any(CITY..CONFIRM)| INTERRUPT_CANCEL                  | CANCEL_INTERRUPT  | fsm_prev_state = current; get_customer_bookings                                                     |
| 38| PRICE_INTERRUPT   | FIELD_FILLED(diameter)            | PRICE_INTERRUPT   | Wave 12 diameter guard: pin fitting_diameter_client; вызвать get_fitting_price                     |
| 39| PRICE_INTERRUPT   | TOOL_SUCCESS(prices)              | PRICE_INTERRUPT   | озвучить + спросить "Записуємо на монтаж?"                                                         |
| 40| PRICE_INTERRUPT   | CONFIRM_YES                       | STORAGE           | вернуться в основной flow с pinned city+station (НЕ в CITY — регрессия Wave 3 #4)                   |
| 41| PRICE_INTERRUPT   | CONFIRM_NO                        | DONE              | farewell «Дякую за звернення»                                                                      |
| 42| PRICE_INTERRUPT   | RESUME                            | (fsm_prev_state)  | если prev_state ≠ None — размораживаем; если None — DONE                                            |
| 43| CANCEL_INTERRUPT  | TOOL_SUCCESS(cancelled)           | CANCEL_INTERRUPT  | ask "Записати на інший час?"                                                                       |
| 44| CANCEL_INTERRUPT  | CONFIRM_YES                       | DATE              | reschedule flow — reuse city+station+storage_choice from cancelled booking                          |
| 45| CANCEL_INTERRUPT  | CONFIRM_NO                        | DONE              | farewell                                                                                           |
| 46| any               | TIMEOUT                           | (same state)      | play SILENCE_*_REPROMPT_TEXT (per-state variant, Wave 5)                                            |
| 47| any               | ESCALATE                          | TRANSFER          | 3× timeout OR 3× parser_null → transfer_to_operator(reason=silence / cannot_parse)                 |
| 48| any               | INTERRUPT (transfer keyword)      | TRANSFER          | explicit «оператор»/«живая людина» → transfer_to_operator(reason=customer_request)                 |

**Итого: 48 transition rules** (min 20 требовалось).

**Notes:**
- Rows 32/33 напрямую отображают backend guards из `src/agent/regression_guards.py`
  (check_krok1_regression) и inline weekday guard в `_get_fitting_slots` — тесты этих
  guards станут тестами FSM transitions.
- Row 40 (PRICE_INTERRUPT → STORAGE, а не CITY) — anti-pattern из Wave 3 #4 (call
  c1e3792e): после price consult LLM возвращался в другой город. FSM пинит station_id
  из price flow и переходит сразу к STORAGE, обходя CITY/STATION.
- Row 23 (2× brand PARSER_NULL → type-fallback) закрывает антипаттерн `fcfb26a9`
  2026-09-03 (клиент 3 раза мучил STT-омонимами Zeekr/BYD).

### 2.3. Per-state config

Каждый state описан как data structure. Runtime engine читает `STATES[current]` и получает
всё нужное для генерации следующей LLM-реплики + принятия решения о переходе.

```python
from typing import Any, Callable
from dataclasses import dataclass, field

@dataclass(frozen=True)
class StateConfig:
    # LLM-facing
    question_template: str          # Ukrainian phrase to prompt the customer
    silence_reprompt: str | None    # per-state text for TIMEOUT (Wave 5 targeted reprompts)

    # Parser wiring (Phase 3 will implement these modules)
    parser: str                     # name of pure-func in src/agent/parsers/
    parser_input: str = "last_user_turn"  # or "last_3_turns", "compound_first_turn"

    # Routing
    next_state: str                 # linear forward state on FIELD_FILLED
    required_context: list[str] = field(default_factory=list)  # fields that MUST be pinned
    auto_skip_if: Callable[["CallSession"], bool] = lambda s: False

    # Tools
    entry_tool: str | None = None   # tool to call on entering this state (side-effect)
    exit_tool: str | None = None    # tool to call on FIELD_FILLED before transitioning

    # Anti-regression
    max_parser_null: int = 3        # after N nulls in a row → escalate branch
    escalate_target: str = "TRANSFER"  # where to go on ESCALATE

STATES: dict[str, StateConfig] = {
    "WELCOME": StateConfig(
        question_template="{greeting_text}",   # rendered from tenant config
        silence_reprompt=None,                 # no reprompt — barge-in expected
        parser="noop_parser",
        next_state="INTENT",
    ),
    "INTENT": StateConfig(
        question_template="",                  # no question — reactive to first user turn
        silence_reprompt="Я на зв'язку, кажіть.",
        parser="intent_classifier",            # Wave 1 module (already in main)
        next_state="CITY",                     # if intent=fitting; else routed via table row 3-5
    ),
    "CITY": StateConfig(
        question_template="У якому місті вам зручніше?",
        silence_reprompt="Оберіть: Київ, Дніпро, Запоріжжя, Харків, Черкаси.",
        parser="city_parser",                  # covers landmarks, STICKY-CITY, STT variants
        next_state="STATION",
        required_context=["intent"],
        auto_skip_if=lambda s: (
            s.fitting_customer_name is not None
            and s.caller_id                     # CallerID profile has city
            and _profile_city_confirmed(s)      # explicitly confirmed (not silent fill)
        ),
    ),
    "STATION": StateConfig(
        question_template="У [city] є [N] точок у районах: [districts]. У якому вам зручніше?",
        silence_reprompt="Оберіть район, або скажіть «будь-яка».",
        parser="station_parser",               # matches address/district/landmarks
        next_state="STORAGE",
        required_context=["city"],
        entry_tool="get_fitting_stations",     # called on entry, populates fitting_stations_seen
        auto_skip_if=lambda s: (
            len(s.fitting_station_ids) == 1    # single station in city — auto-pin
        ),
    ),
    "STORAGE": StateConfig(
        question_template="Шини привозите свої з собою чи ті, що у нас на зберіганні?",
        silence_reprompt="Скажіть: свої з собою або зі зберігання.",
        parser="storage_choice_parser",        # covers все STT «з собою»/«за собою»/«приложи*»
        next_state="DATE",
        required_context=["station_id"],
        exit_tool="find_storage",              # only if choice=contract
        max_parser_null=2,                     # anti-loop: after 2 nulls, default=own
    ),
    "DATE": StateConfig(
        question_template="На яку дату записуємо?",
        silence_reprompt="Назвіть, будь ласка, дату — наприклад, завтра або п'ятницю.",
        parser="date_parser",                  # UA/RU ordinal, weekday, «завтра», «на 26»
        next_state="TIME",
        required_context=["storage_choice"],   # min_date depends on Krok 2 outcome
    ),
    "TIME": StateConfig(
        question_template="На [date] вільно: [slots]. Який зручніше?",
        silence_reprompt="Оберіть час зі списку.",
        parser="time_parser",                  # HH:MM, ordinal час, STT compression 3-4 цифр
        next_state="COLOR",
        required_context=["date", "station_id"],
        entry_tool="get_fitting_slots",        # populates session.fitting_slots_offered
    ),
    "COLOR": StateConfig(
        question_template="Назвіть, будь ласка, колір автомобіля.",
        silence_reprompt="Скажіть колір — білий, чорний, сірий.",  # Wave 5 targeted
        parser="color_parser",                 # src/agent/color_detect.py (already exists)
        next_state="BRAND",
        required_context=["time"],
        max_parser_null=3,                     # after 3 nulls → «колір не розчула»
    ),
    "BRAND": StateConfig(
        question_template="Яка марка вашого авто?",
        silence_reprompt="Скажіть марку — Toyota, VW, BMW.",
        parser="brand_parser",                 # includes rare-brand STT-guard from Krok 6
        next_state="CONFIRM",
        required_context=["color"],
        max_parser_null=2,                     # then type-fallback branch (row 23)
    ),
    "CONFIRM": StateConfig(
        question_template=(
            "{name}, перевіримо: [date] о [time], [address], "
            "[color] [brand]. Підтверджуєте?"
        ),
        silence_reprompt=None,                 # Wave 5 Krok 8 emergency banner instead
        parser="yes_no_parser",
        next_state="BOOK",
        required_context=["name", "city", "station_id", "storage_choice",
                          "date", "time", "color", "brand"],  # ALL 8 fields must be ✅
    ),
    "BOOK": StateConfig(
        question_template="",                  # no question — tool call only
        silence_reprompt=None,
        parser="noop_parser",                  # result comes from tool, not user
        next_state="DONE",
        entry_tool="book_fitting",             # main tool call; errors route via table 28-35
    ),
    "DONE": StateConfig(
        question_template="{name}, ви записані. СМС підтвердження надійде. Дякуємо!",
        silence_reprompt=None,
        parser="noop_parser",
        next_state="DONE",                     # terminal
    ),
    # Side-states use same StateConfig shape but represent branches
    "PRICE_INTERRUPT": StateConfig(
        question_template="Який діаметр коліс?",
        silence_reprompt="Скажіть, будь ласка, діаметр — від 14 до 21.",
        parser="diameter_parser",              # src/agent/diameter_detect.py (Wave 12)
        next_state="PRICE_INTERRUPT",          # loops until CONFIRM_YES/NO
        entry_tool="get_fitting_stations",     # for_price=true — pin station_id
    ),
    "CANCEL_INTERRUPT": StateConfig(
        question_template="Знайшла [N] запис[и]: [list]. Який скасовуємо?",
        silence_reprompt=None,
        parser="booking_id_parser",
        next_state="CANCEL_INTERRUPT",
        entry_tool="get_customer_bookings",
    ),
    "TRANSFER": StateConfig(
        question_template="Переключаю на оператора, зачекайте, будь ласка.",
        silence_reprompt=None,
        parser="noop_parser",
        next_state="TRANSFER",                 # terminal
    ),
}
```

**Rationale по полям:**
- `question_template` — Ukrainian phrase с placeholder'ами (`{name}`, `[date]`, `[slots]`),
  которые заполняются при рендере из session. LLM не «выдумывает» вопрос — engine
  подставляет актуальные значения из session.
- `parser` — ссылка на pure-функцию из `src/agent/parsers/` (Wave 5-B / T18 создаст модуль).
  Тестируется независимо от FSM.
- `entry_tool` / `exit_tool` — engine автоматически вызывает при переходе. LLM больше не
  решает «пора звонить в get_fitting_stations» — это state config.
- `auto_skip_if(session) -> bool` — condition, при котором state скипается автоматически
  (например CITY скипается, если у клиента 1 город в профиле И он уже подтвердил его в
  прошлом turn). Compound pre-parse (§2.6) массово пре-заполняет и триггерит skip.
- `required_context` — статическая проверка: engine не входит в state, пока перечисленные
  поля не запинены в session. Заменяет чек-лист «✅ Місто/точка ⏳ Дата» из
  `_render_fitting_progress`.
- `max_parser_null` + `escalate_target` — anti-loop. STORAGE и BRAND имеют более агрессивный
  лимит (2 vs дефолт 3), потому что там уже наблюдались 4-5 turn loops.

### 2.4. Session extensions

Добавить в `CallSession` в Wave 4-A (T10). Каждое поле serialize'ится в `to_dict()` /
`from_dict()` для Redis-recovery.

```python
class CallSession:
    # ... existing fields ...

    # === FSM state (Wave 4-A) ===
    fsm_state: str | None = None
    """Current FSM state name (values from FSMState enum). None = FSM disabled or
    pre-init. Persisted so Redis recovery mid-call restarts from correct state."""

    fsm_filled_fields: dict[str, Any] = field(default_factory=dict)
    """Field name → parsed value. Aggregates all successful parser outputs across the
    call. Example:
        {
            "intent": "fitting",
            "city": "Київ",
            "station_id": "000000006",
            "storage_choice": "own",
            "date": "2026-09-10",
            "time": "10:20",
            "color": "синій",
            "brand": "Toyota Prado",
        }
    This is the authoritative source for `required_context` checks and for building the
    book_fitting call. Backend guards from Waves 4C→12 read from here rather than
    scattered session fields (fitting_plate/fitting_vehicle_brand/…)."""

    fsm_prev_state: str | None = None
    """Set when entering a side-state (PRICE_INTERRUPT / CANCEL_INTERRUPT). On RESUME
    event, engine transitions back to this state. None outside interrupts."""

    fsm_history: list[dict[str, Any]] = field(default_factory=list)
    """Ring buffer of recent transitions, capped at 20 entries. Each entry:
        {
            "t": float (unix ts),
            "from": str,
            "to": str,
            "event": str,
            "payload": dict | None,   # e.g. {"field": "date", "value": "2026-09-10"}
        }
    Used for:
    - Debugging (Grafana panel + call transcript enrichment)
    - Regression detection (e.g. detect ping-pong CITY↔STORAGE indicating parser bug)
    - Prompt regression tests (assert expected transition sequence for a scenario)"""
```

**Backward compat:** existing fitting_* fields (`fitting_plate`, `fitting_vehicle_brand`,
`fitting_storage_choice`, `selected_fitting_date`, `selected_fitting_time`,
`fitting_diameter_client`, `fitting_customer_name`, `fitting_stations_seen`,
`storage_contracts_found`, …) **остаются**. FSM engine пишет в оба места (in-memory
proxy) до конца migration path (§2.7). После полного перехода — deprecate индивидуальные
поля, оставить только `fsm_filled_fields`.

### 2.5. Interrupt механизм design

Interrupts (PRICE / CANCEL / TRANSFER) обрабатываются на pre-FSM шаге пайплайна, ДО того
как engine выполнит FIELD_FILLED transition для текущего main state.

**Detection (pipeline layer):**
1. После STT получено `user_text`.
2. Pipeline вызывает `intent_classifier(user_text, current_state, filled_fields)` (Wave 1
   компонент — уже в main).
3. Если classifier вернул `secondary_intents=["price"]` при main-state ∈ {CITY..CONFIRM} —
   fired event **INTERRUPT_PRICE** (row 36 transition table).
4. Аналогично для cancel-keywords (INTERRUPT_CANCEL, row 37) и явных transfer-запросов
   (row 48 → TRANSFER).

**Freeze (engine layer):**
```python
def on_interrupt(event: FSMEvent, session: CallSession) -> None:
    """Snapshot current state before entering side-state."""
    if session.fsm_state in FROZEN_STATES:   # {CITY..CONFIRM}
        session.fsm_prev_state = session.fsm_state
        session.fsm_state = TARGET_SIDE[event]   # PRICE_INTERRUPT / CANCEL_INTERRUPT
        _log_transition(session, from_=session.fsm_prev_state, to=session.fsm_state,
                        event=event.value, payload={"reason": "interrupt"})
```

**Ключевое отличие от prompt-based approach:** FSM НЕ теряет collected fields при
interrupt. `fsm_filled_fields` полностью сохраняется. Клиент, вернувшись из price consult,
не проходит city/station/storage заново.

**Resume (engine layer):**
```python
def on_resume(session: CallSession, resume_target: str | None = None) -> None:
    """Return from side-state to main flow.

    If resume_target is explicit (e.g. row 40: PRICE_INTERRUPT + CONFIRM_YES → STORAGE),
    use it. Otherwise fall back to fsm_prev_state.
    """
    target = resume_target or session.fsm_prev_state
    if target is None:
        # Interrupt entered directly from INTENT (no main state to return to)
        _transition(session, to=FSMState.DONE, event=FSMEvent.RESUME)
        return
    session.fsm_state = target
    session.fsm_prev_state = None
    # Re-emit the target state's question_template so LLM has fresh context
    _emit_question(session, STATES[target])
```

**Question re-emission** — важная деталь. После resume клиент часто «забывает» на чём
остановились, поэтому engine перерендерит `question_template` целевого state перед
возвратом контроля LLM. Пример: PRICE_INTERRUPT → RESUME(target=DATE) → LLM получает
"На яку дату записуємо?" в контексте, а не пустое место после цены.

**Explicit resume targets** (перекрывают fsm_prev_state):
- Row 40 (PRICE_INTERRUPT + CONFIRM_YES «записуємо?») → STORAGE (не CITY!) — pinned
  station_id из price flow становится валидным контекстом для STORAGE.
- Row 44 (CANCEL_INTERRUPT + CONFIRM_YES «записати на інший час?») → DATE — reschedule
  переиспользует city/station/storage_choice отменённой брони.

**Metric:** `fsm_interrupts_total{type, resumed}` — split by (price/cancel/transfer) и
(resumed=true/false, где false = переход в DONE после side-state).

### 2.6. Compound pre-parse (для Wave 4-B / T15)

Первое user turn часто содержит 2+ полей одновременно. Пример реальных фраз:
- «на монтаж у Дніпрі, шини з собою, на 5 серпня» → city + storage + date
- «скільки коштує R16 на Оболоні» → city + district + diameter (price scenario)
- «на Донецьке шосе, завтра о 10» → station (query) + date + time

Prompt-based flow тратит 3-4 turns на переспрос каждого поля по-очереди, теряя половину
изначальной информации в context (LLM «забывает» storage_choice к моменту дат).

**Design:**
```python
def compound_preparse(user_text: str, session: CallSession) -> dict[str, Any]:
    """Run all parsers speculatively on the first turn.

    Returns filled_fields subset. Called once, ONLY when
    session.fsm_state == INTENT (i.e. after WELCOME, before any main state).
    """
    preprobers = [
        ("intent",           intent_classifier),   # Wave 1 (already exists)
        ("city",             city_parser),         # T18 Phase 3
        ("station_query",    station_query_parser),  # extracts "Оболонь"/"Речпорт"/…
        ("date",             date_parser),
        ("time",             time_parser),
        ("storage_choice",   storage_choice_parser),
        ("diameter",         diameter_parser),
        ("color",            color_parser),
        ("brand",            brand_parser),
    ]
    filled: dict[str, Any] = {}
    for field_name, parser in preprobers:
        try:
            value = parser(user_text, session=session)
            if value is not None:
                filled[field_name] = value
        except Exception:
            # A parser bug must not derail compound; log and continue
            logger.warning("preparse: %s raised", field_name, exc_info=True)
    return filled
```

**Engine consumes:**
```python
def enter_booking_flow(session: CallSession, user_text: str) -> str:
    # Wave 4-B compound pre-parse
    prefilled = compound_preparse(user_text, session)
    session.fsm_filled_fields.update(prefilled)

    # Walk states in order, skip those with all required_context already filled AND
    # their target field is in prefilled OR auto_skip_if returns True
    for state_name in [FSMState.CITY, FSMState.STATION, FSMState.STORAGE,
                        FSMState.DATE, FSMState.TIME, FSMState.COLOR, FSMState.BRAND]:
        cfg = STATES[state_name]
        # Field already prefilled? skip state
        if state_name.lower() in prefilled:
            _fire(session, FSMEvent.FIELD_FILLED, payload={
                "field": state_name.lower(),
                "value": prefilled[state_name.lower()],
            })
            continue
        if cfg.auto_skip_if(session):
            continue
        # First non-skippable state — enter it
        session.fsm_state = state_name
        _run_entry_tool(session, cfg)
        return _emit_question(session, cfg)
    # All fields filled from a single compound turn (rare but possible) — jump to CONFIRM
    session.fsm_state = FSMState.CONFIRM
    return _emit_question(session, STATES[FSMState.CONFIRM])
```

**Anti-hallucination:** preparse только заполняет `fsm_filled_fields`, но engine всё равно
валидирует через backend guards (Waves 4C→12) на этапе `entry_tool` / `exit_tool`. Если
preparser неверно распарсил "на 20" как date=20-число (а не time=20:00), backend
`get_fitting_slots(date_from="…")` вернёт TOOL_ERROR (past date / weekday mismatch) и
engine откатит field через transition table.

**Testing hook:** compound_preparse — pure function, testable in isolation. Test суite
использует anchor calls из memory (`project_fitting_color_and_price.md`):
- «на монтаж у Дніпрі, шини з собою, на 5 серпня» → assert prefilled == {intent:fitting,
  city:Дніпро, storage_choice:own, date:2026-08-05}.
- «а еще 3 запизнюсь» → assert prefilled НЕ содержит date=«3-е число» (Wave 8 anti-pattern).

### 2.7. Migration path

Два флага, три фазы. `FSM_ENABLED` управляет живым flow, `FSM_SHADOW_ENABLED` — параллельным
логированием.

**Phase A — Shadow mode (`FSM_ENABLED=false`, `FSM_SHADOW_ENABLED=true`):**
- Существующий LLM-driven pipeline управляет клиентом как обычно.
- FSM engine работает **параллельно**: получает те же user_text / STT events, вычисляет
  what-would-be-next-state, ничего клиенту не отправляет.
- Каждое расхождение (FSM решил бы X, LLM сказал Y) логируется в
  `fsm_shadow_divergence_total{fsm_state, fsm_event, llm_action}` + структурированный лог
  с call_id.
- Работает 1-2 недели на всём trafic. Собираем baseline: какие transitions чаще ломаются,
  где parsers дают false-positive / false-negative.
- Готовые metrics для Grafana: `fsm_shadow_agreement_rate{state}` (target ≥95% перед
  переключением).

**Phase B — Production shadow-guard (`FSM_ENABLED=true` per-tenant), LLM fallback on
parser_null chain):**
- FSM engine становится authoritative source для «что спросить дальше».
- LLM генерирует phrasing (natural Ukrainian) из `question_template` + session context, но
  само поле для fill / transition решает FSM.
- Fallback trigger: если parser вернул PARSER_NULL 3 раза подряд для одного state — engine
  делегирует управление старому LLM-driven flow ("agent freestyle mode") на этот один
  диалог, с pinned session fields как контекстом.
- Rollout gradient: 10% tenants → 50% → 100%. Per-tenant флаг в `tenants.config.fsm_enabled`.

**Phase C — Full production (`FSM_ENABLED=true` глобально, старый LLM-driven code path
удаляется):**
- Только после 2 недель Phase B без regression.
- Backend guards Waves 4C→12 остаются как defence-in-depth (§2.8).
- `fitting_*` legacy fields deprecated → engine пишет только в `fsm_filled_fields`.

**Rollback:** каждая фаза откатывается через feature flag (env var + Redis hot-reload
`fsm:config`) без деплоя. Скрипт `scripts/fsm_rollback.py` при переключении `FSM_ENABLED
=false` копирует `fsm_filled_fields` обратно в legacy `fitting_*` fields для активных
сессий (Redis walk по `call_session:*`).

### 2.8. Backward compatibility

Backend guards Waves 4C→12 в `src/main.py` **остаются в силе после включения FSM**, даже
после Phase C. Rationale:

- Guards защищают на уровне tool contracts, независимо от того, кто (LLM или FSM engine)
  вызвал tool. Если из-за бага в FSM engine `book_fitting` попадёт с невалидным
  `station_id` — Wave 4C+ guard всё равно отклонит.
- LLM в hybrid mode (Phase B fallback branch) может произвольно вызвать любой tool. Guards
  ловят hallucinations из того branch'a.
- Guards уже покрыты тестами и повидали продакшн — снятие их = регрессия по определению.

**Mapping guard → FSM transition** (для рёв FSM в purified mode):

| Wave | Guard                                                       | FSM transition row |
|------|-------------------------------------------------------------|--------------------|
| 4C   | false transfer_to_operator on active step                    | row 48 (only on explicit customer request) |
| 5    | escape-hatch color («не назвали» без forget-kw)              | row 19, 29         |
| 5    | Krok 8 auto-book emergency banner                            | row 26             |
| 5    | targeted silence reprompt (color/brand)                      | row 46 per-state   |
| 5    | cancel_fitting invented booking_id                           | row 43             |
| 6    | name auto-persist + overwrite protection                     | pre-CITY prefill   |
| 6    | Krok 8 confabulation guard                                   | row 26 + banner    |
| 6    | CallerID phone enforcement                                   | pre-BOOK check     |
| 7    | Krok 3/4 date/time pinned by get_fitting_slots               | row 28             |
| 7    | type-as-brand guard                                          | row 30             |
| 7    | booking_id persistence (shield vs cancellation)              | row 27 action      |
| 8    | weekday-date sanity check                                    | row 33             |
| 9    | past_krok_2 regression                                       | row 32             |
| 10   | cross_city regression                                        | row 32             |
| 11   | 1С schema (IdTelegram, AutoNumber translit, StoreTires)      | row 27 action      |
| 12A  | book_fitting post-processing isolated try/except             | row 27 action      |
| 12B  | diameter guard for get_fitting_price                         | row 38             |
| 12B  | RU weekday map extension                                     | row 33             |
| 12C  | Comment field ASCII enforcement                              | row 27 action      |

Guards остаются в `src/main.py` в текущем виде. Единственное изменение по мере FSM
adoption — level логов guard triggers на INFO (в purified FSM mode они должны срабатывать
редко, срабатывание = сигнал FSM bug).

### 2.9. Anchor cases (для integration tests)

Из `project_fitting_color_and_price.md`, six сегодняшних жалоб → 2 покрываются FSM
напрямую:

- **Anchor 1 — «Sep 8 auto-pick без запроса даты»** (Wave 8 anti-pattern): FSM STATES["DATE"]
  не имеет default → row 14 (PARSER_NULL → re-ask), а не «пропоную завтра». Prompt line
  `«⛔ НЕ ПРОПОНУЙ ДАТУ ЗА ЗАМОВЧУВАННЯМ»` становится тестом transition rule.
- **Anchor 2 — «14:20 не в списке (state loss)»** (call 2026-08-31 Wave 7 P0): FSM STATES
  ["TIME"] `required_context=["date", "station_id"]` + row 15 validation `time ∈
  fitting_slots_offered`. State никогда не «забывает» station/date, поэтому переход к
  DATE=«31 серпня» + time=«14:20» валиден без повторного today_too_late fallback.

Оставшиеся 4 жалобы решаются в Phase 3 (parsers) / Phase 4+ (STT rules, TTS voice) — они
не про state machine, а про detection layer.

Placeholder → полная секция. См. Phase 3 ниже — Field Parsers, которые FSM engine дергает.

---

## Phase 3: Field Parsers

**TODO (Wave 5-B / T18):** вынести inline-парсеры полей (діаметр, дата, час, колір, місто,
станція) из монолитного LLM prompt в отдельные pure-функции.

Планируется описать:
- API парсеров: input `raw_text` + context → output `parsed_value | None`.
- Существующие модули для миграции: `src/agent/color_detect.py`,
  `src/agent/ua_datetime.py`, `src/agent/diameter_detect.py`, `src/agent/name_detect.py`,
  `src/agent/vehicle_alias_lookup.py`, `src/agent/vehicle_translit.py`,
  `src/agent/color_translit.py`, `src/agent/preparse.py`.
- Отношения с `ExtractedFields` из Phase 1: классификатор даёт первый pass hints, FSM
  finalize через detailed parsers.
- Тесты для парсеров (следовать паттерну `test_color_detect.py`, 87 tests).

Placeholder до заполнения T18.
