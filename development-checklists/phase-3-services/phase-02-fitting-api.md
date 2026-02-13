# Фаза 2: Fitting API (Store Client)

## Статус
- [ ] Не начата
- [ ] В процессе
- [ ] Завершена

**Начата:** -
**Завершена:** -

## Цель фазы

Расширить Store Client endpoints для шиномонтажа: список станций, доступные слоты, создание/отмена/перенос записи, прайс-лист.

## Задачи

### 2.0 ОБЯЗАТЕЛЬНО: Анализ и планирование

#### A. Анализ существующего кода
- [ ] Изучить существующий Store Client (MVP + заказы)
- [ ] Изучить спецификацию Store API фазы 3: `doc/development/api-specification.md`

**Команды для поиска:**
```bash
grep -rn "def.*fitting\|def.*station\|def.*booking" src/store_client/
```

#### B. Анализ зависимостей
- [ ] Store Client с circuit breaker и retry уже реализован
- [ ] Нужны POST, DELETE, PATCH для bookings

**Новые абстракции:** Нет
**Новые env variables:** Нет
**Новые tools:** Нет (tools из phase-01)
**Миграции БД:** Нет

**Референс-модуль:** `src/store_client/client.py`

**Цель:** Расширить Store Client для fitting endpoints.

**Заметки для переиспользования:** Паттерн из существующих методов

---

### 2.1 Endpoint: GET /fitting/stations

- [ ] Реализовать `get_fitting_stations(city)` → `GET /fitting/stations?city=...`
- [ ] Маппинг: id, name, city, district, address, working_hours, services
- [ ] Форматирование для озвучивания (краткое описание каждой станции)

**Файлы:** `src/store_client/client.py`
**Заметки:** -

---

### 2.2 Endpoint: GET /fitting/stations/{id}/slots

- [ ] Реализовать `get_fitting_slots(station_id, date_from, date_to, service_type)` → `GET /fitting/stations/{id}/slots`
- [ ] Маппинг: date, times (only available=true), форматирование для озвучивания
- [ ] Фильтрация только доступных слотов

**Файлы:** `src/store_client/client.py`
**Заметки:** -

---

### 2.3 Endpoint: POST /fitting/bookings

- [ ] Реализовать `create_booking(station_id, date, time, customer_phone, vehicle_info, service_type, tire_diameter, linked_order_id, call_id)` → `POST /fitting/bookings`
- [ ] Передача `source: "ai_agent"` и `call_id`
- [ ] Обработка ответа: booking id, station info, date, time, price, sms_sent

**Файлы:** `src/store_client/client.py`
**Заметки:** -

---

### 2.4 Endpoints: DELETE и PATCH /fitting/bookings/{id}

- [ ] Реализовать `cancel_booking(booking_id)` → `DELETE /fitting/bookings/{id}`
- [ ] Реализовать `reschedule_booking(booking_id, new_date, new_time)` → `PATCH /fitting/bookings/{id}`
- [ ] Обработка ошибок: booking не найден, уже отменён

**Файлы:** `src/store_client/client.py`
**Заметки:** -

---

### 2.5 Endpoint: GET /fitting/prices

- [ ] Реализовать `get_fitting_prices(station_id, tire_diameter)` → `GET /fitting/prices`
- [ ] Маппинг: service → label, price by diameter
- [ ] Форматирование для озвучивания (цена в гривнях)

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
   git commit -m "checklist(phase-3-services): phase-2 fitting API completed"
   ```
5. Обнови PROGRESS.md
6. Открой следующую фазу: `phase-03-knowledge-base.md`
