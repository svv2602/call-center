# Фаза 1: Tools шиномонтажа

## Статус
- [ ] Не начата
- [ ] В процессе
- [x] Завершена

**Начата:** 2026-02-14
**Завершена:** 2026-02-14

## Цель фазы

Добавить tools для записи на шиномонтаж: `get_fitting_stations`, `get_fitting_slots`, `book_fitting`. Также tools для отмены/переноса и цен (не из канонического списка, но нужны по бизнес-требованиям).

## Задачи

### 1.0 ОБЯЗАТЕЛЬНО: Анализ и планирование

#### A. Анализ существующего кода
- [x] Изучить существующие tools в `src/agent/tools.py` (MVP + заказы)
- [x] Проверить канонический список tools в `doc/development/00-overview.md`
- [x] Изучить сценарий записи на монтаж из `doc/development/phase-3-services.md`

**Команды для поиска:**
```bash
grep -rn "\"name\":" src/agent/tools.py
grep -rn "fitting\|booking\|station\|slot" src/
```

#### B. Анализ зависимостей
- [x] Канонические tools: `get_fitting_stations`, `get_fitting_slots`, `book_fitting`, `search_knowledge_base`
- [x] Доп. tools из phase-3-services.md: `cancel_fitting`, `get_fitting_price` (добавить в канонический список?)
- [x] Store API endpoints для шиномонтажа

**Новые абстракции:** Нет
**Новые env variables:** Нет
**Новые tools:** `get_fitting_stations`, `get_fitting_slots`, `book_fitting`, (+`cancel_fitting`, `get_fitting_price`)
**Миграции БД:** `003_add_fitting.py` — таблицы fitting_stations, fitting_bookings

#### C. Проверка архитектуры
- [x] Связка booking с заказом (linked_order_id)
- [x] CallerID для customer_phone в booking

**Референс-модуль:** `src/agent/tools.py` (существующие tools)

**Цель:** Определить schema всех fitting tools.

**Заметки для переиспользования:** Паттерн из существующих tools

---

### 1.1 Tool: get_fitting_stations

- [x] Добавить schema в `src/agent/tools.py`
- [x] Параметры: `city` (string, required)
- [x] Описание: "Получить список точек шиномонтажа"
- [x] Маршрутизация: → `GET /fitting/stations?city=...`

**Файлы:** `src/agent/tools.py`
**Заметки:** -

---

### 1.2 Tool: get_fitting_slots

- [x] Добавить schema в `src/agent/tools.py`
- [x] Параметры: `station_id` (required), `date_from` (YYYY-MM-DD или "today"), `date_to`, `service_type` (enum: tire_change, balancing, full_service)
- [x] Описание: "Получить доступные слоты для записи"
- [x] Маршрутизация: → `GET /fitting/stations/{id}/slots`

**Файлы:** `src/agent/tools.py`
**Заметки:** -

---

### 1.3 Tool: book_fitting

- [x] Добавить schema в `src/agent/tools.py`
- [x] Параметры: `station_id` (required), `date` (YYYY-MM-DD, required), `time` (HH:MM, required), `customer_phone` (required), `vehicle_info`, `service_type`, `tire_diameter`, `linked_order_id`
- [x] Описание: "Записать клиента на шиномонтаж"
- [x] Маршрутизация: → `POST /fitting/bookings`
- [x] CallerID как customer_phone по умолчанию

**Файлы:** `src/agent/tools.py`
**Заметки:** -

---

### 1.4 Дополнительные tools (cancel_fitting, get_fitting_price)

- [x] `cancel_fitting`: booking_id (required), action (enum: cancel, reschedule), new_date, new_time
- [x] Маршрутизация: cancel → `DELETE /fitting/bookings/{id}`, reschedule → `PATCH /fitting/bookings/{id}`
- [x] `get_fitting_price`: tire_diameter (required), station_id, service_type
- [x] Маршрутизация: → `GET /fitting/prices`
- [x] Обновить канонический список tools в `doc/development/00-overview.md` (если добавляются)

**Файлы:** `src/agent/tools.py`
**Заметки:** cancel_fitting и get_fitting_price добавлены в канонический список tools в `doc/development/00-overview.md`

---

### 1.5 Миграция БД для шиномонтажа

- [x] Создать `migrations/versions/003_add_fitting.py`
- [x] Таблица `fitting_stations`: id, name, city, district, address, phone, working_hours, services (JSONB), active
- [x] Таблица `fitting_bookings`: id, station_id, customer_id, linked_order_id, booking_date, booking_time, service_type, tire_diameter, vehicle_info, status, price, source, source_call_id
- [x] Связь calls.fitting_booking_id → fitting_bookings.id

**Файлы:** `migrations/versions/003_add_fitting.py`
**Заметки:** Схема из `doc/technical/data-model.md`

---

## При завершении фазы

1. Убедись, что все задачи отмечены [x]
2. Измени статус фазы: [x] Завершена
3. Заполни дату "Завершена: YYYY-MM-DD"
4. Выполни коммит:
   ```bash
   git add .
   git commit -m "checklist(phase-3-services): phase-1 fitting tools completed"
   ```
5. Обнови PROGRESS.md
6. Открой следующую фазу: `phase-02-fitting-api.md`
