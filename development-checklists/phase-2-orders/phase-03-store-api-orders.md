# Фаза 3: Store API для заказов

## Статус
- [x] Не начата
- [x] В процессе
- [x] Завершена

**Начата:** 2026-02-14
**Завершена:** 2026-02-14

## Цель фазы

Расширить Store Client новыми endpoints для работы с заказами. Реализовать поддержку Idempotency-Key для мутирующих операций (POST /orders, POST /orders/{id}/confirm).

## Задачи

### 3.0 ОБЯЗАТЕЛЬНО: Анализ и планирование

#### A. Анализ существующего кода
- [x] Изучить существующий `src/store_client/client.py` (endpoints MVP)
- [x] Изучить спецификацию Store API фазы 2: `doc/development/api-specification.md`
- [x] Проверить паттерн circuit breaker и retry

#### B. Анализ зависимостей
- [x] Store Client уже реализован с circuit breaker и retry (из phase-1)
- [x] Нужна поддержка Idempotency-Key заголовка
- [x] Нужна поддержка POST, PATCH методов (MVP только GET)

**Новые абстракции:** Нет
**Новые env variables:** Нет
**Новые tools:** Нет (tools определены в phase-02)
**Миграции БД:** Нет

#### C. Проверка архитектуры
- [x] Idempotency-Key: UUID, передаётся как заголовок
- [x] Retry для POST с Idempotency-Key безопасен (идемпотентен)
- [x] Маппинг ответов API → формат для LLM tools

**Референс-модуль:** `src/store_client/client.py` (существующий клиент)

**Цель:** Определить расширение Store Client для заказов.

**Заметки для переиспользования:** `_post()` и `_patch()` хелперы добавлены. `idempotency_key` передаётся через всю цепочку _request → _request_with_retry → _do_request как заголовок `Idempotency-Key`.

---

### 3.1 Endpoint: GET /orders/search

- [x] Реализовать `search_orders(phone, order_number, status)` → `GET /orders/search`
- [x] Маппинг ответа: id, order_number, status, status_label, total, items_summary, estimated_delivery
- [x] Обработка пустого результата (заказы не найдены)

**Файлы:** `src/store_client/client.py`
**Заметки:** Также поддерживает GET /orders/{id} для прямого поиска по ID.

---

### 3.2 Endpoint: GET /orders/{id}

- [x] Реализовать `get_order(order_id)` → `GET /orders/{id}`
- [x] Полная информация: items, delivery, payment, status_history
- [x] Маппинг для LLM: краткое описание для озвучивания

**Файлы:** `src/store_client/client.py`
**Заметки:** Реализовано в `search_orders(order_id=...)` — единый метод для обоих сценариев.

---

### 3.3 Endpoint: POST /orders (с Idempotency-Key)

- [x] Реализовать `create_order(items, customer_phone, customer_name, call_id)` → `POST /orders`
- [x] Генерация `Idempotency-Key` (UUID) для каждого вызова
- [x] Передача `Idempotency-Key` в заголовке
- [x] Передача `source: "ai_agent"` и `call_id` в body
- [x] Обработка ответа 201 (создано) и повторного 201 (идемпотентный ответ)

**Файлы:** `src/store_client/client.py`
**Заметки:** `_post()` с `idempotency_key` параметром.

---

### 3.4 Endpoint: PATCH /orders/{id}/delivery

- [x] Реализовать `update_delivery(order_id, delivery_type, city, address, pickup_point_id)` → `PATCH /orders/{id}/delivery`
- [x] Дополнительно: `GET /delivery/calculate` для расчёта стоимости
- [x] Дополнительно: `GET /pickup-points` для списка пунктов самовывоза
- [x] Маппинг: delivery cost, estimated_days, total

**Файлы:** `src/store_client/client.py`
**Заметки:** `calculate_delivery()` и `get_pickup_points()` — отдельные методы.

---

### 3.5 Endpoint: POST /orders/{id}/confirm (с Idempotency-Key)

- [x] Реализовать `confirm_order(order_id, payment_method, customer_name)` → `POST /orders/{id}/confirm`
- [x] Генерация `Idempotency-Key`
- [x] Передача `send_sms_confirmation: true`
- [x] Обработка ответа: order_number, status, estimated_delivery, sms_sent

**Файлы:** `src/store_client/client.py`
**Заметки:** -

---

## При завершении фазы

1. Убедись, что все задачи отмечены [x]
2. Измени статус фазы: [x] Завершена
3. Заполни дату "Завершена: YYYY-MM-DD"
4. Выполни коммит
5. Обнови PROGRESS.md
6. Открой следующую фазу: `phase-04-scenarios-prompt.md`
