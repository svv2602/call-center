# Фаза 8: Логирование и мониторинг

## Статус
- [x] Не начата
- [x] В процессе
- [x] Завершена

**Начата:** 2026-02-13
**Завершена:** 2026-02-13

## Цель фазы

Реализовать полное логирование звонков в PostgreSQL, хранение сессий в Redis, метрики Prometheus. Каждый звонок логируется с транскрипцией, tool calls и метриками задержек.

## Задачи

### 8.0 ОБЯЗАТЕЛЬНО: Анализ и планирование

#### A. Анализ существующего кода
- [x] Проверить наличие миграций БД (из phase-01)
- [x] Изучить схему данных: `doc/technical/data-model.md`
- [x] Проверить существующие модели SQLAlchemy

#### B. Анализ зависимостей
- [x] PostgreSQL: таблицы `calls`, `call_turns`, `call_tool_calls`, `customers`
- [x] Redis: сессии с TTL
- [x] Prometheus: метрики для всех компонентов
- [x] Structured JSON logging: `call_id` + `request_id` для трассировки

#### C. Проверка архитектуры
- [x] Асинхронная запись логов (не блокирует основной поток)
- [x] PII санитизация (маскировка телефонов, имён)
- [x] Корреляция: `call_id` проходит через все компоненты
- [x] Партиционирование таблиц по месяцам

**Заметки для переиспользования:** `CallLogger` использует raw SQL через `text()` — подходит для партиционированных таблиц. PII санитизация только в stdout, не в PostgreSQL.

---

### 8.1 Call Logger (PostgreSQL)

- [x] Создать модуль логирования `src/logging/call_logger.py`
- [x] Запись в таблицу `calls`: начало/конец звонка, сценарий, результат, стоимость
- [x] Запись в таблицу `call_turns`: каждая реплика (speaker, content, latency)
- [x] Запись в таблицу `call_tool_calls`: каждый tool call (name, args, result, duration)
- [x] Асинхронная запись (не блокирует обработку звонка)
- [x] Batch-запись turns (накопить несколько → записать одним INSERT)

**Файлы:** `src/logging/call_logger.py`
**Заметки:** Методы: `log_call_start()`, `log_call_end()`, `log_turn()`, `log_tool_call()`. Async через `async_sessionmaker`.

---

### 8.2 Customer tracking

- [x] Создание/обновление записи в таблице `customers` при звонке
- [x] Идентификация по CallerID (phone)
- [x] Обновление `total_calls`, `last_call_at`
- [x] Связь `calls.customer_id` → `customers.id`

**Файлы:** `src/logging/call_logger.py`
**Заметки:** `upsert_customer(phone, name)` — SELECT + INSERT/UPDATE. Возвращает customer_id.

---

### 8.3 PII санитизация

- [x] Создать `PIISanitizer` — маскировка персональных данных в логах
- [x] Маскировка телефонов: `+380XXXXXXXXX` → `+380***XXX`
- [x] Маскировка имён в тексте логов
- [x] Применение ко всем structured logs
- [x] НЕ маскировать в PostgreSQL (нужно для поиска), ТОЛЬКО в stdout/файловых логах

**Файлы:** `src/logging/pii_sanitizer.py`
**Заметки:** Regex для украинских номеров. `sanitize_pii()` вызывается в `JSONFormatter.format()`.

---

### 8.4 Structured JSON logging

- [x] Настроить Python logging в формате structured JSON
- [x] Обязательные поля: `timestamp`, `level`, `call_id`, `component`, `event`
- [x] Дополнительные поля: `request_id`, `tool`, `duration_ms`, `success`
- [x] Уровни: INFO (начало/конец звонка), DEBUG (каждый turn), WARNING (retry, timeout), ERROR (ошибки API)
- [x] Настройка через `LOG_LEVEL` и `LOG_FORMAT` env variables

**Файлы:** `src/logging/structured_logger.py`
**Заметки:** `JSONFormatter` — custom logging.Formatter. `setup_logging()` конфигурирует root logger. Шумные библиотеки (aiohttp, asyncio, uvicorn, google) ставятся на WARNING.

---

### 8.5 Метрики Prometheus

- [x] Создать `src/monitoring/metrics.py` с определением всех метрик
- [x] `active_calls` (Gauge) — количество активных звонков
- [x] `call_duration_seconds` (Histogram) — длительность звонков
- [x] `stt_latency_ms` (Histogram) — задержка STT
- [x] `llm_latency_ms` (Histogram) — задержка LLM
- [x] `tts_latency_ms` (Histogram) — задержка TTS
- [x] `total_response_latency_ms` (Histogram) — end-to-end задержка
- [x] `tool_call_duration_ms` (Histogram, labels: tool_name) — задержка tool calls
- [x] `store_api_errors_total` (Counter, labels: status_code) — ошибки Store API
- [x] `transfers_to_operator_total` (Counter, labels: reason) — переключения на оператора
- [x] Endpoint `/metrics` на порту 8080 (FastAPI)

**Файлы:** `src/monitoring/metrics.py`, `src/main.py`
**Заметки:** 12 метрик определено. `/metrics` endpoint добавлен в FastAPI app. `get_metrics()` возвращает `generate_latest()`.

---

### 8.6 Graceful degradation при сбое БД

- [x] При недоступности PostgreSQL: звонки продолжают работать
- [x] Логи буферизуются в Redis (временно)
- [x] При восстановлении PostgreSQL: flush буфера в БД
- [x] При недоступности Redis: звонки работают с in-memory state

**Файлы:** `src/logging/call_logger.py`
**Заметки:** `_buffer_to_redis()` при ошибках PG. `flush_redis_buffer()` для восстановления. Redis buffer TTL: 1 час.

---

## При завершении фазы

1. Убедись, что все задачи отмечены [x]
2. Измени статус фазы: [x] Завершена
3. Заполни дату "Завершена: YYYY-MM-DD"
4. Выполни коммит:
   ```bash
   git add .
   git commit -m "checklist(phase-1-mvp): phase-8 logging completed"
   ```
5. Обнови PROGRESS.md
6. Открой следующую фазу: `phase-09-testing.md`
