# Фаза 2: ARI Transfer Implementation

## Статус
- [x] Не начата
- [x] В процессе
- [x] Завершена

**Начата:** 2026-03-04
**Завершена:** 2026-03-04

## Цель фазы
Подключить существующий ARI client к transfer_to_operator tool. Реализовать реальный SIP redirect.

## Задачи

### 2.1 Подготовить ARI client для использования в tool handler
- [x] В `src/main.py`: создать shared `AsteriskARIClient` instance при startup (если ARI URL настроен)
- [x] Хранить в module-level variable `_ari_client: AsteriskARIClient | None`
- [x] Инициализировать в `startup` event handler
- [x] Cleanup в `shutdown` event handler (close aiohttp session)

**Файлы:** `src/main.py`

---

### 2.2 Обновить transfer_to_operator tool handler
- [x] Если `_ari_client` is not None: вызвать `await _ari_client.transfer_to_queue(channel_uuid)`
- [x] При успехе: `session.transferred = True`, return `{"status": "transferring"}`
- [x] При ошибке ARI (timeout, connection error): return `{"status": "error", "message": "Не вдалося з'єднати..."}`
- [x] При `_ari_client is None` (ARI не настроен): return `{"status": "unavailable"}` + WARNING log
- [x] Добавить Prometheus counter: `callcenter_transfers_attempted_total{result=success|error|unavailable}`
- [x] Добавить timeout для ARI call: 5 секунд

**Файлы:** `src/main.py`
**Audit refs:** CRIT-01

---

### 2.3 Обновить pipeline lifecycle при transfer
- [x] В `src/core/pipeline.py`: при `session.transferred == True`:
  - Если ARI transfer успешен: не закрывать AudioSocket (Asterisk заберёт канал)
  - Дождаться hangup пакета от Asterisk (означает что transfer завершён)
  - Или timeout 30с → закрыть соединение
- [x] Проиграть TRANSFER_TEXT перед ARI redirect (пока ещё на AudioSocket)
- [x] Не проигрывать farewell после transfer

**Примечание:** Логика ожидания hangup добавлена в `main.py` после `pipeline.run()` (функция `_wait_for_hangup()`), а не в pipeline.py, так как pipeline не должен знать о деталях ARI. Pipeline уже корректно: проигрывает TRANSFER_TEXT, ставит TRANSFERRING state и break. После pipeline.run() main.py ждёт hangup до 30с.

**Файлы:** `src/main.py`, `src/core/pipeline.py` (без изменений — логика уже корректна)

---

### 2.4 Обновить Asterisk конфигурацию
- [x] В `asterisk/queues.conf`: раскомментировать или добавить хотя бы одного оператора
- [x] Добавить комментарий: как добавить новых операторов
- [x] В `asterisk/extensions.conf`: убедиться что `[transfer-to-operator]` корректен
- [x] Queue timeout 120с → VoiceMail → Hangup: проверить что voicemail.conf настроен

**Примечание:** Добавлен `exten => s` в `[transfer-to-operator]` так как ARI redirect использует `Local/s@transfer-to-operator`. Без этого Asterisk не matching pattern `_X.` (который ожидает цифры, а не `s`).

**Файлы:** `asterisk/queues.conf`, `asterisk/extensions.conf`

---

### 2.5 Тесты
- [x] Unit тест: transfer_to_operator с mock ARI client → success → session.transferred=True
- [x] Unit тест: transfer_to_operator с ARI timeout → error response
- [x] Unit тест: transfer_to_operator без ARI client → unavailable response
- [x] Unit тест: pipeline behavior при transferred=True — не проигрывает farewell

**Файлы:** `tests/unit/test_transfer.py` (новый)

---

## При завершении фазы
1. Выполни коммит:
   ```bash
   git add src/main.py src/core/pipeline.py src/core/asterisk_ari.py asterisk/ tests/
   git commit -m "checklist(p0-transfer-to-operator): phase-2 ARI transfer implemented"
   ```
2. Обнови PROGRESS.md: Текущая фаза: 3
