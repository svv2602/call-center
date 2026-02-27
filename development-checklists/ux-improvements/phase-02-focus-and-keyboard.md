# Фаза 2: Focus Management & Keyboard Navigation

## Статус
- [ ] Не начата
- [ ] В процессе
- [ ] Завершена

**Начата:** -
**Завершена:** -

## Цель фазы
Реализовать полноценную клавиатурную навигацию: focus trap в модалках, keyboard-доступные dropdown и sidebar, правильное управление фокусом при открытии/закрытии UI-элементов.

## Задачи

### 2.0 ОБЯЗАТЕЛЬНО: Анализ и планирование

#### A. Анализ существующего кода
- [ ] Изучить все модалки в проекте — как они открываются/закрываются
- [ ] Найти все dropdown-меню — как реализованы (hover/click)
- [ ] Изучить текущий Escape-обработчик в `main.js`
- [ ] Проверить sidebar expand/collapse — текущий механизм

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
- [ ] Нужны ли новые утилиты (focus trap helper)? — Да, создать `admin-ui/src/focus-trap.js`
- [ ] Нужны ли изменения в tw.js? — Возможно, для focus стилей
- [ ] Какие страницы имеют модалки? — Составить полный список

**Новые абстракции:** `focus-trap.js` (переиспользуемая утилита)
**Новые env variables:** Нет
**Новые tools:** Нет
**Миграции БД:** Нет

#### C. Проверка архитектуры
- [ ] Составить полный список всех модалок по страницам
- [ ] Составить полный список всех dropdown-меню по страницам
- [ ] Определить единый паттерн для focus trap

**Референс-модуль:** `admin-ui/src/main.js` (текущий Escape handler)

**Цель:** Понять все точки, где нужно управление фокусом.

**Заметки для переиспользования:** -

---

### 2.1 Утилита Focus Trap

Создать переиспользуемый модуль `admin-ui/src/focus-trap.js`:

- [ ] Реализовать функцию `trapFocus(containerEl)` — возвращает cleanup-функцию
- [ ] Перехватывать Tab и Shift+Tab внутри контейнера
- [ ] Находить все focusable-элементы: `a[href], button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])`
- [ ] При Tab на последнем элементе — переход на первый
- [ ] При Shift+Tab на первом — переход на последний
- [ ] Реализовать `releaseFocus(cleanup)` — восстановить предыдущее состояние
- [ ] Сохранять `document.activeElement` перед trap, восстанавливать при release

**Файлы:** `admin-ui/src/focus-trap.js` (новый)
**Заметки:** Не использовать библиотеку — утилита простая, ~40 строк. Экспортировать `{ trapFocus }`.

---

### 2.2 Focus trap и роли для всех модалок

Применить focus trap ко всем модалкам в проекте:

- [ ] Добавить `role="dialog"` и `aria-modal="true"` на каждый `.modal-overlay`
- [ ] Добавить `aria-labelledby` (указать на заголовок модалки)
- [ ] При открытии модалки: вызвать `trapFocus(modalEl)`, установить фокус на первый input или close-кнопку
- [ ] При закрытии: вызвать cleanup, вернуть фокус на trigger-элемент
- [ ] Обновить Escape-обработчик: закрывать текущую модалку (ближайшую к фокусу), не все подряд

**Файлы:** Все страницы с модалками (calls, customers, users, operators, knowledge, prompts, scenarios, tenants, vehicles, stt-hints, point-hints, tools-config, notifications-page)
**Заметки:** Паттерн: `const release = trapFocus(modal); // ... при закрытии: release();`. Текущий Escape handler в `main.js:113` закрывает ВСЕ модалки — нужно закрывать только верхнюю.

---

### 2.3 Keyboard navigation для dropdown action-menu

Dropdown-меню в таблицах (ellipsis `…` кнопка):

- [ ] Сделать кнопку `…` фокусируемой (`<button>` с `aria-haspopup="true"`)
- [ ] Добавить `aria-expanded="false/true"` при открытии/закрытии
- [ ] Открывать по Enter/Space (не только click)
- [ ] Навигация внутри: Arrow Down/Up перемещают фокус по пунктам
- [ ] Enter — выполнить выбранный пункт
- [ ] Escape — закрыть dropdown, вернуть фокус на кнопку `…`
- [ ] Создать переиспользуемую утилиту `admin-ui/src/dropdown-keyboard.js`

**Файлы:** `admin-ui/src/dropdown-keyboard.js` (новый), страницы с dropdown (calls, users, knowledge, audit, operators, prompts)
**Заметки:** Текущие dropdown — `hover:block` или toggle через `classList`. Нужно унифицировать в click-based с keyboard.

---

### 2.4 Keyboard navigation для sidebar

- [ ] Сделать все nav-items фокусируемыми (Tab order)
- [ ] Arrow Down/Up — перемещение между пунктами
- [ ] Enter/Space — активация пункта
- [ ] Для group-элементов (Training, System): Enter/Space открывает подменю
- [ ] Arrow Right — открыть подменю группы
- [ ] Arrow Left — закрыть подменю, вернуть фокус на группу
- [ ] Escape из подменю — закрыть подменю

**Файлы:** `admin-ui/index.html`, `admin-ui/src/main.js`
**Заметки:** Sidebar items — `<a>` теги внутри `<li>`. Нужно добавить `role="menubar"` на контейнер, `role="menuitem"` на пункты. Группы: `role="menu"` на подменю.

---

### 2.5 Help drawer — focus trap и Escape

- [ ] Добавить `role="complementary"` или `role="dialog"` на help drawer
- [ ] При открытии: `trapFocus(drawerEl)`, фокус на close-кнопку
- [ ] Escape — закрывает drawer
- [ ] При закрытии: вернуть фокус на help-кнопку (trigger)
- [ ] Backdrop click — закрывает drawer (проверить что уже работает)

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
