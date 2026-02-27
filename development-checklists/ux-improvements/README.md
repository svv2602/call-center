# UX-улучшения Admin UI

## Цель
Довести Admin UI до профессионального уровня UX: accessibility (WCAG 2.1 AA), отзывчивые паттерны взаимодействия, качественная мобильная версия, оптимизация производительности.

## Критерии успеха
- [ ] Accessibility: ARIA landmarks, focus trap, keyboard navigation для всех интерактивных элементов
- [ ] Все модалки: focus trap, Escape, возврат фокуса, `role="dialog"`
- [ ] Submit-кнопки: disabled + spinner во время запроса
- [ ] Таблицы: sticky headers, skeleton loading
- [ ] Toast: 3 типа (success/error/warning), dismiss кнопка, `aria-live`
- [ ] Mobile: touch-friendly dropdown, увеличенные touch targets
- [ ] Кэширование API-ответов, request deduplication
- [ ] i18n: pluralization для ключевых строк, fallback без raw-ключей

## Фазы работы
1. **Accessibility foundations** — ARIA landmarks, семантика, skip-link, focus-visible
2. **Focus management & keyboard** — Focus trap в модалках, keyboard navigation для dropdown и sidebar
3. **Interactive patterns** — Button loading states, sticky headers, unsaved changes guard
4. **Feedback & notifications** — Toast улучшения, skeleton loaders, error handling
5. **Responsive & mobile** — Touch targets, dropdown positioning, mobile UX polish
6. **Performance & session** — API кэш, request dedup, token refresh, offline detection
7. **i18n & help** — Pluralization, missing key fallback, контекстная справка

## Источник требований
Комплексный UX-анализ Admin UI (проведён 2026-02-27), результаты в контексте беседы.

## Правила переиспользования кода

### ОБЯЗАТЕЛЬНО перед реализацией:
1. **Поиск существующего функционала** — перед написанием нового кода ВСЕГДА ищи похожий существующий код
2. **Анализ паттернов** — изучи как реализованы похожие фичи в проекте
3. **Переиспользование модулей** — используй существующие модули, базовые классы, утилиты

### Где искать:
```
admin-ui/src/
├── main.js              # Инициализация, глобальные обработчики событий
├── router.js            # Hash-роутинг, lazy-loading модулей
├── api.js               # HTTP-клиент с auth, timeout, error handling
├── auth.js              # JWT, permissions, login/logout
├── i18n.js              # t(), initLang(), toggleLang(), getLocale()
├── notifications.js     # showToast() — система уведомлений
├── utils.js             # escapeHtml(), badge helpers, date formatting
├── tw.js                # Tailwind CSS-константы (85 компонентов)
├── sorting.js           # Сортировка таблиц
├── pagination.js        # Рендеринг пагинации
├── websocket.js         # WebSocket подключение и reconnect
├── theme.js             # Dark/light mode toggle
├── help-content.js      # Реестр справочных секций
├── help-drawer.js       # Компонент help drawer
├── styles/
│   └── main.css         # Глобальные стили (448 строк)
├── translations/
│   ├── ru.js            # Русские переводы (~909 ключей)
│   └── en.js            # Английские переводы (~909 ключей)
└── pages/               # 22 модуля страниц
    ├── dashboard.js
    ├── calls.js
    ├── customers.js
    ├── ...
```

### Чеклист перед написанием кода:
- [ ] Искал похожий функционал в codebase?
- [ ] Изучил паттерны из похожих файлов?
- [ ] Переиспользую существующие модули/утилиты?
- [ ] Соблюдаю conventions проекта?

## Правила кода

### Архитектурные паттерны Admin UI:

| Паттерн | Где применяется | Пример |
|---------|----------------|--------|
| `tw.js` constants | Все компоненты | `tw.btnPrimary`, `tw.card`, `tw.emptyState` |
| `window._pages.*` | Все страницы | `window._pages.calls = { loadCalls, ... }` |
| `showToast(msg, type)` | Уведомления | `showToast(t('key'), 'error')` |
| `api(path, opts)` | HTTP-запросы | `await api('/admin/calls')` |
| `t('key', {param})` | Локализация | `t('calls.count', {n: 5})` |
| `escapeHtml(str)` | XSS-защита | НЕ использовать в `data-*` атрибутах! |
| `registerPageLoader` | Lazy loading | `registerPageLoader('calls', async () => {...})` |
| `withButtonLock` | Блокировка кнопок | `withButtonLock(btnId, asyncFn, 'Loading...')` |

### i18n правила:
- Новые ключи добавлять в ОБА файла: `translations/ru.js` и `translations/en.js`
- Неймспейсы: `common.*`, `nav.*`, `dashboard.*`, `calls.*` и т.д.
- Статический HTML: `data-i18n="key"` / `data-i18n-placeholder="key"`
- Динамический JS: `import { t } from '../i18n.js'` → `t('key')`

### Чеклист:
- [ ] Все строки через i18n (`t('key')`)?
- [ ] Ключи добавлены в ru.js И en.js?
- [ ] ARIA-атрибуты добавлены?
- [ ] Tailwind классы из `tw.js` (не хардкод)?
- [ ] Dark mode поддерживается (dark: варианты)?
- [ ] Mobile-responsive (max-md: варианты)?

## Правила тестирования

### Для каждого UI-изменения:
- [ ] Визуальная проверка в light + dark mode
- [ ] Проверка на mobile (≤768px) и desktop
- [ ] Keyboard navigation (Tab, Shift+Tab, Escape, Enter)
- [ ] Screen reader тест (хотя бы проверка ARIA)
- [ ] ruff check src/ (если затронут бэкенд)

## Начало работы
Для начала или продолжения работы прочитай PROGRESS.md
