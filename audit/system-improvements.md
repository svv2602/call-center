# Рекомендации по улучшению работы системы (скорость/качество/надёжность)

Контекст: модульная сборка system prompt + инструменты + стриминг аудио. Базовые точки входа: агентный цикл в [`src/agent/agent.py`](src/agent/agent.py:184) и/или стриминг в [`src/agent/streaming_loop.py`](src/agent/streaming_loop.py:1), сборка system prompt в [`build_system_prompt_with_context()`](src/agent/prompts.py:1057), компрессия истории в [`summarize_old_messages()`](src/agent/history_compressor.py:76), метрики Prometheus в [`src/monitoring/metrics.py`](src/monitoring/metrics.py:1).

## 1) Наблюдаемость (самый быстрый рычаг улучшений)

### 1.1. “4 золотых сигнала” для LLM-тура и инструментов

У вас уже есть хорошая база метрик (latency/STT/TTS/tools) в [`src/monitoring/metrics.py`](src/monitoring/metrics.py:30). Но для управления качеством агента почти всегда не хватает **разрезов по типу промпта/сценарию/режимам компрессии**.

Рекомендации (bounded cardinality):

1) Добавить метрики по промпту/контексту:
   - `system_prompt_chars` (Histogram) — длина system prompt в символах.
   - `history_messages_count` (Histogram) — сколько сообщений в истории после компрессии/trim.
   - `history_mode_total{mode="compress|summarize|none"}` (Counter) — какой режим применили.
   - `scenario_total{scenario}` у вас уже есть как [`call_scenario_total`](src/monitoring/metrics.py:137), но нужно гарантировать, что сценарий выставляется *всегда* и одинаково в agent/streaming.

2) Для tool calls:
   - `tool_call_errors_total{tool_name, error_type}` (Counter) — сейчас есть только duration ([`tool_call_duration_ms`](src/monitoring/metrics.py:78)). Ошибки есть в логах, но в метриках нет.
   - `tool_call_timeouts_total{tool_name}` (Counter) — у вас есть таймаут в [`_execute_one()`](src/agent/agent.py:369), это критичный сигнал деградации.

3) Для LLM:
   - `llm_stop_reason_total{reason}` (Counter) — stop_reason уже логируется в [`src/agent/agent.py`](src/agent/agent.py:261), но для алертов/дашбордов нужна метрика.

### 1.2. Корреляция: call_id/turn_id/tenant_id во ВСЕХ логах

Чтобы отвечать “что, где и почему” без дебага вживую, нужно, чтобы каждый лог-ивент (LLM usage, tool execute, ошибки) имел общие ключи:

- `call_id`
- `turn_number` (или `turn_id`)
- `tenant_id`
- `scenario`
- `provider_key` (LLM провайдер)

Сейчас usage логируется кусочно (см. накопление usage в [`src/agent/agent.py`](src/agent/agent.py:257)), а также есть DB-логгер usage в [`src/monitoring/llm_usage_logger.py`](src/monitoring/llm_usage_logger.py:82). Усилить связность — самый дешёвый способ ускорить расследования.

### 1.3. Дашборды/алерты: что реально ловить

Минимальный набор алертов (по Prometheus):

- `p95(callcenter_llm_latency_ms) > X` и/или рост `callcenter_total_response_latency_ms` ([`llm_latency_ms`](src/monitoring/metrics.py:38), [`total_response_latency_ms`](src/monitoring/metrics.py:50))
- рост `tool_call_timeouts_total`
- рост `transfer_attempts_total{result="unavailable"}` ([`transfer_attempts_total`](src/monitoring/metrics.py:106))
- рост `calls_total{status="error"}` ([`calls_total`](src/monitoring/metrics.py:24))

## 2) Управление токенами и скоростью (системно)

### 2.1. Явный token budget per turn

Сейчас система полагается на компрессию истории (детерминированную) и на кэширование префикса (комментарий в [`build_system_prompt_with_context()`](src/agent/prompts.py:1145)). Рекомендую добавить **контрольный контур**:

1) Перед LLM вызовом оценивать:
   - длину system prompt
   - длину history
   - ожидаемый максимум

2) Если бюджет превышен — включать более агрессивный режим:
   - уменьшить `keep_recent` в [`summarize_old_messages()`](src/agent/history_compressor.py:76) (например, динамически 10→6)
   - сильнее ужимать tool_result (у вас уже есть [`compress_tool_result()`](src/agent/tool_result_compressor.py:159))
   - при необходимости отключать “тяжёлые” контексты (например, caller_history) при превышении бюджета.

Важно: **не делать это “тихо”** — логировать выбранную стратегию, иначе анализ качества станет невозможен.

### 2.2. Stable-per-call vs dynamic-per-turn: сделать это реальным контрактом

В коде внутри [`build_system_prompt_with_context()`](src/agent/prompts.py:1057) уже есть разделение, но на практике функция вызывается каждый терн.

Рекомендация: вынести вычисление stable-per-call секций в слой сессии (например, в `CallSession`) и кешировать:

- date/season блок (см. [`src/agent/prompts.py`](src/agent/prompts.py:1158))
- safety/few-shot/promotions
- customer_profile/caller_history/storage_context (если они не меняются в ходе звонка)

Эффект: даже при отсутствии prefix caching у провайдера вы сокращаете input tokens.

## 3) Качество: маршрутизация сценариев и устойчивость диалога

### 3.1. Keyword routing: добавить “confidence” и многозначность

Текущая детекция сценария — простая substring эвристика в [`detect_scenario_from_text()`](src/agent/prompts.py:736) с `_SCENARIO_KEYWORDS` ([`src/agent/prompts.py`](src/agent/prompts.py:686)).

Рекомендации:

- Считать score = количество совпавших ключей/групп; если score низкий или совпали 2 сценария — **не фиксировать сценарий**, а задать 1 уточняющий вопрос.
- Логировать `scenario_detected`, `scores`, `ambiguous=true/false`.

### 3.2. “Topic switching” и дедупликация модулей

Сейчас при добавлении модулей при смене темы дедупликация завязана на `id()` строкового объекта (см. [`src/agent/prompts.py`](src/agent/prompts.py:1112)). Лучше дедуплицировать по значению, чтобы избежать редких, но неприятных дублей и раздувания промпта.

### 3.3. Негативные паттерны (“НЕ РОБИТИ”) — сделать проверяемыми

Формат паттернов для промпта в [`PatternSearch.format_for_prompt()`](src/sandbox/patterns.py:80) хороший, но полезно дополнить:

- метрикой: сколько негативных паттернов было инжектировано в терн
- офлайн-оценкой: корреляция негативных паттернов с уменьшением transfer rate / ошибочных tool calls

## 4) Надёжность: инструменты, таймауты, деградация

### 4.1. Политика деградации при сбоях tools

У вас есть таймаут и fallback-текст на tool timeout в [`_execute_one()`](src/agent/agent.py:369). Рекомендую сделать это системной политикой:

- различать классы ошибок: timeout vs 5xx vs validation
- для timeout: просить разрешение “повторить ещё раз” или предложить перевод на оператора, если 2 таймаута подряд
- **не** пытаться бесконечно вызывать один и тот же tool (у вас уже есть дедуп на одинаковые args — [`src/agent/agent.py`](src/agent/agent.py:353))

### 4.2. Лимит tool rounds и качество ответа

Сейчас `MAX_TOOL_CALLS_PER_TURN = 5` и при исчерпании делается fallback summary ([`src/agent/agent.py`](src/agent/agent.py:398)).

Рекомендации:

- Логировать “tool_rounds_exhausted” как отдельный инцидент качества.
- Добавить метрику `tool_rounds_per_turn`.

## 5) Качество разработки: тесты и регрессии

### 5.1. Регрессионные сценарии “разговор → ожидаемые tool calls/итог”

У вас уже есть sandbox/regression инфраструктура (см. записи истории в [`src/api/sandbox.py`](src/api/sandbox.py:933) и скрипты в [`src/sandbox/regression.py`](src/sandbox/regression.py:1)).

Рекомендации:

- Зафиксировать 20–50 “эталонных” диалогов по ключевым сценариям.
- Проверять:
  - количество tool calls
  - долю переводов на оператора
  - средний input/output tokens
  - наличие обязательных шагов (например, уточнить сезон/размер)

### 5.2. A/B тестирование промптов: не только “конверсия”, но и “стоимость/латентность”

Поскольку `prompt_versions` используется для A/B (см. [`src/agent/ab_testing.py`](src/agent/ab_testing.py:52)), в метрики успеха добавьте:

- p95 latency
- tokens/call
- tool_timeout rate
- transfer rate

## 6) Короткий “план внедрения” (по шагам)

1) Добавить метрики/логи по длине system prompt, режиму компрессии, таймаутам tools.
2) Ввести корреляционные поля (`call_id`, `turn_number`, `tenant_id`) в ключевые логи LLM/tools.
3) Ввести token budget и управляющую стратегию (динамическое `keep_recent`, отключение тяжёлых контекстов при перегрузе).
4) Улучшить routing: score/ambiguous → уточняющий вопрос.
5) Закрепить регрессионные диалоги и прогонять их на PR.

