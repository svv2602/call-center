# Фаза 5: Admin UI Modularization

## Статус
- [ ] Не начата
- [ ] В процессе
- [x] Завершена

**Начата:** 2026-02-15
**Завершена:** 2026-02-15

## Цель фазы
Разбить монолитный `admin-ui/index.html` (1400+ строк) на модули с build pipeline. После этой фазы admin UI maintainable, testable и поддерживает hot-reload для разработки.

## Задачи

### 5.0 ОБЯЗАТЕЛЬНО: Анализ и планирование

#### A. Анализ существующего кода
- [x] Изучить `admin-ui/index.html` — структура: HTML, CSS (inline), JS (inline)
- [x] Определить логические компоненты:
  - Auth (login form, token management)
  - Dashboard (метрики, графики)
  - Call Journal (список звонков, фильтры, детали)
  - Operators (список, статусы, управление)
  - Prompts (A/B тесты, версии)
  - Knowledge Base (CRUD)
  - Settings (конфигурация)
  - WebSocket client (real-time updates)
  - Common (API client, utils, notifications)
- [x] Изучить зависимости между компонентами (shared state, API client)
- [x] Выбрать подход: Vite + vanilla JS modules vs lightweight framework

**Команды для поиска:**
```bash
# Размер файла
wc -l admin-ui/index.html
# Функции JS
grep -n "function " admin-ui/index.html
# CSS секции
grep -n "\/\*.*\*\/" admin-ui/index.html
# API вызовы
grep -n "fetch(" admin-ui/index.html
```

#### B. Анализ зависимостей
- [x] Нужен ли package.json? — Да (Vite, dev dependencies)
- [x] Нужен ли build step в CI? — Да
- [x] Нужны ли изменения в backend? — Минимальные (static files serving)
- [x] Нужна ли миграция данных? — Нет

**Новые файлы:** `admin-ui/package.json`, `admin-ui/vite.config.js`

#### C. Проверка архитектуры
- [x] Vite для bundling и dev server (hot-reload)
- [x] Vanilla JS modules (ES modules) — без тяжёлого framework
- [x] CSS в отдельных файлах, по компонентам
- [x] Build output → `admin-ui/dist/` → serve через FastAPI StaticFiles

**Референс-модуль:** текущий `admin-ui/index.html` (функциональность для сохранения)

**Цель:** Понять существующие паттерны проекта ПЕРЕД написанием кода.

**Заметки для переиспользования:** Выбран подход Vite + vanilla JS ES modules. Каждый page module экспортирует `init()` и регистрирует page loader. Функции для onclick-обработчиков HTML доступны через `window._pages.*` и `window._app.*`.

---

### 5.1 Инициализация Vite проекта

- [x] Создать `admin-ui/package.json` с Vite dependency
- [x] Создать `admin-ui/vite.config.js` — proxy к backend API для dev
- [x] Создать `admin-ui/index.html` (новый, minimal) — entry point с `<script type="module">`
- [x] Создать `admin-ui/src/main.js` — точка входа приложения
- [x] Проверить `npm run dev` — hot-reload работает
- [x] Проверить `npm run build` — production build в `dist/`

**Файлы:** `admin-ui/package.json`, `admin-ui/vite.config.js`, `admin-ui/src/main.js`
**Заметки:** Сохранить резервную копию `admin-ui/index.html` как `admin-ui/index.html.legacy`

---

### 5.2 Извлечение CSS

- [x] Создать `admin-ui/src/styles/variables.css` — CSS custom properties (цвета, шрифты, отступы)
- [x] Создать `admin-ui/src/styles/base.css` — общие стили (reset, layout, typography)
- [x] Создать `admin-ui/src/styles/components.css` — стили компонентов (cards, tables, forms, modals)
- [x] Создать `admin-ui/src/styles/responsive.css` — media queries (768px, 480px)
- [x] Импортировать все CSS в `main.js`
- [x] Проверить что все стили работают как раньше

**Файлы:** `admin-ui/src/styles/`
**Заметки:** -

---

### 5.3 Извлечение JS модулей — Core

- [x] `admin-ui/src/api.js` — API client (fetch wrapper, token management, base URL)
- [x] `admin-ui/src/auth.js` — login/logout, token storage, expiry check
- [x] `admin-ui/src/websocket.js` — WebSocket client, auto-reconnect, event dispatching
- [x] `admin-ui/src/router.js` — SPA navigation (показ/скрытие секций)
- [x] `admin-ui/src/notifications.js` — toast notifications
- [x] `admin-ui/src/utils.js` — форматирование дат, чисел, durations

**Файлы:** `admin-ui/src/`
**Заметки:** Каждый модуль — ES module с export/import

---

### 5.4 Извлечение JS модулей — Pages

- [x] `admin-ui/src/pages/dashboard.js` — dashboard: загрузка метрик, рендер карточек, графики
- [x] `admin-ui/src/pages/calls.js` — call journal: фильтры, таблица, детали звонка
- [x] `admin-ui/src/pages/operators.js` — операторы: список, статусы, очередь
- [x] `admin-ui/src/pages/prompts.js` — промпты: CRUD, A/B тесты, версии
- [x] `admin-ui/src/pages/knowledge.js` — база знаний: CRUD
- [x] `admin-ui/src/pages/settings.js` — настройки
- [x] Проверить что вся функциональность работает как в монолите

**Файлы:** `admin-ui/src/pages/`
**Заметки:** Добавлены также `users.js` и `audit.js` (admin-only страницы)

---

### 5.5 CI интеграция

- [x] Добавить build step в `.github/workflows/ci.yml`: `npm ci && npm run build`
- [x] Обновить `Dockerfile` — собирать admin UI при build (multi-stage: node:20-slim → npm run build)
- [x] Обновить FastAPI для serving из `admin-ui/dist/` в production, `admin-ui/` в dev
- [x] Обновить `.gitignore` — добавить `admin-ui/node_modules/`, `admin-ui/dist/`

**Файлы:** `.github/workflows/ci.yml`, `Dockerfile`, `src/main.py`, `.gitignore`
**Заметки:** FastAPI auto-detects dist/ vs root directory. Dockerfile uses multi-stage build with ui-builder stage.

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
   git commit -m "checklist(post-production-improvements): phase-5 admin UI modularization completed"
   ```
5. Обнови PROGRESS.md:
   - Текущая фаза: 6
   - Добавь запись в историю
6. Открой следующую фазу и продолжи работу
