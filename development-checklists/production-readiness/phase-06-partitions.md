# Фаза 6: Автоматизация партиций PostgreSQL

## Статус
- [ ] Не начата
- [ ] В процессе
- [x] Завершена

**Начата:** 2026-02-14
**Завершена:** 2026-02-14

## Цель фазы
Партиции в `migrations/versions/001_initial_schema.py` захардкожены на 2026-01..05. Нужна автоматическая Celery-задача для создания будущих партиций.

## Задачи

### 6.0 ОБЯЗАТЕЛЬНО: Анализ и планирование

#### A. Анализ существующего кода
- [x] Изучить `migrations/versions/001_initial_schema.py` — 3 таблицы (calls, call_turns, call_tool_calls), партиции 2026_01..2026_05
- [x] Изучить `src/tasks/celery_app.py` — beat_schedule с crontab
- [x] Изучить `src/tasks/data_retention.py` — паттерн sync wrapper → async helper
- [x] Изучить `src/config.py` — DATABASE_URL через get_settings()

#### B. Анализ зависимостей
- [x] Таблицы: `calls`, `call_turns`, `call_tool_calls`
- [x] Формат: `{table}_{YYYY}_{MM}`
- [x] Celery beat уже запущен, нужно только добавить task + schedule

#### C. Проверка архитектуры
- [x] Паттерн: из data_retention.py
- [x] Idempotent: IF NOT EXISTS / IF EXISTS
- [x] Создавать на 3 месяца вперёд

---

### 6.1 Создать src/tasks/partition_manager.py
- [x] Создать файл `src/tasks/partition_manager.py`
- [x] Celery task `ensure_partitions` с async-обёрткой
- [x] Для 3 таблиц создать партиции на 3 месяца вперёд
- [x] `CREATE TABLE IF NOT EXISTS` — идемпотентно
- [x] Логирование созданных партиций
- [x] Возвращает dict с результатами

---

### 6.2 Добавить schedule в celery_app.py
- [x] Добавить task в `beat_schedule`: `crontab(hour=2, minute=0, day_of_month="1")`
- [x] Добавить routing: `"src.tasks.partition_manager.*": {"queue": "stats"}`

---

### 6.3 Добавить политику ретеншна партиций
- [x] DROP старых партиций для `call_turns` и `call_tool_calls` (>12 месяцев)
- [x] `calls` НЕ удаляются (metadata хранится дольше)
- [x] `DROP TABLE IF EXISTS` — идемпотентно

---

### 6.4 Написать unit-тесты
- [x] `tests/unit/test_partition_manager.py` — 12 тестов
- [x] Тест: _months_range генерирует корректные диапазоны
- [x] Тест: корректные имена партиций (padding, dec→jan)
- [x] Тест: константы (PARTITIONED_TABLES, RETENTION_TABLES)

---

### 6.5 Ручная проверка
- [x] Unit-тесты: 12/12 passed

---

## При завершении фазы
Все задачи выполнены. Partition manager создан, тесты проходят.
