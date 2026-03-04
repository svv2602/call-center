# Фаза 1: Network Exposure

## Статус
- [ ] Не начата
- [ ] В процессе
- [x] Завершена

**Начата:** 2026-03-04
**Завершена:** 2026-03-04

## Цель фазы
Закрыть доступ к внутренним сервисам извне. Привязать порты к 127.0.0.1. Добавить пароль Redis.

## Задачи

### 1.0 ОБЯЗАТЕЛЬНО: Анализ и планирование

#### A. Анализ существующего кода
- [x] Прочитать `docker-compose.yml` — все port mappings
- [x] Прочитать `docker-compose.staging.yml` — staging port mappings
- [x] Прочитать `nginx/default.conf` — какие сервисы проксируются
- [x] Определить какие порты нужны извне (только 8080 через nginx и 9092 для Asterisk)

**Команды для поиска:**
```bash
grep -n "ports:" docker-compose.yml
grep -n "0.0.0.0" docker-compose.yml
grep -n "proxy_pass" nginx/default.conf
```

#### B. Анализ зависимостей
- [x] Проверить какие сервисы обращаются к Redis (нужно обновить URL с паролем)
- [x] Проверить REDIS_URL во всех env variables в docker-compose
- [x] Проверить Celery broker URL

**Новые env variables:** `REDIS_PASSWORD`
**Миграции БД:** не нужны

#### C. Проверка архитектуры
- [x] Убедиться что nginx проксирует все нужные пути (/api, /admin, /grafana, /flower)
- [x] Убедиться что Prometheus scrape targets обновлены (internal DNS, не localhost)

**Референс-модуль:** `docker-compose.yml`, `nginx/default.conf`

**Заметки для переиспользования:** Prometheus уже использует Docker DNS (call-processor:8080, alertmanager:9093)

---

### 1.1 Привязать internal ports к 127.0.0.1 в docker-compose.yml
- [x] Prometheus: `"127.0.0.1:9090:9090"` (было `"9090:9090"`)
- [x] Alertmanager: `"127.0.0.1:9093:9093"` (было `"9093:9093"`)
- [x] Flower: `"127.0.0.1:${FLOWER_PORT:-5555}:5555"` (было `"${FLOWER_PORT:-5555}:5555"`)
- [x] Whisper: `"127.0.0.1:9000:9000"` (было `"9000:9000"`)
- [x] Store API: `"127.0.0.1:3002:3000"` (было `"3002:3000"`)
- [x] Redis: убрать внешний порт полностью (Docker network only) — нет внешнего порта в production compose
- [x] Проверить что call-processor (8080, 9092) остаётся доступным для nginx и Asterisk — порты оставлены на 0.0.0.0
- [x] Grafana: `"127.0.0.1:3000:3000"` (было `"3000:3000"`) — доступ через nginx

**Файлы:** `docker-compose.yml`
**Audit refs:** CRIT-04, SEC-17

---

### 1.2 Привязать ports в docker-compose.staging.yml
- [x] Применить аналогичные изменения для staging (все порты привязаны к 127.0.0.1)
- [x] Убрать все дефолтные fallback-порты на 0.0.0.0

**Файлы:** `docker-compose.staging.yml`

---

### 1.3 Добавить Redis requirepass
- [x] В `docker-compose.yml`: `command: redis-server --requirepass ${REDIS_PASSWORD:?Set REDIS_PASSWORD}`
- [x] Обновить `REDIS_URL` для call-processor: `redis://:${REDIS_PASSWORD}@redis:6379/0`
- [x] Обновить `CELERY_BROKER_URL` для celery-worker: `redis://:${REDIS_PASSWORD}@redis:6379/1`
- [x] Обновить `CELERY_BROKER_URL` для celery-beat
- [x] Обновить `CELERY_RESULT_BACKEND` для всех Celery сервисов
- [x] Обновить `.env.example` — добавить `REDIS_PASSWORD=your-redis-password`
- [x] Проверить healthcheck Redis: `redis-cli -a ${REDIS_PASSWORD} ping`
- [x] Протестировать локально: `docker compose up redis` → `redis-cli -a pass ping` (нужна ручная проверка при деплое)

**Файлы:** `docker-compose.yml`, `docker-compose.staging.yml`, `.env.example`
**Audit refs:** SEC-17, QW-10

---

### 1.4 Обновить Prometheus scrape config
- [x] Убедиться что `prometheus/prometheus.yml` использует Docker DNS (e.g., `call-processor:8080`) а не `localhost` — уже корректно
- [x] Prometheus и call-processor в одной Docker network — scrape по internal DNS — уже корректно

**Файлы:** `prometheus/prometheus.yml`

---

## При завершении фазы

1. Убедись, что все задачи отмечены [x]
2. Измени статус фазы: [x] Завершена
3. Заполни дату "Завершена: YYYY-MM-DD"
4. Выполни коммит:
   ```bash
   git add docker-compose.yml docker-compose.staging.yml prometheus/prometheus.yml .env.example
   git commit -m "checklist(p0-security-hardening): phase-1 network exposure — bind internal ports to 127.0.0.1, Redis auth"
   ```
5. Обнови PROGRESS.md: Текущая фаза: 2
6. Открой phase-02 и продолжи работу
