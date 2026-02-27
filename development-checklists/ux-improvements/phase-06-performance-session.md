# Фаза 6: Performance & Session Management

## Статус
- [x] Не начата
- [x] В процессе
- [x] Завершена

**Начата:** 2026-02-27
**Завершена:** 2026-02-27

## Цель фазы
Оптимизировать производительность клиентского приложения: кэширование API-ответов, предотвращение дублирующих запросов, улучшение управления сессией (token refresh, offline detection).

## Задачи

### 6.0 ОБЯЗАТЕЛЬНО: Анализ и планирование

#### A. Анализ существующего кода
- [x]Изучить `admin-ui/src/api.js` — текущий HTTP-клиент, timeout, error handling
- [x]Изучить `admin-ui/src/auth.js` — token management, expiry check
- [x]Изучить `admin-ui/src/websocket.js` — reconnect logic
- [x]Проверить какие API-вызовы делаются при каждой навигации

**Команды для поиска:**
```bash
# Все API вызовы
grep -rn "await api(" admin-ui/src/pages/
# Token management
grep -rn "token\|localStorage\|setToken\|getToken" admin-ui/src/auth.js
# AbortController (если есть)
grep -rn "AbortController\|abort\|signal" admin-ui/src/
# Cache (если есть)
grep -rn "cache\|Cache\|TTL\|ttl" admin-ui/src/
```

#### B. Анализ зависимостей
- [x]Какие API-вызовы повторяются при навигации? (dashboard, calls)
- [x]Есть ли уже AbortController? — Проверить
- [x]Какой бэкенд endpoint даёт refresh token? — Проверить `/auth/refresh`

**Новые абстракции:** `api-cache.js` (кэш с TTL), расширение `api.js`
**Новые env variables:** Нет
**Новые tools:** Нет
**Миграции БД:** Нет

#### C. Проверка архитектуры
- [x]Определить стратегию кэширования (stale-while-revalidate vs TTL)
- [x]Определить какие endpoints кэшировать (GET only, не мутации)
- [x]Определить стратегию token refresh (silent refresh vs redirect)

**Референс-модуль:** `admin-ui/src/api.js`

**Цель:** Спроектировать кэш и dedup без breaking changes в api().

**Заметки для переиспользования:** -

---

### 6.1 Request deduplication (AbortController)

Предотвратить дублирующие запросы:

- [x]Добавить AbortController в `api()` — при повторном вызове с тем же URL отменять предыдущий
- [x]Хранить Map: `url → AbortController` (ключ = method+url)
- [x]При навигации на другую страницу — отменять все pending-запросы текущей страницы
- [x]Не отменять POST/PUT/DELETE (мутации)
- [x]Обработать `AbortError` — не показывать как ошибку пользователю

**Файлы:** `admin-ui/src/api.js`
**Заметки:** Пример: пользователь быстро кликает по pagination → каждый клик создаёт запрос. Без dedup — race condition, может показаться не та страница.

---

### 6.2 Клиентский кэш API-ответов

- [x]Создать `admin-ui/src/api-cache.js` с Map<key, {data, timestamp}>
- [x]Функция `cachedApi(url, opts, ttlMs)` — обёртка над `api()`
- [x]TTL по умолчанию: 30 секунд (для списков), 5 минут (для справочников)
- [x]Инвалидация: после POST/PUT/DELETE на тот же ресурс
- [x]Экспортировать `invalidateCache(urlPrefix)` для ручной инвалидации
- [x]Не кэшировать: auth endpoints, WebSocket, uploads

**Файлы:** `admin-ui/src/api-cache.js` (новый)
**Заметки:** Стратегия: cache-first для быстрого отображения, background revalidate при TTL expired. Не кэшировать dashboard (обновляется в реальном времени).

---

### 6.3 Применение кэша к страницам

- [x]Calls list — кэш 30с (инвалидация при WebSocket call:started/ended)
- [x]Users list — кэш 60с
- [x]Knowledge articles — кэш 60с
- [x]Tenants list — кэш 5 мин (редко меняется)
- [x]Operators — НЕ кэшировать (real-time статус)
- [x]Dashboard — НЕ кэшировать (real-time метрики)

**Файлы:** Соответствующие страницы в `admin-ui/src/pages/`
**Заметки:** Заменить `await api(...)` на `await cachedApi(...)` только для GET-запросов.

---

### 6.4 Session expiry warning

- [x]За 5 минут до истечения JWT — показать warning toast: «Сессия скоро истечёт»
- [x]Добавить кнопку «Продлить» в toast (вызывает refresh endpoint)
- [x]Добавить API endpoint для refresh (если нет на бэкенде — создать)
- [x]При успешном refresh — обновить token в localStorage, показать success toast
- [x]При неудачном refresh — logout
- [x]Добавить i18n ключи: `auth.sessionExpiringSoon`, `auth.extendSession`, `auth.sessionExtended`

**Файлы:** `admin-ui/src/auth.js`, `admin-ui/src/translations/ru.js`, `admin-ui/src/translations/en.js`
**Заметки:** Текущий `checkTokenExpiry()` (60с interval) проверяет exp, но не предупреждает. Нужно предупреждение за 300с до exp.

---

### 6.5 Offline detection

- [x]Слушать `window.addEventListener('online'/'offline')` события
- [x]При offline: показать persistent banner вверху страницы «Нет подключения к сети»
- [x]При восстановлении: убрать banner, показать toast «Подключение восстановлено»
- [x]При offline: блокировать submit-кнопки (disabled + tooltip)
- [x]Добавить i18n ключи: `common.offline`, `common.backOnline`

**Файлы:** `admin-ui/src/main.js`, `admin-ui/src/styles/main.css`, `admin-ui/src/translations/ru.js`, `admin-ui/src/translations/en.js`
**Заметки:** Banner — fixed top, yellow/amber фон, z-index выше всего остального. Не путать с WebSocket disconnect (WS может быть вне сети, а HTTP работает через другой путь).

---

### 6.6 Cleanup refresh-таймеров при logout

- [x]При logout: очистить ВСЕ setInterval/setTimeout (dashboard 30s, operators 10s, token check 60s)
- [x]Создать registry таймеров: `registerTimer(id, timer)` / `clearAllTimers()`
- [x]Вызывать `clearAllTimers()` в logout handler
- [x]Проверить что WebSocket disconnect вызывается при logout

**Файлы:** `admin-ui/src/main.js`, `admin-ui/src/auth.js`, `admin-ui/src/router.js`
**Заметки:** Текущая проблема: при logout таймеры refresh продолжают работать → запросы к API с невалидным token.

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
   git commit -m "checklist(ux-improvements): phase-6 performance and session management completed"
   ```
5. Обнови PROGRESS.md:
   - Текущая фаза: 7
   - Добавь запись в историю
6. Открой следующую фазу и продолжи работу
