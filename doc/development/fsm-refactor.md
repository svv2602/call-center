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

**TODO (Wave 3-B / T9):** детерминированный state-machine для fitting-flow.

Планируется описать:
- Состояния: `INITIAL`, `NAME`, `CITY`, `STATION`, `DATE`, `TIME`, `COLOR`, `VEHICLE`,
  `CONFIRM`, `BOOKED` (плюс отдельные ветки для CANCEL/RESCHEDULE/PRICE).
- Переходы: какие поля обязательны для каждого перехода, что происходит при `PRICE`
  mid-flow, как обрабатывать `requires_clarification`.
- Anchor cases для FSM concerns (2 из 6 сегодняшних жалоб): Sep 8 auto-pick без запроса
  даты, 14:20 не в списке (state loss).
- Интеграция с классификатором: как FSM реагирует на secondary_intents и
  requires_clarification.

Placeholder до заполнения T9.

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
