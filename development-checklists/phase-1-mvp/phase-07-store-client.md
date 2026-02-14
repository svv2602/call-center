# Фаза 7: Store API клиент

## Статус
- [x] Не начата
- [x] В процессе
- [x] Завершена

**Начата:** 2026-02-13
**Завершена:** 2026-02-13

## Цель фазы

Реализовать HTTP-клиент для Store API магазина с circuit breaker, retry и маппингом ответов. Клиент обеспечивает интеграцию LLM tools с реальным API магазина.

## Задачи

### 7.0 ОБЯЗАТЕЛЬНО: Анализ и планирование

#### A. Анализ существующего кода
- [x] Проверить наличие `src/store_client/client.py`
- [x] Изучить спецификацию Store API: `doc/development/api-specification.md`
- [x] Определить модели данных для ответов API

#### B. Анализ зависимостей
- [x] Нужна ли абстракция Protocol? — Нет, единственный Store API
- [x] Нужны ли новые env variables? — `STORE_API_URL`, `STORE_API_KEY` (уже в config)
- [x] Нужна библиотека `aiobreaker` для circuit breaker

#### C. Проверка архитектуры
- [x] Circuit breaker: `aiobreaker.CircuitBreaker(fail_max=5, timeout_duration=30)`
- [x] Retry: до 2 раз, только для 429 и 503, exponential backoff (1s → 2s)
- [x] Таймаут запроса: 5 секунд
- [x] Заголовки: `Authorization: Bearer <API_KEY>`, `X-Request-Id`

**Заметки для переиспользования:** `StoreClient` — aiohttp-based, singleton session. Методы `search_tires()` и `check_availability()` — готовые handlers для ToolRouter.

---

### 7.1 HTTP-клиент (aiohttp)

- [x] Создать `src/store_client/client.py` — класс `StoreClient`
- [x] Singleton aiohttp.ClientSession (переиспользование соединений)
- [x] Base URL из конфигурации: `STORE_API_URL`
- [x] Аутентификация: `Authorization: Bearer {STORE_API_KEY}`
- [x] Заголовок `X-Request-Id` для каждого запроса (UUID)
- [x] Таймаут запроса: 5 секунд (из спецификации)
- [x] Graceful закрытие сессии при shutdown

**Файлы:** `src/store_client/client.py`
**Заметки:** `open()` / `close()` для lifecycle. Timeout через `aiohttp.ClientTimeout(total=5)`.

---

### 7.2 Реализация endpoints (MVP)

- [x] `search_tires(params)` → `GET /tires/search` — поиск шин по параметрам
- [x] `get_tire(tire_id)` → `GET /tires/{id}` — детали шины
- [x] `check_availability(tire_id)` → `GET /tires/{id}/availability` — наличие товара
- [x] `get_vehicle_tires(make, model, year, season)` → `GET /vehicles/tires` — подбор по авто
- [x] Маппинг ответов API в формат для LLM tools (краткий, понятный)

**Файлы:** `src/store_client/client.py`
**Заметки:** `search_tires()` автоматически выбирает endpoint по наличию vehicle_ параметров. `_format_tire_results()` обрезает до 5 результатов и убирает image/description.

---

### 7.3 Circuit Breaker

- [x] Настроить `aiobreaker.CircuitBreaker(fail_max=5, timeout_duration=30)`
- [x] Обернуть все вызовы Store API в circuit breaker
- [x] При Open-состоянии: сразу возвращать ошибку (не ждать таймаут)
- [x] Логирование переходов состояний (Closed → Open → Half-Open → Closed)
- [x] Метрика Prometheus: `store_api_circuit_breaker_state`

**Файлы:** `src/store_client/client.py`
**Заметки:** Модульный `_store_breaker` через `call_async()`. При `CircuitBreakerError` → `StoreAPIError(503, ...)`.

---

### 7.4 Retry с exponential backoff

- [x] Retry до 2 раз, только для HTTP 429 и 503
- [x] Exponential backoff: 1s → 2s
- [x] НЕ ретраить HTTP 500 (ошибка логики, ретрай не поможет)
- [x] Уважать заголовок `Retry-After` для 429 и 503
- [x] Логирование каждого retry

**Файлы:** `src/store_client/client.py`
**Заметки:** `_request_with_retry()` — цикл с `_RETRY_DELAYS`. Только 429/503 ретраятся.

---

### 7.5 Обработка ошибок и маппинг

- [x] Обработка HTTP ошибок: 400, 401, 404, 422, 429, 500, 503
- [x] Формирование понятных ошибок для LLM (tool result с описанием проблемы)
- [x] При 401: логировать критическую ошибку (невалидный API key)
- [x] При 404: "товар не найден" — нормальная ситуация, не ошибка
- [x] PII санитизация в логах (маскировка телефонов)

**Файлы:** `src/store_client/client.py`
**Заметки:** `StoreAPIError` с status/message. 401 → `logger.critical`. 404 в `check_availability` → `{"available": False, "message": "Товар не знайдено"}`.

---

## При завершении фазы

1. Убедись, что все задачи отмечены [x]
2. Измени статус фазы: [x] Завершена
3. Заполни дату "Завершена: YYYY-MM-DD"
4. Выполни коммит:
   ```bash
   git add .
   git commit -m "checklist(phase-1-mvp): phase-7 store client completed"
   ```
5. Обнови PROGRESS.md
6. Открой следующую фазу: `phase-08-logging.md`
