# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Call Center AI — система автоматизации обработки входящих телефонных звонков для интернет-магазина шин (Украина). ИИ-агент принимает звонки, ведёт диалог на украинском языке, подбирает шины, оформляет заказы, записывает на шиномонтаж.

**Status:** Planning/documentation phase. Source code not yet committed. Documentation is comprehensive and serves as the implementation specification.

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

- **AudioSocket protocol:** `[type:1B][length:2B BE][payload:NB]`. Types: 0x01=UUID, 0x10=audio, 0x00=hangup, 0xFF=error. Audio: 16kHz, 16-bit signed linear PCM, little-endian.
- **Pipeline flow:** AudioSocket → STT (streaming) → LLM (Claude) → TTS → AudioSocket. Supports barge-in.
- **Multilingual STT:** Primary `uk-UA` + alternative `ru-RU`. Agent always responds in Ukrainian regardless of input language.
- **Session state** is in Redis (stateless Call Processor for horizontal scaling). TTL prevents memory leaks on abnormal disconnects.
- **Circuit breaker** (aiobreaker, fail_max=5, timeout=30s) protects Store API calls.

## Canonical Tool Names

Single source of truth for LLM agent tools is in `doc/development/00-overview.md` (section "Канонический список tools"). When referencing tools in any document, use these exact names:

`get_vehicle_tire_sizes`, `search_tires`, `check_availability`, `transfer_to_operator`, `get_order_status`, `create_order_draft`, `update_order_delivery`, `confirm_order`, `get_fitting_stations`, `get_fitting_slots`, `book_fitting`, `search_knowledge_base`

**Important:** The tool is `create_order_draft` (not `create_order`).

## Planned Tech Stack

Python 3.12+, asyncio + FastAPI, Asterisk 20 AudioSocket, Google Cloud STT v2 / TTS Neural2, Claude API (Anthropic), PostgreSQL 16 + pgvector, Redis 7, Prometheus + Grafana.

## Planned Commands (from documentation)

```bash
# Local dev environment
docker compose -f docker-compose.dev.yml up -d      # PostgreSQL, Redis, test Asterisk
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,test]"
set -a; . ./.env.local; set +a                       # Load env vars (NOT export $(cat...))
python -m src.main                                    # Run Call Processor

# Tests
pytest tests/unit/                                    # Unit tests
pytest tests/integration/                             # Integration (needs Docker)
pytest tests/unit/test_audio_socket.py -v             # Single test file
pytest tests/ --cov=src --cov-report=html             # With coverage

# Lint
ruff check src/ && ruff format src/
mypy src/ --strict
```

## Documentation Structure

- `doc/business/` — investor/CEO materials (presentation, ROI, roadmap)
- `doc/technical/` — architecture (SAD), data model (ERD), NFR, deployment, sequence diagrams
- `doc/security/` — STRIDE threat model, data policy, risk matrix
- `doc/development/` — implementation specs by phase, API spec, local setup, troubleshooting, glossary
- `audit/` — external audit reports (historical, recommendations already applied to docs)

Key entry points: `doc/README.md` (navigation), `doc/development/00-overview.md` (project overview + canonical tools), `doc/development/api-specification.md` (Store API contract).

## Development Phases

1. **MVP** — tire search, availability check, operator transfer
2. **Orders** — order status, order creation flow (draft → delivery → confirm)
3. **Services** — fitting booking, RAG knowledge base consultations
4. **Analytics** — dashboards, quality scoring, A/B prompt testing, self-hosted Whisper

## Admin UI — i18n

Админка (`admin-ui/`) поддерживает два языка: **русский** (по умолчанию) и **английский**. При любых изменениях в UI необходимо:

1. **Новые строки** — добавлять ключ в оба словаря: `admin-ui/src/translations/ru.js` и `admin-ui/src/translations/en.js`
2. **Статический HTML** (`index.html`) — использовать атрибуты `data-i18n="key"` / `data-i18n-placeholder="key"`, текст по умолчанию на русском
3. **Динамический JS** (`pages/*.js`) — использовать `import { t } from '../i18n.js'` и вызовы `t('namespace.key')` / `t('key', {param: value})`
4. **Именование ключей** — неймспейсы по страницам: `common.*`, `nav.*`, `dashboard.*`, `calls.*`, `prompts.*`, `knowledge.*`, `operators.*`, `settings.*`, `users.*`, `audit.*`, `login.*`, `theme.*`, `ws.*`, `api.*`

Ядро i18n: `admin-ui/src/i18n.js` (`t()`, `initLang()`, `toggleLang()`, `translateStaticDOM()`, `getLocale()`).

## Key Design Decisions

- Audio is **never stored** — streaming STT only, transcriptions retained 90 days
- Bot announces automated processing at call start (legal requirement)
- PostgreSQL tables `calls`, `call_turns`, `call_tool_calls` use **monthly partitioning**
- Store API mutations (`POST /orders`, `POST /orders/{id}/confirm`) support **Idempotency-Key**
- All logs use structured JSON with `call_id` + `request_id` for cross-component tracing
- AudioSocket between servers requires **WireGuard/VPN** (no TLS in AudioSocket protocol)
