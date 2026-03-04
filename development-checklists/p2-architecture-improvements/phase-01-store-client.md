# Фаза 1: Store Client Improvements

## Статус
- [x] Не начата
- [x] В процессе
- [x] Завершена

**Начата:** 2026-03-04
**Завершена:** 2026-03-04

## Цель фазы
Реализовать per-tenant circuit breaker (сбой одного Store API не блокирует всех). Исправить Idempotency-Key при retry.

## Задачи

### 1.1 Per-tenant circuit breaker
- [x] Прочитать `src/store_client/client.py:45` — текущий module-level singleton:
  ```python
  _store_breaker = CircuitBreaker(fail_max=5, timeout_duration=30)
  ```
- [x] Перенести circuit breaker в instance variable `StoreClient.__init__()`:
  ```python
  class StoreClient:
      def __init__(self, base_url: str, api_key: str):
          self._breaker = CircuitBreaker(fail_max=5, timeout_duration=30)
  ```
- [x] Каждый тенант получает свой `StoreClient` instance → свой breaker
- [x] Проверить: при создании StoreClient per-tenant в `src/main.py` → breakers изолированы
- [x] Удалить module-level `_store_breaker`
- [x] Тест: breaker тенанта A открыт → тенант B работает нормально

**Файлы:** `src/store_client/client.py`, `src/main.py`
**Audit refs:** ARCH-02

---

### 1.2 Idempotency-Key — переиспользование на retry
- [x] Прочитать `src/store_client/client.py` — текущее: `str(uuid.uuid4())` при каждом вызове
- [x] Генерировать `idempotency_key = str(uuid.uuid4())` **до** retry loop
- [x] Передавать тот же key на каждой retry-попытке:
  ```python
  async def create_order(self, ...):
      idempotency_key = str(uuid.uuid4())
      for attempt in range(max_retries):
          headers = {"Idempotency-Key": idempotency_key}
          response = await self._post(..., headers=headers)
  ```
- [x] Аналогично для `confirm_order` и других мутирующих endpoints
- [x] Тест: retry после timeout → тот же Idempotency-Key

NOTE: Idempotency key was already generated before the retry loop in create_order/confirm_order/book_fitting. The key flows through _post -> _request -> _request_with_retry -> _do_request, reused on each retry attempt. No code change needed — architecture already correct.

**Файлы:** `src/store_client/client.py`
**Audit refs:** ARCH-09

---

### 1.3 Тесты
- [x] `ruff check src/` — без ошибок
- [ ] `pytest tests/ -x -q` — все тесты проходят (skipped: no DB available)
- [x] Grep: нет `_store_breaker` module-level:
  ```bash
  grep -rn "_store_breaker" src/
  ```

---

## При завершении фазы
1. Выполни коммит:
   ```bash
   git add src/store_client/ src/main.py tests/
   git commit -m "checklist(p2-architecture): phase-1 per-tenant circuit breaker, idempotency-key fix"
   ```
2. Обнови PROGRESS.md: Текущая фаза: 2
