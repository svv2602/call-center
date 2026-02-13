# Фаза 1 — MVP: Подбор шин и проверка наличия

## Цель

Запустить минимально работающую систему: клиент звонит, ИИ-агент принимает звонок, помогает подобрать шины, проверяет наличие и при необходимости переключает на оператора.

## Критерии успеха

- [ ] Бот принимает звонок и здоровается на украинском
- [ ] Бот распознаёт речь на украинском языке
- [ ] Бот подбирает шины по запросу (авто или размер)
- [ ] Бот проверяет наличие товара
- [ ] Бот отвечает голосом на украинском
- [ ] Бот переключает на оператора по запросу
- [ ] Бот переключает на оператора при невозможности помочь
- [ ] Задержка ответа < 2 секунд (от конца речи клиента до начала ответа)
- [ ] Логирование всех звонков (транскрипция + метаданные)
- [ ] Работа при 10 одновременных звонках

## Фазы работы

1. [Инфраструктура](phase-01-infrastructure.md) — Docker, env, Asterisk dialplan, pyproject.toml
2. [AudioSocket сервер](phase-02-audiosocket-server.md) — TCP-сервер, протокол, Session Manager
3. [STT модуль](phase-03-stt-module.md) — Google STT streaming, мультиязычность
4. [TTS модуль](phase-04-tts-module.md) — Google TTS, кэширование, конвертация
5. [LLM агент](phase-05-llm-agent.md) — Claude API, tools MVP, system prompt
6. [Pipeline](phase-06-pipeline.md) — Оркестрация STT→LLM→TTS, barge-in
7. [Store API клиент](phase-07-store-client.md) — HTTP-клиент, circuit breaker, retry
8. [Логирование](phase-08-logging.md) — PostgreSQL логи, Redis сессии, Prometheus
9. [Тестирование](phase-09-testing.md) — Unit, integration, E2E, adversarial, нагрузка

## Источник требований

- `doc/development/phase-1-mvp.md` — основной
- `doc/technical/architecture.md` — архитектура компонентов
- `doc/technical/data-model.md` — схема БД
- `doc/technical/nfr.md` — нефункциональные требования
- `doc/development/api-specification.md` — спецификация Store API
- `doc/development/00-overview.md` — канонический список tools, стратегия тестирования

## Tools MVP (канонический список)

| Tool | Store API endpoint |
|------|--------------------|
| `search_tires` | `GET /tires/search` |
| `check_availability` | `GET /tires/{id}/availability` |
| `transfer_to_operator` | Asterisk ARI |

## Store API endpoints (MVP)

| Метод | Endpoint | Описание |
|-------|----------|----------|
| GET | `/api/v1/tires/search` | Поиск шин по параметрам |
| GET | `/api/v1/tires/{id}` | Детали шины |
| GET | `/api/v1/tires/{id}/availability` | Наличие товара |
| GET | `/api/v1/vehicles/tires` | Подбор шин по автомобилю |

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
│   └── routes.py        # Health checks, мониторинг
├── store_client/        # Клиент Store API — паттерн circuit breaker + retry
│   └── client.py
└── config.py            # Конфигурация через env variables
```

## Правила кода

### Архитектурные паттерны:

| Паттерн | Где применяется | Пример |
|---------|----------------|--------|
| Protocol (абстракция) | STT, TTS, LLM | `class STTEngine(Protocol)` |
| Circuit Breaker | Store API | `aiobreaker.CircuitBreaker(fail_max=5, timeout=30)` |
| Structured JSON logs | Все компоненты | `{"call_id": "...", "component": "...", "event": "..."}` |
| PII Sanitizer | Логирование | `PIISanitizer.sanitize(text)` — маскирует телефоны, имена |
| Graceful degradation | Внешние сервисы | Сбой → переключение на оператора, не обрыв звонка |
| TTL на сессиях | Redis | `setex("call_session:{uuid}", ttl=1800, ...)` |

### Async паттерны:
- Все I/O операции — `async/await`
- Каждый звонок — отдельная `asyncio.Task`
- Streaming: STT (gRPC), TTS (по предложениям), LLM (token streaming)
- Буферизация аудио в pipeline

## Начало работы

Для начала или продолжения работы прочитай [PROGRESS.md](PROGRESS.md)
