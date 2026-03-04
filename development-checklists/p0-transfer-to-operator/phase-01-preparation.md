# Фаза 1: Подготовка

## Статус
- [x] Не начата
- [x] В процессе
- [x] Завершена

**Начата:** 2026-03-04
**Завершена:** 2026-03-04

## Цель фазы
Понять текущее состояние ARI кода и Asterisk конфигурации. Определить план интеграции.

## Задачи

### 1.0 ОБЯЗАТЕЛЬНО: Анализ и планирование

#### A. Анализ существующего кода
- [x] Прочитать `src/core/asterisk_ari.py` — полностью. Найти `transfer_to_queue()` и все методы
- [x] Прочитать `src/main.py` — найти `transfer_to_operator` tool handler (строки ~1963-1972)
- [x] Прочитать `src/main.py` — найти где создаётся/используется `AsteriskARIClient`
- [x] Прочитать `src/core/pipeline.py` — найти обработку `session.transferred` flag
- [x] Прочитать `src/agent/prompts.py` — найти ERROR_TEXT, TRANSFER_TEXT
- [x] Прочитать `asterisk/extensions.conf` — контекст `[transfer-to-operator]`
- [x] Прочитать `asterisk/queues.conf` — настройки очереди операторов
- [x] Прочитать `src/config.py` — `ARISettings`

**Команды для поиска:**
```bash
grep -rn "transfer_to_operator\|transferred\|transfer_to_queue\|AsteriskARI" src/
grep -rn "ERROR_TEXT\|TRANSFER_TEXT" src/
cat asterisk/extensions.conf
cat asterisk/queues.conf
```

**Результаты анализа:**

1. **`asterisk_ari.py`**: Полноценный ARI client с 4 методами — `get_caller_id()`, `get_channel_variable()`, `transfer_to_queue()`, `open()/close()`. Метод `transfer_to_queue()` делает POST на `/channels/{uuid}/redirect` с `endpoint: Local/s@{context}`. Использует aiohttp BasicAuth, timeout 5s. Метод готов к использованию, но нигде не вызывается для трансфера.

2. **`main.py` transfer handler** (строки 1963-1974): Просто ставит `session.transferred = True` + пишет WARNING лог "ARI not configured — flag set but no SIP transfer performed". Возвращает `{"status": "transferring"}`. Реального ARI вызова нет.

3. **ARI client usage в main.py**: ARI создаётся ad-hoc (open/close) в 3 местах — для CallerID lookup (строка 519), для IVR intent (строка 550), для tenant resolution (строка 618). Нет shared instance — каждый раз new/open/close. Нужен module-level `_ari_client`.

4. **Pipeline transferred handling** (pipeline.py строки 631-636): После каждого цикла обработки проверяет `session.transferred`. Если True — проигрывает TRANSFER_TEXT, делает transition в TRANSFERRING, и break. После этого pipeline.run() завершается. AudioSocket закрывается в finally блоке main.py (не в pipeline).

5. **ERROR_TEXT** (prompts.py строка 1310): `"Перепрошую, виникла технічна помилка. З'єдную з оператором."` — ЛОЖНО обещает оператора. Используется как fallback в pipeline при пустом ответе LLM и при exception. TRANSFER_TEXT (строка 1308): `"Зараз з'єдную вас з оператором. Залишайтесь на лінії."` — OK если transfer реально работает.

6. **`extensions.conf`** `[transfer-to-operator]`: `Queue(operators,t,,,120)` → VoiceMail → Hangup. Для exten `_X.` (любой номер). Контекст корректный.

7. **`queues.conf`**: Очередь `operators` настроена (strategy=ringall, timeout=30, retry=5), но все операторы закомментированы. Нужно раскомментировать хотя бы одного.

8. **`config.py` ARISettings** (строки 96-101): Дефолты `http://localhost:8088/ari`, user=`ari_user`, password=`ari_password`. Env prefix `ARI_`. Всё на месте.

#### B. Анализ зависимостей
- [x] ARI endpoint доступен? (http://asterisk:8088/ari на проде)
- [x] Нужны ли новые env variables? (ARI settings уже есть в config)
- [x] Нужно ли менять pipeline lifecycle при transfer?

**Новые env variables:** нет (ARISettings уже существует)
**Миграции БД:** нет

**Результаты:**
- ARI endpoint: настраивается через `ARI_URL` env var, на проде обычно `http://asterisk:8088/ari` (в docker-compose) или `http://localhost:8088/ari` (single-host). Код клиента уже работает для CallerID lookup.
- Новые env variables не нужны — `ARI_URL`, `ARI_USER`, `ARI_PASSWORD` уже в `ARISettings`.
- Pipeline lifecycle НУЖНО менять: сейчас после `session.transferred` pipeline просто break-ает и AudioSocket закрывается. Нужно: (1) проиграть TRANSFER_TEXT, (2) вызвать ARI redirect, (3) НЕ закрывать AudioSocket — ждать hangup от Asterisk или timeout.

#### C. Проверка архитектуры
- [x] Определить: AudioSocket должен оставаться открытым во время ARI redirect?
- [x] Определить: pipeline должен завершиться или ждать confirmation?
- [x] Определить: что происходит с call_session при transfer?

**Референс-модуль:** `src/core/asterisk_ari.py`

**Результаты:**
1. **AudioSocket при redirect**: ДА, должен оставаться открытым. ARI redirect меняет маршрутизацию канала Asterisk, но до момента redirect AudioSocket — единственное соединение с каналом. Закрытие AudioSocket до redirect приведёт к hangup.
2. **Pipeline**: Должен завершить loop (break), затем в post-pipeline коде в main.py нужно: (a) если transfer успешен — ожидать hangup пакет до 30с, (b) если transfer неуспешен — сообщить клиенту и закрыть. Pipeline сам не должен закрывать AudioSocket.
3. **call_session**: transition LISTENING → TRANSFERRING → ENDED. `transferred=True`, `transfer_reason` записывается. Это уже реализовано в call_session.py (метод `mark_transferred()`).

**Заметки для переиспользования:**
- `transfer_to_queue()` в asterisk_ari.py уже готов — просто нужно вызвать
- ARI client нужно сделать shared (module-level `_ari_client`) чтобы не создавать каждый раз
- Pipeline transfer logic (строки 631-636) нужно расширить: вместо просто break — добавить ARI call
- Важно: ARI redirect использует `Local/s@transfer-to-operator`, но exten в контексте `_X.` — значит нужно либо `Local/s@transfer-to-operator`, либо передать CallerID. Текущий код использует `Local/s@{context}` — это создаст Local channel с exten=s, что НЕ matching `_X.`. Нужно исправить на конкретный exten.

---

## При завершении фазы
1. Выполни коммит: `checklist(p0-transfer-to-operator): phase-1 preparation completed`
2. Обнови PROGRESS.md: Текущая фаза: 2
