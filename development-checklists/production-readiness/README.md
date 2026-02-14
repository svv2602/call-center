# Production Readiness — подготовка к production-развёртыванию

## Цель
Довести проект Call Center AI от состояния "кодовая база готова" до полной production-готовности: автоматизировать операции, настроить staging, расширить тестирование, усилить безопасность и улучшить admin UI.

## Критерии успеха
- [ ] Партиции PostgreSQL создаются автоматически (cron/systemd)
- [ ] Backup-скрипты развёрнуты и работают по расписанию
- [ ] Staging-окружение поднимается одной командой
- [ ] E2E-тесты покрывают основные сценарии звонков
- [ ] Load-тесты подтверждают выполнение NFR (p95 < 2s, 20 concurrent calls)
- [ ] API rate limiting на всех публичных эндпоинтах
- [ ] Admin UI обновляется в реальном времени через WebSocket
- [ ] Admin UI корректно работает на мобильных устройствах

## Фазы работы
1. **Operational Automation** — автоматизация партиций, backup-скриптов, cron-задач
2. **Staging Environment** — docker-compose.staging.yml, env-конфигурация, CI-деплой
3. **Testing & Load** — расширение E2E-тестов, load-тестирование с Locust/SIPp
4. **Security Hardening** — API rate limiting, OWASP-ревью, расширенное сканирование зависимостей
5. **Admin UI Improvements** — WebSocket real-time обновления, мобильная адаптивность

## Источник требований
- Независимый анализ production-готовности проекта (2026-02-14)
- `doc/technical/nfr.md` — нефункциональные требования
- `doc/operations/runbooks/` — операционные процедуры
- `scripts/backup_procedures.md` — документация backup

## Текущее состояние (на момент создания чеклиста)

| Компонент | Статус | Комментарий |
|-----------|--------|-------------|
| Кодовая база | 100% | Все модули реализованы |
| Unit-тесты | 95% | 39 файлов, хорошее покрытие |
| Мониторинг/Alerting | 90% | Prometheus + AlertManager + Telegram |
| Runbooks | 90% | 6 runbooks для основных инцидентов |
| Партиции | 70% | Скрипт есть, автоматизация нет |
| Backup | 30% | Документация есть, скрипты не развёрнуты |
| Staging | 10% | Только stub в CI |
| E2E-тесты | 20% | 2 файла, нет аудио-симуляции |
| Load-тесты | 5% | Только stub locustfile |
| Rate limiting | 20% | Только login endpoint |
| WebSocket (Admin) | 0% | Polling каждые 10-30 сек |
| Mobile UI | 30% | Viewport есть, media queries нет |

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
├── tts/                 # Text-to-Speech — аналогичный паттерн
├── agent/               # LLM агент с tool calling
├── api/                 # REST API (FastAPI) — auth, admin, analytics, operators, knowledge, export
├── store_client/        # Клиент Store API — паттерн circuit breaker + retry
├── tasks/               # Celery tasks — quality, stats, retention
├── knowledge/           # RAG embeddings и search
├── logging/             # Structured logging + PII sanitizer
├── monitoring/          # Prometheus metrics
├── cli/                 # CLI tools
├── reports/             # PDF/CSV export
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
