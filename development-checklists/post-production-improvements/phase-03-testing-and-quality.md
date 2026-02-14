# Фаза 3: Testing & Quality

## Статус
- [ ] Не начата
- [ ] В процессе
- [ ] Завершена

**Начата:** -
**Завершена:** -

## Цель фазы
Реализовать chaos-тестирование для проверки graceful degradation и регрессионные тесты промптов для защиты качества диалогов. После этой фазы устойчивость системы к сбоям подтверждена тестами, а обновления промптов защищены от деградации.

## Задачи

### 3.0 ОБЯЗАТЕЛЬНО: Анализ и планирование

#### A. Анализ существующего кода
- [ ] Изучить `tests/` — структура тестов, conftest.py, фикстуры
- [ ] Изучить `src/store_client/client.py` — circuit breaker, retry, fallback логика
- [ ] Изучить `src/agent/prompts.py` — текущие системные промпты
- [ ] Изучить `src/agent/agent.py` — как агент обрабатывает ошибки tools
- [ ] Изучить существующие E2E тесты: `tests/e2e/`

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
- [ ] Нужны ли новые абстракции (Protocol)? — Нет
- [ ] Нужны ли новые env variables? — Нет
- [ ] Нужны ли дополнительные библиотеки? — Возможно `toxiproxy` или mock-based chaos
- [ ] Нужны ли миграции БД? — Нет

**Новые env variables:** нет
**Миграции БД:** нет

#### C. Проверка архитектуры
- [ ] Chaos tests — mock-based (без реальной инфраструктуры) или toxiproxy
- [ ] Prompt regression — записанные диалоги + assertion на качество ответов
- [ ] Оба типа тестов — в `tests/` с соответствующими markers

**Референс-модуль:** `tests/e2e/` (паттерн end-to-end тестирования)

**Цель:** Понять существующие паттерны проекта ПЕРЕД написанием кода.

**Заметки для переиспользования:** -

---

### 3.1 Chaos Testing — Redis failure

- [ ] Тест: Redis недоступен → сессии не создаются, но звонок корректно завершается (transfer to operator)
- [ ] Тест: Redis timeout → rate limiter пропускает запросы (fail-open)
- [ ] Тест: Redis недоступен → WebSocket events не публикуются (silent fail, no crash)
- [ ] Тест: Redis reconnect → восстановление работы после reconnect

**Файлы:** `tests/chaos/test_redis_failure.py`
**Подход:** Mock Redis client с `ConnectionError` / `TimeoutError`
**Заметки:** -

---

### 3.2 Chaos Testing — PostgreSQL и Store API failure

- [ ] Тест: PostgreSQL недоступен → звонки обрабатываются, логи теряются gracefully
- [ ] Тест: Store API circuit breaker open → агент сообщает клиенту о временной проблеме
- [ ] Тест: Store API slow (>5s timeout) → timeout handling, fallback response
- [ ] Тест: Store API 500 error → retry logic, после N retries → graceful error message
- [ ] Тест: Все внешние сервисы down → звонок переводится на оператора

**Файлы:** `tests/chaos/test_service_failures.py`
**Подход:** Mock с side_effect для имитации сбоев
**Заметки:** -

---

### 3.3 Prompt Regression Tests

- [ ] Создать набор тестовых диалогов (10-15 сценариев) — записанные пары вопрос/ожидаемое поведение:
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
- [ ] Реализовать test runner: отправка сообщения агенту → проверка tool call / ключевых слов в ответе
- [ ] Добавить pytest marker `@pytest.mark.prompt_regression`
- [ ] Документировать формат тестовых сценариев для добавления новых

**Файлы:** `tests/prompt_regression/`, `tests/prompt_regression/test_scenarios.py`, `tests/prompt_regression/scenarios.json`
**Заметки:** Тесты НЕ вызывают реальный Claude API — используют mock с проверкой что prompt содержит нужные инструкции, или используют реальный API с `@pytest.mark.slow`

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
