# Фаза 4: TTS модуль (Text-to-Speech)

## Статус
- [x] Не начата
- [x] В процессе
- [x] Завершена

**Начата:** 2026-02-13
**Завершена:** 2026-02-13

## Цель фазы

Реализовать модуль синтеза украинской речи. Текст ответа агента → аудио в формате AudioSocket (16kHz, 16-bit PCM). Кэширование частых фраз, streaming по предложениям.

## Задачи

### 4.0 ОБЯЗАТЕЛЬНО: Анализ и планирование

#### A. Анализ существующего кода
- [x] Проверить наличие `src/tts/base.py`, `src/tts/google_tts.py`
- [x] Изучить Google Cloud TTS API
- [x] Изучить паттерн из `src/stt/base.py` (аналогичная абстракция)

#### B. Анализ зависимостей
- [x] Нужна абстракция Protocol для TTS? — Да, `TTSEngine(Protocol)`
- [x] Нужны ли новые env variables? — `GOOGLE_TTS_VOICE`, `GOOGLE_TTS_SPEAKING_RATE`
- [x] Метрики: `tts_latency_ms`, `tts_cache_hit_rate`

**Новые абстракции:** `TTSEngine(Protocol)` в `src/tts/base.py`
**Новые env variables:** TTS voice config (уже в phase-01)
**Новые tools:** Нет
**Миграции БД:** Нет

#### C. Проверка архитектуры
- [x] Формат аудио: LINEAR16, 16kHz — совпадает с AudioSocket
- [x] Кэширование частых фраз (приветствие, прощание, ожидание)
- [x] Streaming по предложениям для длинных ответов
- [x] Бюджет задержки TTS: ≤ 400ms (из NFR)

**Референс-модуль:** `src/stt/base.py` (аналогичная структура)

**Заметки для переиспользования:** Паттерн Protocol + frozen dataclass из STT полностью переиспользован. SHA256 хеш текста как ключ кэша.

---

### 4.1 Абстрактный интерфейс TTS (Protocol)

- [x] Создать `src/tts/base.py` с Protocol-классом `TTSEngine`
- [x] Методы: `synthesize(text) -> bytes`, `synthesize_stream(text) -> AsyncIterator[bytes]`
- [x] Dataclass `TTSConfig`: `language_code`, `voice_name`, `speaking_rate`, `sample_rate_hertz`

**Файлы:** `src/tts/base.py`
**Заметки:** `@runtime_checkable Protocol`, `TTSConfig` — frozen dataclass с slots.

---

### 4.2 Google Cloud TTS реализация

- [x] Создать `src/tts/google_tts.py` — реализация `TTSEngine`
- [x] Конфигурация голоса: `uk-UA-Standard-A` (или Neural2 для лучшего качества)
- [x] Формат аудио: `LINEAR16`, `sample_rate_hertz=16000`
- [x] `speaking_rate=1.0` (нормальная скорость)
- [x] Обработка ошибок API с логированием

**Файлы:** `src/tts/google_tts.py`
**Заметки:** Использует `TextToSpeechAsyncClient`. `initialize()` создаёт клиента и предзагружает кэш.

---

### 4.3 Кэширование частых фраз

- [x] Реализовать кэширование в `GoogleTTSEngine`
- [x] Кэш в памяти (dict) для часто используемых фраз
- [x] Предзагрузка фраз при старте: "Добрий день!", "Зачекайте, будь ласка", "Дякую за дзвінок!"
- [x] Метрика: cache hit rate

**Файлы:** `src/tts/google_tts.py`
**Заметки:** 7 фраз предзагружаются. Короткие фразы (<100 символов) кэшируются автоматически. `cache_hit_rate` property для метрик.

---

### 4.4 Streaming по предложениям

- [x] Разбиение текста ответа на предложения
- [x] Синтез и отправка каждого предложения отдельно
- [x] Параллельная обработка: синтез следующего предложения во время воспроизведения текущего

**Файлы:** `src/tts/google_tts.py`
**Заметки:** `synthesize_stream()` использует regex `(?<=[.!?])\s+` для разбиения. Каждое предложение синтезируется отдельно и кэшируется.

---

### 4.5 Mock-реализация для тестов

- [x] Создать mock-реализацию `TTSEngine` для unit-тестов
- [x] Возврат пустого аудио (тишина) нужной длительности
- [x] Имитация задержек и ошибок

**Файлы:** `tests/unit/mocks/mock_tts.py`
**Заметки:** `MockTTSEngine`: 640 байт тишины × кол-во слов × `frames_per_word`. `error` parameter для имитации ошибок.

---

## При завершении фазы

1. Убедись, что все задачи отмечены [x]
2. Измени статус фазы: [x] Завершена
3. Заполни дату "Завершена: YYYY-MM-DD"
4. Выполни коммит:
   ```bash
   git add .
   git commit -m "checklist(phase-1-mvp): phase-4 TTS module completed"
   ```
5. Обнови PROGRESS.md
6. Открой следующую фазу: `phase-05-llm-agent.md`
