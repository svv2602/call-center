# Фаза 8: Логирование и мониторинг

## Статус
- [ ] Не начата
- [ ] В процессе
- [ ] Завершена

**Начата:** -
**Завершена:** -

## Цель фазы

Реализовать полное логирование звонков в PostgreSQL, хранение сессий в Redis, метрики Prometheus. Каждый звонок логируется с транскрипцией, tool calls и метриками задержек.

## Задачи

### 8.0 ОБЯЗАТЕЛЬНО: Анализ и планирование

#### A. Анализ существующего кода
- [ ] Проверить наличие миграций БД (из phase-01)
- [ ] Изучить схему данных: `doc/technical/data-model.md`
- [ ] Проверить существующие модели SQLAlchemy

**Команды для поиска:**
```bash
ls migrations/versions/
grep -rn "SQLAlchemy\|Base\|Column\|Table" src/
grep -rn "prometheus\|Counter\|Histogram\|Gauge" src/
```

#### B. Анализ зависимостей
- [ ] PostgreSQL: таблицы `calls`, `call_turns`, `call_tool_calls`, `customers`
- [ ] Redis: сессии с TTL
- [ ] Prometheus: метрики для всех компонентов
- [ ] Structured JSON logging: `call_id` + `request_id` для трассировки

**Новые абстракции:** Нет
**Новые env variables:** `DATABASE_URL`, `REDIS_URL` (уже в config)
**Новые tools:** Нет
**Миграции БД:** Начальная схема (если не создана в phase-01)

#### C. Проверка архитектуры
- [ ] Асинхронная запись логов (не блокирует основной поток)
- [ ] PII санитизация (маскировка телефонов, имён)
- [ ] Корреляция: `call_id` проходит через все компоненты
- [ ] Партиционирование таблиц по месяцам

**Референс-модуль:** `doc/technical/data-model.md`, `doc/technical/architecture.md` — секция 3.8

**Цель:** Определить структуру логирования и метрик.

**Заметки для переиспользования:** -

---

### 8.1 Call Logger (PostgreSQL)

- [ ] Создать модуль логирования `src/logging/call_logger.py`
- [ ] Запись в таблицу `calls`: начало/конец звонка, сценарий, результат, стоимость
- [ ] Запись в таблицу `call_turns`: каждая реплика (speaker, content, latency)
- [ ] Запись в таблицу `call_tool_calls`: каждый tool call (name, args, result, duration)
- [ ] Асинхронная запись (не блокирует обработку звонка)
- [ ] Batch-запись turns (накопить несколько → записать одним INSERT)

**Файлы:** `src/logging/call_logger.py`
**Заметки:** -

---

### 8.2 Customer tracking

- [ ] Создание/обновление записи в таблице `customers` при звонке
- [ ] Идентификация по CallerID (phone)
- [ ] Обновление `total_calls`, `last_call_at`
- [ ] Связь `calls.customer_id` → `customers.id`

**Файлы:** `src/logging/call_logger.py`
**Заметки:** -

---

### 8.3 PII санитизация

- [ ] Создать `PIISanitizer` — маскировка персональных данных в логах
- [ ] Маскировка телефонов: `+380XXXXXXXXX` → `+380***XXX`
- [ ] Маскировка имён в тексте логов
- [ ] Применение ко всем structured logs
- [ ] НЕ маскировать в PostgreSQL (нужно для поиска), ТОЛЬКО в stdout/файловых логах

**Файлы:** `src/logging/pii_sanitizer.py`
**Заметки:** -

---

### 8.4 Structured JSON logging

- [ ] Настроить Python logging в формате structured JSON
- [ ] Обязательные поля: `timestamp`, `level`, `call_id`, `component`, `event`
- [ ] Дополнительные поля: `request_id`, `tool`, `duration_ms`, `success`
- [ ] Уровни: INFO (начало/конец звонка), DEBUG (каждый turn), WARNING (retry, timeout), ERROR (ошибки API)
- [ ] Настройка через `LOG_LEVEL` и `LOG_FORMAT` env variables

**Файлы:** `src/logging/structured_logger.py`

**Пример:**
```json
{
    "timestamp": "2025-03-15T10:30:00.123Z",
    "level": "INFO",
    "call_id": "a1b2c3d4-...",
    "request_id": "req-e5f6g7h8-...",
    "component": "store_client",
    "event": "tool_call_completed",
    "tool": "search_tires",
    "duration_ms": 145,
    "success": true
}
```

**Заметки:** -

---

### 8.5 Метрики Prometheus

- [ ] Создать `src/monitoring/metrics.py` с определением всех метрик
- [ ] `active_calls` (Gauge) — количество активных звонков
- [ ] `call_duration_seconds` (Histogram) — длительность звонков
- [ ] `stt_latency_ms` (Histogram) — задержка STT
- [ ] `llm_latency_ms` (Histogram) — задержка LLM
- [ ] `tts_latency_ms` (Histogram) — задержка TTS
- [ ] `total_response_latency_ms` (Histogram) — end-to-end задержка
- [ ] `tool_call_duration_ms` (Histogram, labels: tool_name) — задержка tool calls
- [ ] `store_api_errors_total` (Counter, labels: status_code) — ошибки Store API
- [ ] `transfers_to_operator_total` (Counter, labels: reason) — переключения на оператора
- [ ] Endpoint `/metrics` на порту 8080 (FastAPI)

**Файлы:** `src/monitoring/metrics.py`, `src/api/routes.py`
**Заметки:** Алерты настраиваются позже (фаза 4)

---

### 8.6 Graceful degradation при сбое БД

- [ ] При недоступности PostgreSQL: звонки продолжают работать
- [ ] Логи буферизуются в Redis (временно)
- [ ] При восстановлении PostgreSQL: flush буфера в БД
- [ ] При недоступности Redis: звонки работают с in-memory state

**Файлы:** `src/logging/call_logger.py`
**Заметки:** Из NFR: "PostgreSQL недоступен → логирование в Redis (буфер)"

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
