# Фаза 1: Инфраструктура

## Статус
- [ ] Не начата
- [ ] В процессе
- [ ] Завершена

**Начата:** -
**Завершена:** -

## Цель фазы

Подготовить инфраструктуру проекта: Docker-окружение, конфигурацию Asterisk, структуру Python-проекта, переменные окружения. После завершения — можно запускать dev-окружение одной командой.

## Задачи

### 1.0 ОБЯЗАТЕЛЬНО: Анализ и планирование

#### A. Анализ существующего кода
- [ ] Проверить наличие существующих файлов конфигурации (docker-compose, pyproject.toml)
- [ ] Изучить структуру каталогов проекта
- [ ] Проверить наличие конфигурации Asterisk

**Команды для поиска:**
```bash
ls -la
ls src/ 2>/dev/null || echo "src/ не существует"
ls asterisk/ 2>/dev/null || echo "asterisk/ не существует"
cat pyproject.toml 2>/dev/null || echo "pyproject.toml не существует"
cat docker-compose.yml 2>/dev/null || echo "docker-compose.yml не существует"
```

#### B. Анализ зависимостей
- [ ] Определить список Python-зависимостей для MVP
- [ ] Определить Docker-образы для сервисов (PostgreSQL 16, Redis 7, Asterisk 20)
- [ ] Определить переменные окружения

**Новые абстракции:** Нет (инфраструктурная фаза)
**Новые env variables:** Все основные (см. задачу 1.5)
**Новые tools:** Нет
**Миграции БД:** Начальная схема (Alembic init)

#### C. Проверка архитектуры
- [ ] Структура проекта соответствует `doc/development/00-overview.md`
- [ ] Все порты соответствуют архитектуре (9092 AudioSocket, 8080 API, 5432 PG, 6379 Redis)

**Референс-модуль:** `doc/development/00-overview.md` (структура проекта)

**Цель:** Понять существующее состояние проекта ПЕРЕД созданием инфраструктуры.

**Заметки для переиспользования:** -

---

### 1.1 Создание структуры каталогов

- [ ] Создать дерево каталогов `src/` согласно `doc/development/00-overview.md`
- [ ] Создать `src/core/`, `src/stt/`, `src/tts/`, `src/agent/`, `src/api/`, `src/store_client/`
- [ ] Создать `__init__.py` в каждом пакете
- [ ] Создать `tests/unit/`, `tests/integration/`, `tests/e2e/`
- [ ] Создать `asterisk/` для конфигурации Asterisk
- [ ] Создать `migrations/` для Alembic

**Файлы:**
```
src/__init__.py
src/core/__init__.py
src/stt/__init__.py
src/tts/__init__.py
src/agent/__init__.py
src/api/__init__.py
src/store_client/__init__.py
src/config.py
src/main.py
tests/__init__.py
tests/unit/__init__.py
tests/integration/__init__.py
tests/e2e/__init__.py
asterisk/
migrations/
```

**Заметки:** -

---

### 1.2 Конфигурация Python-проекта (pyproject.toml)

- [ ] Создать `pyproject.toml` с метаданными проекта
- [ ] Добавить основные зависимости: `fastapi`, `uvicorn`, `anthropic`, `google-cloud-speech`, `google-cloud-texttospeech`, `aiohttp`, `redis[hiredis]`, `asyncpg`, `sqlalchemy[asyncio]`, `alembic`, `aiobreaker`, `prometheus-client`
- [ ] Добавить dev-зависимости: `pytest`, `pytest-asyncio`, `pytest-cov`, `aioresponses`, `ruff`, `mypy`
- [ ] Настроить `[tool.ruff]` для линтинга
- [ ] Настроить `[tool.mypy]` для strict type checking
- [ ] Настроить `[tool.pytest.ini_options]` для pytest

**Файлы:** `pyproject.toml`
**Заметки:** Python 3.12+ обязателен

---

### 1.3 Docker Compose для dev-окружения

- [ ] Создать `docker-compose.dev.yml` с сервисами: PostgreSQL 16, Redis 7, тестовый Asterisk
- [ ] PostgreSQL: порт 5432, база `callcenter_dev`, volume для данных
- [ ] Redis: порт 6379, alpine-образ
- [ ] Добавить healthcheck для каждого сервиса
- [ ] Создать `docker-compose.yml` (production) с call-processor, postgres, redis

**Файлы:** `docker-compose.dev.yml`, `docker-compose.yml`
**Заметки:** PostgreSQL с расширением pgvector: `pgvector/pgvector:pg16`

---

### 1.4 Конфигурация Asterisk dialplan

- [ ] Создать `asterisk/extensions.conf` с контекстом `[incoming]` — перенаправление на AudioSocket
- [ ] Настроить формат аудио: `slin16` (16kHz, 16-bit signed linear PCM)
- [ ] Добавить контекст `[operators]` — очередь операторов с Queue()
- [ ] Добавить `AudioSocket(${UNIQUE_ID},127.0.0.1:9092)` в dialplan

**Файлы:** `asterisk/extensions.conf`

**Пример dialplan:**
```ini
[incoming]
exten => _X.,1,NoOp(Incoming call from ${CALLERID(num)})
 same => n,Answer()
 same => n,Set(CHANNEL(audioreadformat)=slin16)
 same => n,Set(CHANNEL(audiowriteformat)=slin16)
 same => n,AudioSocket(${UNIQUE_ID},127.0.0.1:9092)
 same => n,Hangup()

[operators]
exten => _X.,1,NoOp(Transfer to operator queue)
 same => n,Queue(operator-queue,t,,,60)
 same => n,Hangup()
```

**Заметки:** AudioSocket между серверами требует WireGuard/VPN (нет TLS в протоколе)

---

### 1.5 Переменные окружения

- [ ] Создать `.env.example` со всеми переменными и комментариями
- [ ] Создать `.env.local` для локальной разработки (добавить в `.gitignore`)
- [ ] Добавить `.env*` и секретные файлы в `.gitignore`

**Переменные:**
```bash
# Asterisk AudioSocket
AUDIOSOCKET_HOST=0.0.0.0
AUDIOSOCKET_PORT=9092

# Google Cloud
GOOGLE_APPLICATION_CREDENTIALS=/path/to/gcp-key.json
GOOGLE_STT_LANGUAGE_CODE=uk-UA
GOOGLE_STT_ALTERNATIVE_LANGUAGES=ru-RU
GOOGLE_TTS_VOICE=uk-UA-Standard-A

# Claude API (Anthropic)
ANTHROPIC_API_KEY=sk-ant-...
ANTHROPIC_MODEL=claude-sonnet-4-5-20250929

# Store API
STORE_API_URL=http://localhost:3000/api/v1
STORE_API_KEY=...

# PostgreSQL
DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/callcenter

# Redis
REDIS_URL=redis://localhost:6379/0
REDIS_SESSION_TTL=1800

# Asterisk ARI
ARI_URL=http://localhost:8088/ari
ARI_USER=ari_user
ARI_PASSWORD=ari_password

# Monitoring
PROMETHEUS_PORT=8080

# Logging
LOG_LEVEL=INFO
LOG_FORMAT=json
```

**Файлы:** `.env.example`, `.env.local`, `.gitignore`
**Заметки:** Загрузка env: `set -a; . ./.env.local; set +a`

---

### 1.6 Alembic инициализация

- [ ] Инициализировать Alembic: `alembic init migrations`
- [ ] Настроить `migrations/env.py` для async SQLAlchemy
- [ ] Создать начальную миграцию со схемой для таблиц: `calls`, `call_turns`, `call_tool_calls`, `customers`
- [ ] Настроить партиционирование таблиц `calls`, `call_turns`, `call_tool_calls` по `RANGE(started_at)` / `RANGE(created_at)`
- [ ] Добавить индексы: `idx_calls_caller_id`, `idx_calls_started_at`, `idx_call_turns_call_id`, `idx_customers_phone`

**Файлы:** `migrations/env.py`, `migrations/versions/001_initial_schema.py`
**Заметки:** Схема из `doc/technical/data-model.md`

---

### 1.7 Конфигурационный модуль

- [ ] Создать `src/config.py` — загрузка конфигурации из env variables
- [ ] Использовать Pydantic BaseSettings для валидации
- [ ] Определить все конфигурационные параметры с дефолтами
- [ ] Добавить валидацию обязательных параметров (API keys, URLs)

**Файлы:** `src/config.py`
**Заметки:** Без хардкода URL, ключей, пороговых значений

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
