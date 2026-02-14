# Фаза 6: Управление операторами

## Статус
- [x] Не начата
- [x] В процессе
- [x] Завершена

**Начата:** 2026-02-14
**Завершена:** 2026-02-14

## Цель фазы
Дать администратору инструменты для управления операторами:
отслеживание статусов (online/offline), мониторинг очереди переводов,
просмотр нагрузки и истории переводов.

## Задачи

### 6.0 ОБЯЗАТЕЛЬНО: Анализ и планирование

#### A. Анализ существующего кода
- [x] Изучить `src/agent/tools.py` — tool `transfer_to_operator` (как происходит перевод)
- [x] Изучить Asterisk интеграцию — как бот передаёт звонок оператору (ARI)
- [x] Изучить `src/config.py` → `ARISettings` — подключение к Asterisk
- [x] Изучить `prometheus/alerts.yml` → `OperatorQueueOverflow` — алерт переполнения очереди
- [x] Изучить `src/monitoring/metrics.py` → `operator_queue_length`, `transfers_to_operator_total`

**Команды для поиска:**
```bash
# Transfer tool
grep -rn "transfer_to_operator\|operator" src/agent/tools.py
# ARI integration
grep -rn "ARI\|ari\|asterisk" src/
# Operator metrics
grep -rn "operator\|transfer" src/monitoring/metrics.py
# Asterisk config
grep -rn "ARISettings\|ari_url" src/config.py
```

#### B. Анализ зависимостей
- [x] Нужны ли миграции БД? (ДА — таблица operators)
- [x] Нужен ли прямой доступ к Asterisk ARI для статусов операторов?
- [x] Нужна ли интеграция с внешней системой управления сменами?

**Новые пакеты:** нет (ARI-клиент уже есть)
**Новые env variables:** нет
**Миграции БД:** `008_add_operators.py`

#### C. Проверка архитектуры
- [x] Статус оператора: из Asterisk AMI/ARI (реальное состояние) или из БД (ручное управление)?
- [x] Очередь: данные из Asterisk Queue или из Redis?
- [x] Интеграция: REST API → Asterisk ARI для управления очередью

**Референс-модуль:** `src/store_client/client.py` (паттерн HTTP-клиента для внешнего сервиса)

**Цель:** Определить источник данных о статусах операторов и очереди.

**Заметки для переиспользования:** Статусы операторов хранятся в БД (operator_status_log) — ручное управление через API. Очередь переводов рассчитывается из таблицы calls.

---

### 6.1 Миграция БД: таблица operators
- [x] Создать миграцию `migrations/versions/008_add_operators.py`
- [x] Таблица `operators`: id, name, extension (SIP-номер, unique), is_active, skills (jsonb), shift_start (time), shift_end (time), created_at, updated_at
- [x] Таблица `operator_status_log`: id, operator_id (FK), status (enum: online/offline/busy/break), changed_at
- [x] Индексы: `operators(extension)`, `operator_status_log(operator_id, changed_at)`
- [x] Написать тесты миграции

**Файлы:** `migrations/versions/008_add_operators.py`
**Заметки:** `skills` — массив тегов, например `["tires", "fitting", "orders"]` для skill-based routing в будущем.

---

### 6.2 API: управление операторами
- [x] Создать `src/api/operators.py` — CRUD роутер
- [x] `GET /operators` — список операторов с текущим статусом
- [x] `POST /operators` — создание оператора (name, extension, skills, shift)
- [x] `PATCH /operators/{id}` — обновление данных оператора
- [x] `DELETE /operators/{id}` — деактивация оператора (soft delete)
- [x] `PATCH /operators/{id}/status` — ручная смена статуса (online/offline/break)
- [x] Только роль `admin` и `operator` (для смены своего статуса)
- [x] Написать тесты: `tests/unit/test_operators_api.py`

**Файлы:** `src/api/operators.py`, `tests/unit/test_operators_api.py`
**Заметки:** Паттерн — аналогично `src/api/knowledge.py` (CRUD + фильтры). Роль `admin` для CRUD, `admin` + `operator` для смены статуса.

---

### 6.3 API: мониторинг очереди
- [x] Добавить `GET /operators/queue` — текущее состояние очереди переводов
- [x] Данные: количество ожидающих, среднее время ожидания, операторы на линии
- [x] Источник: Asterisk ARI `GET /ari/queues` или Redis (метрика `operator_queue_length`)
- [x] Добавить `GET /operators/transfers` — история переводов за период
- [x] Фильтры: date_from, date_to, operator_id, reason
- [x] Написать тесты

**Файлы:** `src/api/operators.py`, `tests/unit/test_operators_api.py`
**Заметки:** Данные из PostgreSQL (таблицы calls, operator_status_log). Фильтры: date_from, date_to, reason, limit, offset.

---

### 6.4 API: статистика по операторам
- [x] Добавить `GET /operators/{id}/stats` — статистика оператора за период
- [x] Метрики: принятые звонки, среднее время обработки, количество переводов от бота
- [x] Агрегация из таблицы `calls` по полю `transferred_to_operator_id`
- [x] Написать тесты

**Файлы:** `src/api/operators.py`, `tests/unit/test_operators_api.py`
**Заметки:** Статистика включает историю статусов оператора (последние 20 записей).

---

### 6.5 Admin UI: страница операторов
- [x] Добавить страницу «Операторы» в навигацию
- [x] Список операторов с цветовой индикацией статуса: зелёный (online), серый (offline), жёлтый (busy), синий (break)
- [x] Кнопки управления: добавить, редактировать, деактивировать
- [x] Форма создания/редактирования оператора
- [x] Виджет текущей очереди: количество ожидающих, среднее время
- [x] Автообновление статусов каждые 10 секунд

**Файлы:** `admin-ui/index.html`
**Заметки:** Статус операторов обновлять через `setInterval` + `GET /operators`. Модал для создания/редактирования оператора с полями: name, extension, skills, shift_start, shift_end.

---

### 6.6 Grafana: дашборд операторов
- [x] Создать `grafana/dashboards/operators.json` — дашборд для операторов
- [x] Панели: длина очереди (graph), количество переводов по причинам (pie), среднее время ожидания (stat), операторы online/offline (table)
- [x] Временной диапазон: последние 24 часа по умолчанию
- [x] Добавить в Grafana provisioning

**Файлы:** `grafana/dashboards/operators.json`, `grafana/provisioning/dashboards/dashboards.yml`
**Заметки:** Данные из Prometheus метрик: `operator_queue_length`, `transfers_to_operator_total`. Provisioning уже подхватывает все файлы из /var/lib/grafana/dashboards.

---

## При завершении фазы

Выполни следующие действия:

1. Убедись, что все задачи отмечены [x]
2. Измени статус фазы:
   - [x] Завершена
3. Заполни дату "Завершена: YYYY-MM-DD"
4. Выполни коммит:
   ```bash
   git add .
   git commit -m "checklist(admin-convenience): phase-6 operator management completed"
   ```
5. Обнови PROGRESS.md:
   - Общий прогресс: 54/54 (100%)
   - Добавь запись в историю: «Все фазы завершены»
6. Обновить README.md — отметить все критерии успеха как [x]
