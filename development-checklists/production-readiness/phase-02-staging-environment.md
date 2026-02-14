# Фаза 2: Staging Environment

## Статус
- [x] Не начата
- [x] В процессе
- [x] Завершена

**Начата:** 2026-02-14
**Завершена:** 2026-02-14

## Цель фазы
Создать полноценное staging-окружение, максимально приближённое к production. Staging должен подниматься одной командой и позволять тестировать всю цепочку: AudioSocket → STT → LLM → TTS → Store API без внешних зависимостей.

## Задачи

### 2.0 ОБЯЗАТЕЛЬНО: Анализ и планирование

#### A. Анализ существующего кода
- [x] Изучить `docker-compose.yml` — production: 9 сервисов (call-processor, store-api, postgres, redis, prometheus, grafana, celery-worker, celery-beat, flower, alertmanager)
- [x] Изучить `docker-compose.dev.yml` — dev: минимальный (store-api, postgres, redis)
- [x] Изучить `mock-store-api/` — полноценный mock с all endpoints (tires, orders, fitting, knowledge)
- [x] Изучить `.env.example` — 25+ env variables
- [x] Изучить `.github/workflows/ci.yml` — deploy-staging was a stub (`echo`)
- [x] Изучить `asterisk/` — extensions.conf для production Asterisk

#### B. Анализ зависимостей
- [x] Asterisk — не включён в staging (внешняя зависимость, AudioSocket тестируется через test client)
- [x] STT/TTS — staging использует placeholder keys; для реального staging нужны CI secrets
- [x] PostgreSQL — отдельный instance с суффиксом `_staging` и offset портами

**Новые абстракции:** нет
**Новые env variables:** все в `.env.staging`
**Новые tools:** нет
**Миграции БД:** нет (используются те же миграции)

#### C. Проверка архитектуры
- [x] Staging изолирован: отдельные named volumes, отдельная БД `callcenter_staging`
- [x] Все порты с offset +10000 (18080, 19092, 15432, 16379, 19090, 13000, 13002, 19093)
- [x] Healthcheck на каждом сервисе

**Референс-модуль:** `docker-compose.yml` (production), `docker-compose.dev.yml` (dev)

**Заметки для переиспользования:**
- Mock Store API уже покрывает все нужные endpoints
- Celery Beat + Worker включены в staging для полного тестирования background tasks

---

### 2.1 Создание docker-compose.staging.yml

- [x] Создать `docker-compose.staging.yml` на основе `docker-compose.yml` с staging-настройками
- [x] Включить все сервисы: call-processor, postgres, redis, store-api, prometheus, grafana, alertmanager, celery-worker, celery-beat
- [x] Asterisk не включён — документировано в .env.staging
- [x] Настроить отдельные порты (+10000 offset)
- [x] Добавить healthcheck для каждого сервиса
- [x] Добавить named volumes (pgdata_staging, redisdata_staging, etc.)
- [x] Добавить resource limits

**Файлы:** `docker-compose.staging.yml`

---

### 2.2 Конфигурация staging-окружения

- [x] Создать `.env.staging` с staging-specific переменными
- [x] STT/TTS: placeholder keys (real keys через CI secrets для external API тестирования)
- [x] LLM: placeholder key (аналогично)
- [x] Store API mock: использует тот же mock-store-api с тестовыми данными
- [x] Создать `scripts/seed_staging.py` — заполнение staging БД
- [x] В seed: тестовые операторы (4), статьи knowledge base (4), sample calls (20)

**Файлы:** `.env.staging`, `scripts/seed_staging.py`

---

### 2.3 Smoke-тесты для staging

- [x] Создать `scripts/smoke_test_staging.sh`
- [x] Проверка `/health` — 200 OK
- [x] Проверка `/health/ready` — 200 OK
- [x] Проверка Store API mock health
- [x] Проверка Store API authenticated endpoint
- [x] Проверка Redis PING
- [x] Проверка Prometheus health
- [x] Цветной вывод: зелёный (OK), красный (FAIL), жёлтый (WARN)

**Файлы:** `scripts/smoke_test_staging.sh`

---

### 2.4 Интеграция staging в CI/CD

- [x] Обновить `.github/workflows/ci.yml` — `staging-smoke` job вместо stub
- [x] Build + start staging with docker compose
- [x] Run smoke tests
- [x] Teardown with `docker compose down -v` (always, even on failure)
- [x] Только для `main` ветки
- [x] Timeout 10 минут

**Файлы:** `.github/workflows/ci.yml`

---

## При завершении фазы
Все задачи завершены, фаза отмечена как completed.
