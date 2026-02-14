# Фаза 4: RBAC и безопасность

## Статус
- [x] Не начата
- [x] В процессе
- [x] Завершена

**Начата:** 2026-02-14
**Завершена:** 2026-02-14

## Цель фазы
Реализовать многопользовательский доступ с ролями.
Добавить аудит-лог действий администратора. Усилить безопасность сессий.

## Задачи

### 4.0 ОБЯЗАТЕЛЬНО: Анализ и планирование

#### A. Анализ существующего кода
- [x] Изучить `src/api/auth.py` — текущая JWT-авторизация (create_jwt, verify_jwt, require_admin)
- [x] Изучить `src/config.py` → `AdminSettings` — текущий механизм хранения credentials
- [x] Изучить все `require_admin()` зависимости в роутерах
- [x] Проверить, как admin UI хранит и использует JWT-токен

**Команды для поиска:**
```bash
# Текущая авторизация
grep -rn "require_admin\|verify_jwt\|create_jwt" src/
# Использование в роутерах
grep -rn "Depends.*require_admin\|Depends.*auth" src/api/
# JWT payload
grep -rn "jwt.encode\|jwt.decode" src/api/auth.py
```

#### B. Анализ зависимостей
- [x] Нужны ли миграции БД? (ДА — таблицы admin_users, admin_audit_log)
- [x] Нужны ли новые пакеты? (`passlib[bcrypt]` для хэширования паролей)
- [x] Нужны ли новые env variables? (нет, пользователи в БД)

**Новые пакеты:** `passlib[bcrypt]`
**Новые env variables:** `ADMIN_INITIAL_PASSWORD` (для первоначального seed)
**Миграции БД:** `007_add_admin_users.py`

#### C. Проверка архитектуры
- [x] Роли: `admin` (полный доступ), `analyst` (только чтение аналитики), `operator` (только операторский интерфейс)
- [x] Пароли — bcrypt hash в PostgreSQL
- [x] JWT payload включает `role` field
- [x] Middleware для проверки ролей: `require_role("admin")`, `require_role("analyst", "admin")`

**Референс-модуль:** `src/api/auth.py` (текущая авторизация)

**Цель:** Спроектировать RBAC-модель с минимальными изменениями существующего кода.

**Заметки для переиспользования:** -

---

### 4.1 Миграция БД: таблицы admin_users и admin_audit_log
- [x] Создать миграцию `migrations/versions/007_add_admin_users.py`
- [x] Таблица `admin_users`: id, username (unique), password_hash, role (enum: admin/analyst/operator), is_active, created_at, last_login_at
- [x] Таблица `admin_audit_log`: id, user_id (FK), action (varchar), resource_type (varchar), resource_id (varchar), details (jsonb), ip_address (inet), created_at
- [x] Индексы: `admin_audit_log(user_id)`, `admin_audit_log(created_at)`, `admin_users(username)`
- [x] Seed: создать начального admin-пользователя из env `ADMIN_INITIAL_PASSWORD`
- [x] Написать тесты миграции

**Файлы:** `migrations/versions/007_add_admin_users.py`, `tests/unit/test_migrations.py`
**Заметки:** `admin_audit_log` не партиционируется (низкий объём). Retention — 1 год.

---

### 4.2 Модель данных и репозиторий
- [x] Создать `src/api/models/admin_user.py` — SQLAlchemy модель AdminUser
- [x] Создать `src/api/models/audit_log.py` — SQLAlchemy модель AuditLog
- [x] Создать `src/api/repositories/admin_repo.py` — CRUD для пользователей (create, get_by_username, update_last_login, list, deactivate)
- [x] Создать `src/api/repositories/audit_repo.py` — запись и чтение аудит-лога
- [x] Написать unit-тесты

**Файлы:** `src/api/models/admin_user.py`, `src/api/models/audit_log.py`, `src/api/repositories/admin_repo.py`, `src/api/repositories/audit_repo.py`
**Заметки:** Паттерн — асинхронный SQLAlchemy с `async_session`.

---

### 4.3 Обновить авторизацию
- [x] Обновить `src/api/auth.py` — проверка credentials через БД вместо env variables
- [x] Хэширование паролей через `bcrypt` (raw bcrypt вместо passlib из-за совместимости с Python 3.13)
- [x] JWT payload: добавить `role`, `user_id`
- [x] Создать dependency `require_role(*roles)` — проверяет роль из JWT
- [x] Сохранить обратную совместимость: если таблица `admin_users` пуста, fallback на env credentials
- [x] Написать тесты: `tests/unit/test_auth_rbac.py`

**Файлы:** `src/api/auth.py`, `tests/unit/test_auth_rbac.py`
**Заметки:** `require_admin()` заменить на `require_role("admin")`. Для аналитических эндпоинтов — `require_role("admin", "analyst")`.

---

### 4.4 Защита эндпоинтов по ролям
- [x] `POST /prompts`, `PATCH /prompts/{id}/activate` — только `admin`
- [x] `POST /prompts/ab-tests`, `PATCH /prompts/ab-tests/{id}/stop` — только `admin`
- [x] `GET /analytics/*` — `admin`, `analyst`
- [x] `POST /knowledge/*`, `PATCH /knowledge/*`, `DELETE /knowledge/*` — только `admin`
- [x] `GET /knowledge/*` — `admin`, `analyst`
- [x] Health-check эндпоинты — без авторизации (для мониторинга)

**Файлы:** `src/api/analytics.py`, `src/api/prompts.py`, `src/api/knowledge.py`
**Заметки:** Заменить `Depends(require_admin)` на `Depends(require_role("admin"))` или `Depends(require_role("admin", "analyst"))`.

---

### 4.5 Аудит-лог middleware
- [x] Создать `src/api/middleware/audit.py` — FastAPI middleware
- [x] Логировать все мутирующие запросы (POST, PATCH, DELETE) в `admin_audit_log`
- [x] Записывать: user_id (из JWT), action (HTTP method + path), resource_type, resource_id (из URL), ip_address, details (request body summary)
- [x] НЕ логировать GET-запросы (слишком много)
- [x] НЕ логировать health-check и metrics
- [x] Написать тесты

**Файлы:** `src/api/middleware/audit.py`, `tests/unit/test_audit_middleware.py`
**Заметки:** Middleware должен быть лёгким — асинхронная запись в БД, не блокировать response.

---

### 4.6 API управления пользователями
- [x] `GET /admin/users` — список пользователей (только admin)
- [x] `POST /admin/users` — создание пользователя (username, password, role) (только admin)
- [x] `PATCH /admin/users/{id}` — изменение роли, активация/деактивация (только admin)
- [x] `POST /admin/users/{id}/reset-password` — сброс пароля (только admin)
- [x] `GET /admin/audit-log` — просмотр аудит-лога с фильтрами (только admin)
- [x] Написать тесты: `tests/unit/test_admin_users_api.py`

**Файлы:** `src/api/admin_users.py`, `tests/unit/test_admin_users_api.py`
**Заметки:** Паттерн — аналогично `src/api/prompts.py` (CRUD + фильтры).

---

### 4.7 Admin UI: управление пользователями
- [x] Добавить страницу «Пользователи» в навигацию (видна только admin)
- [x] Таблица пользователей: username, role, is_active, last_login
- [x] Форма создания пользователя
- [x] Кнопки: деактивировать, сменить роль, сбросить пароль
- [x] Скрывать элементы интерфейса по роли (analyst не видит управление промптами)

**Файлы:** `admin-ui/index.html`
**Заметки:** Роль пользователя доступна из JWT payload (декодировать на клиенте).

---

### 4.8 Admin UI: аудит-лог
- [x] Добавить страницу «Аудит-лог» в навигацию (видна только admin)
- [x] Таблица: дата, пользователь, действие, ресурс, IP
- [x] Фильтры: по пользователю, по типу действия, по дате
- [x] Пагинация

**Файлы:** `admin-ui/index.html`
**Заметки:** -

---

### 4.9 Управление сессиями
- [x] Добавить `POST /auth/logout` — инвалидация текущего JWT (через Redis blacklist)
- [x] Настраиваемый JWT TTL через `AdminSettings.jwt_ttl_hours` (по умолчанию 24)
- [x] Rate limiting на `POST /auth/login`: максимум 5 попыток за 15 минут (через Redis counter)
- [x] Логирование неудачных попыток входа в аудит-лог
- [x] Написать тесты

**Файлы:** `src/api/auth.py`, `tests/unit/test_auth_session.py`
**Заметки:** JWT blacklist в Redis: `SET jwt_blacklist:{jti} 1 EX {remaining_ttl}`. `jti` — уникальный идентификатор токена.

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
   git commit -m "checklist(admin-convenience): phase-4 RBAC and security completed"
   ```
5. Обнови PROGRESS.md:
   - Текущая фаза: 5
   - Добавь запись в историю
6. Открой следующую фазу и продолжи работу
