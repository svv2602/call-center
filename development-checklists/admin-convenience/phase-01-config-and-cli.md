# Фаза 1: Валидация конфигурации и CLI-фундамент

## Статус
- [x] Не начата
- [x] В процессе
- [x] Завершена

**Начата:** 2026-02-14
**Завершена:** 2026-02-14

## Цель фазы
Защитить администратора от ошибок конфигурации при старте приложения.
Создать скелет CLI-инструмента `call-center-admin` для операционных задач.
Добавить автоматические бэкапы PostgreSQL.

## Задачи

### 1.0 ОБЯЗАТЕЛЬНО: Анализ и планирование

#### A. Анализ существующего кода
- [x] Изучить `src/config.py` — текущая система конфигурации (Pydantic Settings)
- [x] Изучить `src/main.py` — как запускается приложение, порядок инициализации
- [x] Изучить `scripts/load_knowledge_base.py` — единственный существующий CLI-скрипт
- [x] Изучить `Makefile` — существующие команды
- [x] Проверить `src/tasks/celery_app.py` — конфигурация Celery для бэкап-задачи

**Команды для поиска:**
```bash
# Текущая конфигурация
grep -rn "class.*Settings" src/config.py
# Как запускается приложение
head -100 src/main.py
# Существующие CLI-скрипты
ls scripts/
# Makefile команды
cat Makefile
# Celery задачи
ls src/tasks/
```

#### B. Анализ зависимостей
- [x] Нужны ли новые Python-пакеты? (`click` или `typer` для CLI)
- [x] Нужны ли новые env variables? (BACKUP_DIR, BACKUP_S3_BUCKET)
- [x] Нужны ли миграции БД? (нет для этой фазы)

**Новые пакеты:** `typer` (CLI framework) или `click`
**Новые env variables:** `BACKUP_DIR`, `BACKUP_RETENTION_DAYS`
**Новые tools:** нет
**Миграции БД:** нет

#### C. Проверка архитектуры
- [x] CLI-инструмент — синхронный или async? (typer поддерживает async)
- [x] Бэкап через Celery beat или системный cron?
- [x] Валидация — при старте или отдельная команда?

**Референс-модуль:** `scripts/load_knowledge_base.py` (паттерн CLI-скрипта)

**Цель:** Понять существующие паттерны проекта ПЕРЕД написанием кода.

**Заметки для переиспользования:** -

---

### 1.1 Валидация конфигурации при старте приложения
- [x] Добавить в `src/config.py` метод `Settings.validate_required()` — проверяет наличие и формат всех обязательных переменных
- [x] Проверять: `ANTHROPIC_API_KEY` (не пустой), `DATABASE_URL` (формат postgresql://), `REDIS_URL` (формат redis://), `STORE_API_URL` (валидный URL), `GOOGLE_APPLICATION_CREDENTIALS` (файл существует)
- [x] Выводить **человекочитаемые** сообщения об ошибках, например: `❌ ANTHROPIC_API_KEY не задан. Установите: export ANTHROPIC_API_KEY=sk-ant-...`
- [x] Вызывать `Settings.validate_required()` в `src/main.py` ДО инициализации компонентов
- [x] Написать unit-тесты: `tests/unit/test_config_validation.py`

**Файлы:** `src/config.py`, `src/main.py`, `tests/unit/test_config_validation.py`
**Заметки:** Pydantic validators уже проверяют типы, но не проверяют семантику (файл существует, URL доступен). Добавить именно семантические проверки.

---

### 1.2 Скелет CLI-инструмента `call-center-admin`
- [x] Создать `src/cli/__init__.py` и `src/cli/main.py` с Typer-приложением
- [x] Добавить в `pyproject.toml` точку входа: `[project.scripts] call-center-admin = "src.cli.main:app"`
- [x] Реализовать команду `call-center-admin version` — показать версию приложения
- [x] Реализовать команду `call-center-admin config check` — вызывает `Settings.validate_required()` и показывает статус каждого параметра
- [x] Реализовать команду `call-center-admin config show` — показывает текущую конфигурацию (с маскированием секретов: `sk-ant-***`)
- [x] Добавить `typer` в зависимости проекта
- [x] Написать unit-тесты: `tests/unit/test_cli.py`

**Файлы:** `src/cli/__init__.py`, `src/cli/main.py`, `pyproject.toml`, `tests/unit/test_cli.py`
**Заметки:** Typer — надстройка над Click с поддержкой type hints. Паттерн: группы команд через `typer.Typer()` + `app.add_typer()`.

---

### 1.3 CLI: команда для работы с базой данных
- [x] Создать `src/cli/db.py` с группой команд `db`
- [x] Реализовать `call-center-admin db backup` — выполняет `pg_dump` в указанную директорию с таймстампом в имени файла
- [x] Реализовать `call-center-admin db backup --compress` — gzip-сжатие дампа
- [x] Реализовать `call-center-admin db restore <file>` — восстановление из дампа (с подтверждением)
- [x] Реализовать `call-center-admin db migrations status` — показать текущую ревизию Alembic
- [x] Написать тесты для парсинга аргументов и формирования команд

**Файлы:** `src/cli/db.py`, `tests/unit/test_cli_db.py`
**Заметки:** `pg_dump` вызывается через `subprocess.run()`. DATABASE_URL парсится из Settings.

---

### 1.4 CLI: команды для аналитики
- [x] Создать `src/cli/analytics.py` с группой команд `stats`
- [x] Реализовать `call-center-admin stats today` — сводка за сегодня (звонки, resolved %, transfers, avg quality)
- [x] Реализовать `call-center-admin stats recalculate --date YYYY-MM-DD` — пересчёт `daily_stats` за указанную дату (вызов Celery task)
- [x] Реализовать `call-center-admin calls list --date YYYY-MM-DD --limit 20` — список звонков
- [x] Реализовать `call-center-admin calls show <call_id>` — детали звонка (транскрипция, tool calls, quality)

**Файлы:** `src/cli/analytics.py`, `tests/unit/test_cli_analytics.py`
**Заметки:** Переиспользовать логику из `src/api/analytics.py`. Формат вывода — таблица (typer + rich).

---

### 1.5 CLI: управление промптами
- [x] Создать `src/cli/prompts.py` с группой команд `prompts`
- [x] Реализовать `call-center-admin prompts list` — список версий промптов
- [x] Реализовать `call-center-admin prompts activate <version_id>` — активация версии
- [x] Реализовать `call-center-admin prompts rollback` — откат к предыдущей активной версии

**Файлы:** `src/cli/prompts.py`, `tests/unit/test_cli_prompts.py`
**Заметки:** Переиспользовать логику из `src/api/prompts.py`.

---

### 1.6 Автоматические бэкапы PostgreSQL
- [x] Создать `src/tasks/backup.py` — Celery-задача `backup_database()`
- [x] Бэкап по расписанию: ежедневно в 04:00 (Celery beat)
- [x] Хранение: директория из `BACKUP_DIR` env variable, формат `callcenter_YYYY-MM-DD_HHMMSS.sql.gz`
- [x] Ротация: удаление бэкапов старше `BACKUP_RETENTION_DAYS` (по умолчанию 7)
- [x] Логирование: structured JSON с размером файла и длительностью
- [x] Добавить конфигурацию в `src/config.py`: `BackupSettings(backup_dir, retention_days)`
- [x] Написать тесты: `tests/unit/test_backup.py`

**Файлы:** `src/tasks/backup.py`, `src/config.py`, `tests/unit/test_backup.py`
**Заметки:** Паттерн — аналогично `src/tasks/daily_stats.py` (Celery beat schedule). `pg_dump` через subprocess.

---

### 1.7 Обновление Makefile
- [x] Добавить команду `make backup` — ручной запуск бэкапа
- [x] Добавить команду `make config-check` — проверка конфигурации
- [x] Добавить команду `make cli-help` — показать справку CLI

**Файлы:** `Makefile`
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
   git commit -m "checklist(admin-convenience): phase-1 config validation and CLI foundation completed"
   ```
5. Обнови PROGRESS.md:
   - Текущая фаза: 2
   - Добавь запись в историю
6. Открой следующую фазу и продолжи работу
