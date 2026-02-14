# Фаза 1: Security & Auth

## Статус
- [x] Не начата
- [x] В процессе
- [x] Завершена

**Начата:** 2026-02-14
**Завершена:** 2026-02-14

## Цель фазы
Реализовать полноценный JWT logout через Redis blacklist и расширить PII sanitizer для маскирования email, адресов и номеров карт. После этой фазы безопасность аутентификации и защита персональных данных значительно усилены.

## Задачи

### 1.0 ОБЯЗАТЕЛЬНО: Анализ и планирование

#### A. Анализ существующего кода
- [x] Изучить `src/api/auth.py` — текущая JWT логика (login, token generation, decode)
- [x] Изучить `src/logging/pii_sanitizer.py` — текущие паттерны маскирования (телефоны, имена)
- [x] Изучить `src/config.py` — как добавлять новые env variables
- [x] Изучить как Redis используется в проекте (сессии, rate limiting, pub/sub)

**Команды для поиска:**
```bash
# JWT логика
grep -rn "jwt\|JWT\|token\|decode" src/api/auth.py
# PII sanitizer
cat src/logging/pii_sanitizer.py
# Redis usage
grep -rn "redis\|Redis\|aioredis" src/
# Middleware паттерны
ls src/api/middleware/
```

#### B. Анализ зависимостей
- [x] Нужны ли новые абстракции (Protocol)? — Нет
- [x] Нужны ли новые env variables? — Да: `JWT_BLACKLIST_TTL`
- [x] Нужны ли новые tools для LLM-агента? — Нет
- [x] Нужны ли миграции БД (Alembic)? — Нет (Redis-only)

**Новые env variables:** `JWT_BLACKLIST_TTL` (default: время жизни access token)
**Миграции БД:** нет

#### C. Проверка архитектуры
- [x] Redis blacklist — использовать SETEX с TTL = время жизни токена
- [x] Проверка blacklist — middleware или декоратор на каждый protected endpoint
- [x] PII регулярные выражения — расширить существующий sanitizer

**Референс-модуль:** `src/api/middleware/rate_limit.py` (паттерн Redis в middleware)

**Цель:** Понять существующие паттерны проекта ПЕРЕД написанием кода.

**Заметки для переиспользования:** -

---

### 1.1 JWT Blacklist (полноценный logout)

- [x] Создать функцию `blacklist_token(jti: str, ttl: int)` — добавить JTI в Redis с TTL
- [x] Создать функцию `is_token_blacklisted(jti: str) -> bool` — проверить JTI в Redis
- [x] Добавить `jti` (JWT ID) в payload токена при генерации (если не добавлен)
- [x] Обновить endpoint `/auth/logout` — вызывать `blacklist_token()` при logout
- [x] Обновить middleware/dependency `get_current_user` — проверять blacklist перед допуском
- [x] Добавить Prometheus метрику `jwt_blacklist_size` (Gauge) или `jwt_logouts_total` (Counter)
- [x] Unit-тесты: logout инвалидирует токен, повторный запрос с тем же токеном → 401

**Файлы:** `src/api/auth.py`, `src/monitoring/metrics.py`, `tests/unit/test_auth.py`
**Redis key pattern:** `jwt_blacklist:{jti}` с TTL = JWT expiry time
**Заметки:** -

---

### 1.2 Расширение PII Sanitizer

- [x] Добавить маскирование email: `user@example.com` → `u***@***.com`
- [x] Добавить маскирование номеров карт: `4111 1111 1111 1111` → `4111 **** **** 1111`
- [x] Добавить маскирование адресов (ул., пр., бульв. + номер дома)
- [x] Добавить маскирование IBAN: `UA21 3223 1300 0002 6007 2335 6600 1` → `UA21 **** **** 6001`
- [x] Unit-тесты для каждого нового паттерна маскирования
- [x] Проверить что существующие тесты не сломались

**Файлы:** `src/logging/pii_sanitizer.py`, `tests/unit/test_pii_sanitizer.py`
**Заметки:** Регулярные выражения должны работать с украинскими и русскими форматами

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
   git commit -m "checklist(post-production-improvements): phase-1 security and auth completed"
   ```
5. Обнови PROGRESS.md:
   - Текущая фаза: 2
   - Добавь запись в историю
6. Открой следующую фазу и продолжи работу
