# Фаза 4: TLS & Headers

## Статус
- [ ] Не начата
- [ ] В процессе
- [x] Завершена

**Начата:** 2026-03-04
**Завершена:** 2026-03-04

## Цель фазы
Настроить TLS termination на nginx. Исправить security headers. Валидировать X-Forwarded-For.

## Задачи

### 4.0 ОБЯЗАТЕЛЬНО: Анализ и планирование

#### A. Анализ существующего кода
- [x] Прочитать `nginx/default.conf` — текущая конфигурация
- [x] Прочитать `scripts/setup_nginx_grafana.sh` — скрипт настройки
- [x] Прочитать `src/api/middleware/security_headers.py` — CSP, HSTS
- [x] Прочитать `src/api/middleware/rate_limit.py:84-89` — `_get_client_ip()`
- [x] Определить: self-signed cert для internal deployment

---

### 4.1 TLS termination в nginx
- [x] Создать `scripts/generate-self-signed-cert.sh` для internal deployment
- [x] Обновить `nginx/default.conf`: добавить `server` block на 443 с SSL
- [x] Добавить redirect HTTP -> HTTPS: `return 301 https://$host$request_uri;` (с исключением /health)
- [x] Добавить SSL params: `ssl_protocols TLSv1.2 TLSv1.3;`, `ssl_ciphers`, `ssl_prefer_server_ciphers`
- [x] Добавить `nginx/ssl/` в `.gitignore`
- [x] Примечание: docker-compose.yml nginx service не включён в compose — nginx запускается как системный сервис на хосте. Cert volume нужно настроить при деплое.

**Файлы:** `nginx/default.conf`, `scripts/generate-self-signed-cert.sh`, `.gitignore`
**Audit refs:** CRIT-06, SEC-12, SEC-18

---

### 4.2 Исправить X-Forwarded-For validation
- [x] В `src/api/middleware/rate_limit.py`: `_get_client_ip()` — доверять XFF только если `request.client.host` в trusted proxy set
- [x] Добавить env variable `TRUSTED_PROXY_IPS` (default: `127.0.0.1,172.16.0.0/12,10.0.0.0/8,192.168.0.0/16`)
- [x] В `src/config.py`: добавить `TrustedProxySettings` с `ips: str`
- [x] Если request не от trusted proxy: использовать `request.client.host`
- [x] Написать тест: spoofed XFF от untrusted IP -> используется client.host
- [x] Написать тест: XFF от trusted proxy -> используется XFF value
- [x] Написать тест: CIDR range в trusted proxies

**Файлы:** `src/api/middleware/rate_limit.py`, `src/config.py`, `tests/unit/test_xff_validation.py`
**Audit refs:** SEC-01, HIGH-4

---

### 4.3 Исправить HSTS header
- [x] В `src/api/middleware/security_headers.py`: HSTS только если request пришёл через HTTPS
- [x] Проверять `X-Forwarded-Proto: https` от nginx
- [x] Убрать `includeSubDomains` (не нужно для internal service)

**Файлы:** `src/api/middleware/security_headers.py`
**Audit refs:** SEC-12

---

### 4.4 Улучшить CSP
- [x] Оценить возможность замены `'unsafe-inline'` на nonce-based CSP — невозможно без рефакторинга SPA
- [x] Задокументировать причину в коде: Admin UI (Vite SPA) использует inline scripts в index.html
- [x] Проверить что `frame-src 'self'` корректен для Grafana iframe — да, корректен

**Файлы:** `src/api/middleware/security_headers.py`
**Audit refs:** SEC-13

---

## При завершении фазы

1. Выполни коммит:
   ```bash
   git add nginx/ src/api/middleware/ src/config.py .gitignore scripts/ tests/
   git commit -m "checklist(p0-security-hardening): phase-4 TLS termination, XFF validation, security headers"
   ```
2. Обнови PROGRESS.md: Текущая фаза: 5
