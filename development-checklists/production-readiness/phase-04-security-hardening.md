# Фаза 4: Security Hardening

## Статус
- [ ] Не начата
- [ ] В процессе
- [ ] Завершена

**Начата:** -
**Завершена:** -

## Цель фазы
Усилить безопасность API, расширить rate limiting за пределы login endpoint, провести OWASP-ревью, настроить расширенное сканирование зависимостей. После этой фазы все публичные эндпоинты защищены, а CI ловит уязвимости до production.

## Задачи

### 4.0 ОБЯЗАТЕЛЬНО: Анализ и планирование

#### A. Анализ существующего кода
- [ ] Изучить `src/api/auth.py` — текущий rate limiting (только login, 5 попыток / 15 мин)
- [ ] Изучить все API-роуты: `src/api/` — какие эндпоинты публичные, какие требуют auth
- [ ] Изучить `doc/security/` — существующая документация по безопасности (STRIDE, data policy)
- [ ] Изучить `src/logging/sanitizer.py` — PII sanitizer
- [ ] Изучить `.github/workflows/ci.yml` — текущий security job (pip-audit)
- [ ] Изучить `src/agent/tools.py` — валидация входных параметров tools

**Команды для поиска:**
```bash
# Все API эндпоинты
grep -rn "@app\.\|@router\." src/api/
# Middleware
grep -rn "middleware\|Middleware" src/
# Auth/RBAC
grep -rn "Depends.*auth\|require_role\|get_current_user" src/api/
# Rate limiting
grep -rn "rate_limit\|throttle\|slowapi" src/
# Input validation
grep -rn "Annotated\|Field\|validator\|Query\|Body" src/api/
# Security headers
grep -rn "CORS\|CSP\|X-Frame\|HSTS" src/
```

#### B. Анализ зависимостей
- [ ] Определить: использовать `slowapi` или кастомный middleware для rate limiting
- [ ] Определить: нужен ли WAF (Web Application Firewall) или достаточно middleware
- [ ] Определить: добавить ли SAST-сканер (bandit, semgrep) в CI

**Новые абстракции:** -
**Новые env variables:** -
**Новые tools:** -
**Миграции БД:** -

#### C. Проверка архитектуры
- [ ] Rate limiting должен быть распределённый (через Redis) для горизонтального масштабирования
- [ ] Rate limit конфигурация через env variables (не хардкод)
- [ ] Security headers не должны ломать admin UI

**Референс-модуль:** `src/api/auth.py` — текущий rate limiting на Redis

**Цель:** Понять текущую security-позицию и спроектировать расширение.

**Заметки для переиспользования:** -

---

### 4.1 Расширение API rate limiting

Сейчас rate limiting только на `/auth/login`. Нужно защитить все публичные эндпоинты.

- [ ] Выбрать подход: `slowapi` (FastAPI-совместимый) или кастомный middleware на Redis
- [ ] Реализовать глобальный rate limiter: 100 req/min per IP для всех эндпоинтов
- [ ] Реализовать per-user rate limiter: 60 req/min для аутентифицированных пользователей
- [ ] Добавить более строгие лимиты для чувствительных эндпоинтов:
  - `/auth/login` — 5 req/15 min (уже есть)
  - `/api/export/*` — 10 req/min (тяжёлые операции)
  - `/api/knowledge/` POST/PATCH/DELETE — 30 req/min (мутации)
- [ ] Добавить заголовки `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset`
- [ ] Возвращать `429 Too Many Requests` с `Retry-After` header при превышении
- [ ] Добавить Prometheus метрику `api_rate_limit_exceeded_total{endpoint, ip}`
- [ ] Конфигурация через env variables: `RATE_LIMIT_GLOBAL`, `RATE_LIMIT_PER_USER`
- [ ] Unit-тесты для rate limiter

**Файлы:** `src/api/`, `src/middleware/` (если новый файл)
**Заметки:** Использовать существующий Redis из `src/api/auth.py` как паттерн. Rate limiting должен быть stateless-compatible (Redis-backed).

---

### 4.2 Security headers и CORS

- [ ] Проверить текущую CORS конфигурацию в `src/main.py`
- [ ] Добавить security headers middleware:
  - `X-Content-Type-Options: nosniff`
  - `X-Frame-Options: DENY` (или `SAMEORIGIN` если admin UI в iframe)
  - `X-XSS-Protection: 0` (deprecated, но для legacy браузеров)
  - `Strict-Transport-Security: max-age=31536000; includeSubDomains` (HSTS)
  - `Content-Security-Policy` — ограничить источники скриптов для admin UI
  - `Referrer-Policy: strict-origin-when-cross-origin`
- [ ] Проверить что CORS разрешает только нужные origins (не `*` в production)
- [ ] Проверить что admin UI работает с новыми headers (CSP может сломать inline scripts)
- [ ] Unit-тесты: проверить наличие всех headers в ответах

**Файлы:** `src/main.py`
**Заметки:** Admin UI использует inline CSS и JS в `index.html` — CSP должен учитывать `'unsafe-inline'` или перенести стили/скрипты в отдельные файлы.

---

### 4.3 OWASP Top 10 ревью и исправления

- [ ] **Injection (A03:2021):** Проверить все SQL-запросы используют параметризованные запросы (SQLAlchemy)
- [ ] **Broken Authentication (A07:2021):** Проверить JWT/session management, password hashing (bcrypt)
- [ ] **Sensitive Data Exposure (A02:2021):** Проверить что PII sanitizer покрывает все логи
- [ ] **XML External Entities:** Не применимо (JSON API), но проверить нет ли XML-парсинга
- [ ] **Broken Access Control (A01:2021):** Проверить RBAC на всех admin эндпоинтах
- [ ] **Security Misconfiguration (A05:2021):** Проверить debug mode OFF в production, нет stack traces в ответах
- [ ] **Insecure Deserialization (A08:2021):** Проверить что Redis pickle/JSON десериализация безопасна
- [ ] Создать `doc/security/owasp-review.md` — результаты ревью с findings и mitigations
- [ ] Исправить все найденные уязвимости

**Файлы:** `src/`, `doc/security/owasp-review.md`
**Заметки:** Основные риски для этого проекта: prompt injection через LLM agent, PII в логах, RBAC bypass.

---

### 4.4 Расширение CI security pipeline

- [ ] Добавить `bandit` в CI — Python SAST-сканер (ищет hardcoded secrets, SQL injection, etc.)
- [ ] Добавить `safety` или расширить `pip-audit` — проверка CVE в зависимостях
- [ ] Добавить проверку secrets в коде: `detect-secrets` или `gitleaks` в CI
- [ ] Рассмотреть Dependabot: создать `.github/dependabot.yml` для автоматического обновления зависимостей
- [ ] Добавить pre-commit hook для security-проверок (опционально)
- [ ] Задокументировать security pipeline в `doc/security/ci-security.md`

**Файлы:** `.github/workflows/ci.yml`, `.github/dependabot.yml`, `doc/security/`
**Заметки:** bandit может давать false positives — настроить `.bandit` конфиг для исключений.

---

## При завершении фазы

Выполни следующие действия:

1. Убедись, что все задачи отмечены [x]
2. Измени статус фазы:
   - [x] Завершена
3. Заполни дату "Завершена: YYYY-MM-DD"
4. Выполни коммит:
   ```bash
   git add .
   git commit -m "checklist(production-readiness): phase-4 security hardening completed"
   ```
5. Обнови PROGRESS.md:
   - Текущая фаза: 5
   - Добавь запись в историю
6. Открой следующую фазу и продолжи работу
