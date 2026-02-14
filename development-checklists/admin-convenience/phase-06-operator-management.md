# Фаза 6: Управление операторами

## Статус
- [ ] Не начата
- [ ] В процессе
- [ ] Завершена

**Начата:** -
**Завершена:** -

## Цель фазы
Дать администратору инструменты для управления операторами:
отслеживание статусов (online/offline), мониторинг очереди переводов,
просмотр нагрузки и истории переводов.

## Задачи

### 6.0 ОБЯЗАТЕЛЬНО: Анализ и планирование

#### A. Анализ существующего кода
- [ ] Изучить `src/agent/tools.py` — tool `transfer_to_operator` (как происходит перевод)
- [ ] Изучить Asterisk интеграцию — как бот передаёт звонок оператору (ARI)
- [ ] Изучить `src/config.py` → `ARISettings` — подключение к Asterisk
- [ ] Изучить `prometheus/alerts.yml` → `OperatorQueueOverflow` — алерт переполнения очереди
- [ ] Изучить `src/monitoring/metrics.py` → `operator_queue_length`, `transfers_to_operator_total`

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
- [ ] Нужны ли миграции БД? (ДА — таблица operators)
- [ ] Нужен ли прямой доступ к Asterisk ARI для статусов операторов?
- [ ] Нужна ли интеграция с внешней системой управления сменами?

**Новые пакеты:** нет (ARI-клиент уже есть)
**Новые env variables:** нет
**Миграции БД:** `008_add_operators.py`

#### C. Проверка архитектуры
- [ ] Статус оператора: из Asterisk AMI/ARI (реальное состояние) или из БД (ручное управление)?
- [ ] Очередь: данные из Asterisk Queue или из Redis?
- [ ] Интеграция: REST API → Asterisk ARI для управления очередью

**Референс-модуль:** `src/store_client/client.py` (паттерн HTTP-клиента для внешнего сервиса)

**Цель:** Определить источник данных о статусах операторов и очереди.

**Заметки для переиспользования:** -

---

### 6.1 Миграция БД: таблица operators
- [ ] Создать миграцию `migrations/versions/008_add_operators.py`
- [ ] Таблица `operators`: id, name, extension (SIP-номер, unique), is_active, skills (jsonb), shift_start (time), shift_end (time), created_at, updated_at
- [ ] Таблица `operator_status_log`: id, operator_id (FK), status (enum: online/offline/busy/break), changed_at
- [ ] Индексы: `operators(extension)`, `operator_status_log(operator_id, changed_at)`
- [ ] Написать тесты миграции

**Файлы:** `migrations/versions/008_add_operators.py`
**Заметки:** `skills` — массив тегов, например `["tires", "fitting", "orders"]` для skill-based routing в будущем.

---

### 6.2 API: управление операторами
- [ ] Создать `src/api/operators.py` — CRUD роутер
- [ ] `GET /operators` — список операторов с текущим статусом
- [ ] `POST /operators` — создание оператора (name, extension, skills, shift)
- [ ] `PATCH /operators/{id}` — обновление данных оператора
- [ ] `DELETE /operators/{id}` — деактивация оператора (soft delete)
- [ ] `PATCH /operators/{id}/status` — ручная смена статуса (online/offline/break)
- [ ] Только роль `admin` и `operator` (для смены своего статуса)
- [ ] Написать тесты: `tests/unit/test_operators_api.py`

**Файлы:** `src/api/operators.py`, `tests/unit/test_operators_api.py`
**Заметки:** Паттерн — аналогично `src/api/knowledge.py` (CRUD + фильтры).

---

### 6.3 API: мониторинг очереди
- [ ] Добавить `GET /operators/queue` — текущее состояние очереди переводов
- [ ] Данные: количество ожидающих, среднее время ожидания, операторы на линии
- [ ] Источник: Asterisk ARI `GET /ari/queues` или Redis (метрика `operator_queue_length`)
- [ ] Добавить `GET /operators/transfers` — история переводов за период
- [ ] Фильтры: date_from, date_to, operator_id, reason
- [ ] Написать тесты

**Файлы:** `src/api/operators.py`, `tests/unit/test_operators_queue.py`
**Заметки:** Если ARI недоступен — fallback на данные из Prometheus/Redis метрик.

---

### 6.4 API: статистика по операторам
- [ ] Добавить `GET /operators/{id}/stats` — статистика оператора за период
- [ ] Метрики: принятые звонки, среднее время обработки, количество переводов от бота
- [ ] Агрегация из таблицы `calls` по полю `transferred_to_operator_id`
- [ ] Написать тесты

**Файлы:** `src/api/operators.py`, `tests/unit/test_operators_stats.py`
**Заметки:** Добавить `transferred_to_operator_id` в таблицу `calls` если отсутствует.

---

### 6.5 Admin UI: страница операторов
- [ ] Добавить страницу «Операторы» в навигацию
- [ ] Список операторов с цветовой индикацией статуса: зелёный (online), серый (offline), жёлтый (busy), синий (break)
- [ ] Кнопки управления: добавить, редактировать, деактивировать
- [ ] Форма создания/редактирования оператора
- [ ] Виджет текущей очереди: количество ожидающих, среднее время
- [ ] Автообновление статусов каждые 10 секунд

**Файлы:** `admin-ui/index.html`
**Заметки:** Статус операторов обновлять через `setInterval` + `GET /operators`.

---

### 6.6 Grafana: дашборд операторов
- [ ] Создать `grafana/dashboards/operators.json` — дашборд для операторов
- [ ] Панели: длина очереди (graph), количество переводов по причинам (pie), среднее время ожидания (stat), операторы online/offline (table)
- [ ] Временной диапазон: последние 24 часа по умолчанию
- [ ] Добавить в Grafana provisioning

**Файлы:** `grafana/dashboards/operators.json`, `grafana/provisioning/dashboards/dashboards.yml`
**Заметки:** Данные из Prometheus метрик: `operator_queue_length`, `transfers_to_operator_total`.

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
