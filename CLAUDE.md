# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Call Center AI — система автоматизации обработки входящих телефонных звонков для интернет-магазина шин (Украина). ИИ-агент принимает звонки, ведёт диалог на украинском языке, подбирает шины, оформляет заказы, записывает на шиномонтаж.

**Status:** Active development. All 4 phases implemented in code (977+ tests, 20 DB migrations, full FastAPI backend, Admin UI, 82 knowledge base articles). Remaining: Grafana UI integration, Whisper GPU deployment, end-to-end testing with live Asterisk.

## Language Convention

All documentation is written in **Russian**. The AI agent speaks **Ukrainian** to customers. When creating or editing docs, write in Russian. When writing system prompts or user-facing text, write in Ukrainian.

## Architecture (Big Picture)

```
Клиент → SIP Provider → Asterisk 20 → AudioSocket TCP :9092 → Call Processor (Python)
                                                                    ├── STT (Google Cloud, streaming gRPC)
                                                                    ├── LLM Agent (Claude API, tool calling)
                                                                    ├── TTS (Google Cloud, uk-UA voice)
                                                                    ├── Tools → Store API (REST)
                                                                    ├── Redis (session state, TTL 1800s)
                                                                    └── PostgreSQL (logs, analytics)
```

- **AudioSocket protocol:** `[type:1B][length:2B BE][payload:NB]`. Types: 0x01=UUID, 0x10=audio, 0x00=hangup, 0xFF=error. Audio: 8kHz, 16-bit signed linear PCM, little-endian.
- **Pipeline flow:** AudioSocket → STT (streaming) → LLM (Claude) → TTS → AudioSocket. Supports barge-in.
- **Multilingual STT:** Primary `uk-UA` + alternative `ru-RU`. Agent always responds in Ukrainian regardless of input language.
- **Session state** is in Redis (stateless Call Processor for horizontal scaling). TTL prevents memory leaks on abnormal disconnects.
- **Circuit breaker** (aiobreaker, fail_max=5, timeout=30s) protects Store API calls.

## Canonical Tool Names

Single source of truth for LLM agent tools is in `doc/development/00-overview.md` (section "Канонический список tools"). When referencing tools in any document, use these exact names:

`get_vehicle_tire_sizes`, `search_tires`, `check_availability`, `transfer_to_operator`, `get_order_status`, `create_order_draft`, `update_order_delivery`, `confirm_order`, `get_pickup_points`, `get_fitting_stations`, `get_fitting_slots`, `book_fitting`, `cancel_fitting`, `get_fitting_price`, `get_customer_bookings`, `search_knowledge_base`

**Important:** The tool is `create_order_draft` (not `create_order`).

## Tech Stack

Python 3.12+, asyncio + FastAPI, Asterisk 20 AudioSocket, Google Cloud STT v2 / TTS Neural2, Claude API (Anthropic), PostgreSQL 16 + pgvector, Redis 7, Prometheus + Grafana, Celery (background tasks).

## Commands

```bash
# Local dev environment
source .venv/bin/activate
set -a; . ./.env.local; set +a                       # Load env vars
python -m src.main                                    # Run backend (port 8080)
cd admin-ui && npm run dev                            # Admin UI dev server (port 5173)
cd admin-ui && npm run build                          # Build Admin UI for prod

# Tests
pytest tests/ -x -q                                   # All tests (680+)
pytest tests/unit/ -x -q                              # Unit tests only
pytest tests/unit/test_audio_socket.py -v             # Single test file
pytest tests/ --cov=src --cov-report=html             # With coverage

# Lint & format
ruff check src/                                       # Lint
ruff format .                                         # Format

# Database
alembic upgrade head                                  # Apply migrations (20 total)
python -m scripts.import_vehicle_db                   # Import vehicle tire DB from CSV

# Knowledge base seed
# Import all .md articles from knowledge_seed/ via Admin UI bulk import
# or API: curl -X POST /knowledge/articles/import -F "files=@file.md"
```

## Documentation Structure

- `doc/business/` — investor/CEO materials (presentation, ROI, roadmap)
- `doc/technical/` — architecture (SAD), data model (ERD), NFR, deployment, sequence diagrams
- `doc/security/` — STRIDE threat model, data policy, risk matrix
- `doc/development/` — implementation specs by phase, API spec, local setup, troubleshooting, glossary
- `audit/` — external audit reports (historical, recommendations already applied to docs)

Key entry points: `doc/README.md` (navigation), `doc/development/00-overview.md` (project overview + canonical tools), `doc/development/api-specification.md` (Store API contract).

## Development Phases

1. **MVP** — tire search, availability check, operator transfer — **done**
2. **Orders** — order status, order creation flow (draft → delivery → confirm) — **done**
3. **Services** — fitting booking, RAG knowledge base consultations — **done**
4. **Analytics** — dashboards, quality scoring, A/B prompt testing, self-hosted Whisper — **mostly done** (Grafana UI and Whisper GPU deploy pending)

## Project Structure

- `src/` — Python/FastAPI backend (port 8080): `core/` (pipeline, AudioSocket, sessions), `agent/` (LLM, tools, A/B testing), `stt/` + `tts/`, `api/` (14 routers), `store_client/`, `knowledge/` (RAG), `tasks/` (Celery), `monitoring/`
- `admin-ui/` — Vite SPA: `src/pages/` (dashboard, calls, operators, training, users, vehicles, settings), i18n (ru/en)
- `migrations/versions/` — 20 Alembic migrations (raw SQL)
- `tests/` — 977+ tests: `unit/`, `integration/`, `e2e/`, `load/`, `chaos/`, `prompt_regression/`
- `scripts/` — import scripts (vehicle DB)
- `knowledge_seed/` — 82 seed articles (.md) for RAG knowledge base, organized by category: `faq/` (20), `guides/` (10), `comparisons/` (10), `brands/` (10), `procedures/` (7), `delivery/` (5), `warranty/` (5), `returns/` (5), `policies/` (5), `general/` (5). File naming: `NN_category_topic.md` (category auto-detected from filename on import).
- `k8s/`, `prometheus/`, `grafana/`, `alertmanager/` — infrastructure configs

## Admin UI — i18n

Админка (`admin-ui/`) поддерживает два языка: **русский** (по умолчанию) и **английский**. При любых изменениях в UI необходимо:

1. **Новые строки** — добавлять ключ в оба словаря: `admin-ui/src/translations/ru.js` и `admin-ui/src/translations/en.js`
2. **Статический HTML** (`index.html`) — использовать атрибуты `data-i18n="key"` / `data-i18n-placeholder="key"`, текст по умолчанию на русском
3. **Динамический JS** (`pages/*.js`) — использовать `import { t } from '../i18n.js'` и вызовы `t('namespace.key')` / `t('key', {param: value})`
4. **Именование ключей** — неймспейсы по страницам: `common.*`, `nav.*`, `dashboard.*`, `calls.*`, `prompts.*`, `knowledge.*`, `operators.*`, `settings.*`, `users.*`, `audit.*`, `login.*`, `theme.*`, `ws.*`, `api.*`

Ядро i18n: `admin-ui/src/i18n.js` (`t()`, `initLang()`, `toggleLang()`, `translateStaticDOM()`, `getLocale()`).

## Admin UI — Help Content (обязательно при изменениях UI)

При добавлении нового функционала или изменении существующего в Admin UI **обязательно** обновить справку для пользователей:

1. **Новая страница** — добавить запись в `admin-ui/src/help-content.js` (реестр `HELP_PAGES`) с `titleKey`, `overviewKey`, `sections[]`, `tipsKey`
2. **Новая функция на существующей странице** — добавить секцию в соответствующий блок `HELP_PAGES.<page>.sections[]`
3. **Тексты справки** — добавить i18n-ключи `help.<page>.*` в оба словаря: `admin-ui/src/translations/ru.js` и `admin-ui/src/translations/en.js`
4. **Формат секций** — каждая секция содержит `titleKey` + `contentKey`; для пошаговых инструкций добавлять пару `steps` + `stepsContent`

Файлы справочной системы:
- `admin-ui/src/help-content.js` — реестр секций (i18n-ключи, не сырой текст)
- `admin-ui/src/translations/ru.js` — русские тексты (`help.*` ключи)
- `admin-ui/src/translations/en.js` — английские тексты (`help.*` ключи)
- `admin-ui/src/help-drawer.js` — компонент отрисовки (обычно не меняется)

## Key Design Decisions

- Audio is **never stored** — streaming STT only, transcriptions retained 90 days
- Bot announces automated processing at call start (legal requirement)
- PostgreSQL tables `calls`, `call_turns`, `call_tool_calls` use **monthly partitioning**
- Store API mutations (`POST /orders`, `POST /orders/{id}/confirm`) support **Idempotency-Key**
- All logs use structured JSON with `call_id` + `request_id` for cross-component tracing
- AudioSocket between servers requires **WireGuard/VPN** (no TLS in AudioSocket protocol)
