# Фаза 4: TTS модуль (Text-to-Speech)

## Статус
- [ ] Не начата
- [ ] В процессе
- [ ] Завершена

**Начата:** -
**Завершена:** -

## Цель фазы

Реализовать модуль синтеза украинской речи. Текст ответа агента → аудио в формате AudioSocket (16kHz, 16-bit PCM). Кэширование частых фраз, streaming по предложениям.

## Задачи

### 4.0 ОБЯЗАТЕЛЬНО: Анализ и планирование

#### A. Анализ существующего кода
- [ ] Проверить наличие `src/tts/base.py`, `src/tts/google_tts.py`
- [ ] Изучить Google Cloud TTS API
- [ ] Изучить паттерн из `src/stt/base.py` (аналогичная абстракция)

**Команды для поиска:**
```bash
ls src/tts/
grep -rn "class.*Protocol\|class.*TTS" src/
grep -rn "TextToSpeech\|synthesize" src/
```

#### B. Анализ зависимостей
- [ ] Нужна абстракция Protocol для TTS? — Да, `TTSEngine(Protocol)`
- [ ] Нужны ли новые env variables? — `GOOGLE_TTS_VOICE`, `GOOGLE_TTS_SPEAKING_RATE`
- [ ] Метрики: `tts_latency_ms`, `tts_cache_hit_rate`

**Новые абстракции:** `TTSEngine(Protocol)` в `src/tts/base.py`
**Новые env variables:** TTS voice config (уже в phase-01)
**Новые tools:** Нет
**Миграции БД:** Нет

#### C. Проверка архитектуры
- [ ] Формат аудио: LINEAR16, 16kHz — совпадает с AudioSocket
- [ ] Кэширование частых фраз (приветствие, прощание, ожидание)
- [ ] Streaming по предложениям для длинных ответов
- [ ] Бюджет задержки TTS: ≤ 400ms (из NFR)

**Референс-модуль:** `src/stt/base.py` (аналогичная структура)

**Цель:** Понять API Google TTS и определить стратегию кэширования.

**Заметки для переиспользования:** Паттерн Protocol из STT модуля

---

### 4.1 Абстрактный интерфейс TTS (Protocol)

- [ ] Создать `src/tts/base.py` с Protocol-классом `TTSEngine`
- [ ] Методы: `synthesize(text) -> bytes`, `synthesize_stream(text) -> AsyncIterator[bytes]`
- [ ] Dataclass `TTSConfig`: `language_code`, `voice_name`, `speaking_rate`, `sample_rate_hertz`

**Файлы:** `src/tts/base.py`

**Интерфейс:**
```python
class TTSEngine(Protocol):
    async def synthesize(self, text: str) -> bytes: ...
    async def synthesize_stream(self, text: str) -> AsyncIterator[bytes]: ...
```

**Заметки:** -

---

### 4.2 Google Cloud TTS реализация

- [ ] Создать `src/tts/google_tts.py` — реализация `TTSEngine`
- [ ] Конфигурация голоса: `uk-UA-Standard-A` (или Neural2 для лучшего качества)
- [ ] Формат аудио: `LINEAR16`, `sample_rate_hertz=16000`
- [ ] `speaking_rate=1.0` (нормальная скорость)
- [ ] Обработка ошибок API с логированием

**Файлы:** `src/tts/google_tts.py`

**Конфигурация:**
```python
voice = texttospeech.VoiceSelectionParams(
    language_code="uk-UA",
    name="uk-UA-Standard-A",
    ssml_gender=texttospeech.SsmlVoiceGender.FEMALE,
)
audio_config = texttospeech.AudioConfig(
    audio_encoding=texttospeech.AudioEncoding.LINEAR16,
    sample_rate_hertz=16000,
    speaking_rate=1.0,
)
```

**Заметки:** Neural2 голоса качественнее, но дороже

---

### 4.3 Кэширование частых фраз

- [ ] Реализовать `CachedTTSEngine` — обёртка с кэшированием
- [ ] Кэш в памяти (dict) для часто используемых фраз
- [ ] Предзагрузка фраз при старте: "Добрий день!", "Зачекайте, будь ласка", "Дякую за дзвінок!"
- [ ] Метрика: cache hit rate

**Файлы:** `src/tts/google_tts.py`

**Фразы для кэширования:**
```python
CACHED_PHRASES = [
    "Добрий день! Інтернет-магазин шин. Чим можу допомогти?",
    "Зачекайте, будь ласка, я шукаю інформацію.",
    "Зараз з'єдную вас з оператором. Залишайтесь на лінії.",
    "Дякую за дзвінок! До побачення!",
    "Ви ще на лінії?",
]
```

**Заметки:** -

---

### 4.4 Streaming по предложениям

- [ ] Разбиение текста ответа на предложения
- [ ] Синтез и отправка каждого предложения отдельно
- [ ] Параллельная обработка: синтез следующего предложения во время воспроизведения текущего

**Файлы:** `src/tts/google_tts.py`
**Заметки:** Позволяет начать воспроизведение быстрее (не ждать синтеза всего текста)

---

### 4.5 Mock-реализация для тестов

- [ ] Создать mock-реализацию `TTSEngine` для unit-тестов
- [ ] Возврат пустого аудио (тишина) нужной длительности
- [ ] Имитация задержек и ошибок

**Файлы:** `tests/unit/mocks/mock_tts.py`
**Заметки:** -

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
