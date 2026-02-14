# Фаза 2: Tools для заказов

## Статус
- [x] Не начата
- [x] В процессе
- [x] Завершена

**Начата:** 2026-02-14
**Завершена:** 2026-02-14

## Цель фазы

Добавить 4 новых tool для LLM-агента: `get_order_status`, `create_order_draft`, `update_order_delivery`, `confirm_order`. Определить schema, валидацию параметров и маршрутизацию.

## Задачи

### 2.0 ОБЯЗАТЕЛЬНО: Анализ и планирование

#### A. Анализ существующего кода
- [x] Изучить существующие tools в `src/agent/tools.py` (search_tires, check_availability, transfer_to_operator)
- [x] Изучить паттерн Tool Router из phase-1 agent
- [x] Проверить канонический список tools в `doc/development/00-overview.md`

#### B. Анализ зависимостей
- [x] Все 4 новых tool из канонического списка: `get_order_status`, `create_order_draft`, `update_order_delivery`, `confirm_order`
- [x] Валидация: quantity > 0 и < 100, customer_phone формат +380...
- [x] Store API endpoints для каждого tool

**Новые абстракции:** Нет
**Новые env variables:** Нет
**Новые tools:** `get_order_status`, `create_order_draft`, `update_order_delivery`, `confirm_order`
**Миграции БД:** `002_add_orders.py` — таблицы orders, order_items

#### C. Проверка архитектуры
- [x] Tool names строго из канонического списка
- [x] Idempotency-Key для `create_order_draft` и `confirm_order`
- [x] Безопасность: подтверждение перед confirm_order

**Референс-модуль:** `src/agent/tools.py` (существующие tools)

**Цель:** Определить schema всех 4 tools по спецификации phase-2.

**Заметки для переиспользования:** Паттерн из существующих tools в `src/agent/tools.py`. `ALL_TOOLS = MVP_TOOLS + ORDER_TOOLS`.

---

### 2.1 Tool: get_order_status

- [x] Добавить schema в `src/agent/tools.py`
- [x] Параметры: `phone` (string), `order_id` (string) — хотя бы один обязателен
- [x] Описание: "Получить статус заказа по телефону клиента или номеру заказа"
- [x] Маршрутизация: phone → `GET /orders/search?phone=...`, order_id → `GET /orders/{id}`
- [x] Обработка: несколько заказов → вернуть список, один → вернуть детали

**Файлы:** `src/agent/tools.py`, `src/agent/agent.py`
**Заметки:** Schema с phone и order_id, описание на украинском.

---

### 2.2 Tool: create_order_draft

- [x] Добавить schema в `src/agent/tools.py`
- [x] Параметры: `items` (array: product_id, quantity), `customer_phone` (string, required)
- [x] Валидация: quantity > 0 и < 100, items не пустой
- [x] Маршрутизация: → `POST /orders` с Idempotency-Key
- [x] Генерация Idempotency-Key (UUID) для предотвращения дубликатов

**Файлы:** `src/agent/tools.py`, `src/agent/agent.py`

**Важно:** Имя tool — `create_order_draft` (НЕ `create_order`)

**Заметки:** items — array of {product_id, quantity}, required: ["items", "customer_phone"]

---

### 2.3 Tool: update_order_delivery

- [x] Добавить schema в `src/agent/tools.py`
- [x] Параметры: `order_id` (required), `delivery_type` (enum: delivery, pickup), `city`, `address`, `pickup_point_id`
- [x] Валидация: для delivery — city и address обязательны, для pickup — pickup_point_id
- [x] Маршрутизация: → `PATCH /orders/{id}/delivery`

**Файлы:** `src/agent/tools.py`, `src/agent/agent.py`
**Заметки:** required: ["order_id", "delivery_type"]

---

### 2.4 Tool: confirm_order

- [x] Добавить schema в `src/agent/tools.py`
- [x] Параметры: `order_id` (required), `payment_method` (enum: cod, online, card_on_delivery), `customer_name` (string)
- [x] Маршрутизация: → `POST /orders/{id}/confirm` с Idempotency-Key
- [x] **Безопасность:** перед вызовом агент ОБЯЗАН озвучить состав, сумму и получить подтверждение

**Файлы:** `src/agent/tools.py`, `src/agent/agent.py`
**Заметки:** Описание содержит "ОБОВ'ЯЗКОВО: перед викликом оголоси клієнту склад, суму та отримай підтвердження 'так'."

---

### 2.5 Миграция БД для заказов

- [x] Создать `migrations/versions/002_add_orders.py`
- [x] Таблица `orders`: id, order_number, customer_id, status, items (JSONB), subtotal, delivery_cost, total, delivery_type, delivery_address, payment_method, source, source_call_id
- [x] Таблица `order_items`: id, order_id, product_id, product_name, quantity, price_per_unit, total
- [x] Индексы: orders по customer_id, order_number
- [x] Связь calls.order_id → orders.id

**Файлы:** `migrations/versions/002_add_orders.py`
**Заметки:** FK: customer_id → customers.id, order_items.order_id → orders.id ON DELETE CASCADE. Индексы: customer_id, order_number (unique), idempotency_key.

---

## При завершении фазы

1. Убедись, что все задачи отмечены [x]
2. Измени статус фазы: [x] Завершена
3. Заполни дату "Завершена: YYYY-MM-DD"
4. Выполни коммит
5. Обнови PROGRESS.md
6. Открой следующую фазу: `phase-03-store-api-orders.md`
