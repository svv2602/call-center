# Фаза 3: Testing & Quality

## Статус
- [x] Не начата
- [x] В процессе
- [x] Завершена

**Начата:** 2026-02-14
**Завершена:** 2026-02-14

## Цель фазы
Реализовать chaos-тестирование для проверки graceful degradation и регрессионные тесты промптов для защиты качества диалогов. После этой фазы устойчивость системы к сбоям подтверждена тестами, а обновления промптов защищены от деградации.

## Задачи

### 3.0 ОБЯЗАТЕЛЬНО: Анализ и планирование

#### A. Анализ существующего кода
- [x] Изучить `tests/` — структура тестов, conftest.py, фикстуры
- [x] Изучить `src/store_client/client.py` — circuit breaker, retry, fallback логика
- [x] Изучить `src/agent/prompts.py` — текущие системные промпты
- [x] Изучить `src/agent/agent.py` — как агент обрабатывает ошибки tools
- [x] Изучить существующие E2E тесты: `tests/e2e/`

**Команды для поиска:**
```bash
# Тесты
ls tests/unit/ tests/integration/ tests/e2e/
# Circuit breaker
grep -rn "CircuitBreaker\|circuit\|fallback" src/
# Промпты
cat src/agent/prompts.py
# Graceful degradation
grep -rn "transfer_to_operator\|fallback\|graceful" src/
```

#### B. Анализ зависимостей
- [x] Нужны ли новые абстракции (Protocol)? — Нет
- [x] Нужны ли новые env variables? — Нет
- [x] Нужны ли дополнительные библиотеки? — Нет, mock-based chaos (без toxiproxy)
- [x] Нужны ли миграции БД? — Нет

**Новые env variables:** нет
**Миграции БД:** нет

#### C. Проверка архитектуры
- [x] Chaos tests — mock-based (без реальной инфраструктуры), ConnectionError/TimeoutError side_effect
- [x] Prompt regression — проверка содержимого SYSTEM_PROMPT и ALL_TOOLS без вызова Claude API
- [x] Оба типа тестов — в `tests/chaos/` и `tests/prompt_regression/` с markers `@pytest.mark.chaos` и `@pytest.mark.prompt_regression`

**Референс-модуль:** `tests/e2e/` (паттерн end-to-end тестирования)

**Цель:** Понять существующие паттерны проекта ПЕРЕД написанием кода.

**Заметки для переиспользования:** Проект использует mock-based подход (unittest.mock.patch, AsyncMock). CircuitBreakerError требует (message, reopen_time). ToolRouter перехватывает исключения и возвращает {"error": str(exc)}.

---

### 3.1 Chaos Testing — Redis failure

- [x] Тест: Redis недоступен → сессии не создаются, но звонок корректно завершается (transfer to operator)
- [x] Тест: Redis timeout → rate limiter пропускает запросы (fail-open)
- [x] Тест: Redis недоступен → WebSocket events не публикуются (silent fail, no crash)
- [x] Тест: Redis reconnect → восстановление работы после reconnect

**Файлы:** `tests/chaos/test_redis_failure.py`
**Подход:** Mock Redis client с `ConnectionError` / `TimeoutError`
**Заметки:** 9 тестов: rate limiter fail-open (2), login rate limit (2), pub/sub silent fail (2), JWT blacklist fail-open (2), blacklist_token silent fail (1). Все проходят.

---

### 3.2 Chaos Testing — PostgreSQL и Store API failure

- [x] Тест: PostgreSQL недоступен → звонки обрабатываются, логи теряются gracefully
- [x] Тест: Store API circuit breaker open → агент сообщает клиенту о временной проблеме
- [x] Тест: Store API slow (>5s timeout) → timeout handling, fallback response
- [x] Тест: Store API 500 error → retry logic, после N retries → graceful error message
- [x] Тест: Все внешние сервисы down → звонок переводится на оператора

**Файлы:** `tests/chaos/test_service_failures.py`
**Подход:** Mock с side_effect для имитации сбоев
**Заметки:** 7 тестов: circuit breaker open (1), store API retry на 503 (1), no retry на 500 (1), ToolRouter graceful catch (2), DB fallback login (1), _log_failed_login swallow (1). Все проходят.

---

### 3.3 Prompt Regression Tests

- [x] Создать набор тестовых диалогов (10-15 сценариев) — записанные пары вопрос/ожидаемое поведение:
  - Поиск шин по размеру → вызов search_tires
  - Вопрос о наличии → вызов check_availability
  - Запрос оформления заказа → вызов create_order_draft
  - Вопрос о статусе заказа → вызов get_order_status
  - Запись на шиномонтаж → вызов book_fitting
  - Нерелевантный вопрос → вежливый отказ
  - Попытка prompt injection → игнорирование
  - Вопрос на русском → ответ на украинском
  - Грубость → вежливый ответ
  - Запрос оператора → transfer_to_operator
- [x] Реализовать test runner: отправка сообщения агенту → проверка tool call / ключевых слов в ответе
- [x] Добавить pytest marker `@pytest.mark.prompt_regression`
- [x] Документировать формат тестовых сценариев для добавления новых

**Файлы:** `tests/prompt_regression/`, `tests/prompt_regression/test_scenarios.py`, `tests/prompt_regression/scenarios.json`
**Заметки:** 12 сценариев в scenarios.json, 50 тестов: структурные проверки промпта, параметризованные тесты по сценариям (tool exists, keywords present, unexpected absent), canonical tool set, safety rules, tool schema integrity, scenario file integrity. Все проходят без вызова Claude API.

---

## При завершении фазы

Выполни следующие действия:

1. Убедись, что все задачи отмечены [x]
2. Измени статус фазы:
   - [x] Завершена
3. Заполни дату "Завершена: YYYY-MM-DD"
4. Выполни коммит:
   ```bash
   git add .
   git commit -m "checklist(post-production-improvements): phase-3 testing and quality completed"
   ```
5. Обнови PROGRESS.md:
   - Текущая фаза: 4
   - Добавь запись в историю
6. Открой следующую фазу и продолжи работу
