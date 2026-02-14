# OWASP Top 10 Security Review

**Дата:** 2026-02-14
**Версия:** 1.0
**Ревьюер:** AI Security Review (автоматизированный)

## Статус

| Категория | Критичность | Статус |
|-----------|-------------|--------|
| A01: Broken Access Control | Medium | Частично митигировано |
| A02: Cryptographic Failures | Medium | Требует внимания |
| A03: Injection | Medium | Митигировано параметризацией |
| A04: Insecure Design | Low | Архитектура хорошая |
| A05: Security Misconfiguration | Medium | Требует внимания |
| A06: Vulnerable Components | Unknown | Автоматизировано pip-audit |
| A07: Authentication Failures | Medium | Митигировано |
| A08: Data Integrity Failures | Low | OK |
| A09: Logging Failures | Low | Хорошо реализовано |
| A10: SSRF | Low | Не применимо |

## A01: Broken Access Control

### Findings

1. **RBAC реализован на всех admin-эндпоинтах** — `require_role()` dependency.
   - `/admin/*` — только admin
   - `/analytics/*` — admin, analyst
   - `/operators/*` — admin, operator (для смены статуса)
   - `/knowledge/*` — admin, analyst
   - `/export/*` — admin, analyst

2. **Missing:** Object-level authorization (BOLA) — нет проверки владельца ресурса.
   - Любой analyst может видеть все звонки, все статьи knowledge base.
   - **Митигация:** Приемлемо для MVP — количество пользователей ограничено, все сотрудники.

3. **`/health/celery`** — не требует аутентификации, раскрывает информацию о Celery workers.
   - **Митигация:** Низкий риск — показывает только количество workers, не sensitive данные.

### Рекомендации
- На будущее: добавить audit trail для доступа к данным звонков
- Рассмотреть ограничение `/health/celery` по роли

## A02: Cryptographic Failures

### Findings

1. **JWT реализация** — custom HS256 (`src/api/auth.py`).
   - `hmac.compare_digest()` используется для сравнения подписей — **хорошо**.
   - HMAC-SHA256 — достаточно для HS256.
   - **Issue:** Fallback env credentials сравниваются через `==`, не `hmac.compare_digest()`.
   - **Severity:** Low — timing attack на fallback credentials маловероятен на практике.

2. **Пароли** — bcrypt hashing (`src/api/auth.py`).
   - `bcrypt.checkpw()` — industry standard.

3. **Default JWT secret** — `"change-me-in-production"` в `src/config.py`.
   - **Митигация:** `validate_required()` выдаёт ошибку при дефолтном значении.

### Рекомендации
- Использовать `hmac.compare_digest()` для сравнения env credentials
- На будущее: рассмотреть RS256 с key rotation

## A03: Injection

### Findings

1. **SQL:** Все запросы используют `sqlalchemy.text()` с параметрами (`:param`).
   - Dynamic SET/WHERE clause construction через `.join()` — но значения всегда параметризованы.
   - Имена колонок в SET clause захардкожены в коде (не от пользователя).
   - **Severity:** Low — параметризация работает корректно.

2. **`src/tasks/data_retention.py`** — f-string в SQL с константами.
   - `_RETENTION_TRANSCRIPTS_DAYS` — integer constant, не user input.
   - **Severity:** Low — не эксплуатируемо.

3. **Prompt Injection** — user input добавляется в LLM prompt.
   - **Митигация:** PII masking через PIIVault перед отправкой в Claude.
   - Claude API `system` параметр отделён от user messages.
   - Tool arguments проходят через Store API валидацию.
   - **Severity:** Low — Claude имеет встроенную защиту от prompt injection.

### Рекомендации
- Рассмотреть ORM (SQLAlchemy models) вместо raw SQL для UPDATE
- Мониторить prompt injection attempts через логи

## A05: Security Misconfiguration

### Findings

1. **Hardcoded defaults** в `src/config.py`:
   - DATABASE_URL с dev credentials
   - Redis без пароля
   - Admin username/password = admin/admin
   - **Митигация:** `validate_required()` ловит JWT secret; остальные — dev defaults.

2. **Security headers** — добавлены (X-Content-Type-Options, X-Frame-Options, HSTS, CSP, Referrer-Policy).

3. **CORS** — настроен через env variable `CORS_ALLOWED_ORIGINS`, дефолт — пустой список (безопасно).

4. **Rate limiting** — глобальный (100 req/min per IP), per-user (60 req/min), per-endpoint для export и mutations.

### Рекомендации
- Добавить проверку всех sensitive defaults в production
- Рассмотреть secrets manager для production

## A07: Authentication Failures

### Findings

1. **Rate limiting на /auth/login** — 5 попыток / 15 минут per IP+username.
2. **JWT TTL** — 24 часа (настраивается).
3. **Logout** — не инвалидирует токен (acknowledged — MVP).
   - **Митигация:** TTL 24 часа ограничивает окно.

### Рекомендации
- Реализовать JWT blacklist через Redis (post-MVP)

## A08: Software and Data Integrity Failures (Deserialization)

### Findings

1. **Redis** — использует JSON serialization, НЕ pickle. **Безопасно.**
2. **JSON parsing** — все через `json.loads()` с try/except.
3. **No XML parsing** — только JSON API.

**Статус:** Безопасно, уязвимостей не обнаружено.

## A09: Security Logging and Monitoring Failures

### Findings

1. **Audit middleware** — логирует все мутации (POST/PATCH/DELETE) в `admin_audit_log`.
2. **PII sanitizer** — маскирует телефоны и имена в логах.
3. **Structured logging** — JSON format с `call_id`, `request_id`.
4. **Failed login logging** — записывается в audit log.
5. **Prometheus metrics** — rate limit exceeded counter.

**Статус:** Хорошо реализовано.

### Рекомендации
- Расширить PII sanitizer: email, адреса
- Добавить alerting на подозрительные паттерны (массовые 401/403)

## Исправления, внесённые в этом ревью

1. **Rate limiting middleware** — `src/api/middleware/rate_limit.py`
   - Глобальный: 100 req/min per IP
   - Per-user: 60 req/min
   - Per-endpoint: export (10/min), knowledge mutations (30/min)
   - Redis-backed sliding window для distributed deployment
   - X-RateLimit-* headers, 429 + Retry-After

2. **Security headers middleware** — `src/api/middleware/security_headers.py`
   - X-Content-Type-Options, X-Frame-Options, HSTS, CSP, Referrer-Policy, Permissions-Policy

3. **CORS middleware** — `src/main.py`
   - Configurable origins via `CORS_ALLOWED_ORIGINS` env var
   - Default: no origins allowed (secure)

4. **Prometheus metric** — `rate_limit_exceeded_total{endpoint, ip}`

## Итого

Кодовая база имеет хорошую архитектурную основу безопасности:
- RBAC с ролями admin/analyst/operator
- bcrypt для паролей, HS256 JWT
- PII masking в логах
- Audit trail для мутаций
- Rate limiting (расширен в этом ревью)
- Security headers (добавлены в этом ревью)
- Параметризованные SQL-запросы

Критических уязвимостей не обнаружено. Основные рекомендации на будущее:
- JWT blacklist для полноценного logout
- ORM для UPDATE-запросов
- Расширение PII sanitizer
- Secrets manager для production
