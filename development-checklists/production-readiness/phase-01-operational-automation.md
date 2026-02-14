# Фаза 1: Operational Automation

## Статус
- [x] Не начата
- [x] В процессе
- [x] Завершена

**Начата:** 2026-02-14
**Завершена:** 2026-02-14

## Цель фазы
Автоматизировать критические операционные задачи: создание партиций PostgreSQL, backup всех компонентов, мониторинг выполнения cron-задач. После этой фазы система сможет работать в production без ручного вмешательства в рутинные операции.

## Задачи

### 1.0 ОБЯЗАТЕЛЬНО: Анализ и планирование

#### A. Анализ существующего кода
- [x] Изучить `scripts/create_future_partitions.py` — логика создания партиций
- [x] Изучить `scripts/backup_procedures.md` — задокументированные процедуры backup
- [x] Изучить `docker-compose.yml` — текущие сервисы и volumes
- [x] Изучить `prometheus/alerts.yml` — существующие алерты
- [x] Изучить миграции в `migrations/versions/` — структура партиционированных таблиц

#### B. Анализ зависимостей
- [x] Определить: использовать cron в Docker или Celery Beat для периодических задач
- [x] Проверить нужны ли новые Docker volumes для backup
- [x] Проверить нужны ли новые env variables для backup destinations

**Новые абстракции:** нет
**Новые env variables:** `BACKUP_REDIS_DIR`, `BACKUP_KNOWLEDGE_DIR`
**Новые tools:** нет
**Миграции БД:** нет

#### C. Проверка архитектуры
- [x] Определить подход: cron в отдельном контейнере vs Celery Beat schedule
- [x] Продумать хранение backup: локально + S3/GCS
- [x] Продумать мониторинг: как отслеживать выполнение cron-задач

**Референс-модуль:** `src/tasks/backup.py`, `src/tasks/partition_manager.py`

**Цель:** Понять существующие паттерны автоматизации ПЕРЕД написанием кода.

**Заметки для переиспользования:**
- **Celery Beat уже настроен** — `src/tasks/celery_app.py` содержит расписание для партиций (ежемесячно) и backup PostgreSQL (ежедневно 04:00 + верификация 04:30)
- `src/tasks/partition_manager.py` — создаёт 3 месяца вперёд, удаляет старые транскрипции (12 мес retention)
- `src/tasks/backup.py` — pg_dump + gzip + ротация, верификация целостности
- `src/monitoring/metrics.py` — **НЕТ метрик для backup/partition** (нужно добавить)
- `prometheus/alerts.yml` — **НЕТ алертов для backup/partition** (нужно добавить)
- Redis backup и knowledge backup — **НЕ реализованы** (нужно добавить)
- Restore скрипты — **НЕ реализованы** (нужны shell-скрипты)

---

### 1.1 Автоматизация создания партиций PostgreSQL

Скрипт `scripts/create_future_partitions.py` уже написан, но не интегрирован в автоматическое выполнение.

- [x] Добавить Celery Beat задачу или systemd timer для ежемесячного запуска — **УЖЕ БЫЛО**: `src/tasks/partition_manager.py` + расписание в `celery_app.py` (1-го числа в 02:00)
- [x] Если выбран Celery Beat: добавить periodic task в `src/tasks/` — **УЖЕ БЫЛО**: `src/tasks/partition_manager.py`
- [x] Добавить Prometheus метрику `partition_creation_last_success_timestamp` для мониторинга — добавлено в `src/monitoring/metrics.py`
- [x] Добавить алерт в `prometheus/alerts.yml`: `PartitionCreationStale` если метрика старше 35 дней — добавлено
- [x] Добавить алерт `PartitionManagementFailed` при ошибках — добавлено
- [x] Тесты — **УЖЕ БЫЛИ**: `tests/unit/test_partition_manager.py`
- [x] Проверить: партиции создаются на 3 месяца вперёд при запуске (MONTHS_AHEAD=3) — проверено

**Файлы:** `src/tasks/partition_manager.py`, `src/monitoring/metrics.py`, `prometheus/alerts.yml`
**Заметки:** Celery Beat уже имел задачу. Добавлены Prometheus метрики (partition_last_success_timestamp, partition_created/dropped_total, partition_errors_total) и алерты.

---

### 1.2 Развёртывание backup-скриптов

PostgreSQL backup уже был реализован через Celery (`src/tasks/backup.py`). Добавлены Redis и knowledge base backup, а также shell-скрипты для restore.

- [x] PostgreSQL backup — **УЖЕ БЫЛ**: `src/tasks/backup.py` (Celery task, daily 04:00)
- [x] Добавить Redis backup — создан `backup_redis` Celery task (daily 04:15)
- [x] Добавить knowledge base backup — создан `backup_knowledge_base` Celery task (weekly Sunday 01:00)
- [x] Создать `scripts/backup/restore_postgres.sh` — восстановление из backup
- [x] Создать `scripts/backup/restore_redis.sh` — восстановление Redis из RDB
- [x] Создать `scripts/backup/test_backup.sh` — верификация целостности backup
- [x] Все скрипты: exit codes, обработку ошибок, set -euo pipefail
- [x] Скрипты параметризованы через аргументы и env variables

**Файлы:** `src/tasks/backup.py`, `scripts/backup/restore_postgres.sh`, `scripts/backup/restore_redis.sh`, `scripts/backup/test_backup.sh`
**Заметки:** Backup реализован через Celery tasks (не shell скрипты) — единый подход с мониторингом. Restore — shell скрипты для ручного восстановления.

---

### 1.3 Автоматизация backup через cron/scheduler

- [x] Расширен Celery Beat — добавлены `backup-redis` и `backup-knowledge-base` в расписание
- [x] Расписание: PostgreSQL daily 04:00, Redis daily 04:15, knowledge weekly Sunday 01:00, verify 04:30
- [x] Добавить Prometheus метрики — `backup_last_success_timestamp{component}`, `backup_last_size_bytes`, `backup_duration_seconds`, `backup_errors_total`
- [x] Добавить алерт `PostgresBackupStale` — backup > 26 часов
- [x] Добавить алерт `RedisBackupStale` — backup > 26 часов
- [x] Добавить алерт `KnowledgeBackupStale` — backup > 8 дней
- [x] Добавить алерт `BackupFailed` — при ошибках для любого компонента
- [x] Добавить Docker volume `backups` для backup storage в docker-compose.yml
- [x] Тесты обновлены — `tests/unit/test_backup.py`

**Файлы:** `src/tasks/celery_app.py`, `src/monitoring/metrics.py`, `prometheus/alerts.yml`, `docker-compose.yml`
**Заметки:** Все backup автоматизированы через Celery Beat. Prometheus отслеживает успешность, размер, и ошибки.

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
   git commit -m "checklist(production-readiness): phase-1 operational automation completed"
   ```
5. Обнови PROGRESS.md:
   - Текущая фаза: 2
   - Добавь запись в историю
6. Открой следующую фазу и продолжи работу
