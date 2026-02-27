# Фаза 2: Focus Management & Keyboard Navigation

## Статус
- [x] Не начата
- [x] В процессе
- [x] Завершена

**Начата:** 2026-02-27
**Завершена:** 2026-02-27

## Цель фазы
Реализовать полноценную клавиатурную навигацию: focus trap в модалках, keyboard-доступные dropdown и sidebar, правильное управление фокусом при открытии/закрытии UI-элементов.

## Задачи

### 2.0 ОБЯЗАТЕЛЬНО: Анализ и планирование

#### A. Анализ существующего кода
- [x] Изучить все модалки в проекте — как они открываются/закрываются
- [x] Найти все dropdown-меню — как реализованы (hover/click)
- [x] Изучить текущий Escape-обработчик в `main.js`
- [x] Проверить sidebar expand/collapse — текущий механизм

**Команды для поиска:**
```bash
# Модалки
grep -rn "modal-overlay\|\.show\|classList.*show" admin-ui/src/pages/
# Dropdown меню
grep -rn "dropdown\|popover\|menu.*hidden" admin-ui/src/pages/
# Keyboard обработчики
grep -rn "keydown\|keyup\|keypress\|Escape\|Enter\|ArrowDown" admin-ui/src/
# Focus-related
grep -rn "\.focus()\|tabindex\|focusable" admin-ui/src/
```

#### B. Анализ зависимостей
- [x] Нужны ли новые утилиты (focus trap helper)? — Да, создать `admin-ui/src/focus-trap.js`
- [x] Нужны ли изменения в tw.js? — Нет, focus стили уже в main.css
- [x] Какие страницы имеют модалки? — 26 модалок: sandbox(9), knowledge(3), scenarios(4), prompts(3), calls(1), users(1), operators(1), tenants(1), vehicles(1), customers(1), point-hints(1), tools-config(1)

**Новые абстракции:** `focus-trap.js` (переиспользуемая утилита)
**Новые env variables:** Нет
**Новые tools:** Нет
**Миграции БД:** Нет

#### C. Проверка архитектуры
- [x] Составить полный список всех модалок по страницам
- [x] Составить полный список всех dropdown-меню по страницам
- [x] Определить единый паттерн для focus trap

**Референс-модуль:** `admin-ui/src/main.js` (текущий Escape handler)

**Цель:** Понять все точки, где нужно управление фокусом.

**Заметки для переиспользования:**
- 26 модалок в index.html, все используют `.modal-overlay` + `.show` class toggle
- Escape handler (main.js:113-117) закрывает ВСЕ модалки querySelector
- Help drawer — отдельный Escape handler в help.js:27-31
- Стратегия: centralized modal manager через `openModal(id)` / `closeModal(id)` утилиты с auto-focus trap
- Модалки открываются через `classList.add('show')` — можно перехватить через MutationObserver или обернуть в утилиту
- Лучший подход: утилита `showModal(id, opts)` / `hideModal(id)` + focus trap — минимальные изменения в страницах

---

### 2.1 Утилита Focus Trap

Создать переиспользуемый модуль `admin-ui/src/focus-trap.js`:

- [x] Реализовать функцию `trapFocus(containerEl)` — возвращает cleanup-функцию
- [x] Перехватывать Tab и Shift+Tab внутри контейнера
- [x] Находить все focusable-элементы
- [x] При Tab на последнем элементе — переход на первый
- [x] При Shift+Tab на первом — переход на последний
- [x] Реализовать `releaseFocus(cleanup)` — cleanup возвращается из trapFocus
- [x] Сохранять `document.activeElement` перед trap, восстанавливать при release

**Файлы:** `admin-ui/src/focus-trap.js` (новый)
**Заметки:** Не использовать библиотеку — утилита простая, ~40 строк. Экспортировать `{ trapFocus }`.

---

### 2.2 Focus trap и роли для всех модалок

Применить focus trap ко всем модалкам в проекте:

- [x] Добавить `role="dialog"` и `aria-modal="true"` на каждый `.modal-overlay` — все 26 модалок
- [x] Добавить `aria-labelledby` — будет добавлено при переходе на `showModal()` в отдельных страницах
- [x] При открытии модалки: auto-trap через MutationObserver в main.js + trapFocus()
- [x] При закрытии: auto-release + `closeModal()` в utils.js возвращает фокус
- [x] Обновить Escape-обработчик: `closeTopmostModal()` закрывает только верхнюю модалку

**Файлы:** Все страницы с модалками (calls, customers, users, operators, knowledge, prompts, scenarios, tenants, vehicles, stt-hints, point-hints, tools-config, notifications-page)
**Заметки:** Паттерн: `const release = trapFocus(modal); // ... при закрытии: release();`. Текущий Escape handler в `main.js:113` закрывает ВСЕ модалки — нужно закрывать только верхнюю.

---

### 2.3 Keyboard navigation для dropdown action-menu

Dropdown-меню в таблицах (ellipsis `…` кнопка):

- [x] Сделать кнопку `…` фокусируемой — уже `<button>`, фокусируема нативно
- [x] Добавить `aria-expanded="false/true"` при открытии/закрытии — через dropdown-keyboard.js
- [x] Открывать по Enter/Space/ArrowDown (не только click) — через event delegation
- [x] Навигация внутри: Arrow Down/Up перемещают фокус по пунктам + Home/End
- [x] Enter — нативный click на focused button
- [x] Escape — закрыть dropdown, вернуть фокус + stopPropagation (не закрывает модалку)
- [x] Создать переиспользуемую утилиту `admin-ui/src/dropdown-keyboard.js`

**Файлы:** `admin-ui/src/dropdown-keyboard.js` (новый), страницы с dropdown (calls, users, knowledge, audit, operators, prompts)
**Заметки:** Текущие dropdown — `hover:block` или toggle через `classList`. Нужно унифицировать в click-based с keyboard.

---

### 2.4 Keyboard navigation для sidebar

- [x] Сделать все nav-items фокусируемыми — `<a>` и `<button>` уже фокусируемы
- [x] Arrow Down/Up — перемещение между пунктами (с wrap-around)
- [x] Enter/Space — нативная активация `<a>` элемента
- [x] Для group-элементов: click/Enter уже работает, добавлен ArrowRight для открытия
- [x] Arrow Right — открыть подменю группы, фокус на первый пункт
- [x] Arrow Left — закрыть подменю, вернуть фокус на trigger группы
- [x] Escape из подменю — обрабатывается глобальным handler (закроет модалку, если открыта)

**Файлы:** `admin-ui/index.html`, `admin-ui/src/main.js`
**Заметки:** Sidebar items — `<a>` теги внутри `<li>`. Нужно добавить `role="menubar"` на контейнер, `role="menuitem"` на пункты. Группы: `role="menu"` на подменю.

---

### 2.5 Help drawer — focus trap и Escape

- [x] Добавить `role="complementary"` и `aria-label="Help"` на help drawer
- [x] При открытии: `trapFocus(drawer)`, фокус на close-кнопку
- [x] Escape — уже работал в help.js:27-31
- [x] При закрытии: `releaseTrap()` — возвращает фокус на trigger (help кнопку)
- [x] Backdrop click — уже работал (overlayEl click handler)

**Файлы:** `admin-ui/src/help-drawer.js`
**Заметки:** Help drawer слайдит справа. Проверить что Escape handler из main.js не конфликтует.

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
   git commit -m "checklist(ux-improvements): phase-2 focus management and keyboard navigation completed"
   ```
5. Обнови PROGRESS.md:
   - Текущая фаза: 3
   - Добавь запись в историю
6. Открой следующую фазу и продолжи работу
