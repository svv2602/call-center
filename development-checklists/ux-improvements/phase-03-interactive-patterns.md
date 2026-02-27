# Фаза 3: Interactive Patterns

## Статус
- [ ] Не начата
- [ ] В процессе
- [ ] Завершена

**Начата:** -
**Завершена:** -

## Цель фазы
Улучшить паттерны взаимодействия: loading-состояния кнопок, sticky table headers, предупреждение о несохранённых изменениях, scroll-to-top.

## Задачи

### 3.0 ОБЯЗАТЕЛЬНО: Анализ и планирование

#### A. Анализ существующего кода
- [ ] Найти все submit-кнопки в модалках и формах
- [ ] Изучить `withButtonLock()` — существующий механизм блокировки кнопок
- [ ] Изучить текущие таблицы — есть ли sticky headers
- [ ] Проверить как работает навигация между страницами (scroll position)

**Команды для поиска:**
```bash
# Submit кнопки и формы
grep -rn "onclick.*save\|onclick.*create\|onclick.*submit\|onclick.*confirm" admin-ui/src/pages/
# Existing button lock
grep -rn "withButtonLock\|btn.*disabled\|button.*disabled" admin-ui/src/
# Table headers
grep -rn "<thead\|<th\|sticky" admin-ui/src/
# Scroll control
grep -rn "scrollTo\|scrollTop\|scroll(" admin-ui/src/
```

#### B. Анализ зависимостей
- [ ] Сколько страниц используют `withButtonLock`? — Составить список
- [ ] Какие страницы имеют таблицы? — Составить список
- [ ] Какие страницы имеют формы с несохранёнными данными?

**Новые абстракции:** Нет (расширение `withButtonLock`)
**Новые env variables:** Нет
**Новые tools:** Нет
**Миграции БД:** Нет

#### C. Проверка архитектуры
- [ ] Определить единый паттерн для кнопок с loading state
- [ ] Определить как sticky headers работают с responsive table (data-label)
- [ ] Определить стратегию unsaved changes (beforeunload + internal navigation)

**Референс-модуль:** `admin-ui/src/utils.js` (withButtonLock)

**Цель:** Понять все точки применения и выбрать единый подход.

**Заметки для переиспользования:** -

---

### 3.1 Button loading state — улучшение withButtonLock

Улучшить существующую утилиту или создать более надёжную:

- [ ] Добавить inline spinner в кнопку (SVG animate) вместо только текста
- [ ] Добавить `disabled` атрибут + `opacity-50 cursor-not-allowed` стили
- [ ] Добавить `aria-busy="true"` на кнопку во время загрузки
- [ ] Восстанавливать оригинальный текст и состояние при завершении (success или error)
- [ ] Применить к ВСЕМ submit-кнопкам в модалках форм

**Файлы:** `admin-ui/src/utils.js`, все страницы с формами
**Заметки:** Текущий `withButtonLock` меняет текст, но не добавляет spinner и не ставит disabled. Паттерн: `<button><span class="spinner-sm"></span> Сохранение...</button>`.

---

### 3.2 Sticky table headers

- [ ] Добавить CSS для sticky thead: `thead th { position: sticky; top: 0; z-index: 10; }`
- [ ] Добавить `background-color` на sticky header (чтобы контент не просвечивал)
- [ ] Проверить в dark mode (bg-neutral-900/950 для dark)
- [ ] Проверить что sticky не ломает responsive table (mobile card layout)
- [ ] Добавить subtle shadow при скролле под header

**Файлы:** `admin-ui/src/styles/main.css`, возможно `admin-ui/src/tw.js`
**Заметки:** Sticky headers работают только если таблица в скроллируемом контейнере. Проверить что parent не имеет `overflow: hidden`.

---

### 3.3 Unsaved changes guard

Создать утилиту для отслеживания несохранённых изменений:

- [ ] Создать `admin-ui/src/form-guard.js` с функциями `markDirty(formId)`, `markClean(formId)`, `isDirty(formId)`
- [ ] Слушать `input` и `change` события на полях формы → `markDirty()`
- [ ] При success-сохранении → `markClean()`
- [ ] Добавить `beforeunload` обработчик: если есть dirty forms → `"У вас есть несохранённые изменения"`
- [ ] При внутренней навигации (смена страницы через router): показывать confirm dialog
- [ ] Добавить i18n ключ `common.unsavedChanges` в ru.js и en.js

**Файлы:** `admin-ui/src/form-guard.js` (новый), `admin-ui/src/router.js` (hook перед переходом)
**Заметки:** Применить к формам в: operators (create/edit), users (create/edit), knowledge (article editor), tenants (create/edit), prompts (create). Не применять к фильтрам.

---

### 3.4 Scroll-to-top при навигации

- [ ] При переключении страницы (router): скролл `#mainContent` наверх
- [ ] При переключении таба внутри страницы: скролл к началу таб-контента
- [ ] Добавить кнопку «вверх» (scroll-to-top FAB) при скролле > 300px
- [ ] CSS для FAB: fixed bottom-right, icon-only, с анимацией появления

**Файлы:** `admin-ui/src/router.js`, `admin-ui/src/styles/main.css`, `admin-ui/index.html`
**Заметки:** Кнопка «вверх» — опциональна, можно добавить позже. Минимум — scroll-to-top при навигации.

---

### 3.5 Индикатор «последнее обновление»

- [ ] Добавить компонент timestamp в dashboard и calls: `"Обновлено: HH:MM:SS"`
- [ ] Обновлять timestamp при каждом успешном fetch данных
- [ ] Обновлять timestamp при WebSocket-событии (если данные перезагружены)
- [ ] Добавить i18n ключи: `common.lastUpdated`, `common.updatedAt`
- [ ] Стиль: мелкий серый текст, справа от заголовка или под фильтрами

**Файлы:** `admin-ui/src/pages/dashboard.js`, `admin-ui/src/pages/calls.js`, `admin-ui/src/translations/ru.js`, `admin-ui/src/translations/en.js`
**Заметки:** Формат: `Обновлено: 14:32:05` (без даты, т.к. обновления частые).

---

### 3.6 Форма: сброс полей при открытии Create-модалки

- [ ] Аудит всех Create/Edit модалок — проверить что Create сбрасывает все поля
- [ ] Добавить `resetForm(formId)` утилиту — очищает все input/select/textarea
- [ ] Вызывать `resetForm()` при открытии Create (не Edit)
- [ ] Проверить: operators, users, knowledge articles, tenants, prompts, scenarios

**Файлы:** `admin-ui/src/utils.js`, все страницы с Create-модалками
**Заметки:** Текущая проблема: Create→Edit→Create может оставить данные из Edit.

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
   git commit -m "checklist(ux-improvements): phase-3 interactive patterns completed"
   ```
5. Обнови PROGRESS.md:
   - Текущая фаза: 4
   - Добавь запись в историю
6. Открой следующую фазу и продолжи работу
