# Развёртывание Call Processor

Пошаговая инструкция по развёртыванию Call Center AI на сервере.

## Содержание

- [1. Требования](#1-требования)
- [2. Варианты развёртывания](#2-варианты-развёртывания)
- [3. Подготовка сервера](#3-подготовка-сервера)
- [4. Подготовка аккаунтов и ключей](#4-подготовка-аккаунтов-и-ключей)
- [5. Клонирование и настройка](#5-клонирование-и-настройка)
- [6. Переменные окружения (.env)](#6-переменные-окружения-env)
- [7. Запуск — Docker Compose (продакшен)](#7-запуск--docker-compose-продакшен)
- [8. Миграции базы данных](#8-миграции-базы-данных)
- [9. Проверка работоспособности](#9-проверка-работоспособности)
- [10. Подключение Asterisk](#10-подключение-asterisk)
- [11. Мониторинг (Prometheus + Grafana)](#11-мониторинг-prometheus--grafana)
- [12. Бэкапы](#12-бэкапы)
- [13. Безопасность](#13-безопасность)
- [14. Обновление](#14-обновление)
- [15. Откат](#15-откат)
- [16. Локальная разработка](#16-локальная-разработка)
- [17. Staging-окружение](#17-staging-окружение)
- [18. Kubernetes (масштабирование)](#18-kubernetes-масштабирование)
- [19. Troubleshooting](#19-troubleshooting)

---

## 1. Требования

### Сервер

| Параметр | MVP (до 30 звонков) | Продакшен (30–200 звонков) |
|---|---|---|
| CPU | 4 vCPU | 8+ vCPU |
| RAM | 8 GB | 16+ GB |
| Диск | 50 GB SSD | 100+ GB SSD |
| ОС | Ubuntu 22.04 LTS | Ubuntu 22.04 LTS |
| Сеть | LAN с Asterisk, интернет 10 Mbit/s | LAN с Asterisk, интернет 20+ Mbit/s |

### Софт

| Компонент | Версия | Проверка |
|---|---|---|
| Docker | 24+ | `docker --version` |
| Docker Compose | v2+ | `docker compose version` |
| Git | любая | `git --version` |

### Сетевые порты

| Порт | Протокол | Назначение | Доступ |
|---|---|---|---|
| 9092 | TCP | AudioSocket (приём звонков от Asterisk) | Только LAN / VPN |
| 8080 | TCP | REST API, Admin UI, метрики Prometheus | Только LAN |
| 3000 | TCP | Grafana | За reverse proxy |
| 9090 | TCP | Prometheus | Только LAN |
| 9093 | TCP | Alertmanager | Только LAN |
| 5555 | TCP | Flower (мониторинг Celery) | Только LAN |

### Внешние сервисы

| Сервис | Что нужно | Как получить |
|---|---|---|
| Google Cloud | Service Account + JSON-ключ | [console.cloud.google.com](https://console.cloud.google.com) |
| Anthropic | API-ключ | [console.anthropic.com](https://console.anthropic.com) |
| OpenAI (опц.) | API-ключ | [platform.openai.com](https://platform.openai.com) |
| DeepSeek (опц.) | API-ключ | [platform.deepseek.com](https://platform.deepseek.com) |
| Google Gemini (опц.) | API-ключ | [aistudio.google.com](https://aistudio.google.com) |
| Telegram | Bot token + Chat ID | Для алертов через Alertmanager |

---

## 2. Варианты развёртывания

### Вариант A: Минимальный (MVP)

Один сервер, всё в Docker Compose. Asterisk — отдельный существующий сервер.

```
┌──────────────────────────────────────────────────┐
│  Новый сервер (4 vCPU, 8GB RAM)                  │
│                                                  │
│  Docker Compose:                                 │
│  ┌─────────────────┐  ┌────────────┐             │
│  │ Call Processor   │  │ Store API  │             │
│  │ :9092 :8080      │  │ :3000      │             │
│  └────────┬────────┘  └────────────┘             │
│           │                                      │
│  ┌────────┴────────┐  ┌────────────┐             │
│  │ PostgreSQL 16   │  │ Redis 7    │             │
│  │ + pgvector      │  │            │             │
│  └─────────────────┘  └────────────┘             │
│                                                  │
│  ┌─────────────────┐  ┌────────────┐             │
│  │ Prometheus       │  │ Grafana    │             │
│  └─────────────────┘  └────────────┘             │
│                                                  │
│  ┌─────────────────┐  ┌────────────┐             │
│  │ Celery Worker   │  │ Celery Beat│             │
│  └─────────────────┘  └────────────┘             │
└──────────────────────────────────────────────────┘
         ▲ TCP :9092 (AudioSocket)
         │
┌────────┴──────────┐
│ Asterisk 20 (сущ.)│
└───────────────────┘
```

**Стоимость:** ~$40–80/мес (VPS).

### Вариант B: Масштабируемый

Несколько серверов, PostgreSQL replica, Redis Cluster. Описание — `doc/technical/deployment.md`.

### Вариант C: С GPU (фаза 4)

Самостоятельный Whisper вместо Google STT. Описание — `doc/technical/deployment.md`.

---

## 3. Подготовка сервера

```bash
# Обновить систему
sudo apt update && sudo apt upgrade -y

# Установить Docker
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
# Перелогиниться для применения группы

# Проверить
docker --version          # Docker 24+
docker compose version    # Docker Compose v2+

# Установить Git
sudo apt install -y git
```

### Firewall

```bash
# Разрешить SSH
sudo ufw allow 22/tcp

# AudioSocket — только от Asterisk
sudo ufw allow from <asterisk-ip> to any port 9092 proto tcp

# API/Admin (если нужен доступ извне — через reverse proxy)
# sudo ufw allow from <office-ip> to any port 8080 proto tcp

# Grafana (если нужен прямой доступ)
# sudo ufw allow from <office-ip> to any port 3000 proto tcp

sudo ufw enable
```

---

## 4. Подготовка аккаунтов и ключей

### Google Cloud

1. Создать проект в [Google Cloud Console](https://console.cloud.google.com)
2. Включить API:
   - Cloud Speech-to-Text API
   - Cloud Text-to-Speech API
3. Создать Service Account:
   - Роли: `Cloud Speech-to-Text User`, `Cloud Text-to-Speech User`
4. Сгенерировать JSON-ключ и скачать

### Anthropic

1. Зарегистрироваться на [console.anthropic.com](https://console.anthropic.com)
2. Создать API-ключ
3. Проверить лимиты (достаточно для планируемой нагрузки)

### Telegram (для алертов)

1. Создать бота через [@BotFather](https://t.me/BotFather), получить `BOT_TOKEN`
2. Создать группу/канал, добавить бота, получить `CHAT_ID`

---

## 5. Клонирование и настройка

```bash
# Клонировать репозиторий
git clone <repo-url> /opt/call-center
cd /opt/call-center

# Создать .env из шаблона
cp .env.example .env
chmod 600 .env

# Разместить GCP-ключ
mkdir -p secrets
cp /path/to/gcp-service-account.json secrets/gcp-key.json
chmod 600 secrets/gcp-key.json
```

---

## 6. Переменные окружения (.env)

Отредактировать `/opt/call-center/.env`. Ниже — все переменные с пояснениями.

### Обязательные (система не запустится без них)

```bash
# ── Anthropic (Claude) ─────────────────────────
ANTHROPIC_API_KEY=sk-ant-...               # API-ключ Claude
ANTHROPIC_MODEL=claude-sonnet-4-5-20250929   # Модель LLM

# ── Google Cloud ───────────────────────────────
GOOGLE_APPLICATION_CREDENTIALS=/secrets/gcp-key.json  # Путь внутри контейнера

# ── База данных ────────────────────────────────
POSTGRES_PASSWORD=<надёжный-пароль-32-символа>
DATABASE_URL=postgresql+asyncpg://callcenter:${POSTGRES_PASSWORD}@postgres:5432/callcenter

# ── Redis ──────────────────────────────────────
REDIS_URL=redis://redis:6379/0

# ── Админ-панель ───────────────────────────────
ADMIN_JWT_SECRET=<случайная-строка-64-символа>   # НЕ оставлять дефолт!
ADMIN_USERNAME=admin
ADMIN_PASSWORD=<надёжный-пароль>
```

### Store API

```bash
STORE_API_URL=http://store-api:3000/api/v1
STORE_API_KEY=<ключ-api-магазина>
STORE_API_TIMEOUT=5
```

### Asterisk ARI

```bash
ARI_URL=http://<asterisk-ip>:8088/ari
ARI_USER=callcenter
ARI_PASSWORD=<ari-пароль>
```

### AudioSocket

```bash
AUDIOSOCKET_HOST=0.0.0.0
AUDIOSOCKET_PORT=9092
```

### Google STT/TTS (тонкая настройка)

```bash
GOOGLE_STT_LANGUAGE_CODE=uk-UA
GOOGLE_STT_ALTERNATIVE_LANGUAGES=ru-RU
GOOGLE_TTS_VOICE=uk-UA-Standard-A
GOOGLE_TTS_SPEAKING_RATE=1.0
```

### Celery (фоновые задачи)

```bash
CELERY_BROKER_URL=redis://redis:6379/1
CELERY_RESULT_BACKEND=redis://redis:6379/1
QUALITY_LLM_MODEL=claude-haiku-4-5-20251001
```

### Скрапер (автоимпорт статей)

```bash
SCRAPER_ENABLED=false                         # Включить скрапер
SCRAPER_AUTO_APPROVE=false                    # false = модерация, true = автопубликация
SCRAPER_BASE_URL=https://prokoleso.ua         # Базовый URL сайта
SCRAPER_INFO_PATH=/ua/info/                   # Путь к разделу статей
SCRAPER_MAX_PAGES=3                           # Кол-во страниц листинга для обхода
SCRAPER_REQUEST_DELAY=2.0                     # Задержка между запросами (сек)
SCRAPER_LLM_MODEL=claude-haiku-4-5-20251001   # Модель для обработки статей
SCRAPER_SCHEDULE_HOUR=6                       # Час запуска (Celery Beat)
SCRAPER_SCHEDULE_DAY_OF_WEEK=monday           # День запуска
```

Управление скрапером — через админ-панель: Training → Sources (включение, модерация, ручной запуск).

### LLM-роутинг (мультипровайдерный)

Роутер позволяет направлять LLM-задачи на разные провайдеры (Anthropic, OpenAI, DeepSeek, Gemini) с автоматическим fallback при недоступности. Управление — через админ-панель: Система → Маршрутизация LLM.

```bash
FF_LLM_ROUTING_ENABLED=false            # Мастер-переключатель (false = прямой вызов Anthropic)

# Дополнительные провайдеры (опционально, включаются в админке)
# OPENAI_API_KEY=sk-...                 # Для GPT-4o / GPT-4o-mini
# DEEPSEEK_API_KEY=sk-...               # Для DeepSeek Chat
# GEMINI_API_KEY=AI...                   # Для Gemini Flash
```

**Как работает:**
- При `FF_LLM_ROUTING_ENABLED=false` — все вызовы идут напрямую в Anthropic (как раньше)
- При `FF_LLM_ROUTING_ENABLED=true` — роутер читает конфиг из Redis (`llm:routing_config`), маршрутизирует задачи по провайдерам, при отказе переключается на fallback
- API-ключи **не хранятся в Redis** — только названия переменных окружения
- Конфиг Redis задаётся через админ-панель или API: `PATCH /admin/llm/config`

**4 типа задач:**

| Задача | По умолчанию | Описание |
|---|---|---|
| `agent` | anthropic-sonnet | Основной диалог с клиентом |
| `article_processor` | anthropic-haiku | Обработка статей скрапера |
| `quality_scoring` | anthropic-haiku | Оценка качества звонков |
| `prompt_optimizer` | anthropic-haiku | Анализ неудачных звонков |

**6 провайдеров:**

| Ключ | Тип | Модель | Env для ключа |
|---|---|---|---|
| `anthropic-sonnet` | anthropic | claude-sonnet-4-5 | `ANTHROPIC_API_KEY` |
| `anthropic-haiku` | anthropic | claude-haiku-4-5 | `ANTHROPIC_API_KEY` |
| `openai-gpt4o` | openai | gpt-4o | `OPENAI_API_KEY` |
| `openai-gpt4o-mini` | openai | gpt-4o-mini | `OPENAI_API_KEY` |
| `deepseek-chat` | deepseek | deepseek-chat | `DEEPSEEK_API_KEY` |
| `gemini-flash` | gemini | gemini-2.0-flash | `GEMINI_API_KEY` |

### Мониторинг

```bash
LOG_LEVEL=INFO
LOG_FORMAT=json
GRAFANA_ADMIN_PASSWORD=<пароль-grafana>
TELEGRAM_BOT_TOKEN=<токен-telegram-бота>
TELEGRAM_CHAT_ID=<id-чата>
```

### Flower (Celery UI)

```bash
FLOWER_PORT=5555
FLOWER_BASIC_AUTH=admin:<пароль>
```

### Redis (сессии)

```bash
REDIS_SESSION_TTL=1800   # 30 мин TTL на сессии звонков
```

### Бэкапы

```bash
BACKUP_BACKUP_DIR=/var/backups/callcenter
BACKUP_RETENTION_DAYS=7
```

### Опциональные

```bash
# OpenAI (для embeddings RAG, фаза 3)
# OPENAI_API_KEY=sk-...
# OPENAI_EMBEDDING_MODEL=text-embedding-3-small

# SMTP (email-отчёты)
# SMTP_HOST=smtp.gmail.com
# SMTP_PORT=587
# SMTP_USER=
# SMTP_PASSWORD=
# SMTP_USE_TLS=true
# SMTP_FROM_ADDRESS=callcenter@example.com
# SMTP_REPORT_RECIPIENTS=admin@example.com

# Feature flags
# FF_STT_PROVIDER=google           # google | whisper
# FF_LLM_ROUTING_ENABLED=false     # true = мультипровайдерный LLM-роутинг (см. раздел выше)
# FF_WHISPER_ROLLOUT_PERCENT=0

# Дополнительные LLM-провайдеры (при FF_LLM_ROUTING_ENABLED=true)
# DEEPSEEK_API_KEY=sk-...
# GEMINI_API_KEY=AI...
```

### Генерация секретов

```bash
# Пароль PostgreSQL
openssl rand -base64 32

# JWT-секрет админ-панели
openssl rand -hex 32

# Пароль Grafana
openssl rand -base64 16
```

---

## 7. Запуск — Docker Compose (продакшен)

```bash
cd /opt/call-center

# Запустить все сервисы
docker compose up -d

# Проверить статус
docker compose ps
```

Ожидаемый вывод:

```
NAME                 STATUS              PORTS
call-processor       Up (healthy)        0.0.0.0:9092->9092, 0.0.0.0:8080->8080
postgres             Up (healthy)        5432/tcp
redis                Up (healthy)        6379/tcp
store-api            Up (healthy)        0.0.0.0:3002->3000
prometheus           Up                  0.0.0.0:9090->9090
grafana              Up                  0.0.0.0:3000->3000
celery-worker        Up                  (очереди: celery, scraper)
celery-beat          Up
flower               Up                  0.0.0.0:5555->5555
alertmanager         Up                  0.0.0.0:9093->9093
```

### Просмотр логов

```bash
# Все сервисы
docker compose logs -f

# Только Call Processor
docker compose logs -f call-processor

# Последние 100 строк
docker compose logs --tail=100 call-processor
```

### Перезапуск отдельного сервиса

```bash
docker compose restart call-processor
```

---

## 8. Миграции базы данных

При первом запуске и после обновлений необходимо применить миграции:

```bash
# Применить все миграции
docker compose exec call-processor alembic upgrade head

# Проверить текущую версию
docker compose exec call-processor alembic current

# Посмотреть историю миграций
docker compose exec call-processor alembic history
```

---

## 9. Проверка работоспособности

### 9.1 Health check (liveness)

```bash
curl -s http://localhost:8080/health | python3 -m json.tool
```

Ожидаемый ответ:

```json
{
    "status": "ok",
    "active_calls": 0,
    "redis": "connected"
}
```

### 9.2 Readiness check

```bash
curl -s http://localhost:8080/health/ready | python3 -m json.tool
```

Ожидаемый ответ:

```json
{
    "status": "ready",
    "redis": "connected",
    "store_api": "reachable",
    "tts_engine": "initialized",
    "claude_api": "reachable",
    "google_stt": "credentials_present"
}
```

Если какой-то компонент не `ready` — проверить логи и соответствующие переменные окружения.

### 9.3 Метрики Prometheus

```bash
curl -s http://localhost:8080/metrics | head -20
```

### 9.4 Админ-панель

Открыть в браузере: `http://<server-ip>:8080/admin`

Логин/пароль — значения из `ADMIN_USERNAME` / `ADMIN_PASSWORD` в `.env`.

### 9.5 AudioSocket

```bash
# Проверить что порт слушает
ss -tlnp | grep 9092
# Ожидание: LISTEN на 0.0.0.0:9092
```

### 9.6 PostgreSQL

```bash
docker compose exec postgres psql -U callcenter -d callcenter -c "SELECT 1"
```

### 9.7 Redis

```bash
docker compose exec redis redis-cli ping
# Ожидание: PONG
```

### 9.8 Store API

```bash
curl -s http://localhost:3002/api/v1/health
```

### 9.9 Smoke-тест (автоматический)

```bash
./scripts/smoke_test_staging.sh
```

---

## 10. Подключение Asterisk

Подробная инструкция — в `asterisk/README.md`.

Краткий чек-лист:

1. Скопировать конфиги из `asterisk/` на сервер Asterisk
2. В `extensions.conf` указать IP Call Processor в `AudioSocket()`
3. Настроить `ari.conf` с тем же паролем, что в `.env` (`ARI_PASSWORD`)
4. Если серверы в разных сетях — настроить WireGuard (см. `asterisk/README.md`)
5. Перезагрузить Asterisk: `asterisk -rx "dialplan reload"`
6. Сделать тестовый звонок

---

## 11. Мониторинг (Prometheus + Grafana)

### Prometheus

URL: `http://<server-ip>:9090`

Конфигурация: `prometheus/prometheus.yml` — скрапит метрики с `call-processor:8080/metrics` каждые 10 сек.

Правила алертов: `prometheus/alerts.yml`.

Ключевые алерты:

| Алерт | Условие | Серьёзность |
|---|---|---|
| `HighTransferRate` | >50% звонков переключены на оператора | high |
| `PipelineErrorsHigh` | >5 ошибок pipeline за 10 мин | high |
| `HighResponseLatency` | p95 ответа > 2500ms | medium |
| `OperatorQueueOverflow` | Очередь > 5 звонков | critical |
| `CeleryWorkerDown` | 0 Celery-воркеров онлайн | high |
| `CircuitBreakerOpen` | Circuit breaker Store API открыт | high |
| `PostgresBackupStale` | Нет бэкапа > 26 часов | critical |

### Grafana

URL: `http://<server-ip>:3000`

Логин: `admin` / значение `GRAFANA_ADMIN_PASSWORD` из `.env`.

Datasource (Prometheus) и дашборды провизионируются автоматически из `grafana/provisioning/`.

### Alertmanager

URL: `http://<server-ip>:9093`

Алерты отправляются в Telegram. Настройка: `alertmanager/config.yml`.

### Flower (Celery)

URL: `http://<server-ip>:5555`

Мониторинг фоновых задач: quality scoring, бэкапы, партиционирование.

---

## 12. Бэкапы

Бэкапы выполняются автоматически через Celery Beat:

| Что | Когда | Куда |
|---|---|---|
| PostgreSQL (`pg_dump`) | Ежедневно 04:00 | `$BACKUP_BACKUP_DIR` |
| Redis (RDB snapshot) | Ежедневно 04:15 | `$BACKUP_BACKUP_DIR` |
| Knowledge base | Еженедельно (вс, 01:00) | `$BACKUP_BACKUP_DIR` |

### Ручной бэкап

```bash
# Через CLI
docker compose exec call-processor call-center-admin db backup --compress

# Или через Make
make backup
```

### Восстановление

```bash
# PostgreSQL
./scripts/backup/restore_postgres.sh <path-to-backup>

# Redis
./scripts/backup/restore_redis.sh <path-to-backup>
```

### Проверка бэкапов

```bash
./scripts/backup/test_backup.sh
```

**Хранение:** 7 дней (настраивается через `BACKUP_RETENTION_DAYS`).

---

## 13. Безопасность

### Чек-лист перед запуском

- [ ] `.env` — права `600`, отсутствует в git
- [ ] `secrets/` — права `600`, отсутствует в git
- [ ] `ADMIN_JWT_SECRET` — **не** дефолтное значение (система не запустится)
- [ ] `POSTGRES_PASSWORD` — случайный, 32+ символов
- [ ] Порт 9092 — закрыт файрволом для внешнего доступа
- [ ] Порт 8080 — закрыт файрволом для внешнего доступа
- [ ] Grafana — за reverse proxy с HTTPS
- [ ] API-ключи **не** попадают в логи

### AudioSocket

AudioSocket передаёт аудио в открытом виде (TCP без шифрования):

| Сценарий | Рекомендация |
|---|---|
| Один сервер | `127.0.0.1` — безопасно |
| Один LAN | WireGuard |
| Разные сети | **Обязательно** VPN/WireGuard |

Подробнее — `asterisk/README.md`, раздел 5.

### PII

- PII sanitizer подключён к логгеру (телефоны, имена маскируются)
- Аудио **не хранится** — только потоковая обработка STT
- Транскрипции хранятся 90 дней

### Ротация секретов

| Секрет | Периодичность |
|---|---|
| ARI-пароль | каждые 180 дней |
| API-ключи (Anthropic, Google, OpenAI, DeepSeek, Gemini) | при компрометации |
| JWT-секрет | при компрометации |
| Пароль PostgreSQL | каждые 365 дней |

---

## 14. Обновление

```bash
cd /opt/call-center

# 1. Получить обновления
git pull

# 2. Пересобрать образ
docker compose build call-processor

# 3. Применить миграции (если есть)
docker compose run --rm call-processor alembic upgrade head

# 4. Перезапустить
docker compose up -d call-processor celery-worker celery-beat

# 5. Проверить
curl -s http://localhost:8080/health/ready
docker compose logs --tail=50 call-processor
```

**Время обновления:** < 2 минуты (zero-downtime при наличии нескольких инстансов).

---

## 15. Откат

### Быстрый откат (< 5 минут)

```bash
# 1. Остановить Call Processor
docker compose down call-processor celery-worker celery-beat

# 2. Вернуться на предыдущую версию
git checkout <previous-tag>

# 3. Пересобрать и запустить
docker compose build call-processor
docker compose up -d call-processor celery-worker celery-beat

# 4. Проверить
curl -s http://localhost:8080/health/ready
```

### Экстренный откат (весь трафик на операторов)

На сервере Asterisk — переключить dialplan:

```ini
; extensions.conf — временно убрать AudioSocket
[incoming]
exten => _X.,1,Answer()
 same => n,Queue(operators,t,,,120)
 same => n,Hangup()
```

```bash
asterisk -rx "dialplan reload"
# Время: < 1 минуты, все звонки идут напрямую операторам
```

---

## 16. Локальная разработка

### Быстрый старт

```bash
# Инфраструктура (PostgreSQL, Redis, Store API)
docker compose -f docker-compose.dev.yml up -d

# Python-окружение
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,test]"

# Загрузить переменные
cp .env.example .env.local
# Отредактировать .env.local
set -a; . ./.env.local; set +a

# Запустить backend
python -m src.main

# Запустить Celery worker (в отдельном терминале)
celery -A src.tasks.celery_app worker -Q celery,scraper -c 1 --loglevel=info
```

### Через start.sh

```bash
# Полная разработка (Docker + backend + Celery + Vite HMR для фронта)
./start.sh dev

# Backend + Celery (Docker + Python, без Vite)
./start.sh backend

# Только инфраструктура
./start.sh docker

# Остановить всё
./start.sh stop
```

**URL в dev-режиме:**

| Сервис | URL |
|---|---|
| Admin UI (Vite HMR) | http://localhost:5173 |
| Backend API | http://localhost:8080 |
| Store API (mock) | http://localhost:3000 |

### Тесты

```bash
make test              # Unit-тесты
make test-integration  # Интеграционные (нужен Docker)
make test-all          # Все + coverage
make lint              # Линтер (ruff)
make typecheck         # Типы (mypy --strict)
make check             # lint + typecheck + test
```

### Makefile

```bash
make install           # Создать venv + установить зависимости
make format            # Автоформатирование
make clean             # Очистка кэшей
make config-check      # Проверка конфигурации
make cli-help          # Справка по CLI
```

---

## 17. Staging-окружение

Staging — полная копия продакшена со смещёнными портами (+10000):

| Сервис | Staging-порт | Production-порт |
|---|---|---|
| API / Admin UI | 18080 | 8080 |
| AudioSocket | 19092 | 9092 |
| PostgreSQL | 15432 | 5432 |
| Redis | 16379 | 6379 |
| Prometheus | 19090 | 9090 |
| Grafana | 13000 | 3000 |

### Запуск staging

```bash
docker compose -f docker-compose.staging.yml --env-file .env.staging up -d --build
```

### Smoke-тест staging

```bash
./scripts/smoke_test_staging.sh
```

Тест проверяет: health, readiness, metrics, admin UI, Store API, Redis, Prometheus.

### Остановка staging

```bash
docker compose -f docker-compose.staging.yml down
```

---

## 18. Kubernetes (масштабирование)

Манифесты в `k8s/`. Для 30+ одновременных звонков.

### Структура

```
k8s/
  call-processor/
    deployment.yml    # 2 реплики, liveness/readiness probes
    service.yml       # ClusterIP :8080, NodePort :9092
    hpa.yml           # Автоскейлинг 2–10 реплик (CPU > 70%)
  configmap.yml       # Переменные окружения
  secrets.yml         # Секреты (base64)
  ingress.yml         # NGINX Ingress + TLS
```

### Ресурсы пода

| Параметр | Request | Limit |
|---|---|---|
| CPU | 250m | 500m |
| Memory | 256Mi | 512Mi |

### Автоскейлинг (HPA)

- Min: 2 реплики
- Max: 10 реплик
- Триггеры: CPU > 70%, Memory > 80%
- Стабилизация scale-down: 300 сек

### Деплой

```bash
kubectl apply -f k8s/configmap.yml
kubectl apply -f k8s/secrets.yml
kubectl apply -f k8s/call-processor/
kubectl apply -f k8s/ingress.yml
```

---

## 19. Troubleshooting

### Call Processor не стартует

```bash
docker compose logs call-processor | head -50
```

Частые причины:

| Ошибка | Причина | Решение |
|---|---|---|
| `ANTHROPIC_API_KEY is empty` | Не задан API-ключ | Заполнить в `.env` |
| `DATABASE_URL must start with postgresql` | Неверный формат URL | Проверить формат |
| `ADMIN_JWT_SECRET equals default` | Дефолтный JWT-секрет | Сгенерировать: `openssl rand -hex 32` |
| `GCP credentials file not found` | Нет JSON-ключа Google | Проверить `secrets/gcp-key.json` |
| `Connection refused` (PostgreSQL) | БД не запустилась | `docker compose logs postgres` |

### AudioSocket: нет подключений от Asterisk

```bash
# 1. Порт слушает?
ss -tlnp | grep 9092

# 2. Asterisk видит Call Processor?
# На Asterisk:
telnet <call-processor-ip> 9092

# 3. Firewall?
sudo iptables -L -n | grep 9092

# 4. IP в dialplan правильный?
asterisk -rx "dialplan show incoming"
```

### Health check возвращает не "ready"

| Компонент | Статус | Решение |
|---|---|---|
| redis: `disconnected` | Redis недоступен | `docker compose logs redis` |
| store_api: `unreachable` | Store API недоступен | `curl http://localhost:3002/api/v1/health` |
| tts_engine: `not_initialized` | Ошибка Google TTS | Проверить GCP-ключ и роли |
| claude_api: `unreachable` | Anthropic API недоступен | Проверить ключ и лимиты |
| google_stt: `no_credentials` | Нет GCP-ключа | Проверить `GOOGLE_APPLICATION_CREDENTIALS` |

### Высокая задержка ответа (> 2 сек)

Бюджет задержки:

| Этап | Норма | Метрика |
|---|---|---|
| AudioSocket → STT | < 50ms | `callcenter_audiosocket_to_stt_ms` |
| STT распознавание | 200–500ms | `callcenter_stt_latency_ms` |
| LLM (TTFT) | 500–1000ms | `callcenter_llm_latency_ms` / `callcenter_llm_provider_latency_ms` |
| TTS синтез | 300–500ms | `callcenter_tts_latency_ms` |
| TTS → AudioSocket | < 50ms | `callcenter_tts_delivery_ms` |
| **Итого** | **< 2000ms** | `callcenter_total_response_latency_ms` |

Если превышает:
- STT > 500ms → проверить сеть до Google, уменьшить chunk size
- LLM > 1000ms → включить streaming, сократить промпт/историю
- TTS > 500ms → включить streaming TTS, использовать кэш

### Утечка памяти

```bash
# Мониторинг
docker stats call-processor

# В Prometheus: process_resident_memory_bytes
```

Типичные причины: сессии без TTL в Redis, аудио-буферы не освобождаются при hangup, история LLM-диалога растёт без ограничения.

### Подробнее

Полный troubleshooting: `doc/development/troubleshooting.md`.
