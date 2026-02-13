# Фаза 9: Тестирование

## Статус
- [ ] Не начата
- [ ] В процессе
- [ ] Завершена

**Начата:** -
**Завершена:** -

## Цель фазы

Полное тестирование MVP: unit-тесты всех модулей, интеграционные тесты pipeline, E2E тесты через SIP, adversarial-тесты безопасности агента, нагрузочные тесты. Покрытие core-модулей: минимум 80%.

## Задачи

### 9.0 ОБЯЗАТЕЛЬНО: Анализ и планирование

#### A. Анализ существующего кода
- [ ] Проверить наличие `tests/unit/`, `tests/integration/`, `tests/e2e/`
- [ ] Проверить pytest конфигурацию в `pyproject.toml`
- [ ] Проверить наличие mock-реализаций (STT, TTS)

**Команды для поиска:**
```bash
ls tests/unit/ tests/integration/ tests/e2e/
grep -rn "def test_\|class Test" tests/
cat pyproject.toml | grep -A 10 "pytest"
```

#### B. Анализ зависимостей
- [ ] pytest, pytest-asyncio, pytest-cov — для unit тестов
- [ ] aioresponses — для мокирования HTTP (Store API)
- [ ] testcontainers — для PostgreSQL и Redis в интеграционных тестах
- [ ] SIPp — для E2E тестов (SIP-звонки)
- [ ] Locust — для нагрузочных тестов

**Новые абстракции:** Нет
**Новые env variables:** Нет
**Новые tools:** Нет
**Миграции БД:** Нет

#### C. Проверка архитектуры
- [ ] Стратегия тестирования из `doc/development/00-overview.md`
- [ ] Adversarial-тесты из того же документа
- [ ] NFR: покрытие 80%, p95 < 2 сек, 0% потерь при 50 звонках

**Референс-модуль:** `doc/development/00-overview.md` — секции "Стратегия тестирования", "Adversarial-тесты"

**Цель:** Определить полный план тестирования MVP.

**Заметки для переиспользования:** -

---

### 9.1 Unit-тесты: AudioSocket

- [ ] `tests/unit/test_audio_socket.py`
- [ ] Тест парсинга UUID-пакета (type=0x01)
- [ ] Тест парсинга аудио-пакета (type=0x10)
- [ ] Тест парсинга hangup-пакета (type=0x00)
- [ ] Тест парсинга error-пакета (type=0xFF)
- [ ] Тест неполных пакетов (буферизация)
- [ ] Тест формирования исходящих аудио-пакетов

**Файлы:** `tests/unit/test_audio_socket.py`
**Заметки:** -

---

### 9.2 Unit-тесты: Agent и Tools

- [ ] `tests/unit/test_agent.py`
- [ ] Тест формирования messages (system + history)
- [ ] Тест обработки text response
- [ ] Тест обработки tool_use response
- [ ] Тест цепочки tool calls (tool → result → следующий ответ)
- [ ] Тест ограничения цепочки tool calls (max 5)
- [ ] `tests/unit/test_tools.py`
- [ ] Тест валидации параметров: price > 0
- [ ] Тест валидации параметров: quantity > 0 и < 100
- [ ] Тест невалидных параметров → корректная ошибка

**Файлы:** `tests/unit/test_agent.py`, `tests/unit/test_tools.py`
**Заметки:** Использовать mock Claude API

---

### 9.3 Unit-тесты: STT и TTS

- [ ] `tests/unit/test_stt.py`
- [ ] Тест обработки interim transcripts
- [ ] Тест обработки final transcripts
- [ ] Тест restart сессии по таймауту (~5 мин)
- [ ] Тест определения языка (uk-UA, ru-RU)
- [ ] `tests/unit/test_tts.py`
- [ ] Тест конвертации формата (LINEAR16, 16kHz)
- [ ] Тест кэширования частых фраз
- [ ] Тест streaming по предложениям

**Файлы:** `tests/unit/test_stt.py`, `tests/unit/test_tts.py`
**Заметки:** Использовать mock gRPC и mock Google TTS

---

### 9.4 Unit-тесты: Store Client

- [ ] `tests/unit/test_store_client.py`
- [ ] Тест `search_tires()` — корректный маппинг ответа
- [ ] Тест `check_availability()` — корректный маппинг
- [ ] Тест retry при 429 и 503
- [ ] Тест НЕ retry при 500
- [ ] Тест circuit breaker: Open → reject запросы
- [ ] Тест circuit breaker: Half-Open → пробный запрос → Close
- [ ] Тест уважения заголовка `Retry-After`

**Файлы:** `tests/unit/test_store_client.py`
**Заметки:** Использовать `aioresponses` для мокирования HTTP

---

### 9.5 Adversarial-тесты (безопасность агента)

- [ ] `tests/unit/test_adversarial.py`
- [ ] Prompt injection: смена роли → агент игнорирует
- [ ] Prompt injection: раскрытие промпта → агент отказывает
- [ ] Заказ на 0 грн → валидация отклоняет
- [ ] Абсурдное количество (10000 шт) → отклонение, переключение на оператора
- [ ] Оскорбления → вежливое предложение переключить на оператора
- [ ] Не по теме ("какая погода?") → "Я допомагаю з шинами"
- [ ] Русский язык → понимает, отвечает украинским
- [ ] Суржик → понимает, отвечает украинским
- [ ] Пустая речь / шум (15 сек) → таймаут → "Ви ще на лінії?"

**Файлы:** `tests/unit/test_adversarial.py`
**Заметки:** Мокированный LLM, проверка что агент НЕ раскрывает промпт, НЕ вызывает tool calls с невалидными параметрами

---

### 9.6 Интеграционные тесты

- [ ] `tests/integration/test_pipeline.py` — полный цикл STT→LLM→TTS с мокированными внешними API
- [ ] `tests/integration/test_postgres.py` — запись логов в PostgreSQL (testcontainers)
- [ ] `tests/integration/test_redis.py` — хранение сессий в Redis (testcontainers)
- [ ] `tests/integration/test_store_api.py` — реальные HTTP-запросы к тестовому Store API

**Файлы:** `tests/integration/`

**Запуск:**
```bash
pytest tests/integration/  # Нужен Docker для testcontainers
```

**Заметки:** -

---

### 9.7 E2E тесты

- [ ] `tests/e2e/test_tire_search.py` — SIP-звонок → "Мені потрібні шини..." → бот отвечает
- [ ] `tests/e2e/test_availability.py` — SIP-звонок → запрос наличия → ответ с ценой
- [ ] `tests/e2e/test_operator_transfer.py` — SIP-звонок → "З'єднайте з оператором" → transfer
- [ ] Подготовка WAV-файлов с записанными фразами для SIPp
- [ ] Assertions на транскрипцию ответа бота

**Файлы:** `tests/e2e/`

**Инструменты:**
```bash
# SIPp для SIP-звонков
sipp -sf tests/e2e/scenarios/tire_search.xml -m 1
```

**Заметки:** Нужен работающий Asterisk + Call Processor

---

### 9.8 Нагрузочные тесты

- [ ] Профиль "нормальная нагрузка": 20 одновременных звонков, 30 мин
- [ ] Профиль "пиковая нагрузка": 50 одновременных звонков, 15 мин
- [ ] Метрики: p95 < 2 сек, 0% потерь при нормальной нагрузке, < 5% ошибок при пиковой
- [ ] Graceful degradation при стресс-тесте (100+ звонков)

**Файлы:** `tests/load/`

**Инструменты:** Locust + SIPp

**Критерии NFR:**
| Профиль | Одновременных | p95 latency | Потери |
|---------|--------------|-------------|--------|
| Нормальная | 20 | < 2 сек | 0% |
| Пиковая | 50 | < 3 сек | < 5% |
| Стресс | 100+ | — | Graceful degradation |

**Заметки:** -

---

## При завершении фазы

1. Убедись, что все задачи отмечены [x]
2. Измени статус фазы: [x] Завершена
3. Заполни дату "Завершена: YYYY-MM-DD"
4. Проверь покрытие:
   ```bash
   pytest tests/ --cov=src --cov-report=html
   # Минимум 80% для core-модулей
   ```
5. Проверь линтинг:
   ```bash
   ruff check src/ && ruff format src/
   mypy src/ --strict
   ```
6. Выполни коммит:
   ```bash
   git add .
   git commit -m "checklist(phase-1-mvp): phase-9 testing completed"
   ```
7. Обнови PROGRESS.md — Фаза 1 MVP завершена!
