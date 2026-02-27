# Фаза 4: Feedback & Notifications

## Статус
- [x] Не начата
- [x] В процессе
- [x] Завершена

**Начата:** 2026-02-27
**Завершена:** 2026-02-27

## Цель фазы
Улучшить систему обратной связи: расширить toast-уведомления (типы, dismiss, длительность), добавить skeleton loaders, улучшить отображение ошибок.

## Задачи

### 4.0 ОБЯЗАТЕЛЬНО: Анализ и планирование

#### A. Анализ существующего кода
- [x] Изучить `admin-ui/src/notifications.js` — текущая реализация showToast
- [x] Найти все вызовы `showToast()` в проекте — какие типы используются
- [x] Изучить текущие loading-состояния (spinner vs skeleton)
- [x] Изучить текущие error-состояния — что видит пользователь при ошибке

**Команды для поиска:**
```bash
# Все вызовы showToast
grep -rn "showToast(" admin-ui/src/pages/
# Типы toast
grep -rn "showToast.*error\|showToast.*success\|showToast.*warn" admin-ui/src/
# Loading индикаторы
grep -rn "loadingWrap\|spinner\|Loading\|loading" admin-ui/src/pages/
# Error отображение
grep -rn "failedToLoad\|emptyState.*error\|catch.*e" admin-ui/src/pages/
```

#### B. Анализ зависимостей
- [x] Сколько вызовов showToast в проекте? — Подсчитать
- [x] Используется ли где-то тип 'warning'? — Проверить
- [x] Какие страницы имеют таблицы (для skeleton)?

**Новые абстракции:** Расширение `notifications.js`
**Новые env variables:** Нет
**Новые tools:** Нет
**Миграции БД:** Нет

#### C. Проверка архитектуры
- [x] Определить API расширенного showToast
- [x] Определить паттерн skeleton loader (сколько строк, какие колонки)

**Референс-модуль:** `admin-ui/src/notifications.js`

**Цель:** Понять текущее использование и спроектировать расширение без breaking changes.

**Заметки для переиспользования:** -

---

### 4.1 Расширение toast-системы

Расширить `admin-ui/src/notifications.js`:

- [x] Добавить тип `warning` (amber/yellow фон)
- [x] Добавить тип `info` (blue фон)
- [x] Увеличить время dismiss: success=4s, info=5s, warning=6s, error=8s
- [x] Добавить кнопку dismiss (×) на каждый toast
- [x] Добавить CSS-анимацию появления (slide-in from right) и исчезновения (fade-out)
- [x] Ограничить max 5 toast одновременно (убирать старые при превышении)
- [x] Сохранить обратную совместимость: `showToast(msg)` → success, `showToast(msg, 'error')` → error

**Файлы:** `admin-ui/src/notifications.js`, `admin-ui/src/styles/main.css`
**Заметки:** Текущая реализация — 11 строк. Расширять аккуратно, сохраняя простоту API.

---

### 4.2 Skeleton loader компонент

Создать переиспользуемый skeleton loader:

- [x] Создать утилиту `renderSkeleton(rows, cols)` в `admin-ui/src/utils.js` или отдельном файле
- [x] Skeleton — серые пульсирующие прямоугольники (Tailwind `animate-pulse bg-neutral-200 dark:bg-neutral-700`)
- [x] Поддержка вариантов: table (строки×колонки), card (прямоугольник), text (несколько строк)
- [x] CSS-анимация в `main.css` (если Tailwind `animate-pulse` недостаточно)

**Файлы:** `admin-ui/src/skeleton.js` (новый) или `admin-ui/src/utils.js`, `admin-ui/src/styles/main.css`
**Заметки:** Skeleton лучше спиннера: показывает структуру будущего контента, снижает perceived latency.

---

### 4.3 Skeleton loaders на страницах с таблицами

Заменить spinner на skeleton loader:

- [x] Dashboard — skeleton для stat-карточек (4 прямоугольника)
- [x] Calls — skeleton для таблицы (5 строк × количество колонок)
- [x] Users — skeleton для таблицы
- [x] Knowledge — skeleton для таблицы статей
- [x] Audit — skeleton для таблицы
- [x] Vehicles — skeleton для таблицы

**Файлы:** Соответствующие страницы в `admin-ui/src/pages/`
**Заметки:** Оставить spinner для мелких действий (загрузка одной модалки). Skeleton — только для основного контента страницы.

---

### 4.4 Улучшение error-состояний

- [x] Ошибки API: показывать user-friendly сообщение, не raw backend detail
- [x] Создать маппинг HTTP-кодов → понятные сообщения: 403→«Нет доступа», 404→«Не найдено», 500→«Серверная ошибка»
- [x] Для network errors: «Нет подключения к серверу. Проверьте соединение.»
- [x] Добавить i18n ключи: `errors.noAccess`, `errors.notFound`, `errors.serverError`, `errors.networkError`
- [x] Retry кнопка — показывать loading state при повторной попытке

**Файлы:** `admin-ui/src/api.js`, `admin-ui/src/translations/ru.js`, `admin-ui/src/translations/en.js`
**Заметки:** Текущий код выбрасывает `body.detail || HTTP ${res.status}` — пользователь видит технические детали. Маппинг делать в `api.js`, не в каждой странице.

---

### 4.5 Подтверждение деструктивных действий

- [x] Создать утилиту `confirmAction(message)` → Promise<boolean> (показывает модалку с Да/Нет)
- [x] Применить к: удаление пользователя, удаление статьи, удаление оператора, удаление тенанта
- [x] Стиль: модалка с красной кнопкой «Удалить» и серой «Отмена»
- [x] Добавить i18n ключи: `common.confirmDelete`, `common.confirmAction`, `common.cancel`, `common.delete`
- [x] Focus на кнопку «Отмена» по умолчанию (safety-first)

**Файлы:** `admin-ui/src/confirm.js` (новый), страницы с delete-действиями
**Заметки:** Текущие удаления — без подтверждения или с browser `confirm()`. Кастомная модалка лучше интегрируется со стилем.

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
   git commit -m "checklist(ux-improvements): phase-4 feedback and notifications completed"
   ```
5. Обнови PROGRESS.md:
   - Текущая фаза: 5
   - Добавь запись в историю
6. Открой следующую фазу и продолжи работу
