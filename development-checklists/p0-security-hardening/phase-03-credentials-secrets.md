# Фаза 3: Credentials & Secrets

## Статус
- [ ] Не начата
- [ ] В процессе
- [x] Завершена

**Начата:** 2026-03-04
**Завершена:** 2026-03-04

## Цель фазы
Убрать все default credentials из production path. Добавить fail-fast проверки при старте.

## Задачи

### 3.0 ОБЯЗАТЕЛЬНО: Анализ и планирование

#### A. Анализ существующего кода
- [x] Прочитать `src/config.py` — все default значения credentials
- [x] Прочитать `src/main.py:2093-2107` — существующая startup validation для JWT secret
- [x] Прочитать `.env.example` — какие credentials документированы
- [x] Прочитать `docker-compose.staging.yml` — fallback defaults

#### B. Анализ зависимостей
- [x] Какие credentials блокируют startup, какие — нет?
- [x] Есть ли тесты которые зависят от default credentials?

---

### 3.1 Startup fail-fast для критических credentials
- [x] В `src/main.py` startup: проверять `ADMIN_PASSWORD != "admin"` в production (Docker path check)
- [x] Проверять `STORE_API_KEY` не является `test-store-api-key`
- [x] Проверять `GRAFANA_ADMIN_PASSWORD` не является `admin` (через env variable check)
- [x] Логировать WARNING (не блокировать) для `ARI_PASSWORD == "ari_password"`
- [x] Убедиться что существующая проверка JWT secret работает и в staging

**Файлы:** `src/main.py`, `src/config.py`
**Audit refs:** CRIT-05, SEC-05

---

### 3.2 Использовать `${VAR:?error}` в docker-compose
- [x] В `docker-compose.yml`: `ADMIN_JWT_SECRET=${ADMIN_JWT_SECRET:?Set ADMIN_JWT_SECRET in .env}`
- [x] `POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?Set POSTGRES_PASSWORD}`
- [x] `ADMIN_PASSWORD=${ADMIN_PASSWORD:?Set ADMIN_PASSWORD}`
- [x] Проверить что `FLOWER_BASIC_AUTH` уже использует `:?` — да, подтверждено
- [x] В `docker-compose.staging.yml`: заменить все `:-fallback` на `:?error`

**Файлы:** `docker-compose.yml`, `docker-compose.staging.yml`
**Audit refs:** SEC-05, SEC-15

---

### 3.3 Очистить .env.example
- [x] Заменить `ONEC_PASSWORD=44332211` на `ONEC_PASSWORD=your-1c-password`
- [x] Заменить все реальные-looking значения на placeholder-ы
- [x] Добавить комментарии для обязательных переменных: `# REQUIRED`
- [x] Добавить новые переменные: `REDIS_PASSWORD`, `INTERNAL_API_SECRET`, `METRICS_BEARER_TOKEN`, `ADMIN_JWT_SECRET`, `ADMIN_PASSWORD`, `GRAFANA_ADMIN_PASSWORD`, `POSTGRES_PASSWORD`, `FLOWER_BASIC_AUTH`

**Файлы:** `.env.example`
**Audit refs:** SEC-21

---

### 3.4 Тесты startup validation
- [x] Тест: startup с default JWT secret в Docker path -> SystemExit
- [x] Тест: startup с default admin password в Docker path -> SystemExit
- [x] Тест: startup с кастомными credentials -> OK
- [x] Убедиться что существующие тесты используют `create_jwt` с test secret и не ломаются

**Файлы:** `tests/unit/test_startup_validation.py` (новый)

---

## При завершении фазы

1. Убедись, что все задачи отмечены [x]
2. Выполни коммит:
   ```bash
   git add src/main.py src/config.py docker-compose*.yml .env.example tests/
   git commit -m "checklist(p0-security-hardening): phase-3 credentials — fail-fast on defaults, clean .env.example"
   ```
3. Обнови PROGRESS.md: Текущая фаза: 4
