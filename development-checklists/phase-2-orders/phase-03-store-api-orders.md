# Фаза 3: Store API для заказов

## Статус
- [ ] Не начата
- [ ] В процессе
- [ ] Завершена

**Начата:** -
**Завершена:** -

## Цель фазы

Расширить Store Client новыми endpoints для работы с заказами. Реализовать поддержку Idempotency-Key для мутирующих операций (POST /orders, POST /orders/{id}/confirm).

## Задачи

### 3.0 ОБЯЗАТЕЛЬНО: Анализ и планирование

#### A. Анализ существующего кода
- [ ] Изучить существующий `src/store_client/client.py` (endpoints MVP)
- [ ] Изучить спецификацию Store API фазы 2: `doc/development/api-specification.md`
- [ ] Проверить паттерн circuit breaker и retry

**Команды для поиска:**
```bash
grep -rn "def.*search_tires\|def.*check_availability" src/store_client/
grep -rn "CircuitBreaker\|Idempotency" src/
```

#### B. Анализ зависимостей
- [ ] Store Client уже реализован с circuit breaker и retry (из phase-1)
- [ ] Нужна поддержка Idempotency-Key заголовка
- [ ] Нужна поддержка POST, PATCH методов (MVP только GET)

**Новые абстракции:** Нет
**Новые env variables:** Нет
**Новые tools:** Нет (tools определены в phase-02)
**Миграции БД:** Нет

#### C. Проверка архитектуры
- [ ] Idempotency-Key: UUID, передаётся как заголовок
- [ ] Retry для POST с Idempotency-Key безопасен (идемпотентен)
- [ ] Маппинг ответов API → формат для LLM tools

**Референс-модуль:** `src/store_client/client.py` (существующий клиент)

**Цель:** Определить расширение Store Client для заказов.

**Заметки для переиспользования:** Паттерн из существующих методов Store Client

---

### 3.1 Endpoint: GET /orders/search

- [ ] Реализовать `search_orders(phone, order_number, status)` → `GET /orders/search`
- [ ] Маппинг ответа: id, order_number, status, status_label, total, items_summary, estimated_delivery
- [ ] Обработка пустого результата (заказы не найдены)

**Файлы:** `src/store_client/client.py`
**Заметки:** -

---

### 3.2 Endpoint: GET /orders/{id}

- [ ] Реализовать `get_order(order_id)` → `GET /orders/{id}`
- [ ] Полная информация: items, delivery, payment, status_history
- [ ] Маппинг для LLM: краткое описание для озвучивания

**Файлы:** `src/store_client/client.py`
**Заметки:** -

---

### 3.3 Endpoint: POST /orders (с Idempotency-Key)

- [ ] Реализовать `create_order(items, customer_phone, customer_name, call_id)` → `POST /orders`
- [ ] Генерация `Idempotency-Key` (UUID) для каждого вызова
- [ ] Передача `Idempotency-Key` в заголовке
- [ ] Передача `source: "ai_agent"` и `call_id` в body
- [ ] Обработка ответа 201 (создано) и повторного 201 (идемпотентный ответ)

**Файлы:** `src/store_client/client.py`
**Заметки:** Idempotency-Key действителен 24 часа

---

### 3.4 Endpoint: PATCH /orders/{id}/delivery

- [ ] Реализовать `update_delivery(order_id, delivery_type, city, address, pickup_point_id)` → `PATCH /orders/{id}/delivery`
- [ ] Дополнительно: `GET /delivery/calculate` для расчёта стоимости
- [ ] Дополнительно: `GET /pickup-points` для списка пунктов самовывоза
- [ ] Маппинг: delivery cost, estimated_days, total

**Файлы:** `src/store_client/client.py`
**Заметки:** -

---

### 3.5 Endpoint: POST /orders/{id}/confirm (с Idempotency-Key)

- [ ] Реализовать `confirm_order(order_id, payment_method, customer_name)` → `POST /orders/{id}/confirm`
- [ ] Генерация `Idempotency-Key`
- [ ] Передача `send_sms_confirmation: true`
- [ ] Обработка ответа: order_number, status, estimated_delivery, sms_sent

**Файлы:** `src/store_client/client.py`
**Заметки:** -

---

## При завершении фазы

1. Убедись, что все задачи отмечены [x]
2. Измени статус фазы: [x] Завершена
3. Заполни дату "Завершена: YYYY-MM-DD"
4. Выполни коммит:
   ```bash
   git add .
   git commit -m "checklist(phase-2-orders): phase-3 store API orders completed"
   ```
5. Обнови PROGRESS.md
6. Открой следующую фазу: `phase-04-scenarios-prompt.md`
