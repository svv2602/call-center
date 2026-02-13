# Фаза 2: Tools для заказов

## Статус
- [ ] Не начата
- [ ] В процессе
- [ ] Завершена

**Начата:** -
**Завершена:** -

## Цель фазы

Добавить 4 новых tool для LLM-агента: `get_order_status`, `create_order_draft`, `update_order_delivery`, `confirm_order`. Определить schema, валидацию параметров и маршрутизацию.

## Задачи

### 2.0 ОБЯЗАТЕЛЬНО: Анализ и планирование

#### A. Анализ существующего кода
- [ ] Изучить существующие tools в `src/agent/tools.py` (search_tires, check_availability, transfer_to_operator)
- [ ] Изучить паттерн Tool Router из phase-1 agent
- [ ] Проверить канонический список tools в `doc/development/00-overview.md`

**Команды для поиска:**
```bash
grep -rn "\"name\":" src/agent/tools.py
grep -rn "tool_use\|tool_call\|tool_result" src/agent/
```

#### B. Анализ зависимостей
- [ ] Все 4 новых tool из канонического списка: `get_order_status`, `create_order_draft`, `update_order_delivery`, `confirm_order`
- [ ] Валидация: quantity > 0 и < 100, customer_phone формат +380...
- [ ] Store API endpoints для каждого tool

**Новые абстракции:** Нет
**Новые env variables:** Нет
**Новые tools:** `get_order_status`, `create_order_draft`, `update_order_delivery`, `confirm_order`
**Миграции БД:** `002_add_orders.py` — таблицы orders, order_items

#### C. Проверка архитектуры
- [ ] Tool names строго из канонического списка
- [ ] Idempotency-Key для `create_order_draft` и `confirm_order`
- [ ] Безопасность: подтверждение перед confirm_order

**Референс-модуль:** `src/agent/tools.py` (существующие tools)

**Цель:** Определить schema всех 4 tools по спецификации phase-2.

**Заметки для переиспользования:** Паттерн из существующих tools в `src/agent/tools.py`

---

### 2.1 Tool: get_order_status

- [ ] Добавить schema в `src/agent/tools.py`
- [ ] Параметры: `phone` (string), `order_id` (string) — хотя бы один обязателен
- [ ] Описание: "Получить статус заказа по телефону клиента или номеру заказа"
- [ ] Маршрутизация: phone → `GET /orders/search?phone=...`, order_id → `GET /orders/{id}`
- [ ] Обработка: несколько заказов → вернуть список, один → вернуть детали

**Файлы:** `src/agent/tools.py`, `src/agent/agent.py`
**Заметки:** Если по CallerID найдено несколько заказов — перечислить последние

---

### 2.2 Tool: create_order_draft

- [ ] Добавить schema в `src/agent/tools.py`
- [ ] Параметры: `items` (array: product_id, quantity), `customer_phone` (string, required)
- [ ] Валидация: quantity > 0 и < 100, items не пустой
- [ ] Маршрутизация: → `POST /orders` с Idempotency-Key
- [ ] Генерация Idempotency-Key (UUID) для предотвращения дубликатов

**Файлы:** `src/agent/tools.py`, `src/agent/agent.py`

**Важно:** Имя tool — `create_order_draft` (НЕ `create_order`)

**Заметки:** -

---

### 2.3 Tool: update_order_delivery

- [ ] Добавить schema в `src/agent/tools.py`
- [ ] Параметры: `order_id` (required), `delivery_type` (enum: delivery, pickup), `city`, `address`, `pickup_point_id`
- [ ] Валидация: для delivery — city и address обязательны, для pickup — pickup_point_id
- [ ] Маршрутизация: → `PATCH /orders/{id}/delivery`

**Файлы:** `src/agent/tools.py`, `src/agent/agent.py`
**Заметки:** -

---

### 2.4 Tool: confirm_order

- [ ] Добавить schema в `src/agent/tools.py`
- [ ] Параметры: `order_id` (required), `payment_method` (enum: cod, online, card_on_delivery), `customer_name` (string)
- [ ] Маршрутизация: → `POST /orders/{id}/confirm` с Idempotency-Key
- [ ] **Безопасность:** перед вызовом агент ОБЯЗАН озвучить состав, сумму и получить подтверждение

**Файлы:** `src/agent/tools.py`, `src/agent/agent.py`
**Заметки:** -

---

### 2.5 Миграция БД для заказов

- [ ] Создать `migrations/versions/002_add_orders.py`
- [ ] Таблица `orders`: id, order_number, customer_id, status, items (JSONB), subtotal, delivery_cost, total, delivery_type, delivery_address, payment_method, source, source_call_id
- [ ] Таблица `order_items`: id, order_id, product_id, product_name, quantity, price_per_unit, total
- [ ] Индексы: orders по customer_id, order_number
- [ ] Связь calls.order_id → orders.id

**Файлы:** `migrations/versions/002_add_orders.py`
**Заметки:** Схема из `doc/technical/data-model.md`

---

## При завершении фазы

1. Убедись, что все задачи отмечены [x]
2. Измени статус фазы: [x] Завершена
3. Заполни дату "Завершена: YYYY-MM-DD"
4. Выполни коммит:
   ```bash
   git add .
   git commit -m "checklist(phase-2-orders): phase-2 order tools completed"
   ```
5. Обнови PROGRESS.md
6. Открой следующую фазу: `phase-03-store-api-orders.md`
