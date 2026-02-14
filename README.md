# Call Center AI

Система автоматизации обработки входящих телефонных звонков для интернет-магазина шин (Украина). ИИ-агент принимает звонки, ведёт диалог на украинском языке, подбирает шины, оформляет заказы, записывает на шиномонтаж.

## Архитектура

```
Клиент -> SIP Provider -> Asterisk 20 -> AudioSocket TCP :9092 -> Call Processor (Python)
                                                                      |-- STT (Google Cloud, streaming gRPC)
                                                                      |-- LLM Agent (Claude API, tool calling)
                                                                      |-- TTS (Google Cloud, uk-UA voice)
                                                                      |-- Tools -> Store API (REST)
                                                                      |-- Redis (session state, TTL 1800s)
                                                                      +-- PostgreSQL (logs, analytics)
```

AudioSocket protocol: `[type:1B][length:2B BE][payload:NB]`. Audio: 16kHz, 16-bit signed linear PCM.

## Tech Stack

- **Python 3.12+**, asyncio, FastAPI, uvicorn
- **Asterisk 20** AudioSocket
- **Google Cloud** STT v2 (streaming gRPC), TTS Neural2
- **Claude API** (Anthropic) — LLM agent с tool calling
- **PostgreSQL 16** + pgvector — логи, аналитика, embeddings
- **Redis 7** — сессии звонков (TTL 1800s)
- **Celery** — фоновые задачи (quality evaluation, stats, retention)
- **Prometheus + Grafana** — мониторинг

## Quick Start (разработка)

```bash
# 1. Поднять PostgreSQL и Redis
docker compose -f docker-compose.dev.yml up -d

# 2. Создать virtualenv
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,test]"

# 3. Настроить переменные окружения
cp .env.example .env.local
# Отредактировать .env.local (API ключи, пути к credentials)
set -a; . ./.env.local; set +a

# 4. Запустить приложение
python -m src.main
```

## Quick Start (Docker)

```bash
# Создать .env с секретами
cp .env.example .env

# Собрать и запустить все сервисы
docker compose up -d

# Проверить статус
docker compose ps
curl http://localhost:8080/health
```

Сервисы: call-processor (:9092, :8080), PostgreSQL (:5432), Redis (:6379), Prometheus (:9090), Grafana (:3000), Celery worker + beat.

## Структура проекта

```
src/
  core/           # AudioSocket сервер, сессии, pipeline (STT->LLM->TTS)
  agent/          # LLM агент, промпты, tools, A/B тестирование
  stt/            # Speech-to-Text (Google Cloud, Whisper)
  tts/            # Text-to-Speech (Google Cloud)
  store_client/   # HTTP клиент Store API (circuit breaker)
  api/            # REST API (health, metrics, analytics, admin)
  tasks/          # Celery tasks (quality, stats, retention)
  knowledge/      # RAG: embeddings + search
  logging/        # Structured logging, PII sanitizer
  monitoring/     # Prometheus metrics
  config.py       # Конфигурация (pydantic-settings)
migrations/       # Alembic миграции
asterisk/         # Конфигурация Asterisk
knowledge_base/   # Markdown-файлы базы знаний (шины, FAQ)
tests/            # Unit и integration тесты
doc/              # Документация (архитектура, API, безопасность)
```

## Тестирование

```bash
pytest tests/unit/                        # Unit-тесты
pytest tests/integration/                 # Integration (нужен Docker)
pytest tests/ --cov=src --cov-report=html # С покрытием
```

## Линтинг

```bash
ruff check src/ && ruff format src/
mypy src/ --strict
```

## Документация

- [Навигация по документации](doc/README.md)
- [Обзор проекта](doc/development/00-overview.md)
- [API спецификация](doc/development/api-specification.md)
- [Архитектура (SAD)](doc/technical/)
- [Безопасность (STRIDE)](doc/security/)

## Contributing

1. Установите dev-зависимости: `pip install -e ".[dev,test]"`
2. Перед коммитом: `ruff check src/ && mypy src/ --strict`
3. Тесты: `pytest tests/unit/`
4. Для ИИ-агентов: см. [CLAUDE.md](CLAUDE.md)

## Лицензия

MIT
