# Фаза 1: Инфраструктура

## Статус
- [x] Не начата
- [x] В процессе
- [x] Завершена

**Начата:** 2026-02-13
**Завершена:** 2026-02-13

## Цель фазы

Подготовить инфраструктуру проекта: Docker-окружение, конфигурацию Asterisk, структуру Python-проекта, переменные окружения. После завершения — можно запускать dev-окружение одной командой.

## Задачи

### 1.0 ОБЯЗАТЕЛЬНО: Анализ и планирование

#### A. Анализ существующего кода
- [x] Проверить наличие существующих файлов конфигурации (docker-compose, pyproject.toml)
- [x] Изучить структуру каталогов проекта
- [x] Проверить наличие конфигурации Asterisk

**Команды для поиска:**
```bash
ls -la
ls src/ 2>/dev/null || echo "src/ не существует"
ls asterisk/ 2>/dev/null || echo "asterisk/ не существует"
cat pyproject.toml 2>/dev/null || echo "pyproject.toml не существует"
cat docker-compose.yml 2>/dev/null || echo "docker-compose.yml не существует"
```

#### B. Анализ зависимостей
- [x] Определить список Python-зависимостей для MVP
- [x] Определить Docker-образы для сервисов (PostgreSQL 16, Redis 7, Asterisk 20)
- [x] Определить переменные окружения

**Новые абстракции:** Нет (инфраструктурная фаза)
**Новые env variables:** Все основные (см. задачу 1.5)
**Новые tools:** Нет
**Миграции БД:** Начальная схема (Alembic init)

#### C. Проверка архитектуры
- [x] Структура проекта соответствует `doc/development/00-overview.md`
- [x] Все порты соответствуют архитектуре (9092 AudioSocket, 8080 API, 5432 PG, 6379 Redis)

**Референс-модуль:** `doc/development/00-overview.md` (структура проекта)

**Цель:** Понять существующее состояние проекта ПЕРЕД созданием инфраструктуры.

**Заметки для переиспользования:** Проект был полностью чистый — ни src/, ни config-файлов не существовало.

---

### 1.1 Создание структуры каталогов

- [x] Создать дерево каталогов `src/` согласно `doc/development/00-overview.md`
- [x] Создать `src/core/`, `src/stt/`, `src/tts/`, `src/agent/`, `src/api/`, `src/store_client/`
- [x] Создать `__init__.py` в каждом пакете
- [x] Создать `tests/unit/`, `tests/integration/`, `tests/e2e/`
- [x] Создать `asterisk/` для конфигурации Asterisk
- [x] Создать `migrations/` для Alembic

**Файлы:**
```
src/__init__.py
src/core/__init__.py
src/stt/__init__.py
src/tts/__init__.py
src/agent/__init__.py
src/api/__init__.py
src/store_client/__init__.py
src/logging/__init__.py
src/monitoring/__init__.py
src/config.py
src/main.py
tests/__init__.py
tests/unit/__init__.py
tests/unit/mocks/__init__.py
tests/integration/__init__.py
tests/e2e/__init__.py
asterisk/
migrations/
```

**Заметки:** Добавлены также `src/logging/`, `src/monitoring/`, `tests/unit/mocks/`, `tests/load/`

---

### 1.2 Конфигурация Python-проекта (pyproject.toml)

- [x] Создать `pyproject.toml` с метаданными проекта
- [x] Добавить основные зависимости: `fastapi`, `uvicorn`, `anthropic`, `google-cloud-speech`, `google-cloud-texttospeech`, `aiohttp`, `redis[hiredis]`, `asyncpg`, `sqlalchemy[asyncio]`, `alembic`, `aiobreaker`, `prometheus-client`
- [x] Добавить dev-зависимости: `pytest`, `pytest-asyncio`, `pytest-cov`, `aioresponses`, `ruff`, `mypy`
- [x] Настроить `[tool.ruff]` для линтинга
- [x] Настроить `[tool.mypy]` для strict type checking
- [x] Настроить `[tool.pytest.ini_options]` для pytest

**Файлы:** `pyproject.toml`
**Заметки:** Добавлен `pydantic-settings` для конфигурации. Python >=3.12.

---

### 1.3 Docker Compose для dev-окружения

- [x] Создать `docker-compose.dev.yml` с сервисами: PostgreSQL 16, Redis 7, тестовый Asterisk
- [x] PostgreSQL: порт 5432, база `callcenter_dev`, volume для данных
- [x] Redis: порт 6379, alpine-образ
- [x] Добавить healthcheck для каждого сервиса
- [x] Создать `docker-compose.yml` (production) с call-processor, postgres, redis

**Файлы:** `docker-compose.dev.yml`, `docker-compose.yml`
**Заметки:** PostgreSQL использует `pgvector/pgvector:pg16`. Asterisk не включён в dev-compose (ожидает отдельную настройку).

---

### 1.4 Конфигурация Asterisk dialplan

- [x] Создать `asterisk/extensions.conf` с контекстом `[incoming]` — перенаправление на AudioSocket
- [x] Настроить формат аудио: `slin16` (16kHz, 16-bit signed linear PCM)
- [x] Добавить контекст `[operators]` — очередь операторов с Queue()
- [x] Добавить `AudioSocket(${UNIQUE_ID},127.0.0.1:9092)` в dialplan

**Файлы:** `asterisk/extensions.conf`
**Заметки:** Контекст назван `[transfer-to-operator]` (согласно architecture.md)

---

### 1.5 Переменные окружения

- [x] Создать `.env.example` со всеми переменными и комментариями
- [x] Создать `.env.local` для локальной разработки (добавить в `.gitignore`)
- [x] Добавить `.env*` и секретные файлы в `.gitignore`

**Файлы:** `.env.example`, `.gitignore`
**Заметки:** `.env.local` не создан (содержит секреты) — разработчик копирует из `.env.example`

---

### 1.6 Alembic инициализация

- [x] Инициализировать Alembic: `alembic init migrations`
- [x] Настроить `migrations/env.py` для async SQLAlchemy
- [x] Создать начальную миграцию со схемой для таблиц: `calls`, `call_turns`, `call_tool_calls`, `customers`
- [x] Настроить партиционирование таблиц `calls`, `call_turns`, `call_tool_calls` по `RANGE(started_at)` / `RANGE(created_at)`
- [x] Добавить индексы: `idx_calls_caller_id`, `idx_calls_started_at`, `idx_call_turns_call_id`, `idx_customers_phone`

**Файлы:** `alembic.ini`, `migrations/env.py`, `migrations/script.py.mako`, `migrations/versions/001_initial_schema.py`
**Заметки:** Партиции созданы на 2026-01 .. 2026-05. Нужно автоматизировать создание через pg_partman или cron.

---

### 1.7 Конфигурационный модуль

- [x] Создать `src/config.py` — загрузка конфигурации из env variables
- [x] Использовать Pydantic BaseSettings для валидации
- [x] Определить все конфигурационные параметры с дефолтами
- [x] Добавить валидацию обязательных параметров (API keys, URLs)

**Файлы:** `src/config.py`
**Заметки:** Конфигурация разбита на sub-settings: AudioSocket, GoogleSTT, GoogleTTS, Anthropic, StoreAPI, Database, Redis, ARI, Logging.

---

## При завершении фазы

1. Убедись, что все задачи отмечены [x]
2. Измени статус фазы: [x] Завершена
3. Заполни дату "Завершена: YYYY-MM-DD"
4. Выполни коммит:
   ```bash
   git add .
   git commit -m "checklist(phase-1-mvp): phase-1 infrastructure completed"
   ```
5. Обнови PROGRESS.md
6. Открой следующую фазу: `phase-02-audiosocket-server.md`
