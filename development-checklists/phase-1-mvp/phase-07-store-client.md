# Фаза 7: Store API клиент

## Статус
- [ ] Не начата
- [ ] В процессе
- [ ] Завершена

**Начата:** -
**Завершена:** -

## Цель фазы

Реализовать HTTP-клиент для Store API магазина с circuit breaker, retry и маппингом ответов. Клиент обеспечивает интеграцию LLM tools с реальным API магазина.

## Задачи

### 7.0 ОБЯЗАТЕЛЬНО: Анализ и планирование

#### A. Анализ существующего кода
- [ ] Проверить наличие `src/store_client/client.py`
- [ ] Изучить спецификацию Store API: `doc/development/api-specification.md`
- [ ] Определить модели данных для ответов API

**Команды для поиска:**
```bash
ls src/store_client/
grep -rn "aiohttp\|ClientSession\|CircuitBreaker" src/
grep -rn "store_client\|StoreClient" src/
```

#### B. Анализ зависимостей
- [ ] Нужна ли абстракция Protocol? — Нет, единственный Store API
- [ ] Нужны ли новые env variables? — `STORE_API_URL`, `STORE_API_KEY` (уже в config)
- [ ] Нужна библиотека `aiobreaker` для circuit breaker

**Новые абстракции:** Нет
**Новые env variables:** Нет (уже определены)
**Новые tools:** Нет (tools определены в phase-05)
**Миграции БД:** Нет

#### C. Проверка архитектуры
- [ ] Circuit breaker: `aiobreaker.CircuitBreaker(fail_max=5, timeout_duration=30)`
- [ ] Retry: до 2 раз, только для 429 и 503, exponential backoff (1s → 2s)
- [ ] Таймаут запроса: 5 секунд
- [ ] Заголовки: `Authorization: Bearer <API_KEY>`, `X-Request-Id`

**Референс-модуль:** `doc/technical/architecture.md` — секция 3.7, `doc/development/api-specification.md`

**Цель:** Определить интерфейс клиента и стратегию retry/circuit breaker.

**Заметки для переиспользования:** -

---

### 7.1 HTTP-клиент (aiohttp)

- [ ] Создать `src/store_client/client.py` — класс `StoreClient`
- [ ] Singleton aiohttp.ClientSession (переиспользование соединений)
- [ ] Base URL из конфигурации: `STORE_API_URL`
- [ ] Аутентификация: `Authorization: Bearer {STORE_API_KEY}`
- [ ] Заголовок `X-Request-Id` для каждого запроса (UUID)
- [ ] Таймаут запроса: 5 секунд (из спецификации)
- [ ] Graceful закрытие сессии при shutdown

**Файлы:** `src/store_client/client.py`
**Заметки:** -

---

### 7.2 Реализация endpoints (MVP)

- [ ] `search_tires(params)` → `GET /tires/search` — поиск шин по параметрам
- [ ] `get_tire(tire_id)` → `GET /tires/{id}` — детали шины
- [ ] `check_availability(tire_id)` → `GET /tires/{id}/availability` — наличие товара
- [ ] `get_vehicle_tires(make, model, year, season)` → `GET /vehicles/tires` — подбор по авто
- [ ] Маппинг ответов API в формат для LLM tools (краткий, понятный)

**Файлы:** `src/store_client/client.py`

**API endpoints MVP:**
| Метод | Endpoint | Tool |
|-------|----------|------|
| GET | `/api/v1/tires/search` | `search_tires` |
| GET | `/api/v1/tires/{id}` | (вспомогательный) |
| GET | `/api/v1/tires/{id}/availability` | `check_availability` |
| GET | `/api/v1/vehicles/tires` | `search_tires` (по авто) |

**Заметки:** Маппинг сокращает объём данных для LLM (убирает image_url, описания и т.д.)

---

### 7.3 Circuit Breaker

- [ ] Настроить `aiobreaker.CircuitBreaker(fail_max=5, timeout_duration=30)`
- [ ] Обернуть все вызовы Store API в circuit breaker
- [ ] При Open-состоянии: сразу возвращать ошибку (не ждать таймаут)
- [ ] Логирование переходов состояний (Closed → Open → Half-Open → Closed)
- [ ] Метрика Prometheus: `store_api_circuit_breaker_state`

**Файлы:** `src/store_client/client.py`

**Пример:**
```python
store_breaker = CircuitBreaker(fail_max=5, timeout_duration=30)

@store_breaker
async def search_tires(params: dict) -> dict:
    async with session.get(f"{STORE_URL}/tires/search", params=params) as resp:
        resp.raise_for_status()
        return await resp.json()
```

**Заметки:** При Open: агент сообщает клиенту о временных проблемах

---

### 7.4 Retry с exponential backoff

- [ ] Retry до 2 раз, только для HTTP 429 и 503
- [ ] Exponential backoff: 1s → 2s
- [ ] НЕ ретраить HTTP 500 (ошибка логики, ретрай не поможет)
- [ ] Уважать заголовок `Retry-After` для 429 и 503
- [ ] Логирование каждого retry

**Файлы:** `src/store_client/client.py`
**Заметки:** -

---

### 7.5 Обработка ошибок и маппинг

- [ ] Обработка HTTP ошибок: 400, 401, 404, 422, 429, 500, 503
- [ ] Формирование понятных ошибок для LLM (tool result с описанием проблемы)
- [ ] При 401: логировать критическую ошибку (невалидный API key)
- [ ] При 404: "товар не найден" — нормальная ситуация, не ошибка
- [ ] PII санитизация в логах (маскировка телефонов)

**Файлы:** `src/store_client/client.py`
**Заметки:** -

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
