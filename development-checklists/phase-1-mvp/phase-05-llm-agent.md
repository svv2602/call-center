# Фаза 5: LLM агент

## Статус
- [x] Не начата
- [x] В процессе
- [x] Завершена

**Начата:** 2026-02-13
**Завершена:** 2026-02-13

## Цель фазы

Реализовать LLM-агента на базе Claude API с tool calling. Агент ведёт диалог на украинском языке, подбирает шины, проверяет наличие, переключает на оператора.

## Задачи

### 5.0 ОБЯЗАТЕЛЬНО: Анализ и планирование

#### A. Анализ существующего кода
- [x] Проверить наличие `src/agent/agent.py`, `src/agent/tools.py`, `src/agent/prompts.py`
- [x] Изучить Anthropic Python SDK (tool calling)
- [x] Проверить канонический список tools в `doc/development/00-overview.md`

#### B. Анализ зависимостей
- [x] Нужны ли новые env variables? — `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL` (уже в config)
- [x] Канонические tools для MVP: `search_tires`, `check_availability`, `transfer_to_operator`
- [x] Метрики: `llm_latency_ms`, `tool_calls_count`, `tokens_used`

**Новые абстракции:** Нет (прямой вызов Claude API через SDK)
**Новые env variables:** Нет (уже определены в phase-01)
**Новые tools:** `search_tires`, `check_availability`, `transfer_to_operator`
**Миграции БД:** Нет

#### C. Проверка архитектуры
- [x] Tool calling через Claude API native tool_use
- [x] Бюджет задержки LLM: ≤ 1000ms TTFT (из NFR)
- [x] Системный промпт на украинском
- [x] Graceful degradation при сбое Claude API → переключение на оператора

**Заметки для переиспользования:** `ToolRouter` — регистрация обработчиков через `register(name, handler)`. `LLMAgent.process_message()` — основной метод, возвращает `(text, history)`.

---

### 5.1 Определение Tools (schema)

- [x] Создать `src/agent/tools.py` с определениями всех MVP tools
- [x] `search_tires` — поиск шин (vehicle_make, vehicle_model, vehicle_year, width, profile, diameter, season, brand)
- [x] `check_availability` — проверка наличия (product_id, query)
- [x] `transfer_to_operator` — переключение (reason: enum, summary: string)
- [x] Валидация параметров перед вызовом
- [x] Формат tools совместим с Anthropic API tool_use

**Файлы:** `src/agent/tools.py`
**Заметки:** `MVP_TOOLS` — список dict в формате Claude API. Описания на украинском.

---

### 5.2 Системный промпт

- [x] Создать `src/agent/prompts.py` с системным промптом на украинском
- [x] Описание роли: голосовий асистент інтернет-магазину шин
- [x] Правила: відповідай коротко (2-3 речення), не вигадуй, ціни у гривнях
- [x] Мультиязычность: розумій українську та російську, відповідай українською
- [x] Уведомление об автоматизированной обработке (юридическое требование)
- [x] Версионирование промптов (подготовка к A/B тестам в фазе 4)

**Файлы:** `src/agent/prompts.py`
**Заметки:** `PROMPT_VERSION = "v1.0-mvp"`. Имя агента: Олена. Константы для всех стандартных фраз (GREETING, FAREWELL, TRANSFER, ERROR, WAIT, SILENCE_PROMPT).

---

### 5.3 LLM Agent (основная логика)

- [x] Создать `src/agent/agent.py` — основной класс агента
- [x] Формирование messages: system + conversation history
- [x] Вызов Claude API с tools
- [x] Обработка ответов: text response или tool_use
- [x] При tool_use: выполнить tool → отправить result → получить следующий ответ
- [x] Ограничение цепочки tool calls (max 5 за один turn)
- [x] Обработка ошибок: retry при transient errors, fallback → оператор

**Файлы:** `src/agent/agent.py`
**Заметки:** `LLMAgent.process_message()` — цикл: Claude → tool_use → execute → tool_result → Claude. `max_tokens=300` для коротких ответов. `MAX_HISTORY_MESSAGES=40`.

---

### 5.4 Tool Router (диспетчеризация)

- [x] Реализовать маршрутизацию tool_use → конкретная функция
- [x] `search_tires` → `store_client.search_tires()`
- [x] `check_availability` → `store_client.check_availability()`
- [x] `transfer_to_operator` → ARI transfer (Asterisk)
- [x] Валидация параметров перед вызовом
- [x] Логирование каждого tool call (name, args, result, duration)

**Файлы:** `src/agent/agent.py`
**Заметки:** `ToolRouter` с dict-based dispatch. Логирует `Tool {name} executed in {duration}ms`. При ошибке возвращает `{"error": str(exc)}`.

---

### 5.5 Обработка transfer_to_operator

- [x] Реализовать переключение на оператора через Asterisk ARI
- [x] HTTP-вызов ARI для перевода канала в очередь операторов
- [x] Перед переключением: агент сообщает "Зараз з'єдную вас з оператором"
- [x] Передача summary (краткое описание) оператору

**Файлы:** `src/agent/agent.py`
**Заметки:** ARI transfer будет зарегистрирован как handler в ToolRouter в main.py (phase-06 pipeline). `TRANSFER_TEXT` из prompts.py.

---

### 5.6 Управление контекстом

- [x] Максимальный размер history — ограничение по токенам
- [x] Стратегия сжатия: удаление старых turns при приближении к лимиту
- [x] Сохранение ключевой информации (CallerID, выбранные товары) во всех turns

**Файлы:** `src/agent/agent.py`
**Заметки:** Простая стратегия: при `len(history) > 40` сохраняем первое сообщение + последние 39. Первое сообщение содержит контекст (CallerID и т.п.).

---

## При завершении фазы

1. Убедись, что все задачи отмечены [x]
2. Измени статус фазы: [x] Завершена
3. Заполни дату "Завершена: YYYY-MM-DD"
4. Выполни коммит:
   ```bash
   git add .
   git commit -m "checklist(phase-1-mvp): phase-5 LLM agent completed"
   ```
5. Обнови PROGRESS.md
6. Открой следующую фазу: `phase-06-pipeline.md`
