# Фаза 4: Security Hardening

## Статус
- [x] Не начата
- [x] В процессе
- [x] Завершена

**Начата:** 2026-02-14
**Завершена:** 2026-02-14

## Цель фазы
Усилить безопасность API, расширить rate limiting за пределы login endpoint, провести OWASP-ревью, настроить расширенное сканирование зависимостей. После этой фазы все публичные эндпоинты защищены, а CI ловит уязвимости до production.

## Задачи

### 4.0 ОБЯЗАТЕЛЬНО: Анализ и планирование

#### A. Анализ существующего кода
- [x] Изучить `src/api/auth.py` — текущий rate limiting (только login, 5 попыток / 15 мин)
- [x] Изучить все API-роуты: `src/api/` — какие эндпоинты публичные, какие требуют auth
- [x] Изучить `doc/security/` — существующая документация по безопасности (STRIDE, data policy)
- [x] Изучить `src/logging/pii_sanitizer.py` — PII sanitizer
- [x] Изучить `.github/workflows/ci.yml` — текущий security job (pip-audit)
- [x] Изучить `src/agent/tools.py` — валидация входных параметров tools

**Заметки для переиспользования:**
- Rate limiting на login через Redis: `login_rl:{ip}:{username}`, INCR + EXPIRE
- RBAC: `require_role()` dependency, роли admin/analyst/operator
- PII sanitizer: маскирует телефоны и имена, не маскирует email/адреса
- CI: только pip-audit, нет SAST, нет secrets detection
- API роуты: /auth — public, остальные — require auth, /health и /metrics — public

#### B. Анализ зависимостей
- [x] Определить: использовать `slowapi` или кастомный middleware для rate limiting
- [x] Определить: нужен ли WAF (Web Application Firewall) или достаточно middleware
- [x] Определить: добавить ли SAST-сканер (bandit, semgrep) в CI

**Решения:**
- Кастомный middleware на Redis (sliding window) — проще, нет зависимости от slowapi
- WAF не нужен — middleware достаточно для MVP
- bandit + gitleaks в CI

#### C. Проверка архитектуры
- [x] Rate limiting должен быть распределённый (через Redis) для горизонтального масштабирования
- [x] Rate limit конфигурация через env variables (не хардкод)
- [x] Security headers не должны ломать admin UI

**Референс-модуль:** `src/api/auth.py` — текущий rate limiting на Redis

---

### 4.1 Расширение API rate limiting

Сейчас rate limiting только на `/auth/login`. Нужно защитить все публичные эндпоинты.

- [x] Выбрать подход: кастомный middleware на Redis (sliding window counter)
- [x] Реализовать глобальный rate limiter: 100 req/min per IP для всех эндпоинтов
- [x] Реализовать per-user rate limiter: 60 req/min для аутентифицированных пользователей
- [x] Добавить более строгие лимиты для чувствительных эндпоинтов:
  - `/auth/login` — 5 req/15 min (уже есть, отдельный механизм)
  - `/analytics/*/export` — 10 req/min (тяжёлые операции)
  - `/knowledge` POST/PATCH/DELETE — 30 req/min (мутации)
- [x] Добавить заголовки `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset`
- [x] Возвращать `429 Too Many Requests` с `Retry-After` header при превышении
- [x] Добавить Prometheus метрику `api_rate_limit_exceeded_total{endpoint, ip}`
- [x] Конфигурация через env variables: `RATE_LIMIT_GLOBAL`, `RATE_LIMIT_PER_USER`
- [x] Unit-тесты для rate limiter

**Файлы:** `src/api/middleware/rate_limit.py`, `src/monitoring/metrics.py`, `tests/unit/test_rate_limit.py`

---

### 4.2 Security headers и CORS

- [x] Проверить текущую CORS конфигурацию в `src/main.py` — отсутствовала
- [x] Добавить security headers middleware:
  - `X-Content-Type-Options: nosniff`
  - `X-Frame-Options: DENY`
  - `X-XSS-Protection: 0` (deprecated, но для legacy браузеров)
  - `Strict-Transport-Security: max-age=31536000; includeSubDomains` (HSTS)
  - `Content-Security-Policy` — script-src 'self' 'unsafe-inline' для admin UI
  - `Referrer-Policy: strict-origin-when-cross-origin`
  - `Permissions-Policy: camera=(), microphone=(), geolocation=()`
- [x] CORS: `CORS_ALLOWED_ORIGINS` env var, дефолт — пустой список (безопасно)
- [x] Admin UI работает с CSP: `'unsafe-inline'` для inline стилей/скриптов
- [x] Unit-тесты: проверка всех headers в ответах

**Файлы:** `src/api/middleware/security_headers.py`, `src/main.py`, `tests/unit/test_security_headers.py`

---

### 4.3 OWASP Top 10 ревью и исправления

- [x] **Injection (A03:2021):** Все SQL-запросы параметризованы через SQLAlchemy text() + :params
- [x] **Broken Authentication (A07:2021):** JWT HS256 с hmac.compare_digest(), bcrypt для паролей
- [x] **Sensitive Data Exposure (A02:2021):** PII sanitizer маскирует телефоны/имена в логах
- [x] **XML External Entities:** Не применимо — JSON API only, XML-парсинг отсутствует
- [x] **Broken Access Control (A01:2021):** RBAC на всех admin эндпоинтах через require_role()
- [x] **Security Misconfiguration (A05:2021):** Security headers добавлены, CORS настроен, debug mode off
- [x] **Insecure Deserialization (A08:2021):** Redis использует JSON, не pickle — безопасно
- [x] Создать `doc/security/owasp-review.md` — полный отчёт с findings и mitigations
- [x] Исправить: timing attack в fallback credentials (hmac.compare_digest)

**Файлы:** `doc/security/owasp-review.md`, `src/api/auth.py`

---

### 4.4 Расширение CI security pipeline

- [x] Добавить `bandit` в CI — Python SAST с конфигурацией в pyproject.toml
- [x] pip-audit уже есть — проверка CVE в зависимостях
- [x] Добавить `gitleaks` в CI — поиск секретов через gitleaks-action
- [x] Создать `.github/dependabot.yml` — pip (weekly), docker (weekly), github-actions (monthly)
- [x] bandit конфиг в `pyproject.toml`: exclude tests/scripts, skip B101
- [x] Задокументировать security pipeline в `doc/security/ci-security.md`

**Файлы:** `.github/workflows/ci.yml`, `.github/dependabot.yml`, `pyproject.toml`, `doc/security/ci-security.md`

---

## При завершении фазы
Все задачи завершены, фаза отмечена как completed.
