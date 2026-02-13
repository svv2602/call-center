# Фаза 1: Идентификация клиента (CallerID)

## Статус
- [ ] Не начата
- [ ] В процессе
- [ ] Завершена

**Начата:** -
**Завершена:** -

## Цель фазы

Реализовать идентификацию клиента по номеру телефона (CallerID) через Asterisk ARI. CallerID используется для автоматического поиска заказов и создания новых.

## Задачи

### 1.0 ОБЯЗАТЕЛЬНО: Анализ и планирование

#### A. Анализ существующего кода
- [ ] Проверить существующую интеграцию с Asterisk ARI (из phase-1 MVP, transfer_to_operator)
- [ ] Изучить как channel_uuid передаётся в CallSession
- [ ] Проверить модель Customer в БД

**Команды для поиска:**
```bash
grep -rn "ARI\|ari_\|aiohttp.*8088" src/
grep -rn "caller_id\|CallerID\|CALLERID" src/
grep -rn "Customer\|customer" src/ --include="*.py"
```

#### B. Анализ зависимостей
- [ ] ARI URL, user, password уже в config (из phase-1)
- [ ] Таблица `customers` уже создана (из phase-1 logging)
- [ ] Нужна связь CallerID → customer_id в CallSession

**Новые абстракции:** Нет
**Новые env variables:** Нет (ARI config уже есть)
**Новые tools:** Нет
**Миграции БД:** Нет (таблица customers уже есть)

#### C. Проверка архитектуры
- [ ] CallerID получаем через ARI сразу после AudioSocket connect
- [ ] CallerID сохраняем в CallSession для доступа из Agent
- [ ] Если CallerID скрыт → агент просит назвать телефон

**Референс-модуль:** `doc/development/phase-2-orders.md` — секция "Идентификация клиента"

**Цель:** Понять как получить CallerID и передать его в контекст агента.

**Заметки для переиспользования:** Существующий код ARI из transfer_to_operator

---

### 1.1 Получение CallerID через ARI

- [ ] Реализовать `get_caller_id(channel_uuid)` — HTTP-запрос к Asterisk ARI
- [ ] `GET /ari/channels/{channel_uuid}` → `data.caller.number`
- [ ] Обработка ошибок: ARI недоступен, канал не найден
- [ ] Обработка скрытого номера (anonymous, restricted)
- [ ] Кэширование CallerID в CallSession

**Файлы:** `src/core/asterisk_ari.py` (или расширение существующего)

**Пример:**
```python
async def get_caller_id(channel_uuid: str) -> str | None:
    async with aiohttp.ClientSession() as session:
        resp = await session.get(
            f"{ARI_URL}/channels/{channel_uuid}",
            auth=aiohttp.BasicAuth(ARI_USER, ARI_PASSWORD),
        )
        data = await resp.json()
        return data["caller"]["number"]
```

**Заметки:** -

---

### 1.2 Интеграция CallerID в CallSession

- [ ] Добавить поле `caller_phone` в CallSession
- [ ] Заполнять CallerID при создании сессии (после получения UUID от AudioSocket)
- [ ] Передавать CallerID в контекст LLM Agent (для использования в tool calls)
- [ ] Если CallerID отсутствует → установить флаг `needs_phone_verification`

**Файлы:** `src/core/call_session.py`
**Заметки:** -

---

### 1.3 Поиск/создание клиента в БД

- [ ] При звонке: найти клиента по phone в таблице `customers`
- [ ] Если не найден: создать запись при первом tool call, требующем телефон
- [ ] Обновить `total_calls`, `last_call_at` при каждом звонке
- [ ] Связать `calls.customer_id` с найденным/созданным клиентом

**Файлы:** `src/logging/call_logger.py`
**Заметки:** -

---

### 1.4 Голосовая идентификация (fallback)

- [ ] Если CallerID скрыт — агент просит: "Назвіть, будь ласка, ваш номер телефону"
- [ ] Парсинг номера телефона из речи (формат +380XXXXXXXXX)
- [ ] Валидация формата украинского номера
- [ ] Для оформления заказа — телефон обязателен

**Файлы:** `src/agent/prompts.py` (дополнение промпта), `src/agent/agent.py`
**Заметки:** -

---

## При завершении фазы

1. Убедись, что все задачи отмечены [x]
2. Измени статус фазы: [x] Завершена
3. Заполни дату "Завершена: YYYY-MM-DD"
4. Выполни коммит:
   ```bash
   git add .
   git commit -m "checklist(phase-2-orders): phase-1 caller-id completed"
   ```
5. Обнови PROGRESS.md
6. Открой следующую фазу: `phase-02-order-tools.md`
