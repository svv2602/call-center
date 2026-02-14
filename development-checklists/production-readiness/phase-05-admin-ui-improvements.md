# Фаза 5: Admin UI Improvements

## Статус
- [ ] Не начата
- [ ] В процессе
- [ ] Завершена

**Начата:** -
**Завершена:** -

## Цель фазы
Улучшить admin UI: заменить polling на WebSocket для real-time обновлений и добавить полноценную мобильную адаптивность. После этой фазы admin-панель обновляется мгновенно и удобно работает на мобильных устройствах.

## Задачи

### 5.0 ОБЯЗАТЕЛЬНО: Анализ и планирование

#### A. Анализ существующего кода
- [ ] Изучить `admin-ui/index.html` — текущая структура UI (HTML/CSS/JS, ~1350 строк)
- [ ] Найти все `setInterval` / polling вызовы — определить что обновляется по таймеру
- [ ] Изучить `src/main.py` — текущие API роуты и middleware
- [ ] Изучить `src/api/` — все admin API эндпоинты
- [ ] Определить какие данные нужно обновлять в реальном времени:
  - Dashboard метрики (активные звонки, очередь)
  - Статусы операторов
  - Новые звонки в логе

**Команды для поиска:**
```bash
# Polling в admin UI
grep -n "setInterval\|setTimeout\|refresh" admin-ui/index.html
# WebSocket в FastAPI
grep -rn "WebSocket\|websocket" src/
# Текущие API эндпоинты для admin
grep -rn "router\.\|@app\." src/api/admin.py src/api/analytics.py
# Responsive/media queries
grep -n "@media\|viewport\|mobile" admin-ui/index.html
```

#### B. Анализ зависимостей
- [ ] Определить: FastAPI WebSocket (встроенный) или starlette WebSocketEndpoint
- [ ] Определить: нужен ли Redis Pub/Sub для трансляции событий между воркерами
- [ ] Определить: CSS framework для responsive или кастомные media queries

**Новые абстракции:** -
**Новые env variables:** -
**Новые tools:** -
**Миграции БД:** -

#### C. Проверка архитектуры
- [ ] WebSocket должен работать через reverse proxy (nginx) — учитывать upgrade headers
- [ ] WebSocket endpoint должен требовать аутентификацию (JWT token в query param или первом сообщении)
- [ ] При масштабировании: WebSocket + Redis Pub/Sub для broadcast

**Референс-модуль:** `src/core/audio_socket.py` — паттерн TCP-соединения, `admin-ui/index.html` — текущий UI

**Цель:** Спроектировать WebSocket и responsive подходы до начала кодирования.

**Заметки для переиспользования:** -

---

### 5.1 WebSocket backend

- [ ] Создать `src/api/websocket.py` — WebSocket endpoint для admin real-time обновлений
- [ ] Реализовать аутентификацию WebSocket (JWT token в query parameter `?token=...`)
- [ ] Реализовать Redis Pub/Sub подписку для получения событий:
  - `call:started` — новый звонок
  - `call:ended` — завершение звонка
  - `call:transferred` — перевод на оператора
  - `operator:status_changed` — изменение статуса оператора
  - `dashboard:metrics_updated` — обновление метрик (каждые 5 сек)
- [ ] Реализовать broadcast: все подключённые admin-клиенты получают события
- [ ] Реализовать heartbeat/ping-pong для обнаружения разорванных соединений
- [ ] Добавить graceful disconnect при закрытии сервера
- [ ] Добавить Prometheus метрики: `admin_websocket_connections_active`, `admin_websocket_messages_sent_total`
- [ ] Unit-тесты для WebSocket endpoint (FastAPI TestClient поддерживает WebSocket)

**Файлы:** `src/api/websocket.py`, `src/main.py` (подключить route)
**Заметки:** FastAPI имеет встроенную поддержку WebSocket: `@app.websocket("/ws")`. Redis Pub/Sub через `aioredis`.

---

### 5.2 Публикация событий в Redis Pub/Sub

Чтобы WebSocket получал обновления, компоненты системы должны публиковать события.

- [ ] Создать `src/events/publisher.py` — async функция публикации событий в Redis Pub/Sub
- [ ] Добавить публикацию `call:started` в `src/core/call_session.py` при начале звонка
- [ ] Добавить публикацию `call:ended` в `src/core/call_session.py` при завершении звонка
- [ ] Добавить публикацию `call:transferred` в `src/agent/tools.py` при вызове `transfer_to_operator`
- [ ] Добавить публикацию `operator:status_changed` в `src/api/operators.py` при смене статуса
- [ ] Формат события: `{"type": "...", "data": {...}, "timestamp": "ISO8601"}`
- [ ] Добавить периодическую публикацию `dashboard:metrics_updated` (каждые 5 сек) — Celery task или asyncio background task
- [ ] Unit-тесты для publisher

**Файлы:** `src/events/publisher.py`, `src/core/call_session.py`, `src/agent/tools.py`, `src/api/operators.py`
**Заметки:** Redis Pub/Sub не гарантирует доставку при reconnect — WebSocket клиент должен при подключении запрашивать полное состояние через HTTP, а далее получать дельты.

---

### 5.3 WebSocket в admin UI (frontend)

- [ ] Заменить `setInterval` polling на WebSocket-соединение в `admin-ui/index.html`
- [ ] Реализовать WebSocket клиент с auto-reconnect и exponential backoff
- [ ] При получении `call:started` / `call:ended` — обновить dashboard метрики и список звонков
- [ ] При получении `operator:status_changed` — обновить страницу операторов
- [ ] При получении `dashboard:metrics_updated` — обновить графики и счётчики
- [ ] Добавить индикатор статуса соединения: зелёный (connected), жёлтый (reconnecting), красный (disconnected)
- [ ] Fallback: если WebSocket недоступен, вернуться к polling
- [ ] При первом подключении: загрузить полное состояние через HTTP, затем применять WebSocket-дельты

**Файлы:** `admin-ui/index.html`
**Заметки:** WebSocket URL: `ws://host:port/ws?token=JWT`. При reconnect — повторная аутентификация.

---

### 5.4 Мобильная адаптивность admin UI

Viewport meta tag уже есть, но отсутствуют media queries для малых экранов.

- [ ] Добавить CSS media queries для breakpoints:
  - `@media (max-width: 768px)` — планшет
  - `@media (max-width: 480px)` — телефон
- [ ] Sidebar (220px fixed) → hamburger menu на мобильных (скрыт по умолчанию, toggle по клику)
- [ ] Dashboard cards → одна колонка на мобильных (`grid-template-columns: 1fr`)
- [ ] Таблицы → горизонтальный скролл (`overflow-x: auto`) или карточный вид
- [ ] Модальные окна → `width: 95%` на мобильных вместо `max-width: 700px`
- [ ] Фильтры → вертикальное расположение вместо горизонтального
- [ ] Кнопки → увеличить размер touch targets (min 44x44px по Apple HIG)
- [ ] Шрифты → использовать `rem` для адаптивности
- [ ] Протестировать на Chrome DevTools Mobile Simulator (iPhone SE, iPad, Pixel)

**Файлы:** `admin-ui/index.html` (CSS section)
**Заметки:** Минимальный подход: добавить media queries в существующий `<style>` блок. Не нужен CSS framework.

---

## При завершении фазы

Выполни следующие действия:

1. Убедись, что все задачи отмечены [x]
2. Измени статус фазы:
   - [x] Завершена
3. Заполни дату "Завершена: YYYY-MM-DD"
4. Выполни коммит:
   ```bash
   git add .
   git commit -m "checklist(production-readiness): phase-5 admin UI improvements completed"
   ```
5. Обнови PROGRESS.md:
   - Текущая фаза: завершено
   - Добавь запись в историю
6. Все фазы завершены — проект готов к production!
