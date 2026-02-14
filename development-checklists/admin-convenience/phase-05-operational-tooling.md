# Фаза 5: Операционный инструментарий

## Статус
- [x] Не начата
- [x] В процессе
- [x] Завершена

**Начата:** 2026-02-14
**Завершена:** 2026-02-14

## Цель фазы
Обеспечить админа инструментами для повседневной эксплуатации:
мониторинг фоновых задач, runbooks для инцидентов, верификация бэкапов,
hot-reload конфигурации без перезапуска.

## Задачи

### 5.0 ОБЯЗАТЕЛЬНО: Анализ и планирование

#### A. Анализ существующего кода
- [x] Изучить `src/tasks/celery_app.py` — конфигурация Celery, очереди, расписание
- [x] Изучить `docker-compose.yml` — текущие сервисы, как добавить Flower
- [x] Изучить `prometheus/alerts.yml` — существующие алерты (базис для runbooks)
- [x] Изучить `alertmanager/config.yml` — текущие каналы уведомлений
- [x] Изучить `src/config.py` — текущая система конфигурации (для hot-reload)
- [x] Изучить `src/tasks/backup.py` — бэкап из фазы 1 (для верификации)

**Команды для поиска:**
```bash
# Celery конфигурация
grep -rn "beat_schedule\|task_routes" src/tasks/celery_app.py
# Docker services
grep -rn "services:" docker-compose.yml
# Алерты
grep -rn "alert:" prometheus/alerts.yml
# Текущие env variables
grep -rn "class.*Settings" src/config.py
```

#### B. Анализ зависимостей
- [x] Нужны ли новые пакеты? (`flower` для мониторинга Celery)
- [x] Нужны ли новые Docker-сервисы? (Flower)
- [x] Нужны ли миграции БД? (нет)

**Новые пакеты:** `flower`
**Новые env variables:** `FLOWER_PORT` (по умолчанию 5555), `FLOWER_BASIC_AUTH`
**Миграции БД:** нет

#### C. Проверка архитектуры
- [x] Flower — отдельный Docker-сервис или запускать вместе с worker?
- [x] Runbooks — markdown-файлы в `doc/operations/` или в admin UI?
- [x] Hot-reload — signal-based (SIGHUP) или API endpoint?

**Референс-модуль:** `docker-compose.yml` (паттерн добавления сервиса)

**Цель:** Определить минимальный набор операционных инструментов.

**Заметки для переиспользования:** -

---

### 5.1 Мониторинг Celery через Flower
- [x] Добавить `flower` в зависимости проекта
- [x] Добавить сервис `flower` в `docker-compose.yml`
- [x] Конфигурация: порт 5555, basic auth из env, подключение к Redis broker
- [x] Проверить доступ к Flower UI: `http://localhost:5555`
- [x] Flower отображает: активные задачи, очереди, worker status, задачи по расписанию
- [x] Добавить ссылку на Flower в Admin UI (страница Settings)

**Файлы:** `docker-compose.yml`, `pyproject.toml`, `admin-ui/index.html`
**Заметки:** Flower конфигурация: `celery -A src.tasks.celery_app flower --port=5555 --basic_auth=admin:password`.

---

### 5.2 Health-check эндпоинт для Celery
- [x] Добавить `GET /health/celery` — проверяет доступность Celery workers
- [x] Проверять: ping worker, количество активных задач в очереди, последнее выполнение scheduled tasks
- [x] Возвращать: `{"workers_online": 2, "queue_quality": 0, "queue_stats": 1, "last_daily_stats": "2026-02-14T01:00:00"}`
- [x] Добавить алерт в Prometheus: `CeleryWorkerDown` — worker недоступен >5 минут
- [x] Написать тесты

**Файлы:** `src/main.py` (или `src/api/health.py`), `prometheus/alerts.yml`, `tests/unit/test_health_celery.py`
**Заметки:** Celery ping: `celery_app.control.ping(timeout=5)`.

---

### 5.3 Верификация бэкапов
- [x] Добавить в `src/tasks/backup.py` функцию `verify_backup(filepath)` — проверяет целостность дампа
- [x] Верификация: gunzip → `pg_restore --list` (проверяет TOC без восстановления)
- [x] Добавить Celery-задачу `verify_latest_backup()` — запуск после каждого бэкапа
- [x] При неудачной верификации — алерт в Telegram
- [x] CLI: `call-center-admin db verify-backup <file>`
- [x] Написать тесты

**Файлы:** `src/tasks/backup.py`, `src/cli/db.py`, `tests/unit/test_backup_verify.py`
**Заметки:** `pg_restore --list` не требует базы данных — только файл дампа.

---

### 5.4 Runbooks для типичных инцидентов
- [x] Создать `doc/operations/runbooks/` директорию
- [x] Runbook: `circuit-breaker-open.md` — Store API circuit breaker открыт
- [x] Runbook: `high-transfer-rate.md` — >50% трансферов за час
- [x] Runbook: `high-latency.md` — p95 response >2.5s (STT/LLM/TTS диагностика)
- [x] Runbook: `celery-worker-down.md` — Celery worker не отвечает
- [x] Runbook: `operator-queue-overflow.md` — очередь операторов >5
- [x] Runbook: `backup-failed.md` — бэкап не выполнился или не прошёл верификацию
- [x] Каждый runbook содержит: симптомы, диагностика (команды), решение, эскалация

**Файлы:** `doc/operations/runbooks/*.md`
**Заметки:** Runbooks привязаны к алертам из `prometheus/alerts.yml`. Формат: markdown с bash-командами для диагностики.

---

### 5.5 Hot-reload конфигурации через API
- [x] Добавить `POST /admin/config/reload` — перезагрузка конфигурации из env без перезапуска
- [x] Перезагружаемые параметры: `QualitySettings`, `FeatureFlagSettings`, `LoggingSettings`
- [x] НЕ перезагружаемые: `DatabaseSettings`, `RedisSettings`, `AudioSocketSettings` (требуют reconnect)
- [x] Обновлять конфигурацию в `Settings` singleton
- [x] Логировать событие reload в аудит-лог
- [x] Только роль `admin`
- [x] Написать тесты

**Файлы:** `src/api/admin_config.py`, `src/config.py`, `tests/unit/test_config_reload.py`
**Заметки:** Не все параметры можно перезагрузить без reconnect. Чётко документировать что можно, что нельзя.

---

### 5.6 API: статус системы (расширенный)
- [x] Расширить `GET /health/ready` — добавить статус Celery workers
- [x] Добавить `GET /admin/system-status` — полный статус системы
- [x] Включить: версию приложения, uptime, активные звонки, Celery workers, Redis memory, PostgreSQL connections, размер БД, последний бэкап
- [x] Только роль `admin`
- [x] Написать тесты

**Файлы:** `src/api/admin_config.py` (или `src/api/system.py`), `tests/unit/test_system_status.py`
**Заметки:** PostgreSQL info: `SELECT pg_database_size('callcenter')`, `SELECT count(*) FROM pg_stat_activity`.

---

### 5.7 Admin UI: страница системного статуса
- [x] Обновить страницу Settings → переименовать в «Система»
- [x] Отобразить расширенный статус из `GET /admin/system-status`
- [x] Цветовая индикация: зелёный (OK), жёлтый (degraded), красный (down)
- [x] Ссылки: Grafana, Flower, Prometheus
- [x] Кнопка «Reload config» → `POST /admin/config/reload`
- [x] Информация о последнем бэкапе: дата, размер, статус верификации

**Файлы:** `admin-ui/index.html`
**Заметки:** -

---

### 5.8 CLI: операционные команды
- [x] `call-center-admin celery status` — статус workers и очередей
- [x] `call-center-admin celery purge <queue>` — очистка очереди (с подтверждением)
- [x] `call-center-admin config reload` — вызов `POST /admin/config/reload`
- [x] `call-center-admin system status` — вызов `GET /admin/system-status` в табличном виде
- [x] Написать тесты

**Файлы:** `src/cli/operations.py`, `tests/unit/test_cli_operations.py`
**Заметки:** -

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
   git commit -m "checklist(admin-convenience): phase-5 operational tooling completed"
   ```
5. Обнови PROGRESS.md:
   - Текущая фаза: 6
   - Добавь запись в историю
6. Открой следующую фазу и продолжи работу
