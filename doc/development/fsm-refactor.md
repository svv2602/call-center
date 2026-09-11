# FSM Refactor for Fitting Flow — Design Document

**Дата:** 2026-09-07, итоги дописаны 2026-09-11
**Автор:** Wave 13 FSM refactor team (Wave 1-A: T1 — design intent classifier)
**Версия:** v1.1
**Статус:** Phase 1-3 реализованы, FSM в live с 2026-09-10, работа закрыта.

⚠️ **Разделы ниже — проектный замысел от 2026-09-07, а не описание текущего кода.**
В двух местах замысел разошёлся с реальностью: FSM не стал authoritative и не говорит
своим голосом, а чеклист Кроків 0-8 остался в `_MOD_FITTING`. Разбор — в разделе
«Итоги» в конце документа.

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

Спецификация написана 2026-09-08 (Wave 4-C / T18) **после** того, как Waves 2-B и 4-A уже
написали 8 из 9 детекторов. Это не green-field дизайн: `src/agent/compound_parse.py`
существует, имеет свою модель confidence, свой контракт ключей и 52 теста, которые его
поведение фиксируют. Задача Phase 3 — не изобрести второй слой поверх него, а описать
**второй режим вызова тех же детекторов** и закрыть три дырки, которые Waves 2-B/4-A
оставили сознательно: `storage_choice` живёт в `src/core/pipeline.py`, `station_hint` не
резолвится в `station_id`, `date_hint`/`time_hint` не нормализуются.

Реализация — Wave 5-A (T19), интеграция с движком — Wave 6-B (T21). Здесь только контракт.

### 3.1. Решение: `FieldParser` — targeted-pass обёртка над теми же детекторами

Есть два режима извлечения поля, и они оба нужны:

- **broad pass** — «один проход по реплике, вытащи всё, что видно». Ловит «Київ, завтра на
  десяту» одной репликой и через `auto_skip_if` проматывает три состояния. Это
  `compound_parse()`, он написан и работает.
- **targeted pass** — «состояние спросило одно поле, разбери ответ на него». Отвечает на
  «шістнадцять» после «Який діаметр коліс?» и на «на другу» после списка слотов. Сегодня
  это hand-rolled блок в `_transcript_processor_loop` (`src/core/pipeline.py`, Wave 6/12/14
  auto-persist: `is_name_question` → `detect_name`, `is_diameter_question` →
  `detect_diameter`, `bot_listed_slots` → `detect_time_choice`).

Рассматривались три варианта:

| # | Вариант | Вердикт |
|---|---------|---------|
| A | `FieldParser` — тонкая targeted-обёртка; `compound_parse` остаётся broad-pass; оба зовут одни и те же `detect_*` | **принят** |
| B | `compound_parse` переписывается как оркестратор списка `FieldParser` | отклонён |
| C | `FieldParser` не заводится, FSM зовёт `compound_parse(text, want="time")` | отклонён |

**Почему не B.** Переписывание ломает `FIELD_KEYS` как выходной контракт и переносит все 52
теста Wave 2-B на новый API — при нулевом выигрыше для клиента. Ровно этот класс изменения
(«сначала красиво, потом польза») был откачен в `c8c6601`. Если оркестратор когда-нибудь
понадобится, он вводится отдельной волной поверх уже работающего A.

**Почему не C.** У полей физически разные входы, и в плоский вызов они не влезают:
`detect_time_choice(customer_text, offered_times, allow_hour_only=…)` требует список
предложенных слотов; `resolve_by_alias(conn, utterance)` — **async** и требует соединение с
БД; `detect_diameter` / `detect_name` безопасны только под гейтом `is_diameter_question` /
`is_name_question` от последней реплики бота; `storage_choice` меняет ширину списка
признаков в зависимости от того, задал ли бот вопрос Krok 2. Протащить всё это через
`compound_parse` означает сделать его async и контекстно-зависимым — а на его синхронности
и отсутствии I/O держится shadow-режим: `CallPipeline._run_fsm_deterministic_step` в
докстринге прямо ссылается на «zero LLM requests, zero Store API calls, no await → no
network» как на основание запускать FSM параллельно живому звонку.

**Инвариант варианта A:** на каждое поле — **один** детектор, две точки вызова.
`compound_parse` и `FieldParser.parse()` обязаны звать одну и ту же функцию из
`src/agent/*_detect.py`. Если targeted-парсер заводит собственную регулярку для поля,
которое уже умеет broad-pass, — это дефект ревью, а не оптимизация. Разница между
режимами выражается **только** в контексте на входе и, как следствие, в confidence.

### 3.2. Контракт

Модуль: `src/agent/parsers/` — пакет, один файл на парсер плюс `registry.py`.
`StateConfig.parser` (уже объявлен в `src/agent/fitting_fsm.py`, но сегодня читается только
тестом `tests/unit/test_fitting_fsm.py`) становится ключом реестра, по которому движок
диспатчит.

```python
@dataclass(frozen=True)
class ParseContext:
    """Всё, что любой из парсеров может попросить. Один тип на все девять."""

    customer_text: str                 # сырой STT-текст текущего turn'а
    last_bot_utterance: str = ""       # для is_*_question / bot_listed_slots гейтов
    state: FsmState | None = None      # состояние, которое задало вопрос (None = broad)
    session: CallSession | None = None # fitting_slots_offered, fitting_station_ids, …
    now: datetime | None = None        # для календарной арифметики DATE
    conn: Any = None                   # соединение БД; None ⇒ aresolve запрещён


@dataclass(frozen=True)
class ParseOutcome:
    """Итог разбора одного поля.

    Три исхода, а не два. `NOT_MENTIONED` («клиент про дату не сказал») и
    `UNRESOLVED` («сказал, но мы не разобрали») требуют разных переспросов —
    ту же разницу `CompoundParseResult` уже сохраняет тем, что держит слабые
    совпадения видимыми в `fields`.
    """

    value: Any = None
    confidence: float = 0.0
    spans: tuple[tuple[int, int], ...] = ()
    status: Literal["value", "unresolved", "not_mentioned"] = "not_mentioned"


class FieldParser(Protocol):
    name: str                # совпадает со StateConfig.parser
    field_name: str          # ключ в session.fsm_filled_fields

    def parse(self, ctx: ParseContext) -> ParseOutcome:
        """Синхронно, без I/O, детерминированно. Обязателен у всех парсеров."""

    #: Опционально: доразрешение через сеть/БД. None у 7 из 9.
    aresolve: Callable[[ParseContext, ParseOutcome], Awaitable[ParseOutcome]] | None = None
```

`ParseOutcome` намеренно повторяет форму `compound_parse._Hit` (`value` / `confidence` /
`spans`) — targeted-парсер возвращает то же, что broad-pass кладёт в `CompoundParseResult`,
и seam между ними остаётся конвертацией полей, а не переводом моделей.

#### Асинхронность: `parse()` + опциональный `aresolve()`

Async нужен ровно двум парсерам из девяти, и обоим — по одной причине: сырое слово клиента
надо превратить в идентификатор, который знает только внешняя система.

| Парсер | `parse()` (sync) | `aresolve()` (async) | Зачем |
|---|---|---|---|
| `brand_parser` | `preparse_fitting()` — курируемый whole-word список брендов | `vehicle_alias_lookup.resolve_by_alias(conn, …)` — 14 561 алиас в БД | «Дастер»/«Тігуан» нет в курируемом списке |
| `station_parser` | `compound_parse._detect_station_hint()` — ландмарк-строка | `get_fitting_stations(query=…)` через tool-роутер | ландмарк ≠ `station_id` |

Правила вызова `aresolve` (это и есть разрешение проблемы, а не обход):

1. Движок **всегда** зовёт `parse()`. Он один определяет, есть ли что доразрешать.
2. `aresolve()` вызывается **только** если `parse()` вернул `status != "value"` или
   `confidence < 0.7`, **и** `ctx.conn is not None`, **и** режим FSM — `live`.
3. В `shadow`-режиме `aresolve()` не вызывается никогда. Это сохраняет инвариант
   «no await → no network» из `_run_fsm_deterministic_step` и объясняет, почему сегодня
   `station_hint` намеренно выброшен из `COMPOUND_TO_FSM_FIELD`: резолв — сетевой вызов,
   а shadow-режим сети не касается. После Wave 5-A это остаётся так же: shadow видит
   `station_hint`, но не `station_id`.
4. Отказ `aresolve()` (таймаут, БД недоступна) — **не** ошибка звонка: логируется на
   WARNING с traceback, движок продолжает с результатом `parse()`. `contextlib.suppress`
   на этом пути запрещён (`37fb2d0`).

#### Контекстная зависимость: `ParseContext`, а не разные сигнатуры

`time_parser` — самый контекстно-зависимый из девяти, и он же показывает, почему один тип
контекста лучше девяти сигнатур. Его `parse()` читает из `ctx`:

- `ctx.session.fitting_slots_offered` → список `HH:MM` для `detect_time_choice`;
- `time_detect.bot_listed_slots(ctx.last_bot_utterance)` → флаг `allow_hour_only`
  (голое «на десяту» однозначно только сразу после зачитанного списка; позже «17» — это
  скорее диаметр);
- `ctx.session.selected_fitting_time` → уже запиненный слот, чтобы не перезаписать его.

Ровно эти три входа сегодня собирает Wave-14-блок в `src/core/pipeline.py`. Парсер их не
получает аргументами — он их **достаёт из контекста сам**. Сигнатура у всех девяти одна;
различие живёт внутри реализации, где ему и место. Именно этого не умеет вариант C: там
различие пришлось бы протаскивать через публичный вызов `compound_parse`.

### 3.3. Единственный порог confidence — `0.7`

Порог один на весь FSM: `compound_parse.APPLY_THRESHOLD = 0.7`, продублированный как
`_FSM_APPLY_THRESHOLD` в `src/core/pipeline.py` с явным комментарием «kept local so the
pipeline never silently inherits a loosened upstream threshold». Wave 5-A **не вводит
второго порога** — `FieldParser` использует тот же `APPLY_THRESHOLD`, импортируя его, а не
переобъявляя.

Все прочие числа в этой секции — уровни confidence (`1.0`, `0.9`, `0.6`, …) и счётчики
попыток (`max_parser_null`), а не пороги. Порог сравнения ровно один.

**Контекст поднимает confidence, а не опускает порог.** Это ключ к тому, чтобы targeted-pass
жил с broad-pass на одном пороге. Голое «16»:

| Режим | Контекст | Confidence | Результат |
|---|---|---|---|
| broad (`compound_parse`) | нет | `0.6` (`_diameter_confidence`, бита `«16»` = диаметр \| час \| число месяца) | ниже порога → не применяется, FSM спросит |
| targeted, state=`PRICE_INTERRUPT` | `is_diameter_question(last_bot)` истинно | `1.0` — вопрос снял омонимию | применяется |
| targeted, state=`TIME`, слот `16:00` предложен | `bot_listed_slots(last_bot)` истинно | `1.0` — `detect_time_choice` может выбрать только из уже предложенного | применяется |

Тот же механизм уже реализован — это и есть смысл гейтов `is_diameter_question` /
`is_name_question` / `bot_listed_slots` в пайплайне. Phase 3 их не изобретает, а
формализует как вход в расчёт confidence.

**Что происходит с полем ниже порога.** Оно не выбрасывается. `ParseOutcome` с
`status="unresolved"` доходит до движка и означает: клиент про поле **сказал**, но
однозначно не разобралось.

| Исход `parse()` | FSM-событие | Что говорит бот |
|---|---|---|
| `status="value"`, `confidence ≥ 0.7` | `FIELD_FILLED` | переходит дальше по таблице §2.2 |
| `status="unresolved"` (`confidence < 0.7`) | `PARSER_NULL` | **уточняющий** переспрос: «Ви сказали „на шістнадцяту“ — це час чи діаметр?» |
| `status="not_mentioned"` | `PARSER_NULL` | обычный `question_template` / `silence_reprompt` |

Оба «нулевых» исхода инкрементируют один и тот же счётчик `max_parser_null` (§3.7):
для анти-лупа важно число попыток, а не их причина.

### 3.4. Реестр парсеров

`STATES` уже называет 12 парсеров по строкам. Спецификация обязана пользоваться **этими**
именами — любое другое именование создаёт вторую схему имён поверх существующей.

| `StateConfig.parser` | State | `field_name` | Тип |
|---|---|---|---|
| `noop_parser` | WELCOME, BOOK, DONE, TRANSFER | — | заглушка, всегда `not_mentioned` |
| `intent_classifier` | INTENT | `intent` | Phase 1, уже написан (`src/agent/intent_classifier.py`) |
| `city_parser` | CITY | `city` | field parser |
| `station_parser` | STATION | `station_id` | field parser + `aresolve` |
| `storage_choice_parser` | STORAGE | `storage_choice` | field parser |
| `date_parser` | DATE | `date` | field parser |
| `time_parser` | TIME | `time` | field parser |
| `color_parser` | COLOR | `color` | field parser |
| `brand_parser` | BRAND | `brand` | field parser + `aresolve` |
| `yes_no_parser` | CONFIRM | `confirmed` | `src/agent/confirm_detect.py` — `is_confirmation()`, `asked_for_confirmation()` |
| `diameter_parser` | PRICE_INTERRUPT | `diameter` | field parser + passive |
| `booking_id_parser` | CANCEL_INTERRUPT | `booking_id` | Wave 5-A, отдельный (не входит в девятку) |

**State-bound и passive парсеры.** Не у каждого парсера есть своё состояние:

- `name_parser` — **тринадцатый**, его нет ни в одном `StateConfig`, потому что в §2.1
  сознательно нет состояния NAME (имя приходит из профиля либо auto-persist'ится на первом
  же turn). При этом `name` входит в `required_context` состояния CONFIRM. Значит
  `name_parser` регистрируется как **passive**: движок гоняет его на каждом turn'е, пока
  `fsm_filled_fields["name"]` пуст, под гейтом `name_detect.is_name_question()`. Это
  дословно поведение Wave-6-блока в `src/core/pipeline.py`.
- `diameter_parser` — и state-bound (PRICE_INTERRUPT), и passive: клиент называет диаметр в
  ответ на ценовой вопрос из любого места main flow.

Passive-парсеры пишут поле, но **никогда не двигают состояние** — `FsmEngine.apply_field()`
переводит в `next_state` текущего состояния, поэтому вызов его для чужого поля даёт слепой
прыжок по MAIN_FLOW. Пайплайн уже несёт этот запрет в комментарии
«NEVER loop apply_field() over the mapped fields»; для passive-парсеров он абсолютный:
только `fsm_filled_fields[...] = value`, без `apply_field`.

### 3.5. Девять полей

| Поле | Sync-источник | `aresolve` | Контекст из `ParseContext` | Что даёт targeted-режим сверх broad |
|---|---|---|---|---|
| `city` | `compound_parse._detect_city()` | — | — | ничего: единственное поле, где режимы совпадают полностью |
| `station_id` | `compound_parse._detect_station_hint()` → ландмарк | `get_fitting_stations(query=…)` | `session.fitting_stations_seen`, `session.fitting_station_ids` | превращает hint в `station_id`; выбор по номеру/району из уже зачитанного списка |
| `storage_choice` | `storage_detect.detect_storage_choice()` (Wave 5-A, §3.6) | — | `_bot_is_asking_storage(last_bot)` | широкий легаси-список признаков доступен только когда бот задал вопрос Krok 2 |
| `date` | `compound_parse._detect_date_hint()` (гейт — `date_detect.mentions_date()`) | — | `now`, `session.fitting_storage_choice` | нормализация hint → ISO с учётом +3 роб. дней на contract и окна 21 день |
| `time` | `time_detect.detect_time_choice()`; broad-заготовка — `compound_parse._detect_time_hint()` | — | `session.fitting_slots_offered`, `time_detect.bot_listed_slots(last_bot)` | валидация против реально предложенных слотов; `allow_hour_only` |
| `color` | `color_detect.detect_color()` | — | — | ничего сверх broad; в targeted-режиме дополнительно принимается escape-hatch «не назвали» (row 19) |
| `brand` | `preparse.preparse_fitting()` → ключ `brand` | `vehicle_alias_lookup.resolve_by_alias()` | `conn` | резолв редких марок и STT-омонимов через БД алиасов; rare-brand переспрос (row 22) |
| `name` | `name_detect.detect_name()` | — | `name_detect.is_name_question(last_bot)` | broad-режим (`compound_parse._detect_name`) берёт **только** явное «мене звати X»; targeted снимает гейт, потому что вопрос уже задан |
| `diameter` | `diameter_detect.detect_diameter()`, confidence — `compound_parse._diameter_confidence()` | — | `diameter_detect.is_diameter_question(last_bot)` | голое «16» поднимается с `0.6` до `1.0` |

Модули `src/agent/vehicle_translit.py` (`normalize_alias()`, `translit_lat_to_cyr()`) и
`src/agent/color_translit.py` (`translit_color_to_latin()`) — не парсеры, а нормализаторы на
выходе: первый нормализует ключ поиска внутри `resolve_by_alias`, второй готовит значение
для 1С. `src/agent/ua_datetime.py` (`date_to_words()`, `time_to_words()`) — форматтер
**наружу**, в TTS-реплику; в цепочку разбора он не входит. `src/stt/numeral_parser.py`
(`words_to_digits()`) работает до парсеров, на уровне STT-коррекции.

#### Долг, который Phase 3 обязана закрыть в Wave 5-A: `date` и `time` пишутся сырыми

Сегодняшний seam `map_compound_fields_to_fsm()` (`src/core/pipeline.py`) кладёт
`date_hint → date` и `time_hint → time` **без нормализации и без валидации**. Значит
`session.fsm_filled_fields["date"]` может содержать строку `"завтра"`, а не ISO-дату из
примера §2.4, а `["time"]` — `"14:00"`, ни разу не сверенное с `fitting_slots_offered`
(это то самое условие row 15 и Anchor 2). В shadow-режиме это безвредно: до `book_fitting`
значения не доходят. При переключении на live — это дефект уровня P0.

Требование к Wave 5-A: seam перестаёт писать сырые hint'ы в `date` / `time`. Либо hint'ы
маппятся в одноимённые FSM-ключи `date_hint` / `time_hint` и нормализуются
targeted-парсерами при входе в DATE / TIME, либо `date_parser.parse()` с непустым
`ctx.now` возвращает уже ISO. Первый вариант предпочтителен: он не требует `now` в
shadow-режиме и оставляет календарную арифметику там, где она сейчас (tool layer).

### 3.6. STORAGE: два уровня признаков (долг Wave 4-A)

Wave 4-A была обязана выводить `storage_choice`, иначе цепочка `auto_skip_if` залипала на
STORAGE, а трогать `compound_parse` она не стала: полезная половина признаков зависит от
того, задал ли бот вопрос Krok 2, а у `compound_parse` нет параметра «последняя реплика
бота». Детектор осел в оркестраторе — `detect_storage_choice()` в `src/core/pipeline.py`.

В пайплайне живут **два разных списка**, и это не дублирование, а разная цена ошибки:

| Список | Ширина | Потребитель | Цена ложного срабатывания |
|---|---|---|---|
| `_STORAGE_OWN_HINTS` + `_STORAGE_OWN_HINTS_WHEN_ASKED` | широкий; содержит STT-огрызки «тобою», «за собою», «не маю» | легаси-подсказка LLM (флаг ✅ в `fitting_progress`) | дёшево: LLM всё равно переспросит |
| `_STORAGE_SELF_EVIDENT_OWN` / `_STORAGE_SELF_EVIDENT_CONTRACT` | узкий; только самоочевидные фразы | основание **пропустить состояние FSM** | дорого: вопрос молча не задан |

Слить их в один список нельзя — вернётся баг «пропустили STORAGE по слову „тобою“».
Разделение выражается **через confidence, при одном пороге `0.7`**:

| Признак | Условие | Confidence | Следствие |
|---|---|---|---|
| `_STORAGE_SELF_EVIDENT_OWN` / `_CONTRACT` | контекст не нужен | `1.0` | ≥ порога → можно пропустить состояние STORAGE |
| широкий легаси-список | `_bot_is_asking_storage(last_bot)` истинно | `0.9` | ≥ порога, но достижимо **только внутри состояния STORAGE** — там вопрос уже задан, пропускать нечего |
| широкий легаси-список | бот про зберігання не спрашивал | `0.5` | < порога → `status="unresolved"`, кормит легаси-подсказку LLM, состояние не пропускает |

Уровень `0.9` физически недостижим в broad-режиме: у `compound_parse` нет
`last_bot_utterance`. Одно и то же поле имеет разную confidence в двух режимах — и это
самый наглядный аргумент за вариант A из §3.1: вариантом C такое не выражается, потому что
режим у него один.

**Неоднозначность → `None`.** Реплика с признаками обоих вариантов («свої привезу, чи те
що у вас на зберіганні?») даёт `value=None`, `status="unresolved"`. Сегодня
`detect_storage_choice()` уже так делает для узких списков — Wave 5-A распространяет то же
правило на широкий. Монетка не подбрасывается: неоднозначность разрешается вопросом.

**План миграции (Wave 5-A):**

1. Новый модуль `src/agent/storage_detect.py`. Переносятся **дословно**, без переупорядочивания и
   переформулировок: `_STORAGE_OWN_HINTS`, `_STORAGE_OWN_HINTS_WHEN_ASKED`,
   `_STORAGE_ASKING_MARKERS`, `_STORAGE_SELF_EVIDENT_OWN`, `_STORAGE_SELF_EVIDENT_CONTRACT`,
   `_bot_is_asking_storage()`, `detect_own_tires()`, `detect_storage_choice()`.
2. `src/core/pipeline.py` импортирует `detect_own_tires` и `_bot_is_asking_storage` из нового
   модуля. Легаси-нудж в `_transcript_processor_loop` не меняется ни на символ:
   при `FSM_ENABLED=false` поведение обязано остаться байт-в-байт прежним, и это проверяется
   тестом на равенство результатов до/после переноса на корпусе реплик из комментариев к
   спискам (звонки 2026-08-03 14:55/14:56, 2026-09-03 10:37/10:38).
3. `compound_parse.FIELD_KEYS` расширяется на `"storage_choice"`, `compound_parse()` начинает
   класть в него **только** контекстно-свободный уровень (`1.0` / отсутствие). Тесты Wave 2-B
   от этого не падают: единственная проверка контракта в `tests/unit/test_compound_parse.py` —
   `set(result.fields) - set(FIELD_KEYS)`, то есть подмножество; добавление ключа разрешено.
   Новые тесты на сам ключ добавляются в Wave 5-A.
4. `COMPOUND_TO_FSM_FIELD` получает `"storage_choice": "storage_choice"`, а
   `map_compound_fields_to_fsm()` теряет спец-случай: параметр `customer_text` и прямой вызов
   `detect_storage_choice()` уходят, значение приходит по общему пути с общим порогом.
5. `pipeline.detect_storage_choice` удаляется (переехал целиком); `pipeline.detect_own_tires`
   удаляется как имя в `pipeline.py`, оставаясь ре-экспортом из `storage_detect`.

### 3.7. Fallback rules

В `StateConfig` уже объявлены три поля. Одно живое, два — нет.

| Поле | Статус на 2026-09-08 | Судьба |
|---|---|---|
| `silence_reprompt` | **читается**: `FsmEngine.silence_reprompt()`, `src/agent/interrupts.py` (PRICE и CANCEL хендлеры) | без изменений |
| `max_parser_null` | объявлено (дефолт 3; STORAGE=2, COLOR=3, BRAND=2), **не читается нигде в `src/`** | получает читателя в Wave 6-B, см. ниже |
| `escalate_target` | объявлено (`FsmState.TRANSFER`), **не читается нигде в `src/`** | сохраняется, но смысл сужается, см. ниже |

Это тот же паттерн, что метрика `fsm_interrupt_total` в Wave 1-C: поле завели и забыли.
Спецификация обязана либо назвать читателя и волну, либо предложить удалить. Оба поля
**сохраняются**, потому что у обоих есть строка в таблице переходов §2.2 (rows 12/20/23 —
исчерпание попыток; row 47 — `any + ESCALATE → TRANSFER`); удалять пришлось бы вместе с
этими строками.

**Счётчик.** Wave 5-A добавляет в `CallSession` поле
`fsm_parser_null_counts: dict[str, int]`, сериализуемое в `to_dict()` / `from_dict()`
ровно как существующий `interrupt_counts`. Ключ — имя состояния. Сбрасывается при
`FIELD_FILLED` для поля этого состояния и при выходе из состояния. Прототип помощников —
`_counts()` / `_is_capped()` / `_bump()` / `_reset_followups()` в `src/agent/interrupts.py`:
там же лежит и обоснование, почему счётчик должен быть в сессии (переживает Redis-recovery),
и почему он вообще нужен (`c8c6601`: хендлер повторил один вопрос пять turn'ов подряд).

**Читатель.** Wave 6-B добавляет `FsmEngine.on_parser_null(field_name) -> FsmState`:

```
count = ++fsm_parser_null_counts[state]
if count < cfg.max_parser_null:
    → PARSER_NULL-строка таблицы (§2.2 rows 7/9/12/14/17/20/22/23/26): переспрос
if count >= cfg.max_parser_null:
    if cfg.on_null_exhausted is not None:  → ветка состояния (см. таблицу ниже)
    else:                                  → FsmEvent.ESCALATE → cfg.escalate_target
```

**`escalate_target` — не действие при исчерпании, а последний рубеж.** Дефолт
`FsmState.TRANSFER` верен для шести состояний, но был бы ложью для трёх: их собственные
комментарии в `STATES` описывают совсем другое поведение. Поэтому Wave 5-A добавляет в
`StateConfig` одно опциональное поле — по образцу уже существующего `auto_skip_if`,
единственного callable в этом dataclass:

```python
on_null_exhausted: Callable[[CallSession], FsmState] | None = None
```

| State | `max_parser_null` | `on_null_exhausted` | Row §2.2 |
|---|---|---|---|
| STORAGE | 2 | пишет `storage_choice="own"`, возвращает `DATE` | 12 |
| COLOR | 3 | пишет `color="колір не розчула"`, возвращает `BRAND` | 20 |
| BRAND | 2 | включает type-fallback (вопрос про тип авто), возвращает `BRAND` | 23 |
| CITY, STATION, DATE, TIME, CONFIRM, PRICE_INTERRUPT, CANCEL_INTERRUPT | 3 (дефолт) | `None` | 47 → TRANSFER |

BRAND — единственный, у кого ветка не сводится к «записать дефолтное значение»: она меняет
задаваемый вопрос. В Wave 6-B это решается либо флагом в сессии, который читает
`render()` состояния BRAND, либо отдельным состоянием `BRAND_TYPE_FALLBACK`. Второй вариант
чище (`on_null_exhausted` остаётся без побочных эффектов, как `auto_skip_if`), но добавляет
16-е состояние — решение принимает Wave 6-B.

**Никакого «escalate на LLM-agent».** Такой ветки в коде нет: `escalate_target` — это
`FsmState.TRANSFER`, то есть оператор. Делегирование обратно LLM описано отдельно и в другом
месте — как Phase B migration path (§2.7, «agent freestyle mode»), и оно управляется
feature-флагом, а не счётчиком парсера.

### 3.8. `parser_input` — предложение удалить

`StateConfig.parser_input` (`"last_user_turn"` / `"last_3_turns"` / `"compound_first_turn"`)
объявлен, нигде не читается и ни одним состоянием не переопределён — все 15 используют
дефолт. С введением `ParseContext` он избыточен: парсер, которому нужны три последних
turn'а (единственный кандидат — escape-hatch «не назвали» у COLOR, row 19), достаёт их из
`ctx.session.dialog_history` сам, как это делает Wave-5-guard сегодня. Wave 5-A либо удаляет
поле, либо даёт ему читателя; третьего состояния («объявлено и забыто») быть не должно.

### 3.9. Тесты Wave 5-A

- Один файл на парсер, `tests/unit/test_parsers_<field>.py`. Паттерн — существующий
  `tests/unit/test_color_detect.py`.
- Корпус — **не выдуманные** фразы: STT-огрызки из комментариев к спискам в
  `compound_parse.py`, `pipeline.py` и `prompts.py` с указанными там call-id.
- На каждый парсер обязательны три кейса: значение выше порога, `unresolved` ниже порога,
  `not_mentioned`. Без третьего невозможно отличить регрессию «перестал детектить» от
  «стал детектить неуверенно».
- **Мутационная проверка обязательна** для `storage_choice_parser`: тест, который зелёный
  и при `confidence=0.9`, и при `confidence=0.5` для широкого списка, не проверяет ничего.
  Порог проламывается вручную, тест обязан покраснеть.
- `AsyncMock` для `conn` в `brand_parser` и для tool-роутера в `station_parser` — **только**
  с `spec=`: мок без spec делает зелёным путь, которого в проде нет.
- Эквивалентность легаси: тест, сравнивающий `storage_detect.detect_own_tires()` с
  до-миграционным поведением на корпусе из §3.6 п. 2.

### 3.10. Чего Phase 3 не делает

- Не переписывает `compound_parse` (это был бы вариант B).
- Не переносит календарную арифметику (окно 21 день, +3 рабочих дня на contract) из tool
  layer в парсеры — это Phase 4+.
- Не трогает LLM-промпт: снятие соответствующих блоков `_MOD_FITTING` возможно только после
  Phase C миграции (§2.7), когда FSM authoritative.
- Не удаляет backend guards Waves 4C→12 — §2.8 остаётся в силе.

---

## Итоги (2026-09-11)

Работа закрыта после 23 чеклистов и 12 послерелизных волн правок по живым звонкам.
Раздел описывает, что получилось, что не получилось и почему остановились здесь.

### Что построено

| Модуль | Что делает |
|---|---|
| `src/agent/fitting_fsm.py` | 15 состояний, 48 переходов, движок и таблица переходов данными |
| `src/agent/parsers/` | 13 `FieldParser` + registry; у части есть `aresolve` для сетевого резолва |
| `src/agent/intent_classifier.py` | Классификация интента реплики |
| `src/agent/interrupts.py` | Обработчики PRICE / CANCEL — единственный путь FSM, который забирает ход |
| `src/agent/compound_parse.py` | Разбор составной реплики («Київ, завтра на десяту») |
| `src/core/pipeline.py` | Проводка: сетевой резолв → детерминированный шаг → interrupt'ы |

Режимы off / shadow / live через `FSM_ENABLED` + `FSM_SHADOW_MODE`; откат — снятие
переменной окружения.

### Метрики before/after

| Метрика | 2026-09-07 | 2026-09-11 | Цель плана |
|---|---|---|---|
| `_MOD_FITTING` | 62,236 | **65,104** | ≤10,000 |
| `_MOD_CORE` | — | 25,197 | — |
| Backend guards | ~25 | ~30 | «преемники в FSM, guards сняты» |
| Тесты | 116 | **1028** | — |
| Записей на монтаж | — | 8 за 27 живых звонков (первые 18 часов live) | ≥95% завершённых booking-flow |

**Промпт вырос, а не сократился, и цель ≤10,000 не достигнута.** Это главный
отрицательный результат, и он описан ниже, а не спрятан.

### Что не сделано и почему

**1. FSM не стал authoritative и не говорит своим голосом.**
Волна 7-0 (`33d926e`) включила три говорящих состояния и была откачена через 34 минуты
(`3b93213`). Причина структурная: реплика FSM подавляет ход LLM, а ход LLM — единственное
место, где выполняются tool calls. На звонке `3639c0b4` STORAGE заговорил поверх
«Харківське шосе», обязательный `get_fitting_stations(query=…)` не выполнился, станция была
выбрана вслепую. `FSM_VOICE_STATES` пуст и должен таким остаться — §2.7 (Phase C миграции)
в исходном виде неисполним.

**2. Чеклист Кроків 0-8 остался в промпте.**
Снятие блоков `_MOD_FITTING` было возможно «только после Phase C», а Phase C не состоялась.
Вырезаны лишь 4 правила «КРИТИЧНИЙ КОНТРАКТ ІНСТРУМЕНТІВ», у которых есть кодовый
преемник — default-deny гард в `_book_fitting_with_metric`, чьё сообщение об отказе заново
выдаёт инструкцию в момент нарушения (`5b84eca`, `d21fb6f`, −1,601 символ). Перед удалением
гарды закрыты 29 тестами через зарегистрированный обработчик; 13 из 13 мутаций убиты.

Разобрано также, из чего состоят 46K чеклиста: это **не формулировки вопросов** (все
`question_template` вместе — 570 символов), а оркестрация инструментов — 63 упоминания имён
tools: какой, с какими аргументами, в каком порядке.

**3. Backend guards не сняты.**
§2.8 остаётся в силе. Пока аргументы инструментов строит LLM, гарды — единственная защита,
и они обязаны быть default-deny.

**4. Предложение «FSM владеет tool calls» рассмотрено и отложено.**
`PROPOSAL-fsm-owns-tools.md` предлагал инвертировать сплит: FSM владеет обязательными
действиями, LLM — формулировкой. Архитектурно это верно, но замер 2026-09-11 опроверг
предпосылку: за 30 дней на 302 действия со `station_id` станций, взятых не из результата
более раннего `get_fitting_stations`, оказалось **ноль** (два кандидата объяснены: один —
внутри 34-минутного окна Волны 7-0, второй — перенос записи, где `station_id` пришёл из
`get_customer_bookings`). Строить action runtime ради дефекта, который нечем воспроизвести,
не стали.

### Чему научились

**Абсолютный счётчик без базлайна инкумбента — не критерий.** Реплей-гейт «16 из 42 →
TRANSFER» пять волн читался как no-go. Когда на том же корпусе измерили живой прод,
выяснилось, что он записывает 10 из 42, а настоящих регрессий FSM — две. Гейт снят,
FSM переключён в live. Та же ошибка была заложена в критерий 7-A («`_MOD_FITTING` ≤ 5,000
chars»), и именно поэтому 7-A закрыта суженной.

**Правило промпта снимается только там, где уже работает кодовый преемник.** Выведено из
двух откатов (`c8c6601`, `3b93213`) и соблюдено в суженной 7-A.

**Код без call-site'ов выглядит как готовая функция.** `PASSIVE_PARSERS` и `aresolve` были
написаны, покрыты тестами и никем не вызывались; таблица алиасов Wave 8 стоила живой записи
поля BRAND. Корпусный тест на предикат не покрывает проводку — мутировать надо call site.

**Мок без `spec=` делает зелёным путь, которого в проде нет.** При приёмке Волны 3-A
нашлось, что 65 из 66 тестов шли через `chat_completion`, метода которого в репозитории
нет вообще.

### Что дальше

Новый чеклист собирается не вокруг архитектурной идеи, а вокруг очереди живых дефектов.
Оба `PROPOSAL-fsm-owns-tools*.md` сохранены как отложенное решение с записанным числом
(0 из 302), чтобы решение пересматривалось по новому замеру, а не по новому спору.
