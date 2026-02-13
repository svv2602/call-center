# Фаза 2: AudioSocket сервер

## Статус
- [ ] Не начата
- [ ] В процессе
- [ ] Завершена

**Начата:** -
**Завершена:** -

## Цель фазы

Реализовать TCP-сервер, принимающий AudioSocket-соединения от Asterisk. Парсинг протокола, управление сессиями звонков, двунаправленная передача аудио.

## Задачи

### 2.0 ОБЯЗАТЕЛЬНО: Анализ и планирование

#### A. Анализ существующего кода
- [ ] Проверить наличие `src/core/audio_socket.py`, `src/core/call_session.py`
- [ ] Изучить паттерн asyncio TCP-сервера (`asyncio.start_server`)
- [ ] Определить формат протокола AudioSocket

**Команды для поиска:**
```bash
ls src/core/
grep -rn "asyncio.start_server\|StreamReader\|StreamWriter" src/
grep -rn "AudioSocket\|audio_socket" src/
```

#### B. Анализ зависимостей
- [ ] Нужна ли абстракция Protocol для AudioSocket? — Нет, единственная реализация
- [ ] Нужны ли новые env variables? — `AUDIOSOCKET_HOST`, `AUDIOSOCKET_PORT` (уже в config)
- [ ] Нужна ли интеграция с Redis для сессий? — Да, Session Manager хранит state в Redis

**Новые абстракции:** Нет
**Новые env variables:** Нет (уже определены в phase-01)
**Новые tools:** Нет
**Миграции БД:** Нет

#### C. Проверка архитектуры
- [ ] Каждый звонок — отдельная `asyncio.Task`
- [ ] Graceful shutdown при hangup (type=0x00) или отключении клиента
- [ ] `call_id` = UUID из AudioSocket (type=0x01)

**Референс-модуль:** `doc/technical/architecture.md` — секции 3.1, 3.2

**Цель:** Понять протокол AudioSocket и архитектуру Session Manager.

**Заметки для переиспользования:** -

---

### 2.1 Парсинг протокола AudioSocket

- [ ] Реализовать парсер пакетов AudioSocket: `[type:1B][length:2B BE][payload:NB]`
- [ ] Обработка типов: `0x01` = UUID, `0x10` = audio, `0x00` = hangup, `0xFF` = error
- [ ] Валидация длины пакета (length соответствует payload)
- [ ] Обработка неполных пакетов (буферизация до полного получения)
- [ ] Функция формирования исходящих аудио-пакетов (type=0x10)

**Файлы:** `src/core/audio_socket.py`

**Протокол:**
```
Type 0x01 = UUID канала Asterisk (первый пакет)
Type 0x10 = Audio data (16kHz, 16-bit signed linear PCM, little-endian)
Type 0x00 = Hangup (клиент повесил трубку)
Type 0xFF = Error
```

**Заметки:** Аудио: 16kHz × 16-bit = 32000 bytes/sec. Фрейм 20ms = 640 bytes.

---

### 2.2 TCP-сервер (asyncio)

- [ ] Реализовать `AudioSocketServer` на базе `asyncio.start_server`
- [ ] Обработка каждого соединения в отдельной `asyncio.Task`
- [ ] При подключении: прочитать UUID-пакет (type=0x01), создать сессию
- [ ] Цикл чтения: читать пакеты, dispatch по типу
- [ ] Запись аудио: метод для отправки TTS-аудио обратно в Asterisk
- [ ] Graceful shutdown: обработка SIGINT/SIGTERM, закрытие всех соединений

**Файлы:** `src/core/audio_socket.py`
**Заметки:** Порт по умолчанию: 9092

---

### 2.3 Session Manager

- [ ] Реализовать `CallSession` — state machine для жизненного цикла звонка
- [ ] Состояния: Connected → Greeting → Listening → Processing → Speaking → Transferring → Ended
- [ ] Хранение истории диалога (messages для LLM)
- [ ] Таймаут тишины: 10 сек → "Ви ще на лінії?", ещё 10 сек → hangup
- [ ] Максимальная длительность звонка — настраиваемый параметр

**Файлы:** `src/core/call_session.py`

**Состояния сессии:**
```
Connected → Greeting → Listening → Processing → Speaking → Listening (цикл)
                                                        → Transferring → Ended
Listening → Timeout (10s) → "Ви ще на лінії?" → Timeout (10s) → Ended
```

**Заметки:** -

---

### 2.4 Хранение сессий в Redis

- [ ] Сериализация/десериализация `CallSession` в Redis
- [ ] Ключ: `call_session:{channel_uuid}`
- [ ] TTL: 1800 секунд (30 мин) — защита от утечки памяти при аварийных обрывах
- [ ] Обновление TTL при каждой активности
- [ ] Удаление сессии при нормальном завершении звонка

**Файлы:** `src/core/call_session.py`

**Пример:**
```python
await redis.setex(
    f"call_session:{channel_uuid}",
    ttl=1800,
    value=session.serialize(),
)
```

**Заметки:** Stateless Call Processor → сессии в Redis → горизонтальное масштабирование

---

### 2.5 Точка входа (main.py)

- [ ] Создать `src/main.py` — запуск AudioSocket сервера
- [ ] Загрузка конфигурации из env
- [ ] Инициализация подключений (Redis, PostgreSQL)
- [ ] Запуск AudioSocket TCP-сервера
- [ ] Graceful shutdown (закрытие всех соединений, flush логов)
- [ ] Health-check endpoint (FastAPI на порту 8080)

**Файлы:** `src/main.py`
**Заметки:** Запуск: `python -m src.main`

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
