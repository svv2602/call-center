# Post-Production Improvements

## Цель
Реализовать улучшения после достижения production-ready статуса: усилить безопасность, улучшить observability, расширить тестирование, подготовить инфраструктуру для масштабирования и модернизировать admin UI.

## Критерии успеха
- [ ] JWT logout полноценно инвалидирует токен через Redis blacklist
- [ ] PII sanitizer маскирует email, адреса, номера карт
- [ ] Grafana дашборды хранятся как JSON в репозитории (reproducibility)
- [ ] Chaos-тесты подтверждают graceful degradation при сбое Redis, PostgreSQL, Store API
- [ ] Регрессионные тесты промптов защищают от деградации качества
- [ ] Kubernetes manifests позволяют развернуть приложение в K8s с auto-scaling
- [ ] Admin UI разбит на модули с build pipeline

## Фазы работы
1. **Security & Auth** — JWT blacklist, расширение PII sanitizer
2. **Observability** — Grafana dashboards as code
3. **Testing & Quality** — Chaos testing, prompt regression tests
4. **Infrastructure** — Kubernetes manifests
5. **Admin UI Modularization** — разбиение на модули, Vite build pipeline
6. **STT & Cost Optimization** — Whisper STT rollout, анализ стоимости

## Источник требований
- `audit/next-steps-2026-02-14.md` — приоритеты 3 и 4
- `development-checklists/production-readiness/` — завершённый production-readiness чеклист

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
│   ├── auth.py          # JWT аутентификация
│   ├── middleware/       # Rate limiting, security headers
│   ├── websocket.py     # WebSocket endpoint для admin UI
│   └── operators.py     # Управление операторами
├── events/              # Redis Pub/Sub события
│   └── publisher.py     # publish_event()
├── monitoring/          # Prometheus метрики
│   └── metrics.py       # Все метрики приложения
├── store_client/        # Клиент Store API — паттерн circuit breaker + retry
│   └── client.py
├── logging/             # PII sanitizer
│   └── pii_sanitizer.py # Маскирование персональных данных
└── config.py            # Конфигурация через env variables
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
| Redis Pub/Sub | События для admin UI | `publish_event("call:started", {...})` |

### Async паттерны:
- Все I/O операции — `async/await`
- Каждый звонок — отдельная `asyncio.Task`
- Streaming: STT (gRPC), TTS (по предложениям), LLM (token streaming)
- Буферизация аудио в pipeline

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
pytest tests/unit/test_audio_socket.py -v # один файл
pytest tests/ --cov=src --cov-report=html # с покрытием
```

## Начало работы
Для начала или продолжения работы прочитай PROGRESS.md
