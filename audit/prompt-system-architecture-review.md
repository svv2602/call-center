# Анализ prompt-system-architecture.md — замечания и рекомендации

Источник: [`audit/prompt-system-architecture.md`](audit/prompt-system-architecture.md:1)

## TL;DR

Документ в целом хорошо отражает идею модульной сборки промпта и компрессии истории. Основные проблемы сейчас — **несогласованность с текущей реализацией** (порядок секций, роль DB prompt_versions, детали компрессии/кэширования) и **недостаточная спецификация границ “stable per call” vs “stable across calls”** для prompt caching.

Ниже — конкретные несоответствия и рекомендации, привязанные к коду.

---

## 1) Несоответствия документа и кода (высокий приоритет)

### 1.1. «STABLE/DYNAMIC» порядок секций и позиционирование даты/сезона

**Что в документе:** в схеме сборки «Current date + season hint» указаны в DYNAMIC (меняется каждый терн) — см. [`audit/prompt-system-architecture.md`](audit/prompt-system-architecture.md:95).

**Что в коде:** дата и сезонный хинт добавляются в блоке, помеченном как **STABLE sections (constant within a single call)** — см. [`build_system_prompt_with_context()`](src/agent/prompts.py:1057), строки [`1156-1204`](src/agent/prompts.py:1156).

**Почему важно:**

- Это влияет на ожидаемую эффективность prompt caching и на то, где “безопасно” добавлять новые динамические вставки.
- В текущей реализации дата/сезон фактически **stable-per-call** (для звонка в течение одного дня/сессии) — но не stable-per-deploy.

**Рекомендация:**

- Внести правку в документ: явно разделить **stable-per-call** (константно в рамках звонка) и **dynamic-per-turn** (меняется на каждом терне). Дату/сезон перенести из DYNAMIC в STABLE-per-call.

### 1.2. Оценка выигрыша от prompt caching

**Что в документе:** экономия ~30–40% latency — см. [`audit/prompt-system-architecture.md`](audit/prompt-system-architecture.md:69).

**Что в коде:** комментарий говорит про “saves ~90% on cached tokens” — см. [`build_system_prompt_with_context()`](src/agent/prompts.py:1145).

**Почему важно:** разные метрики (latency vs cached tokens) и разные величины могут вводить в заблуждение.

**Рекомендация:**

- В документе разделить:
  - экономию **по токенам** (cached input tokens)
  - экономию **по времени** (latency)
  - и добавить примечание, что цифры зависят от провайдера/модели и наличия “prefix cache”.

### 1.3. Роль DB `prompt_versions`

**Что в документе:** таблица `prompt_versions` не используется, все `is_active=false` — см. [`audit/prompt-system-architecture.md`](audit/prompt-system-architecture.md:79).

**Что в коде:**

- В `main.py` явно сказано: “DB prompt_versions are only used for A/B testing” — [`src/main.py`](src/main.py:793).
- Реализован менеджер промптов, который читает/активирует версии в БД — см. запросы к `prompt_versions` в [`src/agent/prompt_manager.py`](src/agent/prompt_manager.py:69).
- A/B тесты промптов используют `prompt_versions` — см. [`src/agent/ab_testing.py`](src/agent/ab_testing.py:52).

**Почему важно:**

- Архитектурно это меняет “источник истины”: сейчас truth не только “код”, но и “код + механика A/B”.

**Рекомендация:**

- Уточнить формулировку в документе: “по умолчанию system prompt собирается из кода; `prompt_versions` используется для A/B тестов и экспериментальных развертываний”.

---

## 2) Замечания по архитектуре (средний приоритет)

### 2.1. Компрессия и суммаризация истории: терминология и триггеры

**Что в документе:**

- “если > 10 терновых пар” → суммаризация и “потолок 25 сообщений” — [`audit/prompt-system-architecture.md`](audit/prompt-system-architecture.md:41).

**Что в коде:**

- Порог суммаризации — **по количеству сообщений**, а не “терновых пар”: `summary_threshold=10`, `keep_recent=10` — [`summarize_old_messages()`](src/agent/history_compressor.py:76).
- Потолок 25 сообщений enforced в агенте — `MAX_HISTORY_MESSAGES = 25` — [`src/agent/agent.py`](src/agent/agent.py:34), trim на [`194-198`](src/agent/agent.py:194).
- Гарантия не разрывать `tool_use/tool_result` реализована как смещение cutoff назад, пока “первое recent” не является tool_result — [`summarize_old_messages()`](src/agent/history_compressor.py:106).

**Рекомендации:**

- В документе заменить “терновые пары” на “сообщения” или прямо описать текущий формат истории (assistant blocks + user tool_result blocks).
- Добавить в документ короткий инвариант: “после компрессии/суммаризации история должна оставаться валидной для провайдера (не терять пару tool_use/tool_result)”.

### 2.2. STABLE секции в коде фактически “stable-per-call”, но вызов — per-turn

Сейчас [`build_system_prompt_with_context()`](src/agent/prompts.py:1057) вызывается **на каждом терне** (см. сборку `system` в [`src/agent/agent.py`](src/agent/agent.py:200)), но содержит блоки, помеченные как stable-per-call (дата/сезон, safety/few-shot/promos и т.д.).

Это нормально при наличии prefix caching, но:

- без caching это лишние токены/латентность;
- даже с caching полезно измерять cache hit rate.

**Рекомендации:**

- Логировать длину system prompt и/или оценку токенов на каждом терне (в идеале — per provider). Это можно привязать к уже существующему логированию usage в [`src/agent/agent.py`](src/agent/agent.py:261).

### 2.3. Подстановка имени агента через `str.replace` (хрупкость)

Текущая подстановка:

- `base_prompt.replace("Тебе звати Олена", ...)` и `base_prompt.replace("ти Олена", ...)` — см. [`build_system_prompt_with_context()`](src/agent/prompts.py:1099).

**Риски:**

- случайные замены в других местах текста;
- локализация/варианты формулировок ломают замену.

**Рекомендация:** перейти на явный плейсхолдер (например, `{AGENT_NAME}`) в `_MOD_CORE` и шаблонизацию при сборке.

### 2.4. Topic switching expansion: дедупликация модулей через `id()`

В ветке переключения темы используется `seen: set[int] = {id(m) for m in base_modules}` и проверка `mod_id not in seen` — см. [`build_system_prompt_with_context()`](src/agent/prompts.py:1112).

**Риск:** если когда-либо модули станут “равными строками, но разными объектами”, дедупликация по `id()` пропустит дубликаты.

**Рекомендация:** дедуплицировать по значению (например, `hash(mod)` или просто `mod in set_of_strings`). Для `infer_expanded_modules()` уже есть проверка `mod not in base_modules` — [`infer_expanded_modules()`](src/agent/prompts.py:621).

### 2.5. Scenario detection через ключевые слова — ожидаемая точность

Детекция сценария по substring-ключам — [`detect_scenario_from_text()`](src/agent/prompts.py:736), словарь `_SCENARIO_KEYWORDS` — [`src/agent/prompts.py`](src/agent/prompts.py:686).

**Замечание:**

- Сейчас это “легкий IVR”, но важно зафиксировать в документе, что это **эвристика**, и она может давать ложные срабатывания (особенно по коротким подстрокам вроде “шин”).

**Рекомендации:**

- В документ добавить раздел “ограничения keyword routing” + fallback (например, если совпали 2+ сценария или confidence низкий — задавать уточняющий вопрос, а не фиксировать сценарий).

---

## 3) Замечания по безопасности промпта / prompt-injection (средний приоритет)

### 3.1. Инъекция “паттернов опыта” из БД

Вставка guidance_note из БД уходит в системный промпт через форматтер — [`PatternSearch.format_for_prompt()`](src/sandbox/patterns.py:80).

Есть санация guidance_note при экспорте turn group → pattern — [`sanitize_guidance_note()`](src/sandbox/patterns.py:22), вызывается в [`export_group_to_pattern()`](src/sandbox/patterns.py:108).

**Замечание:** это хорошая база, но документ стоит дополнить:

- что guidance_note — потенциальная поверхность инъекций;
- какие именно ограничения применяются (удаление code blocks, inline code, ограничение длины).

---

## 4) Рекомендации по улучшению документа (низкий/средний приоритет)

1. Добавить “Source of truth” секцию: какие части промпта в коде, какие в БД, какие включаются всегда/условно (и почему).
2. Явно описать формат `conversation_history` (assistant content blocks, user tool_result blocks) и инварианты валидности для провайдеров — см. [`summarize_old_messages()`](src/agent/history_compressor.py:76).
3. Обновить ключевые файлы: сейчас `prompt_manager.py` и `ab_testing.py` играют заметную роль в жизненном цикле промптов — см. [`src/agent/prompt_manager.py`](src/agent/prompt_manager.py:1) и [`src/agent/ab_testing.py`](src/agent/ab_testing.py:1).

---

## 5) Рекомендуемый план действий

1. Синхронизировать документ с текущей реализацией по пунктам 1.1–1.3 (самое заметное расхождение).
2. Добавить в документ явное определение “stable-per-call” vs “dynamic-per-turn” и привязать к фактическому порядку сборки в [`build_system_prompt_with_context()`](src/agent/prompts.py:1057).
3. Добавить наблюдаемость: логирование размера system prompt и решений компрессора (compress vs summarize) рядом с usage-логами в [`src/agent/agent.py`](src/agent/agent.py:261).

