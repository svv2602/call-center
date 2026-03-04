# Фаза 5: Audit & Monitoring Security

## Статус
- [ ] Не начата
- [ ] В процессе
- [x] Завершена

**Начата:** 2026-03-04
**Завершена:** 2026-03-04

## Цель фазы
Исправить audit logging, rate limiter fail mode, WebSocket auth, Prometheus cardinality.

## Задачи

### 5.0 ОБЯЗАТЕЛЬНО: Анализ и планирование

#### A. Анализ существующего кода
- [x] Прочитать `src/api/middleware/audit.py` — как записывается IP
- [x] Прочитать `src/api/middleware/rate_limit.py:79-81` — fail-open behavior
- [x] Прочитать `src/api/websocket.py:90-97` — JWT в query params
- [x] Прочитать `src/monitoring/metrics.py:238-243` — rate_limit labels

---

### 5.1 Исправить audit log IP capture
- [x] В `src/api/middleware/audit.py`: использовать `_get_client_ip(request)` из rate_limit.py
- [x] Логировать реальный client IP, не proxy IP

**Файлы:** `src/api/middleware/audit.py`
**Audit refs:** SEC-14

---

### 5.2 Добавить логирование successful logins
- [x] В `src/api/auth.py` POST `/auth/login` success path: записать в audit_log
- [x] Логировать: username, IP, timestamp, user_id (для DB users)
- [x] Не логировать пароль!

**Файлы:** `src/api/auth.py`
**Audit refs:** MED-2

---

### 5.3 Rate limiter: fail-closed option
- [x] В `src/api/middleware/rate_limit.py`: добавить env `RATE_LIMIT_FAIL_CLOSED=false`
- [x] При `true` + Redis down: возвращать 429 вместо allow
- [x] При `false` (default): текущее поведение (fail-open) для backward compat
- [x] Логировать WARNING при каждом fail event

**Файлы:** `src/api/middleware/rate_limit.py`
**Audit refs:** SEC-20

---

### 5.4 WebSocket: one-time ticket вместо JWT в URL
- [x] Создать endpoint `POST /auth/ws-ticket` — принимает JWT, возвращает one-time ticket (random token)
- [x] Сохранять ticket в Redis с TTL 30s: `ws_ticket:{token}` -> `user_id`
- [x] В `websocket_endpoint()`: читать `?ticket=` с fallback на legacy `?token=`
- [x] Валидировать ticket из Redis, удалить после использования (one-time)
- [x] Обновить `admin-ui/src/websocket.js` — сначала POST /auth/ws-ticket, потом connect с ticket

**Файлы:** `src/api/auth.py`, `src/api/websocket.py`, `admin-ui/src/websocket.js`
**Audit refs:** SEC-11

---

### 5.5 Убрать IP label из Prometheus counter
- [x] В `src/monitoring/metrics.py`: убрать `"ip"` из labels `rate_limit_exceeded_total`
- [x] В `src/api/middleware/rate_limit.py:190`: убрать `.labels(ip=ip)` — IP остаётся в logger.warning для audit trail

**Файлы:** `src/monitoring/metrics.py`, `src/api/middleware/rate_limit.py`
**Audit refs:** MED-1

---

### 5.6 Prompt injection mitigation для patterns
- [x] В `src/sandbox/patterns.py`: добавить `sanitize_guidance_note()` — length limit (max 500 chars), strip markdown/code blocks
- [x] Применить sanitization в `export_group_to_pattern()`
- [x] В API: `ExportPatternRequest.guidance_note` — `max_length=500`, `PatternUpdate.guidance_note` — `max_length=500`

**Файлы:** `src/sandbox/patterns.py`, `src/api/sandbox.py`
**Audit refs:** SEC-07, SEC-08

---

## При завершении фазы

1. Выполни коммит:
   ```bash
   git add src/api/ src/monitoring/ src/sandbox/ admin-ui/src/websocket.js tests/
   git commit -m "checklist(p0-security-hardening): phase-5 audit logging, rate limit, WebSocket tickets, prompt injection guard"
   ```
2. Обнови PROGRESS.md: Общий прогресс: 32/32 (100%)
3. Добавь в историю: "p0-security-hardening завершён"
