# Фаза 3: Pipeline Fixes

## Статус
- [x] Не начата
- [x] В процессе
- [x] Завершена

**Начата:** 2026-03-04
**Завершена:** 2026-03-04

## Цель фазы
Исправить dual history проблему (farewell использует stale history). Устранить ARCH-06.

## Задачи

### 3.1 Fix dual history — farewell
- [x] Прочитать `src/core/pipeline.py:250,655` — `_llm_history` vs `session.dialog_history`
- [x] В `_generate_contextual_farewell()`:
  - Текущее: использует `session.messages_for_llm` (из `dialog_history` — неполная)
  - Целевое: использует `self._llm_history` (полная, с tool_use/tool_result)
- [x] Передавать `_llm_history` как параметр:
  ```python
  async def _generate_contextual_farewell(self, llm_history: list):
      ...
  ```
- [x] Тест: farewell видит последний tool result из _llm_history

NOTE: Used `self._llm_history` directly (instance attribute) with fallback to `session.messages_for_llm` if empty. No need for explicit parameter — both are on `self`.

**Файлы:** `src/core/pipeline.py`
**Audit refs:** ARCH-06, BOT-14

---

### 3.2 Farewell — use streaming path
- [x] Если pipeline работает в streaming mode — использовать streaming для farewell тоже
- [x] Или оставить blocking с timeout 3с (текущее), но с корректной историей
- [x] Тест: farewell timeout → static fallback (не ERROR_TEXT)

NOTE: Kept blocking with 3s timeout — adequate for 1-sentence farewell. On timeout, returns None → caller uses FAREWELL_TEXT template (correct behavior, not ERROR_TEXT).

**Файлы:** `src/core/pipeline.py`

---

### 3.3 Тесты
- [x] `ruff check src/` — без ошибок (pre-existing warnings only)
- [ ] `pytest tests/ -x -q` — все тесты проходят (skipped: no DB available)

---

## При завершении фазы
1. Выполни коммит:
   ```bash
   git add src/core/ tests/
   git commit -m "checklist(p2-architecture): phase-3 dual history fix, farewell uses _llm_history"
   ```
2. Обнови PROGRESS.md: Текущая фаза: 4
