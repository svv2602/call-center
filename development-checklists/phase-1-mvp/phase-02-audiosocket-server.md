# Фаза 2: AudioSocket сервер

## Статус
- [x] Не начата
- [x] В процессе
- [x] Завершена

**Начата:** 2026-02-13
**Завершена:** 2026-02-13

## Цель фазы

Реализовать TCP-сервер, принимающий AudioSocket-соединения от Asterisk. Парсинг протокола, управление сессиями звонков, двунаправленная передача аудио.

## Задачи

### 2.0 ОБЯЗАТЕЛЬНО: Анализ и планирование

#### A. Анализ существующего кода
- [x] Проверить наличие `src/core/audio_socket.py`, `src/core/call_session.py`
- [x] Изучить паттерн asyncio TCP-сервера (`asyncio.start_server`)
- [x] Определить формат протокола AudioSocket

**Команды для поиска:**
```bash
ls src/core/
grep -rn "asyncio.start_server\|StreamReader\|StreamWriter" src/
grep -rn "AudioSocket\|audio_socket" src/
```

#### B. Анализ зависимостей
- [x] Нужна ли абстракция Protocol для AudioSocket? — Нет, единственная реализация
- [x] Нужны ли новые env variables? — `AUDIOSOCKET_HOST`, `AUDIOSOCKET_PORT` (уже в config)
- [x] Нужна ли интеграция с Redis для сессий? — Да, Session Manager хранит state в Redis

**Новые абстракции:** Нет
**Новые env variables:** Нет (уже определены в phase-01)
**Новые tools:** Нет
**Миграции БД:** Нет

#### C. Проверка архитектуры
- [x] Каждый звонок — отдельная `asyncio.Task`
- [x] Graceful shutdown при hangup (type=0x00) или отключении клиента
- [x] `call_id` = UUID из AudioSocket (type=0x01)

**Референс-модуль:** `doc/technical/architecture.md` — секции 3.1, 3.2

**Цель:** Понять протокол AudioSocket и архитектуру Session Manager.

**Заметки для переиспользования:** Протокол прост: 3-байтовый заголовок + payload. `asyncio.StreamReader.readexactly()` обеспечивает буферизацию неполных пакетов.

---

### 2.1 Парсинг протокола AudioSocket

- [x] Реализовать парсер пакетов AudioSocket: `[type:1B][length:2B BE][payload:NB]`
- [x] Обработка типов: `0x01` = UUID, `0x10` = audio, `0x00` = hangup, `0xFF` = error
- [x] Валидация длины пакета (length соответствует payload)
- [x] Обработка неполных пакетов (буферизация до полного получения)
- [x] Функция формирования исходящих аудио-пакетов (type=0x10)

**Файлы:** `src/core/audio_socket.py`

**Протокол:**
```
Type 0x01 = UUID канала Asterisk (первый пакет)
Type 0x10 = Audio data (16kHz, 16-bit signed linear PCM, little-endian)
Type 0x00 = Hangup (клиент повесил трубку)
Type 0xFF = Error
```

**Заметки:** `read_packet()` и `build_audio_packet()` — основные функции. `readexactly()` автоматически буферизует неполные данные. Аудио отправляется чанками по 640 байт (20ms фрейм).

---

### 2.2 TCP-сервер (asyncio)

- [x] Реализовать `AudioSocketServer` на базе `asyncio.start_server`
- [x] Обработка каждого соединения в отдельной `asyncio.Task`
- [x] При подключении: прочитать UUID-пакет (type=0x01), создать сессию
- [x] Цикл чтения: читать пакеты, dispatch по типу
- [x] Запись аудио: метод для отправки TTS-аудио обратно в Asterisk
- [x] Graceful shutdown: обработка SIGINT/SIGTERM, закрытие всех соединений

**Файлы:** `src/core/audio_socket.py`
**Заметки:** `AudioSocketServer` принимает `on_connection` callback. `_handle_client` читает UUID, создаёт `AudioSocketConnection`, делегирует обработку. `stop()` отменяет все задачи.

---

### 2.3 Session Manager

- [x] Реализовать `CallSession` — state machine для жизненного цикла звонка
- [x] Состояния: Connected → Greeting → Listening → Processing → Speaking → Transferring → Ended
- [x] Хранение истории диалога (messages для LLM)
- [x] Таймаут тишины: 10 сек → "Ви ще на лінії?", ещё 10 сек → hangup
- [x] Максимальная длительность звонка — настраиваемый параметр

**Файлы:** `src/core/call_session.py`

**Состояния сессии:**
```
Connected → Greeting → Listening → Processing → Speaking → Listening (цикл)
                                                        → Transferring → Ended
Listening → Timeout (10s) → "Ви ще на лінії?" → Timeout (10s) → Ended
```

**Заметки:** `CallState` enum + `_VALID_TRANSITIONS` dict для валидации переходов. `record_timeout()` возвращает True при достижении `MAX_TIMEOUTS_BEFORE_HANGUP` (2). `messages_for_llm` — property для формата Claude API.

---

### 2.4 Хранение сессий в Redis

- [x] Сериализация/десериализация `CallSession` в Redis
- [x] Ключ: `call_session:{channel_uuid}`
- [x] TTL: 1800 секунд (30 мин) — защита от утечки памяти при аварийных обрывах
- [x] Обновление TTL при каждой активности
- [x] Удаление сессии при нормальном завершении звонка

**Файлы:** `src/core/call_session.py`

**Пример:**
```python
await redis.setex(
    f"call_session:{channel_uuid}",
    ttl=1800,
    value=session.serialize(),
)
```

**Заметки:** `SessionStore` класс инкапсулирует Redis-операции: `save()`, `load()`, `delete()`, `exists()`. JSON-сериализация через `serialize()`/`deserialize()`.

---

### 2.5 Точка входа (main.py)

- [x] Создать `src/main.py` — запуск AudioSocket сервера
- [x] Загрузка конфигурации из env
- [x] Инициализация подключений (Redis, PostgreSQL)
- [x] Запуск AudioSocket TCP-сервера
- [x] Graceful shutdown (закрытие всех соединений, flush логов)
- [x] Health-check endpoint (FastAPI на порту 8080)

**Файлы:** `src/main.py`
**Заметки:** Redis graceful degradation: если недоступен, приложение продолжает с in-memory сессиями. Health-check возвращает `active_calls` и статус Redis.

---

## При завершении фазы

1. Убедись, что все задачи отмечены [x]
2. Измени статус фазы: [x] Завершена
3. Заполни дату "Завершена: YYYY-MM-DD"
4. Выполни коммит:
   ```bash
   git add .
   git commit -m "checklist(phase-1-mvp): phase-2 audiosocket server completed"
   ```
5. Обнови PROGRESS.md
6. Открой следующую фазу: `phase-03-stt-module.md`
