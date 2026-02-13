# Фаза 3: STT модуль (Speech-to-Text)

## Статус
- [ ] Не начата
- [ ] В процессе
- [ ] Завершена

**Начата:** -
**Завершена:** -

## Цель фазы

Реализовать модуль потокового распознавания речи с мультиязычной поддержкой (украинский + русский). Клиент говорит — система распознаёт в реальном времени.

## Задачи

### 3.0 ОБЯЗАТЕЛЬНО: Анализ и планирование

#### A. Анализ существующего кода
- [ ] Проверить наличие `src/stt/base.py`, `src/stt/google_stt.py`
- [ ] Изучить Google Cloud Speech-to-Text v2 API (streaming gRPC)
- [ ] Определить формат Transcript dataclass

**Команды для поиска:**
```bash
ls src/stt/
grep -rn "class.*Protocol\|class.*STT" src/
grep -rn "StreamingRecognize\|RecognitionConfig" src/
```

#### B. Анализ зависимостей
- [ ] Нужна абстракция Protocol для STT? — Да, `STTEngine(Protocol)`
- [ ] Нужны ли новые env variables? — `GOOGLE_APPLICATION_CREDENTIALS`, `GOOGLE_STT_LANGUAGE_CODE`, `GOOGLE_STT_ALTERNATIVE_LANGUAGES`
- [ ] Метрики: `stt_latency_ms`, `stt_confidence`, `detected_language`

**Новые абстракции:** `STTEngine(Protocol)` в `src/stt/base.py`
**Новые env variables:** GCP credentials (уже в phase-01)
**Новые tools:** Нет
**Миграции БД:** Нет

#### C. Проверка архитектуры
- [ ] Streaming API — отправка аудио-чанков по мере поступления
- [ ] `interim_results=True` — промежуточные результаты
- [ ] Restart сессии каждые ~5 мин (лимит Google STT)
- [ ] VAD (Voice Activity Detection) для определения конца фразы

**Референс-модуль:** `doc/technical/architecture.md` — секция 3.4

**Цель:** Понять streaming gRPC API Google STT и мультиязычную конфигурацию.

**Заметки для переиспользования:** -

---

### 3.1 Абстрактный интерфейс STT (Protocol)

- [ ] Создать `src/stt/base.py` с Protocol-классом `STTEngine`
- [ ] Методы: `start_stream()`, `feed_audio()`, `get_transcripts()`, `stop_stream()`
- [ ] Dataclass `Transcript`: `text`, `is_final`, `confidence`, `language`
- [ ] Dataclass `STTConfig`: `language_code`, `alternative_languages`, `sample_rate_hertz`, `interim_results`

**Файлы:** `src/stt/base.py`

**Интерфейс:**
```python
class STTEngine(Protocol):
    async def start_stream(self, config: STTConfig) -> None: ...
    async def feed_audio(self, chunk: bytes) -> None: ...
    async def get_transcripts(self) -> AsyncIterator[Transcript]: ...
    async def stop_stream(self) -> None: ...

@dataclass
class Transcript:
    text: str
    is_final: bool
    confidence: float
    language: str  # "uk-UA" или "ru-RU"
```

**Заметки:** -

---

### 3.2 Google Cloud STT реализация

- [ ] Создать `src/stt/google_stt.py` — реализация `STTEngine`
- [ ] Streaming gRPC: `StreamingRecognize` с `google.cloud.speech_v2`
- [ ] Конфигурация: `language_code="uk-UA"`, `alternative_language_codes=["ru-RU"]`
- [ ] `model="latest_long"`, `enable_automatic_punctuation=True`
- [ ] Обработка `interim_results` и `is_final` ответов
- [ ] Логирование `detected_language` для каждого final transcript

**Файлы:** `src/stt/google_stt.py`

**Конфигурация:**
```python
config = cloud_speech.StreamingRecognitionConfig(
    config=cloud_speech.RecognitionConfig(
        encoding=cloud_speech.RecognitionConfig.AudioEncoding.LINEAR16,
        sample_rate_hertz=16000,
        language_code="uk-UA",
        alternative_language_codes=["ru-RU"],
        model="latest_long",
        enable_automatic_punctuation=True,
    ),
    interim_results=True,
)
```

**Заметки:** `alternative_language_codes` добавляет ~50-100ms задержки

---

### 3.3 Управление streaming-сессией

- [ ] Автоматический restart сессии каждые ~5 мин (лимит Google STT streaming)
- [ ] Обработка ошибок gRPC (сетевые, лимиты, квоты)
- [ ] Буферизация аудио при restart (не потерять данные)
- [ ] Корректное закрытие потока при завершении звонка

**Файлы:** `src/stt/google_stt.py`
**Заметки:** Google STT streaming имеет лимит ~5 минут на сессию

---

### 3.4 VAD и обработка пауз

- [ ] Определение конца фразы клиента (через `is_final` от Google STT)
- [ ] Таймаут тишины: 10 сек → генерация события "silence_timeout"
- [ ] Обработка фонового шума (не интерпретировать как речь)

**Файлы:** `src/stt/google_stt.py`
**Заметки:** `is_final=True` означает конец фразы → отправка в LLM

---

### 3.5 Mock-реализация для тестов

- [ ] Создать mock-реализацию `STTEngine` для unit-тестов
- [ ] Возможность задать предопределённые транскрипции
- [ ] Имитация задержек и ошибок

**Файлы:** `tests/unit/mocks/mock_stt.py`
**Заметки:** -

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
