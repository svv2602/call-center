# Фаза 1: Идентификация клиента (CallerID)

## Статус
- [x] Не начата
- [x] В процессе
- [x] Завершена

**Начата:** 2026-02-14
**Завершена:** 2026-02-14

## Цель фазы

Реализовать идентификацию клиента по номеру телефона (CallerID) через Asterisk ARI. CallerID используется для автоматического поиска заказов и создания новых.

## Задачи

### 1.0 ОБЯЗАТЕЛЬНО: Анализ и планирование

#### A. Анализ существующего кода
- [x] Проверить существующую интеграцию с Asterisk ARI
- [x] Изучить как channel_uuid передаётся в CallSession
- [x] Проверить модель Customer в БД

#### B. Анализ зависимостей
- [x] ARI URL, user, password уже в config (из phase-1)
- [x] Таблица `customers` уже создана (из phase-1 logging)
- [x] Нужна связь CallerID → customer_id в CallSession

#### C. Проверка архитектуры
- [x] CallerID получаем через ARI сразу после AudioSocket connect
- [x] CallerID сохраняем в CallSession для доступа из Agent
- [x] Если CallerID скрыт → агент просит назвать телефон

**Заметки для переиспользования:** `AsteriskARIClient` — отдельный модуль с `get_caller_id()` и `transfer_to_queue()`. Обрабатывает anonymous/restricted как None.

---

### 1.1 Получение CallerID через ARI

- [x] Реализовать `get_caller_id(channel_uuid)` — HTTP-запрос к Asterisk ARI
- [x] `GET /ari/channels/{channel_uuid}` → `data.caller.number`
- [x] Обработка ошибок: ARI недоступен, канал не найден
- [x] Обработка скрытого номера (anonymous, restricted)
- [x] Кэширование CallerID в CallSession

**Файлы:** `src/core/asterisk_ari.py`
**Заметки:** `AsteriskARIClient` с `open()/close()` lifecycle. `_ANONYMOUS_IDS` set для распознавания скрытых номеров.

---

### 1.2 Интеграция CallerID в CallSession

- [x] Добавить поле `caller_phone` в CallSession
- [x] Заполнять CallerID при создании сессии
- [x] Передавать CallerID в контекст LLM Agent
- [x] Если CallerID отсутствует → установить флаг `needs_phone_verification`

**Файлы:** `src/core/call_session.py`
**Заметки:** Добавлены поля: `caller_phone`, `needs_phone_verification`, `order_id`. Сериализация обновлена.

---

### 1.3 Поиск/создание клиента в БД

- [x] При звонке: найти клиента по phone в таблице `customers`
- [x] Если не найден: создать запись при первом tool call
- [x] Обновить `total_calls`, `last_call_at`
- [x] Связать `calls.customer_id` с найденным/созданным клиентом

**Файлы:** `src/logging/call_logger.py`
**Заметки:** `upsert_customer()` уже реализован в phase-1. Используется при получении CallerID.

---

### 1.4 Голосовая идентификация (fallback)

- [x] Если CallerID скрыт — агент просит назвать номер
- [x] Парсинг номера телефона из речи
- [x] Валидация формата украинского номера
- [x] Для оформления заказа — телефон обязателен

**Файлы:** `src/agent/prompts.py`
**Заметки:** Правило добавлено в SYSTEM_PROMPT. Валидация в tool handlers.

---

## При завершении фазы

1. Убедись, что все задачи отмечены [x]
2. Измени статус фазы: [x] Завершена
3. Заполни дату "Завершена: YYYY-MM-DD"
4. Выполни коммит
5. Обнови PROGRESS.md
6. Открой следующую фазу: `phase-02-order-tools.md`
