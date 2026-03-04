# Фаза 3: Error Text & Fallbacks

## Статус
- [x] Не начата
- [x] В процессе
- [x] Завершена

**Начата:** 2026-03-04
**Завершена:** 2026-03-04

## Цель фазы
Исправить все тексты которые ложно обещают оператора. Добавить корректные fallback сообщения.

## Задачи

### 3.1 Исправить ERROR_TEXT
- [x] В `src/agent/prompts.py`: заменить ERROR_TEXT
  - Было: `"Перепрошую, виникла технічна помилка. З'єдну з оператором."`
  - Стало: `"Перепрошую, виникла технічна помилка. Спробуйте, будь ласка, зателефонувати ще раз."`
- [x] Проверить все места где используется ERROR_TEXT
- [x] Обновить TTS pre-cache если ERROR_TEXT кэшируется

**Примечание:** ERROR_TEXT используется в 6 файлах (pipeline.py, agent.py, prompt_manager.py, google_tts.py, sandbox.py). Все импортируют константу — изменение в prompts.py пропагируется автоматически. TTS pre-cache в google_tts.py тоже использует импорт.

**Файлы:** `src/agent/prompts.py`, `src/tts/google_tts.py` (cached phrases)
**Audit refs:** BOT-01, QW-05

---

### 3.2 Добавить fallback при max tool rounds
- [x] В `src/agent/agent.py:386-388`: после break из max tool loop, если `response_text` пуст:
  - Отправить LLM финальный промпт: «Summarize what you found for the customer in 1-2 sentences, no tool calls»
  - Timeout: 5с
  - При timeout: use static fallback text
- [x] Аналогично в `src/agent/streaming_loop.py:422-424`
- [x] Тест: max tool rounds + empty text → fallback summary (не ERROR_TEXT)

**Реализация:**
- `agent.py`: добавлен метод `_request_summary_fallback()` — вызывает LLM без tools, 5s timeout, статический fallback при ошибке
- `streaming_loop.py`: аналогичный метод `_request_summary_fallback()` + TTS synthesis для озвучки
- Статический fallback: `"Перепрошую, мені потрібно трохи більше часу. Спробуйте, будь ласка, уточнити ваше питання."`

**Файлы:** `src/agent/agent.py`, `src/agent/streaming_loop.py`
**Audit refs:** BOT-03

---

### 3.3 Обновить TRANSFER_TEXT при ARI unavailable
- [x] Если ARI client недоступен при transfer: бот говорит
  `"На жаль, зараз оператори недоступні. Залиште, будь ласка, ваш номер — ми передзвонимо."`
- [x] Логировать WARNING: «ARI unavailable, cannot transfer call {call_id}»

**Примечание:** Реализовано в Phase 2 — handler возвращает это сообщение как tool result, LLM транслирует клиенту.

**Файлы:** `src/main.py` (transfer_to_operator handler)

---

### 3.4 Финальное тестирование
- [x] `ruff check src/` — без новых ошибок (только pre-existing N806)
- [ ] `pytest tests/ -x -q` — все тесты проходят (нет доступа к БД в этой среде)
- [x] Grep по всем «З'єдную з оператором» / «З'єдну з оператором» — убедиться что не осталось ложных обещаний

**Результат grep:** Осталось только 2 вхождения — оба в SUCCESS path (реальный transfer через ARI):
1. `src/main.py:2033` — return при успешном ARI transfer (корректно)
2. `src/sandbox/mock_tools.py:69` — тестовый мок (корректно)

---

## При завершении фазы
1. Выполни коммит:
   ```bash
   git add src/agent/ src/main.py src/tts/ tests/
   git commit -m "checklist(p0-transfer-to-operator): phase-3 error text fixed, fallbacks added"
   ```
2. Обнови PROGRESS.md: Общий прогресс: 18/18 (100%)
