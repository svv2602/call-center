# Фаза 5: LLM агент

## Статус
- [ ] Не начата
- [ ] В процессе
- [ ] Завершена

**Начата:** -
**Завершена:** -

## Цель фазы

Реализовать LLM-агента на базе Claude API с tool calling. Агент ведёт диалог на украинском языке, подбирает шины, проверяет наличие, переключает на оператора.

## Задачи

### 5.0 ОБЯЗАТЕЛЬНО: Анализ и планирование

#### A. Анализ существующего кода
- [ ] Проверить наличие `src/agent/agent.py`, `src/agent/tools.py`, `src/agent/prompts.py`
- [ ] Изучить Anthropic Python SDK (tool calling)
- [ ] Проверить канонический список tools в `doc/development/00-overview.md`

**Команды для поиска:**
```bash
ls src/agent/
grep -rn "anthropic\|Claude\|tool_use" src/
grep -rn "search_tires\|check_availability\|transfer_to_operator" src/
```

#### B. Анализ зависимостей
- [ ] Нужны ли новые env variables? — `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL` (уже в config)
- [ ] Канонические tools для MVP: `search_tires`, `check_availability`, `transfer_to_operator`
- [ ] Метрики: `llm_latency_ms`, `tool_calls_count`, `tokens_used`

**Новые абстракции:** Нет (прямой вызов Claude API через SDK)
**Новые env variables:** Нет (уже определены в phase-01)
**Новые tools:** `search_tires`, `check_availability`, `transfer_to_operator`
**Миграции БД:** Нет

#### C. Проверка архитектуры
- [ ] Tool calling через Claude API native tool_use
- [ ] Бюджет задержки LLM: ≤ 1000ms TTFT (из NFR)
- [ ] Системный промпт на украинском
- [ ] Graceful degradation при сбое Claude API → переключение на оператора

**Референс-модуль:** `doc/development/phase-1-mvp.md` — секция 1.4

**Цель:** Определить структуру агента, tools schema и системный промпт.

**Заметки для переиспользования:** -

---

### 5.1 Определение Tools (schema)

- [ ] Создать `src/agent/tools.py` с определениями всех MVP tools
- [ ] `search_tires` — поиск шин (vehicle_make, vehicle_model, vehicle_year, width, profile, diameter, season, brand)
- [ ] `check_availability` — проверка наличия (product_id, query)
- [ ] `transfer_to_operator` — переключение (reason: enum, summary: string)
- [ ] Валидация параметров: price > 0, quantity > 0 и < 100
- [ ] Формат tools совместим с Anthropic API tool_use

**Файлы:** `src/agent/tools.py`

**Tools schema (канонические имена):**
```python
tools = [
    {
        "name": "search_tires",
        "description": "Поиск шин в каталоге магазина",
        "input_schema": {
            "type": "object",
            "properties": {
                "vehicle_make": {"type": "string"},
                "vehicle_model": {"type": "string"},
                "vehicle_year": {"type": "integer"},
                "width": {"type": "integer"},
                "profile": {"type": "integer"},
                "diameter": {"type": "integer"},
                "season": {"type": "string", "enum": ["summer", "winter", "all_season"]},
                "brand": {"type": "string"},
            },
        },
    },
    {
        "name": "check_availability",
        "description": "Проверка наличия конкретного товара",
        "input_schema": {
            "type": "object",
            "properties": {
                "product_id": {"type": "string"},
                "query": {"type": "string"},
            },
        },
    },
    {
        "name": "transfer_to_operator",
        "description": "Переключить клиента на живого оператора",
        "input_schema": {
            "type": "object",
            "properties": {
                "reason": {"type": "string", "enum": ["customer_request", "cannot_help", "complex_question", "negative_emotion"]},
                "summary": {"type": "string"},
            },
            "required": ["reason", "summary"],
        },
    },
]
```

**Заметки:** Имена tools строго из канонического списка: `doc/development/00-overview.md`

---

### 5.2 Системный промпт

- [ ] Создать `src/agent/prompts.py` с системным промптом на украинском
- [ ] Описание роли: голосовий асистент інтернет-магазину шин
- [ ] Правила: відповідай коротко (2-3 речення), не вигадуй, ціни у гривнях
- [ ] Мультиязычность: розумій українську та російську, відповідай українською
- [ ] Уведомление об автоматизированной обработке (юридическое требование)
- [ ] Версионирование промптов (подготовка к A/B тестам в фазе 4)

**Файлы:** `src/agent/prompts.py`

**Ключевые элементы промпта:**
```
Ти — голосовий асистент інтернет-магазину шин.
Ти спілкуєшся українською мовою, ввічливо та професійно.
Ти ЗАВЖДИ відповідаєш українською, навіть якщо клієнт говорить російською.

Можливості: підбір шин, перевірка наявності, переключення на оператора.

Правила:
- Відповідай коротко і чітко (телефонна розмова, не чат)
- Не вигадуй інформацію — тільки дані з інструментів
- Називай ціни у гривнях
- Максимум 2-3 речення у відповіді
```

**Заметки:** -

---

### 5.3 LLM Agent (основная логика)

- [ ] Создать `src/agent/agent.py` — основной класс агента
- [ ] Формирование messages: system + conversation history
- [ ] Вызов Claude API с tools
- [ ] Обработка ответов: text response или tool_use
- [ ] При tool_use: выполнить tool → отправить result → получить следующий ответ
- [ ] Ограничение цепочки tool calls (max 5 за один turn)
- [ ] Обработка ошибок: retry при transient errors, fallback → оператор

**Файлы:** `src/agent/agent.py`
**Заметки:** -

---

### 5.4 Tool Router (диспетчеризация)

- [ ] Реализовать маршрутизацию tool_use → конкретная функция
- [ ] `search_tires` → `store_client.search_tires()`
- [ ] `check_availability` → `store_client.check_availability()`
- [ ] `transfer_to_operator` → ARI transfer (Asterisk)
- [ ] Валидация параметров перед вызовом
- [ ] Логирование каждого tool call (name, args, result, duration)

**Файлы:** `src/agent/agent.py`
**Заметки:** Результаты tool calls также логируются в `call_tool_calls` таблицу

---

### 5.5 Обработка transfer_to_operator

- [ ] Реализовать переключение на оператора через Asterisk ARI
- [ ] HTTP-вызов ARI для перевода канала в очередь операторов
- [ ] Перед переключением: агент сообщает "Зараз з'єдную вас з оператором"
- [ ] Передача summary (краткое описание) оператору

**Файлы:** `src/agent/agent.py`, (возможно) `src/core/asterisk_ari.py`

**ARI вызов:**
```python
async def transfer_to_operator(channel_uuid: str, reason: str, summary: str):
    async with aiohttp.ClientSession() as session:
        await session.post(
            f"{ARI_URL}/channels/{channel_uuid}/redirect",
            json={"endpoint": "Local/operator@transfer-to-operator"},
            auth=aiohttp.BasicAuth(ARI_USER, ARI_PASSWORD),
        )
```

**Заметки:** -

---

### 5.6 Управление контекстом

- [ ] Максимальный размер history — ограничение по токенам
- [ ] Стратегия сжатия: удаление старых turns при приближении к лимиту
- [ ] Сохранение ключевой информации (CallerID, выбранные товары) во всех turns

**Файлы:** `src/agent/agent.py`
**Заметки:** -

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
