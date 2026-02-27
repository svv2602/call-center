# Фаза 5: Responsive & Mobile UX

## Статус
- [x] Не начата
- [x] В процессе
- [x] Завершена

**Начата:** 2026-02-27
**Завершена:** 2026-02-27

## Цель фазы
Довести мобильную версию до полноценного уровня: увеличенные touch targets, правильное позиционирование dropdown, индикатор прокрутки табов, мобильный UX polish.

## Задачи

### 5.0 ОБЯЗАТЕЛЬНО: Анализ и планирование

#### A. Анализ существующего кода
- [x] Изучить все responsive breakpoints в `main.css` и inline Tailwind
- [x] Проверить touch target размеры кнопок и ссылок на mobile
- [x] Изучить dropdown-меню — как позиционируются на mobile
- [x] Проверить tab-bar (горизонтальные табы) — есть ли индикатор прокрутки

**Команды для поиска:**
```bash
# Responsive breakpoints
grep -rn "max-md:\|max-lg:\|max-\[480px\]\|@media" admin-ui/src/styles/main.css
# Touch target стили
grep -rn "min-h-\|min-w-\|p-3\|p-4\|py-3" admin-ui/src/tw.js
# Dropdown позиционирование
grep -rn "absolute\|fixed\|bottom-\|right-0" admin-ui/src/pages/
# Tab bars
grep -rn "tab-bar\|overflow-x\|scroll.*tab\|flex.*tab" admin-ui/src/
```

#### B. Анализ зависимостей
- [x] Какие компоненты ещё не responsive? — Проверить
- [x] Нужны ли media queries в JS? — Определить
- [x] Есть ли touch-specific стили?

**Новые абстракции:** Нет
**Новые env variables:** Нет
**Новые tools:** Нет
**Миграции БД:** Нет

#### C. Проверка архитектуры
- [x] Протестировать в Chrome DevTools Mobile (375px, 768px)
- [x] Определить список проблемных компонентов

**Референс-модуль:** `admin-ui/src/styles/main.css` (responsive секция)

**Цель:** Составить полную карту mobile-проблем.

**Заметки для переиспользования:** -

---

### 5.1 Увеличение touch targets

Минимальный touch target по WCAG: 44×44px.

- [x] Audit всех кнопок на mobile — какие меньше 44px
- [x] Pagination кнопки: увеличить `min-h-11 min-w-11` (44px) на mobile
- [x] Dropdown trigger (`…` кнопка): увеличить hitarea до 44×44px
- [x] Tab-кнопки: добавить padding `py-3 px-4` на mobile
- [x] Close-кнопки (× в модалках): увеличить до 44×44px

**Файлы:** `admin-ui/src/tw.js`, `admin-ui/src/styles/main.css`, `admin-ui/src/pagination.js`
**Заметки:** Использовать `max-md:min-h-11 max-md:min-w-11` чтобы не менять десктопный вид.

---

### 5.2 Dropdown позиционирование на mobile

- [x] На mobile (≤768px): dropdown показывать как bottom sheet (fixed bottom, full width)
- [x] Добавить backdrop (scrim) при открытом dropdown на mobile
- [x] Анимация: slide-up from bottom
- [x] Увеличить размер пунктов меню на mobile (py-3, text-base вместо text-sm)
- [x] Закрытие: tap на backdrop, swipe down (опционально), Escape

**Файлы:** `admin-ui/src/styles/main.css`, страницы с dropdown
**Заметки:** Bottom sheet — стандартный мобильный паттерн. Не менять desktop-поведение (absolute positioning).

---

### 5.3 Tab-bar scroll indicator

Для страниц с горизонтальными табами (Knowledge: 4 таба, Scenarios: 3 таба):

- [x] Добавить gradient-fade на правом краю при overflow (показывает «есть ещё»)
- [x] CSS: `mask-image: linear-gradient(to right, black 90%, transparent)` при scroll=0
- [x] При скролле до конца — убрать правый gradient, показать левый
- [x] Опционально: показать scroll-arrows (< >) по краям tab-bar

**Файлы:** `admin-ui/src/styles/main.css`
**Заметки:** Текущий tab-bar использует `overflow-x: auto` с `scrollbar-width: none`. Gradient fade — визуальный хинт.

---

### 5.4 Mobile sidebar — улучшение

- [x] Backdrop (scrim) при открытом sidebar на mobile
- [x] Swipe-to-close (touch gesture) — опционально
- [x] Закрытие при tap на пункте навигации (сейчас может не закрываться)
- [x] Анимация: плавный slide-in/out (200ms ease-out)

**Файлы:** `admin-ui/index.html`, `admin-ui/src/main.js`, `admin-ui/src/styles/main.css`
**Заметки:** Проверить текущее поведение sidebar на mobile перед внесением изменений.

---

### 5.5 Responsive модалки

- [x] На mobile (≤768px): модалки должны быть full-screen (100vw × 100vh)
- [x] Убрать border-radius на mobile (визуальный стиль full-screen)
- [x] Fixed header + scrollable body (уже есть, проверить)
- [x] Кнопки действий внизу — sticky footer на mobile

**Файлы:** `admin-ui/src/styles/main.css`
**Заметки:** Текущие модалки могут быть слишком узкими или обрезаться на маленьких экранах.

---

### 5.6 Responsive фильтры — улучшение

- [x] Collapsible фильтры на mobile уже есть — проверить работу
- [x] Добавить badge с количеством активных фильтров на collapsed state
- [x] Кнопка «Сбросить фильтры» — видна при наличии активных фильтров
- [x] Добавить i18n: `common.filtersActive`, `common.resetFilters`

**Файлы:** `admin-ui/src/pages/calls.js` (и другие с фильтрами), `admin-ui/src/translations/ru.js`, `admin-ui/src/translations/en.js`
**Заметки:** Фильтры уже collapsible через `.filter-collapsible.open`. Нужен badge-счётчик.

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
   git commit -m "checklist(ux-improvements): phase-5 responsive and mobile UX completed"
   ```
5. Обнови PROGRESS.md:
   - Текущая фаза: 6
   - Добавь запись в историю
6. Открой следующую фазу и продолжи работу
