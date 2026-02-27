# Фаза 1: Accessibility Foundations

## Статус
- [x] Не начата
- [x] В процессе
- [x] Завершена

**Начата:** 2026-02-27
**Завершена:** 2026-02-27

## Цель фазы
Заложить базовый уровень accessibility (WCAG 2.1 AA): семантическая разметка, ARIA landmarks, skip-link, focus-visible стили, aria-live для уведомлений.

## Задачи

### 1.0 ОБЯЗАТЕЛЬНО: Анализ и планирование

#### A. Анализ существующего кода
- [x] Изучить `admin-ui/index.html` — текущая HTML-структура (sidebar, main content, modals)
- [x] Изучить `admin-ui/src/styles/main.css` — текущие focus-стили
- [x] Проверить текущее использование ARIA: `grep -rn "aria-\|role=" admin-ui/`
- [x] Изучить `admin-ui/src/notifications.js` — текущий toast-контейнер

**Команды для поиска:**
```bash
# Проверка текущих ARIA атрибутов
grep -rn "aria-\|role=" admin-ui/src/ admin-ui/index.html
# Проверка семантических тегов
grep -rn "<main\|<nav\|<aside\|<header\|<footer" admin-ui/index.html
# Проверка focus стилей
grep -rn "focus" admin-ui/src/styles/main.css
# Текущий toast контейнер
grep -rn "toastContainer\|showToast" admin-ui/src/
```

#### B. Анализ зависимостей
- [x] Нужны ли новые абстракции (Protocol)? — Нет, фронтенд
- [x] Нужны ли новые env variables? — Нет
- [x] Нужны ли новые tools для LLM-агента? — Нет
- [x] Нужны ли миграции БД (Alembic)? — Нет

**Новые абстракции:** Нет
**Новые env variables:** Нет
**Новые tools:** Нет
**Миграции БД:** Нет

#### C. Проверка архитектуры
- [x] Составить список всех мест где нужны ARIA landmarks
- [x] Определить приоритет: какие элементы наиболее важны для скринридеров
- [x] Проверить, не сломают ли ARIA-атрибуты существующие стили

**Референс-модуль:** `admin-ui/index.html`, `admin-ui/src/styles/main.css`

**Цель:** Понять текущее состояние accessibility ПЕРЕД внесением изменений.

**Заметки для переиспользования:**
- Sidebar уже `<nav>` тег (строка 42), но нет `aria-label`
- Main content — `<div class="main-content">` (строка 192), нужно `<main>`
- Toast — `#toastContainer` (строка 19), нет `aria-live`
- Единственный ARIA: `aria-label="Close"` в `help.js:15`
- CSS-селекторы не зависят от тега (используют классы), безопасно менять div→main
- `translateStaticDOM()` в `i18n.js` нужно расширить: добавить `data-i18n-aria` для aria-label
- Иконки sidebar: `color: rgb(163 163 163)` в `.nav-item` (main.css:58) — это neutral-400
- 17 nav-items + 2 group triggers + 5 footer controls = 24 элемента, всем нужен aria-label

---

### 1.1 ARIA Landmarks на основном layout

Добавить семантические роли в `admin-ui/index.html`:

- [x] Обернуть sidebar в `<nav role="navigation" aria-label="Main navigation">`
- [x] Добавить `role="main"` и `<main>` тег на контент-область (`#mainContent`)
- [x] Добавить `role="banner"` на header (`.topbar` или аналог) — **Нет topbar, пропущено**
- [x] Добавить `aria-label` ко всем навигационным группам (Training, System)

**Файлы:** `admin-ui/index.html`
**Заметки:** Не менять id/class — только добавлять семантические атрибуты. Проверить что CSS-селекторы не зависят от тега (div→nav).

---

### 1.2 Skip-link для клавиатурной навигации

- [x] Добавить скрытую ссылку `<a href="#mainContent" class="skip-link">Перейти к содержимому</a>` первым элементом `<body>`
- [x] Добавить CSS для skip-link: скрыт по умолчанию, виден при фокусе (`focus:not-sr-only`)
- [x] Добавить i18n ключ `common.skipToContent` в ru.js и en.js
- [x] Убедиться что `#mainContent` имеет `tabindex="-1"` для программного фокуса

**Файлы:** `admin-ui/index.html`, `admin-ui/src/styles/main.css`, `admin-ui/src/translations/ru.js`, `admin-ui/src/translations/en.js`
**Заметки:** skip-link — стандартная практика WCAG 2.4.1 (bypass blocks).

---

### 1.3 aria-label на icon-only кнопки sidebar

- [x] Добавить `aria-label` на каждую icon-only кнопку/ссылку в sidebar (10 nav items + hamburger)
- [x] Добавить `aria-label` на кнопку expand/collapse sidebar
- [x] Добавить `aria-label` на кнопку theme toggle
- [x] Добавить `aria-label` на кнопку language toggle
- [x] Добавить `aria-label` на кнопку logout
- [x] Использовать `data-i18n-aria="key"` для динамической локализации aria-label — расширена `translateStaticDOM()` + добавлена поддержка `data-i18n-title`

**Файлы:** `admin-ui/index.html`, `admin-ui/src/i18n.js`
**Заметки:** Значения aria-label брать из существующих tooltip-текстов. Если `translateStaticDOM()` не поддерживает aria-label — расширить функцию.

---

### 1.4 aria-live на toast-контейнер

- [x] Добавить `aria-live="polite"` и `role="status"` на `#toastContainer`
- [x] Добавить `aria-atomic="true"` чтобы скринридер озвучивал каждый toast целиком
- [x] Проверить что toast-текст не содержит HTML-тегов (только текст) — используется `textContent`

**Файлы:** `admin-ui/index.html`, `admin-ui/src/notifications.js`
**Заметки:** `polite` (не `assertive`) — toast не должны прерывать текущее озвучивание.

---

### 1.5 Улучшение focus-visible стилей

- [x] Добавить глобальный `:focus-visible` стиль в `main.css` (outline: 2px solid blue-500, offset 2px)
- [x] Убрать outline на `:focus:not(:focus-visible)` чтобы mouse-клик не показывал outline
- [x] Проверить что focus-visible работает на: кнопках, ссылках, инпутах, tab-элементах — глобальный стиль покрывает все элементы
- [x] Проверить контраст focus-outline в dark mode — `rgb(96 165 250)` (blue-400) на dark bg

**Файлы:** `admin-ui/src/styles/main.css`
**Заметки:** Tailwind 4.x поддерживает `focus-visible:` вариант нативно. Проверить совместимость.

---

### 1.6 Увеличение контраста иконок sidebar

- [x] Заменить `text-neutral-400` на `text-neutral-300` для иконок sidebar (inactive state) — .nav-item + .nav-flyout-link
- [x] Проверить что active state остаётся визуально отличимым — active = white (255), inactive = neutral-300 (212)
- [x] Проверить контраст в light mode — sidebar bg-zinc-950 в обоих темах, light flyout имеет свои цвета (:root:not(.dark))

**Файлы:** `admin-ui/index.html`
**Заметки:** Текущий контраст neutral-400 на zinc-950 ≈ 2.8:1 (ниже WCAG AA 3:1 для UI components). neutral-300 даст ≈ 4.2:1.

---

## При завершении фазы

Выполни следующие действия:

1. Убедись, что все задачи отмечены [x]
2. Измени статус фазы:
   - [x] Завершена
3. Заполни дату "Завершена: YYYY-MM-DD"
4. Выполни коммит:
   ```bash
   git add admin-ui/
   git commit -m "checklist(ux-improvements): phase-1 accessibility foundations completed"
   ```
5. Обнови PROGRESS.md:
   - Текущая фаза: 2
   - Добавь запись в историю
6. Открой следующую фазу и продолжи работу
