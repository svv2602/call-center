# Production Readiness — Довести проект до запускаемого состояния

## Цель
Привести проект Call Center AI в состояние, когда `git clone && docker compose up` даёт работающую систему. Исправить все инфраструктурные проблемы, выявленные при внешнем аудите.

## Критерии успеха
- [ ] `docker build .` собирает образ без ошибок
- [ ] `docker compose up` поднимает все сервисы (call-processor, postgres, redis, prometheus, grafana, celery)
- [ ] CI (GitHub Actions) запускается на push и PR
- [ ] `pytest tests/unit/` проходит из venv одной командой
- [ ] README.md содержит quickstart для разработчика
- [ ] Партиции PostgreSQL создаются автоматически
- [ ] Embeddings работают без несогласованных зависимостей

## Фазы работы
1. [Dockerfile и сборка образа](phase-01-dockerfile.md) — создать multi-stage Dockerfile, проверить docker build
2. [CI/CD и ветки Git](phase-02-ci-git.md) — исправить main/master, починить CI workflow
3. [README и quickstart](phase-03-readme.md) — создать README.md с инструкциями для разработчика
4. [Docker Compose: внешние сервисы](phase-04-compose-services.md) — добавить mock store-api и конфигурацию Asterisk
5. [Выравнивание Python-версии](phase-05-python-version.md) — согласовать Python 3.12 vs 3.13 в pyproject/CI/Docker
6. [Автоматизация партиций PostgreSQL](phase-06-partitions.md) — Celery task для создания партиций + политика ретеншна
7. [Embeddings: согласование провайдера](phase-07-embeddings.md) — решить OpenAI vs Google, задокументировать
8. [Тестовая инфраструктура](phase-08-test-infra.md) — один запуск тестов из коробки, Makefile/scripts

## Источник требований
Внешний аудит проекта (февраль 2026) + анализ кодовой базы.

## Правила переиспользования кода

### ОБЯЗАТЕЛЬНО перед реализацией:
1. **Поиск существующего функционала** — перед написанием нового кода ВСЕГДА ищи похожий существующий код
2. **Анализ паттернов** — изучи как реализованы похожие фичи в проекте
3. **Переиспользование модулей** — используй существующие модули, базовые классы, утилиты

### Где искать:
```
src/
├── core/                # Ядро обработки звонков
│   ├── audio_socket.py  # AudioSocket сервер — паттерн TCP-обработки
│   ├── call_session.py  # Управление сессией — паттерн state machine
│   └── pipeline.py      # STT → LLM → TTS — паттерн оркестрации
├── stt/                 # Speech-to-Text — паттерн абстракции внешних сервисов
│   ├── base.py          # Абстрактный интерфейс (Protocol)
│   └── google_stt.py    # Реализация Google Cloud STT
├── tts/                 # Text-to-Speech — аналогичный паттерн
│   ├── base.py
│   └── google_tts.py
├── agent/               # LLM агент
│   ├── agent.py         # Логика агента — паттерн tool calling
│   ├── prompts.py       # Системные промпты
│   └── tools.py         # Tool definitions (канонический список: doc/development/00-overview.md)
├── api/                 # REST API (FastAPI)
├── store_client/        # Клиент Store API — паттерн circuit breaker + retry
│   └── client.py
├── tasks/               # Celery tasks (quality, stats, retention)
│   ├── celery_app.py    # Конфигурация + beat schedule
│   ├── data_retention.py
│   ├── daily_stats.py
│   └── quality_evaluator.py
├── knowledge/           # RAG: embeddings + search
│   ├── embeddings.py    # OpenAI API (ВНИМАНИЕ: несогласованность стека)
│   └── search.py
└── config.py            # Конфигурация через env variables (pydantic-settings)
```

### Чеклист перед написанием кода:
- [ ] Искал похожий функционал в codebase?
- [ ] Изучил паттерны из похожих файлов?
- [ ] Переиспользую существующие модули/утилиты?
- [ ] Соблюдаю conventions проекта?

## Правила кода

### Архитектурные паттерны проекта:

| Паттерн | Где применяется | Пример |
|---------|----------------|--------|
| Protocol (абстракция) | STT, TTS, LLM | `class STTEngine(Protocol)` |
| Circuit Breaker | Store API | `aiobreaker.CircuitBreaker(fail_max=5, timeout=30)` |
| Structured JSON logs | Все компоненты | `{"call_id": "...", "component": "...", "event": "..."}` |
| PII Sanitizer | Логирование | `PIISanitizer.sanitize(text)` — маскирует телефоны, имена |
| Graceful degradation | Внешние сервисы | Сбой → переключение на оператора, не обрыв звонка |
| TTL на сессиях | Redis | `setex("call_session:{uuid}", ttl=1800, ...)` |
| Idempotency-Key | Store API mutations | POST /orders, POST /orders/{id}/confirm |
| pydantic-settings | Конфигурация | Каждый компонент — свой BaseSettings с env_prefix |

### Async паттерны:
- Все I/O операции — `async/await`
- Каждый звонок — отдельная `asyncio.Task`
- Streaming: STT (gRPC), TTS (по предложениям), LLM (token streaming)

### Чеклист:
- [ ] Все I/O через async/await?
- [ ] call_id пробрасывается через все компоненты?
- [ ] PII маскируется в логах?
- [ ] Ошибки внешних сервисов обработаны (fallback → оператор)?
- [ ] mypy --strict проходит?
- [ ] ruff check проходит?

## Правила тестирования

### Для каждого модуля:
- [ ] Unit-тесты (pytest, mock внешних API)
- [ ] Adversarial-тесты для agent (prompt injection, невалидные параметры)
- [ ] Проверка PII sanitizer для новых полей

### Запуск:
```bash
pytest tests/unit/                        # unit
pytest tests/integration/                 # integration (нужен Docker)
pytest tests/ --cov=src --cov-report=html # с покрытием
```

## Начало работы
Для начала или продолжения работы прочитай PROGRESS.md
