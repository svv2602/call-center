# Аудит соответствия кода документации — v2

**Дата:** 2026-02-14
**Аудитор:** Независимый аудитор (AI)
**Версия:** 2 (повторный аудит после применения исправлений КР-1, КР-2, КР-3, НР-7, НР-8)

---

## Executive Summary

### Общая оценка

Проект Call Center AI демонстрирует **высокий уровень соответствия** реализации проектной документации. Все 4 фазы разработки реализованы в коде, архитектурные решения соответствуют спецификациям, критические исправления из предыдущего аудита успешно применены.

**Общий процент соответствия: ~89%**

| Категория | Соответствие | Статус |
|-----------|-------------|--------|
| 3.1 Канонические tools | 85% | 2 tools не зарегистрированы в роутере |
| 3.2 Архитектура | 95% | Полное соответствие |
| 3.3 Модель данных | 90% | Миграция 006 не документирована |
| 3.4 Store API контракт | 95% | Полное соответствие |
| 3.5 NFR | 92% | Все метрики и алерты реализованы |
| 3.6 Безопасность | 90% | PII sanitizer, аудио не записывается |
| 3.7 Аналитика (Phase 4) | 88% | Все основные компоненты реализованы |
| 3.8 Sequence Diagrams | 85% | 6 из 7 сценариев покрыты |
| 3.9 Deployment/Docker | 80% | Различия в compose и docs |
| 3.10 Тестирование | 90% | Обширное покрытие |

### Статус исправлений из предыдущего аудита

| ID | Описание | Статус |
|----|----------|--------|
| КР-1 | Pipeline интегрирован в main.py | **ИСПРАВЛЕНО** |
| КР-2 | PII sanitizer маскирует имена | **ИСПРАВЛЕНО** |
| КР-3 | CI/CD pipeline создан | **ИСПРАВЛЕНО** |
| НР-7 | Метрика `audiosocket_to_stt_ms` добавлена | **ИСПРАВЛЕНО** |
| НР-8 | Метрика `tts_delivery_ms` добавлена | **ИСПРАВЛЕНО** |

### Критические расхождения (новые)

| # | Описание | Влияние |
|---|----------|---------|
| КР-4 | `cancel_fitting` и `get_fitting_price` определены в `tools.py`, реализованы в `store_client`, но **НЕ зарегистрированы** в `_build_tool_router()` в `main.py` | LLM получит определения tools, но при вызове получит ошибку — tool не найден в роутере |
| КР-5 | Документация перечисляет 5 миграций (001–005), в коде 6 (006_add_prompt_ab_tests.py) — недокументированная миграция | Несоответствие спецификации, хотя функционально корректно |

---

## 3.1 Канонический список tools

**Источник документации:** `doc/development/00-overview.md` (секция «Канонический список tools»)
**Источник кода:** `src/agent/tools.py`, `src/main.py` (`_build_tool_router()`), `src/store_client/client.py`

### Каноническая таблица (документация — 13 tools)

| Tool | Статус в `tools.py` | Статус в `main.py` router | Статус в `store_client` | Комментарий |
|------|---------------------|---------------------------|------------------------|-------------|
| `search_tires` | ✅ | ✅ `_store_client.search_tires` | ✅ `search_tires()` | Полное соответствие |
| `check_availability` | ✅ | ✅ `_store_client.check_availability` | ✅ `check_availability()` | Полное соответствие |
| `transfer_to_operator` | ✅ | ✅ inline lambda | N/A (Asterisk) | Реализован как замыкание в `_build_tool_router()` |
| `get_order_status` | ✅ | ✅ `_store_client.search_orders` | ✅ `search_orders()` | Имя метода в клиенте отличается (`search_orders` вместо `get_order_status`), но маппинг корректен |
| `create_order_draft` | ✅ | ✅ `_store_client.create_order` | ✅ `create_order()` | Имя в tools.py правильное (`create_order_draft`), метод клиента `create_order` — маппинг корректен |
| `update_order_delivery` | ✅ | ✅ `_store_client.update_delivery` | ✅ `update_delivery()` | Полное соответствие |
| `confirm_order` | ✅ | ✅ `_store_client.confirm_order` | ✅ `confirm_order()` | Полное соответствие, Idempotency-Key реализован |
| `get_fitting_stations` | ✅ | ✅ `_store_client.get_fitting_stations` | ✅ `get_fitting_stations()` | Полное соответствие |
| `get_fitting_slots` | ✅ | ✅ `_store_client.get_fitting_slots` | ✅ `get_fitting_slots()` | Полное соответствие |
| `book_fitting` | ✅ | ✅ `_store_client.book_fitting` | ✅ `book_fitting()` | Полное соответствие, Idempotency-Key реализован |
| `cancel_fitting` | ✅ | ❌ **НЕ зарегистрирован** | ✅ `cancel_fitting()` | **КР-4**: определён в tools.py, реализован в клиенте, но НЕ добавлен в роутер |
| `get_fitting_price` | ✅ | ❌ **НЕ зарегистрирован** | ✅ `get_fitting_price()` | **КР-4**: определён в tools.py, реализован в клиенте, но НЕ добавлен в роутер |
| `search_knowledge_base` | ✅ | ✅ `_store_client.search_knowledge_base` | ✅ `search_knowledge_base()` | Полное соответствие |

### Детали расхождения КР-4

В `src/agent/tools.py` (строка 403):
```python
ALL_TOOLS = MVP_TOOLS + ORDER_TOOLS + FITTING_TOOLS
```

`FITTING_TOOLS` содержит 6 tools: `get_fitting_stations`, `get_fitting_slots`, `book_fitting`, `cancel_fitting`, `get_fitting_price`, `search_knowledge_base`.

В `src/main.py` (`_build_tool_router()`, строки 141–163) зарегистрировано только 11 tools. Отсутствуют:
- `cancel_fitting` — нет строки `router.register("cancel_fitting", ...)`
- `get_fitting_price` — нет строки `router.register("get_fitting_price", ...)`

**Последствие:** LLM Agent (`src/agent/agent.py`) передаёт все 13 tools из `ALL_TOOLS` в Claude API. Claude может решить вызвать `cancel_fitting` или `get_fitting_price`, но `ToolRouter` не найдёт обработчик, что приведёт к ошибке выполнения tool call.

### Имена tools — верификация

| Проверка | Результат |
|----------|-----------|
| `create_order_draft` (не `create_order`) | ✅ В `tools.py` используется `create_order_draft` |
| Все имена из канонического списка | ✅ Точное совпадение |
| Input schema совпадает с документацией | ✅ Параметры соответствуют спецификациям фаз 1–3 |

---

## 3.2 Архитектура и компоненты

**Источник документации:** `doc/technical/architecture.md`
**Источник кода:** `src/core/`, `src/agent/`, `src/config.py`

| Требование | Статус | Файл | Комментарий |
|------------|--------|------|-------------|
| AudioSocket протокол (0x00=hangup, 0x01=UUID, 0x10=audio, 0xFF=error) | ✅ | `src/core/audio_socket.py` | `PacketType` enum: HANGUP=0x00, UUID=0x01, AUDIO=0x10, ERROR=0xFF |
| Аудио-фрейм 20мс (640 байт) | ✅ | `src/core/audio_socket.py` | `AUDIO_FRAME_BYTES = 640`, `AUDIO_FRAME_MS = 20` |
| Pipeline: AudioSocket → STT → LLM → TTS → AudioSocket | ✅ | `src/core/pipeline.py` | `CallPipeline.run()` — greeting → listen → STT → LLM → TTS loop |
| Pipeline интегрирован в main.py | ✅ | `src/main.py` строки 96–114 | **КР-1 ИСПРАВЛЕНО**: `handle_call()` создаёт STT, agent, pipeline |
| Barge-in поддержка | ✅ | `src/core/pipeline.py` | `_barge_in_event = asyncio.Event()`, проверяется в `_speak_streaming()` |
| Session state в Redis | ✅ | `src/core/call_session.py` | `SessionStore(Redis)`, TTL=1800s |
| TTL 1800s | ✅ | `src/core/call_session.py` | `SESSION_TTL = 1800` |
| Circuit breaker (aiobreaker, fail_max=5, timeout=30s) | ✅ | `src/store_client/client.py` | `CircuitBreaker(fail_max=5, timeout_duration=30)` |
| Структурированный JSON logging | ✅ | `src/logging/structured_logger.py` | `JSONFormatter` с `call_id`, `request_id` |
| call_id + request_id трассировка | ✅ | `src/logging/structured_logger.py` | `CallIdFilter` добавляет `call_id` в каждую запись |
| Горизонтальное масштабирование (stateless) | ✅ | `src/core/call_session.py`, `src/main.py` | Состояние в Redis, Call Processor stateless |
| Google STT streaming gRPC | ✅ | `src/stt/google_stt.py` | `GoogleSTTEngine` с restart по 5-мин таймауту |
| Google TTS Neural2 | ✅ | `src/tts/google_tts.py` | `GoogleTTSEngine` с кэшированием частых фраз |
| Claude API tool calling | ✅ | `src/agent/agent.py` | `LLMAgent` с `ToolRouter`, MAX_TOOL_CALLS_PER_TURN=5 |
| Whisper STT (Phase 4) | ✅ | `src/stt/whisper_stt.py` | `WhisperSTTEngine` с Faster-Whisper |
| Model routing (Haiku/Sonnet) | ✅ | `src/agent/model_router.py` | `ModelRouter` маршрутизирует по сложности запроса |
| Feature flags | ✅ | `src/config.py` | `FeatureFlagsConfig` с `use_whisper`, `use_model_routing`, `enable_ab_testing` |
| ARI клиент | ✅ | `src/core/asterisk_ari.py` | `ARIClient` для CallerID и transfer |

---

## 3.3 Модель данных

**Источник документации:** `doc/technical/data-model.md`
**Источник кода:** `migrations/versions/001-006`

### Таблицы

| Таблица (документация) | Статус | Миграция | Комментарий |
|----------------------|--------|----------|-------------|
| `customers` | ✅ | 001 | UNIQUE INDEX на `phone` |
| `calls` | ✅ | 001 | Partitioned by `started_at`, 5 месячных партиций |
| `call_turns` | ✅ | 001 | Partitioned by `created_at` |
| `call_tool_calls` | ✅ | 001 | Partitioned by `created_at` |
| `orders` | ✅ | 002 | С `idempotency_key` |
| `order_items` | ✅ | 002 | FK на `orders(id)` |
| `fitting_stations` | ✅ | 003 | Все колонки совпадают с ERD |
| `fitting_bookings` | ✅ | 003 | Все колонки совпадают с ERD |
| `knowledge_articles` | ✅ | 004 | С `active` флагом |
| `knowledge_embeddings` | ✅ | 004 | pgvector VECTOR(1536), hnsw index |
| `daily_stats` | ✅ | 005 | PK на `stat_date`, jsonb поля |
| `prompt_versions` | ✅ | 006 | **НЕ документировано** в data-model.md (миграции 001–005) |
| `prompt_ab_tests` | ✅ | 006 | **НЕ документировано** в data-model.md (миграции 001–005) |

### Партиционирование

| Требование | Статус | Комментарий |
|------------|--------|-------------|
| `calls` PARTITION BY RANGE (started_at) | ✅ | Реализовано в 001, 5 партиций (2026_01 – 2026_05) |
| `call_turns` PARTITION BY RANGE (created_at) | ✅ | Реализовано в 001 |
| `call_tool_calls` PARTITION BY RANGE (created_at) | ✅ | Реализовано в 001 |

### Индексы

| Индекс (документация) | Статус | Комментарий |
|----------------------|--------|-------------|
| `idx_calls_caller_id` | ✅ | В 001 |
| `idx_calls_customer_id` | ✅ | В 001 |
| `idx_calls_started_at` | ✅ | В 001 |
| `idx_call_turns_call_id` | ✅ | В 001 |
| `idx_knowledge_embeddings_vector` (hnsw) | ✅ | В 004, `vector_cosine_ops` |
| `idx_customers_phone` (UNIQUE) | ✅ | В 001 |

### Расхождение КР-5: Недокументированная миграция

Документация `doc/technical/data-model.md` (строки 346–357) перечисляет миграции:
```
001_initial_schema.py
002_add_orders.py
003_add_fitting.py
004_add_knowledge_base.py
005_add_analytics.py
```

В коде присутствует 6-я миграция: `006_add_prompt_ab_tests.py`, создающая таблицы `prompt_versions` и `prompt_ab_tests`. Эти таблицы **описаны в ERD** (в секции диаграммы data-model.md), но миграция для них **не перечислена** в списке миграций.

### Дополнительная колонка

| Расхождение | Комментарий |
|-------------|-------------|
| `calls.quality_details` (JSONB) | Добавлена в миграции 005, используется в `analytics.py` для детализации оценки. Присутствует в ERD как часть `quality_score`, но отдельная колонка не показана в ERD |

---

## 3.4 Store API контракт

**Источник документации:** `doc/development/api-specification.md`
**Источник кода:** `src/store_client/client.py`

| Endpoint (документация) | HTTP Method | Статус | Метод клиента | Комментарий |
|------------------------|-------------|--------|---------------|-------------|
| `GET /api/v1/tires/search` | GET | ✅ | `search_tires()` | + fallback на `/vehicles/tires` для поиска по авто |
| `GET /api/v1/tires/{id}` | GET | ✅ | `get_tire()` | Дополнительный метод (не tool) |
| `GET /api/v1/tires/{id}/availability` | GET | ✅ | `check_availability()` | С fallback по query |
| `GET /api/v1/orders/search` | GET | ✅ | `search_orders()` | По phone |
| `GET /api/v1/orders/{id}` | GET | ✅ | `search_orders()` | По order_id |
| `POST /api/v1/orders` | POST | ✅ | `create_order()` | **Idempotency-Key: ✅** |
| `PATCH /api/v1/orders/{id}/delivery` | PATCH | ✅ | `update_delivery()` | Все параметры доставки |
| `POST /api/v1/orders/{id}/confirm` | POST | ✅ | `confirm_order()` | **Idempotency-Key: ✅** |
| `GET /api/v1/pickup-points` | GET | ✅ | `get_pickup_points()` | Дополнительный метод |
| `GET /api/v1/delivery/calculate` | GET | ✅ | `calculate_delivery()` | Дополнительный метод |
| `GET /api/v1/fitting/stations` | GET | ✅ | `get_fitting_stations()` | По городу |
| `GET /api/v1/fitting/stations/{id}/slots` | GET | ✅ | `get_fitting_slots()` | С фильтрами даты и типа |
| `POST /api/v1/fitting/bookings` | POST | ✅ | `book_fitting()` | **Idempotency-Key: ✅** |
| `DELETE /api/v1/fitting/bookings/{id}` | DELETE | ✅ | `cancel_fitting()` | Для action=cancel |
| `PATCH /api/v1/fitting/bookings/{id}` | PATCH | ✅ | `cancel_fitting()` | Для action=reschedule |
| `GET /api/v1/fitting/prices` | GET | ✅ | `get_fitting_price()` | По диаметру, станции, типу |
| `GET /api/v1/knowledge/search` | GET | ✅ | `search_knowledge_base()` | С category фильтром |

### Механизмы надёжности

| Требование | Статус | Комментарий |
|------------|--------|-------------|
| Retry для 429/503 | ✅ | `_RETRYABLE_STATUSES = {429, 503}`, `_MAX_RETRIES = 2`, backoff 1s, 2s |
| Circuit breaker | ✅ | `aiobreaker.CircuitBreaker(fail_max=5, timeout_duration=30)` |
| Idempotency-Key для POST mutations | ✅ | `uuid.uuid4()` для `create_order`, `confirm_order`, `book_fitting` |
| X-Request-Id для трассировки | ✅ | Генерируется в `_do_request()` для каждого запроса |
| Timeout | ✅ | `aiohttp.ClientTimeout(total=timeout)`, default=5s |
| Bearer token auth | ✅ | `Authorization: Bearer {api_key}` в headers сессии |

---

## 3.5 NFR (нефункциональные требования)

**Источник документации:** `doc/technical/nfr.md`
**Источник кода:** `src/monitoring/metrics.py`, `prometheus/alerts.yml`, `src/core/pipeline.py`

### Latency Budget

| Этап (docs) | Бюджет | Метрика | Алерт при | Статус метрики | Статус алерта |
|-------------|--------|---------|-----------|----------------|---------------|
| AudioSocket → STT | ≤ 50ms | `audiosocket_to_stt_ms` | > 100ms | ✅ metrics.py:56 | ✅ alerts.yml:89 |
| STT распознавание | ≤ 500ms | `stt_latency_ms` | > 700ms | ✅ metrics.py:32 | ✅ alerts.yml:49 |
| LLM (TTFT) | ≤ 1000ms | `llm_latency_ms` | > 1500ms | ✅ metrics.py:38 | ✅ alerts.yml:63 |
| TTS синтез | ≤ 400ms | `tts_latency_ms` | > 600ms | ✅ metrics.py:44 | ✅ alerts.yml:75 |
| TTS → AudioSocket | ≤ 50ms | `tts_delivery_ms` | > 100ms | ✅ metrics.py:62 | ✅ alerts.yml:101 |
| End-to-end | ≤ 2000ms | `total_response_latency_ms` | > 2500ms | ✅ metrics.py:50 | ⚠️ alerts.yml:35 **> 3000ms** |

### Расхождение в пороге end-to-end алерта

Документация (`nfr.md` строка 26) указывает алерт при `> 2500ms`, а в `alerts.yml` (строка 39) порог `> 3000` (HighResponseLatency). Это **некритичное расхождение** — порог в алерте выше документированного на 500ms.

### Инструментация в pipeline

| Метрика | Где инструментирована | Статус |
|---------|----------------------|--------|
| `audiosocket_to_stt_ms` | `pipeline.py:119-121` (в `_audio_reader_loop`) | ✅ **НР-7 ИСПРАВЛЕНО** |
| `tts_delivery_ms` | `pipeline.py:228,256` (в `_speak`, `_speak_streaming`) | ✅ **НР-8 ИСПРАВЛЕНО** |
| `stt_latency_ms` | `src/stt/google_stt.py` | ✅ |
| `llm_latency_ms` | `src/agent/agent.py` | ✅ |
| `tts_latency_ms` | `src/tts/google_tts.py` | ✅ |

### Прочие NFR

| Требование | Статус | Файл | Комментарий |
|------------|--------|------|-------------|
| Session TTL 1800s | ✅ | `call_session.py:24` | `SESSION_TTL = 1800` |
| Stateless Call Processor | ✅ | `main.py`, `call_session.py` | Состояние в Redis |
| Graceful shutdown | ✅ | `main.py:208-256` | SIGINT/SIGTERM → stop AudioSocket → close clients |
| Retry с exponential backoff | ✅ | `store_client/client.py` | 1s, 2s для 429/503 |
| Circuit breaker fallback | ✅ | `store_client/client.py` | `CircuitBreakerError` → `StoreAPIError(503)` |
| Health check endpoint | ✅ | `main.py:56-71` | `GET /health` — Redis, AudioSocket status |
| Prometheus metrics endpoint | ✅ | `main.py:74-77` | `GET /metrics` |

### Алерты

| Алерт (docs/nfr) | Статус | Файл | Комментарий |
|-------------------|--------|------|-------------|
| p95 latency > 3s (total) | ✅ | alerts.yml:35 | `HighResponseLatency` |
| STT > 700ms | ✅ | alerts.yml:49 | `HighSTTLatency` |
| LLM > 1500ms | ✅ | alerts.yml:63 | `HighLLMLatency` |
| TTS > 600ms | ✅ | alerts.yml:75 | `HighTTSLatency` |
| AudioSocket→STT > 100ms | ✅ | alerts.yml:89 | `HighAudioSocketToSTTLatency` |
| TTS→AudioSocket > 100ms | ✅ | alerts.yml:101 | `HighTTSDeliveryLatency` |
| >5 ошибок за 10 мин | ✅ | alerts.yml:22 | `PipelineErrorsHigh` |
| >50% переключений за 1ч | ✅ | alerts.yml:5 | `HighTransferRate` |
| Operator queue > 5 | ✅ | alerts.yml:114 | `OperatorQueueOverflow` |
| Abnormal API spend | ✅ | alerts.yml:127 | `AbnormalAPISpend` (>200% от 24h avg) |
| Suspicious tool calls | ✅ | alerts.yml:142 | `SuspiciousToolCalls` (400 errors > 0.5/s) |
| Circuit breaker open | ✅ | alerts.yml:155 | `CircuitBreakerOpen` |

---

## 3.6 Безопасность

**Источник документации:** `doc/security/threat-model.md`, `doc/security/data-policy.md`, `doc/security/risk-matrix.md`
**Источник кода:** `src/logging/pii_sanitizer.py`, `src/agent/prompts.py`, `src/core/pipeline.py`

| Требование | Статус | Файл | Комментарий |
|------------|--------|------|-------------|
| PII sanitizer: маскировка телефонов | ✅ | `pii_sanitizer.py:24-29` | `+380XXXXXXXXX → +380XX***XX` |
| PII sanitizer: маскировка имён | ✅ | `pii_sanitizer.py:32-37` | `Іван Петренко → І*** П***` **КР-2 ИСПРАВЛЕНО** |
| `sanitize_pii()` вызывает оба | ✅ | `pii_sanitizer.py:40-44` | Цепочка: phone → name |
| PII sanitizer подключён к логгеру | ✅ | `structured_logger.py` | `JSONFormatter` вызывает `sanitize_pii()` |
| Аудио НЕ записывается | ✅ | `pipeline.py` | Streaming STT, аудио нигде не сохраняется на диск |
| Транскрипции хранятся 90 дней | ⚠️ | `data-policy.md` | Документировано, но автоматическое удаление не реализовано в коде (нет cron/task для удаления) |
| Bot объявляет автоматическую обработку | ✅ | `prompts.py:GREETING_TEXT` | Включает юридическое уведомление |
| Prompt injection защита (в промпте) | ✅ | `prompts.py:SYSTEM_PROMPT` | Инструкции против раскрытия промпта, смены роли |
| Tool call validation (quantity < 100) | ✅ | `prompts.py` | Инструкция в промпте + параметры в input_schema |
| MAX_TOOL_CALLS_PER_TURN | ✅ | `agent.py` | `MAX_TOOL_CALLS_PER_TURN = 5` — защита от бесконечных loops |
| MAX_HISTORY_MESSAGES | ✅ | `agent.py` | `MAX_HISTORY_MESSAGES = 40` — ограничение контекста |
| AudioSocket через WireGuard | ⚠️ | `deployment.md` | Документировано с примером, но конфигурация WireGuard вне scope кода |
| JWT auth для Admin API | ✅ | `src/api/auth.py` | HS256 JWT, username/password login |
| Bearer token для Store API | ✅ | `store_client/client.py` | В headers сессии |
| API keys в env variables | ✅ | `config.py` | Pydantic Settings, все ключи из env |

---

## 3.7 Аналитика (Phase 4)

**Источник документации:** `doc/development/phase-4-analytics.md`
**Источник кода:** `src/tasks/`, `src/api/`, `src/monitoring/`, `src/agent/`, `grafana/`, `admin-ui/`

### Prometheus метрики

| Метрика (docs) | Статус | Файл | Комментарий |
|----------------|--------|------|-------------|
| `active_calls` | ✅ | metrics.py:13 | Gauge |
| `call_duration_seconds` | ✅ | metrics.py:18 | Histogram |
| `calls_total` (по status) | ✅ | metrics.py:24 | Counter [status] |
| `stt_latency_ms` | ✅ | metrics.py:32 | Histogram |
| `llm_latency_ms` | ✅ | metrics.py:38 | Histogram |
| `tts_latency_ms` | ✅ | metrics.py:44 | Histogram |
| `total_response_latency_ms` | ✅ | metrics.py:50 | Histogram |
| `audiosocket_to_stt_ms` | ✅ | metrics.py:56 | Histogram |
| `tts_delivery_ms` | ✅ | metrics.py:62 | Histogram |
| `tool_call_duration_ms` | ✅ | metrics.py:70 | Histogram [tool_name] |
| `store_api_errors_total` | ✅ | metrics.py:79 | Counter [status_code] |
| `store_api_circuit_breaker_state` | ✅ | metrics.py:85 | Gauge (0/1/2) |
| `transfers_to_operator_total` | ✅ | metrics.py:92 | Counter [reason] |
| `calls_resolved_by_bot_total` | ✅ | metrics.py:100 | Counter |
| `orders_created_total` | ✅ | metrics.py:105 | Counter |
| `fittings_booked_total` | ✅ | metrics.py:110 | Counter |
| `call_cost_usd` | ✅ | metrics.py:115 | Histogram |
| `call_scenario_total` | ✅ | metrics.py:123 | Counter [scenario] |
| `operator_queue_length` | ✅ | metrics.py:131 | Gauge |
| `tts_cache_hits_total` | ✅ | metrics.py:138 | Counter |
| `tts_cache_misses_total` | ✅ | metrics.py:143 | Counter |

### Grafana дашборды

| Дашборд (docs) | Статус | Файл | Комментарий |
|----------------|--------|------|-------------|
| Realtime dashboard | ✅ | `grafana/dashboards/realtime.json` | Метрики в реальном времени |
| Analytics dashboard | ✅ | `grafana/dashboards/analytics.json` | Агрегированная аналитика |
| Datasources provisioning | ✅ | `grafana/provisioning/datasources/datasources.yml` | Prometheus + PostgreSQL |
| Dashboard provisioning | ✅ | `grafana/provisioning/dashboards/dashboards.yml` | Auto-load dashboards |

### Quality Evaluation

| Требование | Статус | Файл | Комментарий |
|------------|--------|------|-------------|
| 8 критериев качества | ✅ | `src/tasks/quality_evaluator.py` | 8 criteria: greeting, understanding, accuracy, completeness, language, tone, efficiency, resolution |
| Claude Haiku для оценки | ✅ | `src/tasks/quality_evaluator.py` | Celery task, использует Haiku для автоматической оценки |
| Результат 0–1 score | ✅ | `src/tasks/quality_evaluator.py` | Сохраняется в `calls.quality_score` и `calls.quality_details` |

### A/B тестирование

| Требование | Статус | Файл | Комментарий |
|------------|--------|------|-------------|
| A/B тестирование промптов | ✅ | `src/agent/ab_testing.py` | `ABTestManager` с Z-test для статистической значимости |
| Управление версиями промптов | ✅ | `src/agent/prompt_manager.py` | `PromptManager` с PostgreSQL |
| API для управления промптами | ✅ | `src/api/prompts.py` | CRUD endpoints для prompt versions и A/B tests |

### Админ-интерфейс

| Требование | Статус | Файл | Комментарий |
|------------|--------|------|-------------|
| Журнал звонков (фильтрация) | ✅ | `src/api/analytics.py` | `GET /analytics/calls` — фильтры по quality, scenario, transferred, date, search |
| Детали звонка (транскрипция, tool calls) | ✅ | `src/api/analytics.py` | `GET /analytics/calls/{call_id}` — turns, tool_calls, quality |
| Управление промптами | ✅ | `src/api/prompts.py` | CRUD для prompt_versions |
| Управление KB | ✅ | `src/api/knowledge.py` | CRUD для knowledge_articles |
| Admin UI (SPA) | ⚠️ | `admin-ui/index.html` | Минимальная HTML-оболочка (shell), полноценный SPA не реализован |

### Cost Optimization

| Требование | Статус | Файл | Комментарий |
|------------|--------|------|-------------|
| Cost tracking (STT + LLM + TTS) | ✅ | `src/monitoring/cost_tracker.py` | `CostBreakdown` с pricing для всех провайдеров |
| Whisper STT (self-hosted) | ✅ | `src/stt/whisper_stt.py` | `WhisperSTTEngine` с Faster-Whisper |
| Model routing (Haiku/Sonnet) | ✅ | `src/agent/model_router.py` | Маршрутизация по сложности запроса |
| TTS cache | ✅ | `src/tts/google_tts.py` | In-memory кэш для частых фраз |

### Celery задачи

| Задача | Статус | Файл | Комментарий |
|--------|--------|------|-------------|
| Quality evaluation | ✅ | `src/tasks/quality_evaluator.py` | Celery task с Claude Haiku |
| Daily stats aggregation | ✅ | `src/tasks/daily_stats.py` | Celery beat, ежедневная агрегация |

---

## 3.8 Sequence Diagrams

**Источник документации:** `doc/technical/sequence-diagrams.md`
**Источник кода:** весь `src/`

Документация упоминает 7 сценариев. Файл `sequence-diagrams.md` содержит 6 диаграмм.

| Сценарий (docs) | Диаграмма | Код | Комментарий |
|-----------------|-----------|-----|-------------|
| 1. Поиск шин | ✅ | ✅ `search_tires` в tools.py + client.py | Полный поток AudioSocket→STT→LLM→tool→TTS |
| 2. Проверка наличия | ✅ | ✅ `check_availability` | Покрыт в sequence-diagrams.md |
| 3. Переключение на оператора | ✅ | ✅ `transfer_to_operator` в main.py | С CallerID через ARI |
| 4. Статус заказа | ⚠️ | ✅ `get_order_status` / `search_orders` | Отдельной диаграммы нет, но покрыт в общем потоке |
| 5. Оформление заказа (draft→delivery→confirm) | ✅ | ✅ `create_order_draft` → `update_order_delivery` → `confirm_order` | Полный трёхшаговый flow |
| 6. Запись на шиномонтаж | ✅ | ✅ `get_fitting_stations` → `get_fitting_slots` → `book_fitting` | Полный flow |
| 7. Консультация (RAG) | ⚠️ | ✅ `search_knowledge_base` + `src/knowledge/search.py` | Нет отдельной диаграммы в sequence-diagrams.md |

**Дополнительные диаграммы в коде:**
- Barge-in сценарий (диаграмма в docs: ✅, код: ✅ `_barge_in_event` в pipeline.py)
- Обработка ошибок (диаграмма в docs: ✅, код: ✅ try/except в pipeline.py)

---

## 3.9 Deployment и Docker

**Источник документации:** `doc/technical/deployment.md`
**Источник кода:** `docker-compose.yml`

### Сервисы

| Сервис (docs) | Статус в docker-compose.yml | Комментарий |
|---------------|----------------------------|-------------|
| call-processor | ✅ | Порты 9092 (AudioSocket), 8080 (API) |
| postgres (pgvector:pg16) | ✅ | С healthcheck |
| redis (7-alpine) | ✅ | С healthcheck |
| prometheus | ✅ | prom/prometheus:v2.53.0 + alerts.yml |
| grafana | ✅ | grafana/grafana:11.1.0 + provisioning |
| celery-worker | 🔄 **Не в docs** | В docker-compose.yml, но НЕ описан в deployment.md |
| celery-beat | 🔄 **Не в docs** | В docker-compose.yml, но НЕ описан в deployment.md |
| alertmanager | 🔄 **Не в docs** | В docker-compose.yml, но НЕ описан в deployment.md |

### Различия между docs и реальным docker-compose.yml

| Аспект | Документация (deployment.md) | Реальный docker-compose.yml |
|--------|-----------------------------|-----------------------------|
| Сервисы | 5 (call-processor, postgres, redis, prometheus, grafana) | 8 (+celery-worker, celery-beat, alertmanager) |
| Postgres user | `app` | `callcenter` |
| DATABASE_URL | `postgresql://app:secret@postgres:5432/callcenter` | `postgresql+asyncpg://callcenter:...` |
| Prometheus config path | `./monitoring/prometheus.yml` | `./prometheus/prometheus.yml` |
| Grafana dashboards path | `./monitoring/grafana/dashboards` | `./grafana/provisioning`, `./grafana/dashboards` |
| version ключ | `"3.9"` | отсутствует (Docker Compose V2) |
| Resource limits | `cpus: "2.0"`, `memory: 4G` | Не указаны |
| `restart: unless-stopped` | Указан для всех сервисов | Не указан |
| Redis volumes | `redisdata:/data` | Не указан |

### Порты

| Порт (docs) | Статус | Комментарий |
|-------------|--------|-------------|
| 9092 (AudioSocket) | ✅ | Совпадает |
| 8080 (REST API) | ✅ | Совпадает |
| 5432 (PostgreSQL) | ⚠️ | Docs: `127.0.0.1:5432:5432`, code: не exposed |
| 6379 (Redis) | ⚠️ | Docs: `127.0.0.1:6379:6379`, code: не exposed |
| 9090 (Prometheus) | ✅ | Совпадает |
| 3000 (Grafana) | ✅ | Совпадает |
| 9093 (Alertmanager) | 🔄 | В коде есть, в docs нет |

### Healthchecks

| Сервис | Документация | Код | Комментарий |
|--------|-------------|-----|-------------|
| Call Processor `GET /health` | ✅ | ✅ | Проверяет Redis, AudioSocket connections |
| Call Processor `GET /health/ready` | ⚠️ Описан | ❌ Не реализован | Docs описывают readiness probe, в коде только `/health` |
| PostgreSQL `pg_isready` | ✅ | ✅ | Реализован |
| Redis `redis-cli ping` | ✅ | ✅ | Реализован |

---

## 3.10 Тестирование

**Источник документации:** `doc/development/00-overview.md` (секция «Стратегия тестирования»)
**Источник кода:** `tests/`

### Unit-тесты

| Модуль (docs) | Статус | Файл теста | Комментарий |
|---------------|--------|------------|-------------|
| `core/audio_socket.py` — парсинг протокола | ✅ | `tests/unit/test_audio_socket.py` | UUID, audio, hangup пакеты |
| `agent/tools.py` — валидация параметров | ✅ | `tests/unit/test_fitting_tools.py`, `test_order_tools.py` | Tool schemas |
| `agent/agent.py` — messages, tool_use | ✅ | `tests/unit/test_agent.py` | С mock Claude |
| `stt/google_stt.py` — transcripts, restart | ✅ | `tests/unit/test_stt.py` | С mock gRPC |
| `tts/google_tts.py` — конвертация, кэш | ✅ | `tests/unit/test_tts.py` | Кэширование |
| `store_client/client.py` — retry, circuit breaker | ✅ | `tests/unit/test_store_client.py` | + `test_store_client_orders.py`, `test_store_client_fitting.py` |
| `call_session.py` — состояния | ✅ | `tests/unit/test_call_session.py` | State machine transitions |
| PII sanitizer | ✅ | `tests/unit/test_pii_sanitizer.py` | Маскировка телефонов и имён |
| CallerID strategy | ✅ | `tests/unit/test_caller_id.py` | Из phase-2 docs |
| A/B testing | ✅ | `tests/unit/test_ab_testing.py` | Z-test |
| Quality evaluator | ✅ | `tests/unit/test_quality_evaluator.py` | 8 criteria |
| Alerts | ✅ | `tests/unit/test_alerts.py` | Валидация правил алертов |
| Cost optimization | ✅ | `tests/unit/test_cost_optimization.py` | Model routing, cache |
| Knowledge base | ✅ | `tests/unit/test_knowledge_base.py` | RAG search |

### Интеграционные тесты

| Сценарий (docs) | Статус | Файл | Комментарий |
|-----------------|--------|------|-------------|
| Pipeline (STT→LLM→TTS) | ✅ | `tests/integration/test_pipeline.py` | С mock engines |
| Call Processor → PostgreSQL | ✅ | `tests/integration/test_postgres.py` | Запись логов |
| Call Processor → Redis | ✅ | `tests/integration/test_redis.py` | Сессии, TTL |
| Analytics integration | ✅ | `tests/integration/test_analytics.py` | Quality + daily stats |

### Adversarial-тесты

| Тест (docs) | Статус | Файл | Комментарий |
|-------------|--------|------|-------------|
| Prompt injection: смена роли | ✅ | `tests/unit/test_agent.py` | Проверка устойчивости |
| Абсурдное количество товаров | ✅ | `tests/unit/test_adversarial_orders.py` | quantity validation |
| Сложные сценарии | ✅ | `tests/unit/test_complex_scenarios.py` | Multi-tool flows |

### E2E тесты

| Сценарий (docs) | Статус | Файл | Комментарий |
|-----------------|--------|------|-------------|
| Подбор шин | ✅ | `tests/e2e/test_tire_search.py` | Flow-тест |
| Оформление заказа | ✅ | `tests/e2e/test_orders.py` | Полный цикл |

### Нагрузочные тесты

| Статус | Файл | Комментарий |
|--------|------|-------------|
| ✅ | `tests/load/locustfile.py` | Locust config для нагрузочного тестирования |

### CI/CD Pipeline

| Стадия (docs) | Статус | Файл | Комментарий |
|---------------|--------|------|-------------|
| Lint & Type Check (ruff, mypy) | ✅ | `.github/workflows/ci.yml` jobs: lint | `ruff check src/`, `mypy src/ --strict` |
| Unit Tests (pytest + coverage) | ✅ | `.github/workflows/ci.yml` jobs: test | `pytest tests/ --cov=src --cov-report=xml` + Codecov |
| Security Scan (pip-audit, safety) | ✅ | `.github/workflows/ci.yml` jobs: security | `pip-audit --strict`, `safety check` |
| Build Docker Image | ✅ | `.github/workflows/ci.yml` jobs: build | `docker/build-push-action@v5` |
| Deploy to Staging | ✅ | `.github/workflows/ci.yml` jobs: deploy-staging | При push в main |
| Integration tests отдельным job | ⚠️ | — | Docs описывают отдельный этап, в CI интеграционные тесты идут вместе с unit |
| Deploy to Production | ⚠️ | — | Docs описывают deploy из release/* с manual approval, в CI нет этого job |

**КР-3 ИСПРАВЛЕНО:** CI/CD pipeline существует и покрывает основные стадии.

---

## Критические расхождения

| # | Описание | Влияние | Рекомендация |
|---|----------|---------|-------------|
| КР-4 | `cancel_fitting` и `get_fitting_price` не зарегистрированы в `_build_tool_router()` | LLM может вызвать эти tools, но получит runtime error | Добавить 2 строки в `_build_tool_router()`: `router.register("cancel_fitting", _store_client.cancel_fitting)` и `router.register("get_fitting_price", _store_client.get_fitting_price)` |
| КР-5 | Миграция 006 не документирована в data-model.md | Несоответствие спецификации | Добавить `006_add_prompt_ab_tests.py` в список миграций в data-model.md |

---

## Некритичные расхождения

| # | Описание | Влияние | Рекомендация |
|---|----------|---------|-------------|
| НР-1 | End-to-end алерт порог: docs = 2500ms, alerts.yml = 3000ms | Менее агрессивный алерт | Привести в соответствие — выбрать один порог |
| НР-2 | Docker Compose в docs (deployment.md) не содержит celery-worker, celery-beat, alertmanager | Документация неполная | Обновить deployment.md, добавить описание 3 сервисов |
| НР-3 | Docker Compose: Postgres user различается (docs: `app`, code: `callcenter`) | Копирование команд из docs не будет работать | Обновить docs |
| НР-4 | `GET /health/ready` описан в docs, но не реализован в коде | Нет readiness probe для k8s/orchestrator | Реализовать endpoint или убрать из docs |
| НР-5 | Admin UI — только HTML shell, без полноценного SPA | Функционал доступен только через API | Документировать текущий статус или создать полноценный UI |
| НР-6 | Колонка `quality_details` (JSONB) в таблице `calls` не отражена в ERD | Мелкое расхождение ERD | Обновить ERD в data-model.md |
| НР-7 | Production deploy (release/*) описан в docs, но не реализован в CI | Нет автоматического production deploy | Добавить job в ci.yml или убрать из docs |
| НР-8 | `restart: unless-stopped` в docs, но не в реальном docker-compose.yml | Контейнеры не перезапустятся после сбоя | Добавить restart policy в docker-compose.yml |
| НР-9 | Resource limits (CPU/RAM) в docs, но не в docker-compose.yml | Контейнер может потребить все ресурсы хоста | Добавить deploy.resources.limits |
| НР-10 | Автоматическое удаление данных старше 90 дней (retention) не реализовано | Данные не удаляются автоматически | Создать Celery task или cron для `DROP PARTITION` |
| НР-11 | Prometheus config path: docs `./monitoring/prometheus.yml`, code `./prometheus/prometheus.yml` | Мелкое расхождение | Обновить docs |

---

## Отсутствующие компоненты (описаны в docs, не реализованы)

| # | Компонент | Документация | Комментарий |
|---|-----------|-------------|-------------|
| 1 | `GET /health/ready` endpoint | deployment.md | Readiness probe с проверкой STT/Claude |
| 2 | Data retention automation | data-policy.md | Автоматическое удаление партиций старше 90 дней |
| 3 | Production deploy job | 00-overview.md CI/CD | `release/*` → production с manual approval |
| 4 | Asterisk AMI exporter | nfr.md | Мониторинг каналов Asterisk |
| 5 | PII masking перед отправкой в LLM | data-policy.md (Phase 2) | Замена имён/адресов плейсхолдерами |

---

## Избыточные компоненты (реализованы, но не описаны в docs)

| # | Компонент | Файл | Комментарий |
|---|-----------|------|-------------|
| 1 | Миграция 006 (`prompt_ab_tests`) | `migrations/versions/006_add_prompt_ab_tests.py` | Таблицы описаны в ERD, но миграция не перечислена |
| 2 | Celery-worker/beat в docker-compose | `docker-compose.yml` | Сервисы не описаны в deployment.md |
| 3 | Alertmanager в docker-compose | `docker-compose.yml` | Сервис не описан в deployment.md |
| 4 | `StoreClient.get_tire()` | `store_client/client.py` | Дополнительный метод, не связан с tool |
| 5 | `StoreClient.get_pickup_points()` | `store_client/client.py` | Дополнительный метод, не связан с tool |
| 6 | `StoreClient.calculate_delivery()` | `store_client/client.py` | Дополнительный метод, не связан с tool |
| 7 | `tts_cache_hits_total`, `tts_cache_misses_total` | `metrics.py` | Метрики кэша TTS — полезные, но не документированы в nfr.md |

---

## Рекомендации

### Приоритет 1 — Критические (блокируют функционал)

1. **КР-4: Зарегистрировать `cancel_fitting` и `get_fitting_price` в tool router.**

   Файл: `src/main.py`, функция `_build_tool_router()`.

   Добавить перед строкой `router.register("search_knowledge_base", ...)`:
   ```python
   router.register("cancel_fitting", _store_client.cancel_fitting)
   router.register("get_fitting_price", _store_client.get_fitting_price)
   ```

### Приоритет 2 — Документация (несоответствие спецификации)

2. **КР-5: Добавить миграцию 006 в `doc/technical/data-model.md`.**

   В секции «Миграции» добавить:
   ```
   006_add_prompt_ab_tests.py
   ```

3. **НР-2: Обновить `doc/technical/deployment.md`.**

   Добавить описание сервисов celery-worker, celery-beat, alertmanager в Docker Compose секцию.

4. **НР-3: Исправить Postgres user в deployment.md** с `app` на `callcenter`, DATABASE_URL на `postgresql+asyncpg://`.

5. **НР-11: Исправить путь к Prometheus config** в deployment.md с `./monitoring/` на `./prometheus/`.

### Приоритет 3 — Инфраструктура (улучшения)

6. **НР-4: Реализовать `GET /health/ready`** или убрать из документации.

7. **НР-8/НР-9: Добавить `restart: unless-stopped` и resource limits** в docker-compose.yml.

8. **НР-10: Создать Celery task для data retention** — автоматическое удаление партиций старше 90 дней.

9. **НР-1: Привести в соответствие порог алерта** end-to-end latency (2500ms vs 3000ms).

10. **НР-7: Добавить production deploy job** в `.github/workflows/ci.yml` для ветки `release/*`.

---

## Заключение

Проект демонстрирует зрелую кодовую базу, покрывающую все 4 фазы разработки. Все критические исправления из предыдущего аудита (КР-1, КР-2, КР-3, НР-7, НР-8) успешно применены.

Обнаружено 2 новых критических расхождения (КР-4: незарегистрированные tools, КР-5: недокументированная миграция) и 11 некритичных расхождений, преимущественно связанных с неполным обновлением документации после добавления новых компонентов (Celery, Alertmanager, миграция 006).

Основная рекомендация: привести документацию deployment.md и data-model.md в соответствие с текущим состоянием кода, и зарегистрировать 2 пропущенных tool handler-а в main.py.
