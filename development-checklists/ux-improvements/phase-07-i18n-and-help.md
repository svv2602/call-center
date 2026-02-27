# Фаза 7: i18n & Help System

## Статус
- [ ] Не начата
- [ ] В процессе
- [ ] Завершена

**Начата:** -
**Завершена:** -

## Цель фазы
Довести локализацию и справочную систему до полного уровня: pluralization, user-friendly fallback для missing keys, контекстная справка, поиск по справке.

## Задачи

### 7.0 ОБЯЗАТЕЛЬНО: Анализ и планирование

#### A. Анализ существующего кода
- [ ] Изучить `admin-ui/src/i18n.js` — текущая реализация t(), translateStaticDOM()
- [ ] Изучить `admin-ui/src/help-content.js` — структура HELP_PAGES
- [ ] Изучить `admin-ui/src/help-drawer.js` — рендеринг help drawer
- [ ] Подсчитать все строки с числовыми значениями (где нужна pluralization)

**Команды для поиска:**
```bash
# Текущая функция t()
cat admin-ui/src/i18n.js
# Использование t() с числовыми параметрами
grep -rn "t('.*count\|t('.*total\|t('.*num" admin-ui/src/
# Missing key fallback
grep -rn "?? key\|return key" admin-ui/src/i18n.js
# Help content structure
head -50 admin-ui/src/help-content.js
```

#### B. Анализ зависимостей
- [ ] Сколько строк нужна pluralization? — Подсчитать (calls, users, articles, pages...)
- [ ] Есть ли help для всех 22 страниц? — Проверить
- [ ] Какие формы нуждаются в tooltip-справке?

**Новые абстракции:** Расширение i18n.js (pluralization), расширение help-drawer.js (search)
**Новые env variables:** Нет
**Новые tools:** Нет
**Миграции БД:** Нет

#### C. Проверка архитектуры
- [ ] Определить формат pluralization rules для русского/английского
- [ ] Определить API для tooltip-справки на полях форм

**Референс-модуль:** `admin-ui/src/i18n.js`

**Цель:** Спроектировать расширение i18n без breaking changes.

**Заметки для переиспользования:** -

---

### 7.1 Pluralization в i18n

Русский язык имеет 3 формы: 1 звонок, 2 звонка, 5 звонков.

- [ ] Расширить `t()` для поддержки pluralization: `t('calls.count', {n: 5})` → «5 звонков»
- [ ] Формат ключей: `calls.count_one`, `calls.count_few`, `calls.count_many` (или `_1`/`_2`/`_5`)
- [ ] Правила русского: n%10==1 && n%100!=11 → one; n%10 in [2,3,4] && n%100 not in [12,13,14] → few; else → many
- [ ] Правила английского: n==1 → one; else → other
- [ ] Создать helper `pluralize(n, one, few, many)` — экспортировать из i18n.js
- [ ] Обратная совместимость: если `_one`/`_few`/`_many` не найдены — использовать основной ключ

**Файлы:** `admin-ui/src/i18n.js`
**Заметки:** Минимальное расширение — добавить функцию + правила. Не менять формат существующих ключей.

---

### 7.2 Применение pluralization к ключевым строкам

- [ ] Calls: «N звонков» — pagination info, dashboard stat
- [ ] Users: «N пользователей» — users list header
- [ ] Articles: «N статей» — knowledge tab header
- [ ] Operators: «N операторов» — operators page
- [ ] Pages: «Страница N из M» — pagination
- [ ] Добавить все `_one`/`_few`/`_many` варианты в ru.js и en.js

**Файлы:** `admin-ui/src/translations/ru.js`, `admin-ui/src/translations/en.js`, страницы где отображаются счётчики
**Заметки:** Начать с Dashboard (самая видимая страница) и pagination (самая частая).

---

### 7.3 User-friendly fallback для missing keys

- [ ] Вместо показа raw ключа (`calls.failedToLoad`) — показать generic сообщение
- [ ] В dev mode (localhost): показать ключ в скобках `[calls.failedToLoad]` + console.warn
- [ ] В prod: показать пустую строку или generic placeholder
- [ ] Определить «dev mode» по `window.location.hostname === 'localhost'`

**Файлы:** `admin-ui/src/i18n.js`
**Заметки:** Текущий fallback: `LANGS[currentLang]?.[key] ?? LANGS['ru']?.[key] ?? key`. Нужно: `?? (isDev ? `[${key}]` : '')`.

---

### 7.4 Контекстные tooltip-подсказки на полях форм

- [ ] Создать утилиту `renderTooltip(key)` — иконка (?) с hover/click tooltip
- [ ] Стиль: маленький info-circle рядом с label, popover при hover
- [ ] Применить к сложным полям: tenant extensions, user permissions, LLM routing config
- [ ] Добавить i18n ключи: `tooltip.<page>.<field>` в ru.js и en.js
- [ ] Tooltip должен быть accessible: `aria-describedby` на input, `role="tooltip"` на popover

**Файлы:** `admin-ui/src/tooltip.js` (новый), страницы с формами, `admin-ui/src/translations/ru.js`, `admin-ui/src/translations/en.js`
**Заметки:** Начать с 5-10 самых неочевидных полей. Не добавлять tooltip на каждое поле — только на сложные.

---

### 7.5 Поиск по справке

- [ ] Добавить поле поиска вверху help drawer
- [ ] Поиск по текстам справки (содержимому i18n ключей `help.*`)
- [ ] Показывать секции, в тексте которых есть совпадение
- [ ] Подсвечивать найденный текст (`<mark>`)
- [ ] Debounce поиска (300ms)

**Файлы:** `admin-ui/src/help-drawer.js`, `admin-ui/src/styles/main.css`
**Заметки:** Поиск client-side — все тексты уже загружены в translations. Искать через `Object.entries(LANGS[lang]).filter(([k,v]) => k.startsWith('help.') && v.includes(query))`.

---

### 7.6 Missing help pages

Добавить справку для страниц, у которых она отсутствует:

- [ ] Проверить список: сравнить HELP_PAGES ключи с router pages
- [ ] Добавить help-content для каждой страницы без справки
- [ ] Добавить i18n ключи `help.<page>.*` в ru.js и en.js

**Файлы:** `admin-ui/src/help-content.js`, `admin-ui/src/translations/ru.js`, `admin-ui/src/translations/en.js`
**Заметки:** По результатам анализа — 17 из 22 страниц имеют help. Проверить какие 5 отсутствуют.

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
   git commit -m "checklist(ux-improvements): phase-7 i18n and help system completed"
   ```
5. Обнови PROGRESS.md:
   - Текущая фаза: завершено
   - Добавь запись в историю
6. Проверить все критерии успеха в README.md — отметить выполненные
