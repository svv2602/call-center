# Фаза 3: STT модуль (Speech-to-Text)

## Статус
- [x] Не начата
- [x] В процессе
- [x] Завершена

**Начата:** 2026-02-13
**Завершена:** 2026-02-13

## Цель фазы

Реализовать модуль потокового распознавания речи с мультиязычной поддержкой (украинский + русский). Клиент говорит — система распознаёт в реальном времени.

## Задачи

### 3.0 ОБЯЗАТЕЛЬНО: Анализ и планирование

#### A. Анализ существующего кода
- [x] Проверить наличие `src/stt/base.py`, `src/stt/google_stt.py`
- [x] Изучить Google Cloud Speech-to-Text v2 API (streaming gRPC)
- [x] Определить формат Transcript dataclass

#### B. Анализ зависимостей
- [x] Нужна абстракция Protocol для STT? — Да, `STTEngine(Protocol)`
- [x] Нужны ли новые env variables? — `GOOGLE_APPLICATION_CREDENTIALS`, `GOOGLE_STT_LANGUAGE_CODE`, `GOOGLE_STT_ALTERNATIVE_LANGUAGES`
- [x] Метрики: `stt_latency_ms`, `stt_confidence`, `detected_language`

**Новые абстракции:** `STTEngine(Protocol)` в `src/stt/base.py`
**Новые env variables:** GCP credentials (уже в phase-01)
**Новые tools:** Нет
**Миграции БД:** Нет

#### C. Проверка архитектуры
- [x] Streaming API — отправка аудио-чанков по мере поступления
- [x] `interim_results=True` — промежуточные результаты
- [x] Restart сессии каждые ~5 мин (лимит Google STT)
- [x] VAD (Voice Activity Detection) для определения конца фразы

**Референс-модуль:** `doc/technical/architecture.md` — секция 3.4

**Заметки для переиспользования:** Google STT v2 API использует `language_codes` (список) вместо `language_code` + `alternative_language_codes`. Модель: `latest_long`. Автоперезапуск через 290 секунд.

---

### 3.1 Абстрактный интерфейс STT (Protocol)

- [x] Создать `src/stt/base.py` с Protocol-классом `STTEngine`
- [x] Методы: `start_stream()`, `feed_audio()`, `get_transcripts()`, `stop_stream()`
- [x] Dataclass `Transcript`: `text`, `is_final`, `confidence`, `language`
- [x] Dataclass `STTConfig`: `language_code`, `alternative_languages`, `sample_rate_hertz`, `interim_results`

**Файлы:** `src/stt/base.py`
**Заметки:** `Transcript` и `STTConfig` — frozen dataclasses с slots. `STTEngine` — `@runtime_checkable Protocol`.

---

### 3.2 Google Cloud STT реализация

- [x] Создать `src/stt/google_stt.py` — реализация `STTEngine`
- [x] Streaming gRPC: `StreamingRecognize` с `google.cloud.speech_v2`
- [x] Конфигурация: `language_code="uk-UA"`, `alternative_language_codes=["ru-RU"]`
- [x] `model="latest_long"`, `enable_automatic_punctuation=True`
- [x] Обработка `interim_results` и `is_final` ответов
- [x] Логирование `detected_language` для каждого final transcript

**Файлы:** `src/stt/google_stt.py`
**Заметки:** Использует `SpeechAsyncClient` и `AutoDetectDecodingConfig`. Результаты ставятся в `asyncio.Queue` для асинхронного чтения.

---

### 3.3 Управление streaming-сессией

- [x] Автоматический restart сессии каждые ~5 мин (лимит Google STT streaming)
- [x] Обработка ошибок gRPC (сетевые, лимиты, квоты)
- [x] Буферизация аудио при restart (не потерять данные)
- [x] Корректное закрытие потока при завершении звонка

**Файлы:** `src/stt/google_stt.py`
**Заметки:** `_restart_session()` дожидается завершения текущего потока, создаёт новую очередь и новый task. Restart по таймеру 290 сек.

---

### 3.4 VAD и обработка пауз

- [x] Определение конца фразы клиента (через `is_final` от Google STT)
- [x] Таймаут тишины: 10 сек → генерация события "silence_timeout"
- [x] Обработка фонового шума (не интерпретировать как речь)

**Файлы:** `src/stt/google_stt.py`
**Заметки:** VAD реализован через `is_final` от Google STT. Таймаут тишины обрабатывается в pipeline (phase-06) через `asyncio.wait_for` на `get_transcripts()`.

---

### 3.5 Mock-реализация для тестов

- [x] Создать mock-реализацию `STTEngine` для unit-тестов
- [x] Возможность задать предопределённые транскрипции
- [x] Имитация задержек и ошибок

**Файлы:** `tests/unit/mocks/mock_stt.py`
**Заметки:** `MockSTTEngine` принимает список `Transcript`, `delay` для имитации задержки, `error_on_feed` для ошибок. Свойство `feed_count` для проверки.

---

## При завершении фазы

1. Убедись, что все задачи отмечены [x]
2. Измени статус фазы: [x] Завершена
3. Заполни дату "Завершена: YYYY-MM-DD"
4. Выполни коммит:
   ```bash
   git add .
   git commit -m "checklist(phase-1-mvp): phase-3 STT module completed"
   ```
5. Обнови PROGRESS.md
6. Открой следующую фазу: `phase-04-tts-module.md`
