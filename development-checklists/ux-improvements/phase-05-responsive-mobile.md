# Фаза 5: Responsive & Mobile UX

## Статус
- [ ] Не начата
- [ ] В процессе
- [ ] Завершена

**Начата:** -
**Завершена:** -

## Цель фазы
Довести мобильную версию до полноценного уровня: увеличенные touch targets, правильное позиционирование dropdown, индикатор прокрутки табов, мобильный UX polish.

## Задачи

### 5.0 ОБЯЗАТЕЛЬНО: Анализ и планирование

#### A. Анализ существующего кода
- [ ] Изучить все responsive breakpoints в `main.css` и inline Tailwind
- [ ] Проверить touch target размеры кнопок и ссылок на mobile
- [ ] Изучить dropdown-меню — как позиционируются на mobile
- [ ] Проверить tab-bar (горизонтальные табы) — есть ли индикатор прокрутки

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
- [ ] Какие компоненты ещё не responsive? — Проверить
- [ ] Нужны ли media queries в JS? — Определить
- [ ] Есть ли touch-specific стили?

**Новые абстракции:** Нет
**Новые env variables:** Нет
**Новые tools:** Нет
**Миграции БД:** Нет

#### C. Проверка архитектуры
- [ ] Протестировать в Chrome DevTools Mobile (375px, 768px)
- [ ] Определить список проблемных компонентов

**Референс-модуль:** `admin-ui/src/styles/main.css` (responsive секция)

**Цель:** Составить полную карту mobile-проблем.

**Заметки для переиспользования:** -

---

### 5.1 Увеличение touch targets

Минимальный touch target по WCAG: 44×44px.

- [ ] Audit всех кнопок на mobile — какие меньше 44px
- [ ] Pagination кнопки: увеличить `min-h-11 min-w-11` (44px) на mobile
- [ ] Dropdown trigger (`…` кнопка): увеличить hitarea до 44×44px
- [ ] Tab-кнопки: добавить padding `py-3 px-4` на mobile
- [ ] Close-кнопки (× в модалках): увеличить до 44×44px

**Файлы:** `admin-ui/src/tw.js`, `admin-ui/src/styles/main.css`, `admin-ui/src/pagination.js`
**Заметки:** Использовать `max-md:min-h-11 max-md:min-w-11` чтобы не менять десктопный вид.

---

### 5.2 Dropdown позиционирование на mobile

- [ ] На mobile (≤768px): dropdown показывать как bottom sheet (fixed bottom, full width)
- [ ] Добавить backdrop (scrim) при открытом dropdown на mobile
- [ ] Анимация: slide-up from bottom
- [ ] Увеличить размер пунктов меню на mobile (py-3, text-base вместо text-sm)
- [ ] Закрытие: tap на backdrop, swipe down (опционально), Escape

**Файлы:** `admin-ui/src/styles/main.css`, страницы с dropdown
**Заметки:** Bottom sheet — стандартный мобильный паттерн. Не менять desktop-поведение (absolute positioning).

---

### 5.3 Tab-bar scroll indicator

Для страниц с горизонтальными табами (Knowledge: 4 таба, Scenarios: 3 таба):

- [ ] Добавить gradient-fade на правом краю при overflow (показывает «есть ещё»)
- [ ] CSS: `mask-image: linear-gradient(to right, black 90%, transparent)` при scroll=0
- [ ] При скролле до конца — убрать правый gradient, показать левый
- [ ] Опционально: показать scroll-arrows (< >) по краям tab-bar

**Файлы:** `admin-ui/src/styles/main.css`
**Заметки:** Текущий tab-bar использует `overflow-x: auto` с `scrollbar-width: none`. Gradient fade — визуальный хинт.

---

### 5.4 Mobile sidebar — улучшение

- [ ] Backdrop (scrim) при открытом sidebar на mobile
- [ ] Swipe-to-close (touch gesture) — опционально
- [ ] Закрытие при tap на пункте навигации (сейчас может не закрываться)
- [ ] Анимация: плавный slide-in/out (200ms ease-out)

**Файлы:** `admin-ui/index.html`, `admin-ui/src/main.js`, `admin-ui/src/styles/main.css`
**Заметки:** Проверить текущее поведение sidebar на mobile перед внесением изменений.

---

### 5.5 Responsive модалки

- [ ] На mobile (≤768px): модалки должны быть full-screen (100vw × 100vh)
- [ ] Убрать border-radius на mobile (визуальный стиль full-screen)
- [ ] Fixed header + scrollable body (уже есть, проверить)
- [ ] Кнопки действий внизу — sticky footer на mobile

**Файлы:** `admin-ui/src/styles/main.css`
**Заметки:** Текущие модалки могут быть слишком узкими или обрезаться на маленьких экранах.

---

### 5.6 Responsive фильтры — улучшение

- [ ] Collapsible фильтры на mobile уже есть — проверить работу
- [ ] Добавить badge с количеством активных фильтров на collapsed state
- [ ] Кнопка «Сбросить фильтры» — видна при наличии активных фильтров
- [ ] Добавить i18n: `common.filtersActive`, `common.resetFilters`

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
