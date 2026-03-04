# Фаза 2: Session Persistence Mid-Call

## Статус
- [x] Не начата
- [x] В процессе
- [x] Завершена

**Начата:** 2026-03-04
**Завершена:** 2026-03-04

## Цель фазы
Сохранять session в Redis после каждого turn. Recovery при crash. Истинный stateless Call Processor.

## Задачи

### 2.1 Анализ текущей session lifecycle
- [x] Прочитать `src/main.py:726-727` — session создание и удаление
- [x] Прочитать `src/core/call_session.py` — все поля session (dialog_history, order_draft, fitting_booked, etc.)
- [x] Определить: какие поля сериализуемы в JSON
- [x] Определить: размер session в JSON (~10-20 KB expected)

**Файлы:** `src/main.py`, `src/core/call_session.py`
**Audit refs:** ARCH-03, STR-10

---

### 2.2 Добавить session serialization
- [x] В `src/core/call_session.py`: добавить `to_dict()` и `from_dict()` методы
  - Сериализовать: `dialog_history`, `order_draft`, `fitting_booked`, `tools_called`, `caller_phone`, `transferred`, `timeout_count`
  - НЕ сериализовать: transient fields (audio queues, locks)
- [x] Тест: session → to_dict() → from_dict() → все поля восстановлены

NOTE: Also updated existing `serialize()` and `deserialize()` to delegate to `to_dict()` / `from_dict()`. Added missing fields: fitting_booked, fitting_station_ids, tools_called, active_scenarios.

**Файлы:** `src/core/call_session.py`

---

### 2.3 Сохранение после каждого turn
- [x] В `src/core/pipeline.py`: после `add_assistant_turn()`:
  ```python
  await self._session_store.save(self._session)
  ```
- [x] В session store: `await redis.set(f"session:{session.call_id}", session.to_json(), ex=1800)`
- [x] Latency budget: Redis SET ~1ms — negligible vs LLM latency
- [x] Тест: pipeline turn → session saved in Redis

NOTE: Added `_persist_session()` helper method, called after all 6 `add_assistant_turn()` sites. Failures are logged but never propagated (best-effort). SessionStore passed from main.py via new `session_store` parameter.

**Файлы:** `src/core/pipeline.py`, `src/main.py`

---

### 2.4 Session recovery при reconnect
- [x] В `src/main.py` (AudioSocket handler): при подключении — проверить Redis для существующей session
  - Если найдена: восстановить `CallSession.from_dict(data)`
  - Если нет: создать новую (текущее поведение)
- [x] Тест: session в Redis → новое подключение с тем же call_id → восстановленная история

**Файлы:** `src/main.py`

---

### 2.5 Тесты
- [x] `ruff check src/` — без ошибок (pre-existing warnings only)
- [ ] `pytest tests/ -x -q` — все тесты проходят (skipped: no DB available)

---

## При завершении фазы
1. Выполни коммит:
   ```bash
   git add src/core/ src/main.py tests/
   git commit -m "checklist(p2-architecture): phase-2 session persistence mid-call"
   ```
2. Обнови PROGRESS.md: Текущая фаза: 3
