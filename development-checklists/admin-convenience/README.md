# Удобство работы администратора — Admin Convenience

## Цель
Обеспечить комфортную работу администратора с системой Call Center AI:
рабочий веб-интерфейс, CLI-инструменты, экспорт отчётов, разграничение доступа,
автоматические бэкапы и операционный инструментарий.

## Критерии успеха
- [ ] Admin UI подключён к реальным API и отображает живые данные
- [ ] Валидация конфигурации при старте с понятными ошибками
- [ ] CLI-инструмент `call-center-admin` с базовыми командами
- [ ] Экспорт звонков и статистики в CSV
- [ ] Автоматические бэкапы PostgreSQL по расписанию
- [ ] RBAC: минимум 3 роли (admin, analyst, operator)
- [ ] Аудит-лог действий администратора
- [ ] Мониторинг Celery-задач через Flower
- [ ] Runbook-и для типичных инцидентов

## Фазы работы
1. [Валидация конфига и CLI-фундамент] — быстрые улучшения, защита от ошибок конфигурации, скелет CLI
2. [Интеграция Admin UI с API] — подключение веб-интерфейса к реальному бэкенду
3. [Экспорт данных и отчётность] — CSV-экспорт, PDF-отчёты, email по расписанию
4. [RBAC и безопасность] — роли, аудит-лог, управление сессиями
5. [Операционный инструментарий] — бэкапы, Flower, runbooks, hot-reload конфига
6. [Управление операторами] — статусы, очередь, мониторинг нагрузки

## Источник требований
- Аудит удобства работы администратора (сессия 2026-02-14)
- `doc/development/phase-4-analytics.md` — запланированные фичи Admin Panel
- `doc/technical/deployment.md` — стратегия бэкапов
- `doc/security/threat-model.md` — управление доступом
- `doc/technical/nfr.md` — конфигурация, логирование

## Правила переиспользования кода

### ОБЯЗАТЕЛЬНО перед реализацией:
1. **Поиск существующего функционала** — перед написанием нового кода ВСЕГДА ищи похожий существующий код
2. **Анализ паттернов** — изучи как реализованы похожие фичи в проекте
3. **Переиспользование модулей** — используй существующие модули, базовые классы, утилиты

### Где искать:
```
src/
├── api/                 # REST API (FastAPI) — 5 файлов
│   ├── auth.py          # JWT авторизация — паттерн для RBAC
│   ├── analytics.py     # Аналитика звонков — паттерн CRUD + фильтры
│   ├── knowledge.py     # CRUD статей базы знаний
│   └── prompts.py       # Версии промптов + A/B тесты
├── config.py            # Pydantic Settings — сюда добавлять новые параметры
├── tasks/               # Celery задачи
│   ├── celery_app.py    # Конфигурация Celery
│   ├── daily_stats.py   # Ежедневная агрегация — паттерн scheduled task
│   ├── data_retention.py# Очистка старых данных
│   └── quality_evaluator.py # Оценка качества — паттерн per-call async task
├── monitoring/
│   ├── metrics.py       # 22 Prometheus метрики
│   └── cost_tracker.py  # Трекер стоимости звонков
├── logging/
│   ├── structured_logger.py # JSON-логирование
│   ├── call_logger.py       # Логирование звонков в БД
│   └── pii_sanitizer.py     # Маскирование PII
├── main.py              # Точка входа — роутеры FastAPI, AudioSocket, health checks
└── store_client/        # HTTP-клиент Store API — паттерн circuit breaker
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
| Pydantic Settings | Конфигурация | `class Settings(BaseSettings)` + env prefix |
| JWT Auth | Admin API | `create_jwt()`, `verify_jwt()`, `require_admin()` |

### Async паттерны:
- Все I/O операции — `async/await`
- FastAPI роутеры — async handlers
- Celery задачи — синхронный wrapper вокруг async кода

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
- [ ] Проверка PII sanitizer для новых полей

### Запуск:
```bash
pytest tests/unit/                        # unit
pytest tests/integration/                 # integration (нужен Docker)
pytest tests/ --cov=src --cov-report=html # с покрытием
```

## Начало работы
Для начала или продолжения работы прочитай PROGRESS.md
