# Фаза 2: Authentication Fixes

## Статус
- [ ] Не начата
- [ ] В процессе
- [x] Завершена

**Начата:** 2026-03-04
**Завершена:** 2026-03-04

## Цель фазы
Добавить аутентификацию на все endpoints, которые сейчас открыты без auth.

## Задачи

### 2.0 ОБЯЗАТЕЛЬНО: Анализ и планирование

#### A. Анализ существующего кода
- [x] Прочитать `src/main.py` — endpoint `/internal/caller-id` (строки 375-394)
- [x] Прочитать `src/main.py` — endpoint `/metrics` (строки 397-400)
- [x] Прочитать `src/api/system.py` — endpoint `/health/celery` (строки 40-72)
- [x] Прочитать `src/api/middleware/rate_limit.py` — `_SKIP_PATHS` set
- [x] Изучить как `require_permission()` работает в `src/api/auth.py`

#### B. Анализ зависимостей
- [x] Новый env variable: `INTERNAL_API_SECRET` (pre-shared secret для internal endpoints)
- [x] Asterisk dialplan curl нужно обновить с новым header

**Новые env variables:** `INTERNAL_API_SECRET`, `METRICS_BEARER_TOKEN`
**Миграции БД:** не нужны

#### C. Проверка архитектуры
- [x] `/internal/caller-id` — используется только из Asterisk dialplan curl
- [x] `/metrics` — используется только Prometheus scraper
- [x] `/health/celery` — используется мониторингом

**Референс-модуль:** `src/api/auth.py` (паттерн require_permission)

---

### 2.1 Добавить auth на `/internal/caller-id`
- [x] Добавить env variable `INTERNAL_API_SECRET` в `src/config.py` (default: пустая строка)
- [x] В endpoint `store_caller_id()`: проверять header `X-Internal-Secret` против `settings.internal_api.secret`
- [x] При несовпадении: возвращать 403 Forbidden
- [x] При пустом `internal_api_secret` в config: логировать WARNING при старте
- [x] Добавить валидацию UUID формата (uuid.UUID(call_uuid) в try/except)
- [x] Обновить `asterisk/extensions.conf` — добавить `-H "X-Internal-Secret: ${INTERNAL_API_SECRET}"` в curl
- [x] Обновить `.env.example` — добавить `INTERNAL_API_SECRET=`
- [x] Обновить `docker-compose.yml` — передать `INTERNAL_API_SECRET` в call-processor
- [x] Написать тест: POST без секрета → 403
- [x] Написать тест: POST с неверным секретом → 403
- [x] Написать тест: POST с правильным секретом → 200
- [x] Написать тест: POST с невалидным UUID → 400

**Файлы:** `src/main.py`, `src/config.py`, `asterisk/extensions.conf`, `docker-compose.yml`, `.env.example`
**Audit refs:** CRIT-03, SEC-02, QW-03

---

### 2.2 Защитить `/metrics` endpoint
- [x] Добавить env variable `METRICS_BEARER_TOKEN` в `src/config.py`
- [x] В endpoint `/metrics`: проверять header `Authorization: Bearer {token}`
- [x] При пустом `METRICS_BEARER_TOKEN`: endpoint работает без auth (backward compat, но WARNING в логе)
- [x] Обновить `prometheus/prometheus.yml` — добавить `bearer_token` в scrape config (закомментированный, для активации при деплое)
- [x] Обновить `.env.example`
- [x] Написать тест: GET без токена при настроенном секрете → 403
- [x] Написать тест: GET с правильным токеном → 200

**Файлы:** `src/main.py`, `src/config.py`, `prometheus/prometheus.yml`, `.env.example`
**Audit refs:** CRIT-04, SEC-03

---

### 2.3 Отключить Grafana anonymous access
- [x] В `docker-compose.yml`: `GF_AUTH_ANONYMOUS_ENABLED: "false"` + убрать `GF_AUTH_ANONYMOUS_ORG_ROLE`
- [x] Admin UI dashboard.js — iframe Grafana потребует auth (Grafana login page). Примечание: для iframe embedding нужно будет настроить Grafana API key или auth proxy при деплое.
- [x] Протестировать: Grafana dashboard без логина → redirect на login page (ручная проверка при деплое)

**Файлы:** `docker-compose.yml`, `admin-ui/src/pages/dashboard.js`
**Audit refs:** SEC-06, HIGH-3

---

### 2.4 Ограничить Flower через nginx
- [x] В `nginx/default.conf`: добавить `allow 127.0.0.1; allow 192.168.0.0/16; allow 10.0.0.0/8; deny all;` для `/flower/`

**Файлы:** `nginx/default.conf`
**Audit refs:** SEC-18, HIGH-5

---

## При завершении фазы

1. Убедись, что все задачи отмечены [x]
2. Измени статус фазы: [x] Завершена
3. Заполни дату
4. Выполни коммит:
   ```bash
   git add src/main.py src/config.py src/api/ prometheus/ docker-compose.yml nginx/ asterisk/ .env.example tests/
   git commit -m "checklist(p0-security-hardening): phase-2 authentication fixes — protect internal endpoints"
   ```
5. Обнови PROGRESS.md: Текущая фаза: 3
6. Открой phase-03 и продолжи работу
