# Исследование моделей Google STT v2 для улучшения распознавания речи

**Период:** 2026-08-04 — 2026-08-05  
**Статус:** Завершено. Оптимального решения не найдено — возвращены базовые настройки.

---

## Цель

Включить STT phrase hints (1200 фраз общего словаря + 57 boost-фраз для номерных знаков с boost=15), чтобы улучшить распознавание:
- названий городов (Дніпро, Запоріжжя, Харків...)
- марок шин (Michelin, Continental, Bridgestone...)
- размеров шин (205/55 R16, 245/45 R18...)
- букв и префиксов номерных знаков (АА, КА, ВН...)

**Проблема-триггер:** Баннер "Підсказки STT тимчасово не застосовуються" в Admin UI — модель `latest_long` в локации `global` не поддерживает speech adaptation. При каждом звонке в логах: `STT attempt 1 (с хинтами) → FAIL → attempt 2 (без хинтов) → OK`.

---

## Архитектура STT в системе

```
GoogleSTTSettings (config.py)
  GOOGLE_STT_MODEL         → STTConfig.model
  GOOGLE_STT_LOCATION      → STTConfig.location → recognizer path + ClientOptions
  GOOGLE_STT_ALTERNATIVE_LANGUAGES → STTConfig.alternative_languages
  GOOGLE_STT_ENDPOINTING_SENSITIVITY → StreamingRecognitionFeatures
  GOOGLE_STT_SPEECH_END_TIMEOUT_MS  → VoiceActivityTimeout.speech_end_timeout
  GOOGLE_STT_TRANSCRIPT_BUFFER_SEC  → Pipeline: окно слияния финалов (сек)
```

**Логика retry в `google_stt.py`:**
1. Attempt 1: `chirp_2/latest_long` + phrase hints (adaptation)
2. Если attempt 1 упал → Attempt 2: та же модель без хинтов
3. Если attempt 2 упал → STT мертв, бот молчит после приветствия

**Региональные эндпоинты:** при `location != "global"` используется `ClientOptions(api_endpoint=f"{location}-speech.googleapis.com")`. Без этого глобальный клиент отклоняет региональные recognizer-пути (ошибка "Expected resource location to be global").

---

## Исходное состояние (baseline)

```
GOOGLE_STT_MODEL=latest_long
GOOGLE_STT_LOCATION=global
GOOGLE_STT_ALTERNATIVE_LANGUAGES=ru-RU
GOOGLE_STT_ENDPOINTING_SENSITIVITY=SHORT
```

**Поведение:** Бот работает стабильно. Attempt 1 (с хинтами) всегда падает — `latest_long` в `global` не поддерживает speech adaptation. Attempt 2 (без хинтов) всегда успешен. Хинты не применяются никогда.

---

## Матрица тестирования

### Попытка 1 — `telephony` + `global` (2026-08-04)

**Гипотеза:** модель `telephony` поддерживает adaptation, в отличие от `latest_long`.

**Настройки:**
```
GOOGLE_STT_MODEL=telephony
GOOGLE_STT_LOCATION=global
```

**Результат:** Бот молчит после приветствия.

**Ошибка:**
```
400 The language "uk-UA" is not supported by the model "telephony" in the location named "global"
```

**Вывод:** `telephony` в `global` не поддерживает украинский язык.

---

### Попытка 2 — `chirp_2` + `europe-west4` (без регионального эндпоинта, 2026-08-04)

**Гипотеза:** `chirp_2` — универсальная модель нового поколения, поддерживает uk-UA + adaptation. Доступна в `europe-west4`.

**Настройки:**
```
GOOGLE_STT_MODEL=chirp_2
GOOGLE_STT_LOCATION=europe-west4
```
*(Региональный эндпоинт ещё не был реализован в коде)*

**Результат:** Бот молчит после приветствия.

**Ошибка:**
```
400 Expected resource location to be global, but found europe-west4 in resource name
```

**Причина:** `SpeechAsyncClient()` без `ClientOptions` подключается к глобальному эндпоинту `speech.googleapis.com`, который отклоняет recognizer-пути с региональными локациями.

**Фикс:** Добавлен в `google_stt.py:start_stream()`:
```python
if location and location != "global":
    client_options = ClientOptions(api_endpoint=f"{location}-speech.googleapis.com")
    self._client = SpeechAsyncClient(client_options=client_options)
```

---

### Попытка 3 — `chirp_2` + `europe-west4` (с региональным эндпоинтом, 2026-08-04)

**Гипотеза:** После фикса эндпоинта `chirp_2` + `europe-west4` должен заработать.

**Настройки:**
```
GOOGLE_STT_MODEL=chirp_2
GOOGLE_STT_LOCATION=europe-west4
GOOGLE_STT_ALTERNATIVE_LANGUAGES=ru-RU
```

**Результат:** Бот молчит после приветствия.

**Ошибка (attempt 2, без хинтов):**
```
400 Multiple language recognition is only available in the following locations: eu, global, us.
```

**Причина:** `europe-west4` — специфический регион, не мультирегион. Multilingual (несколько language_codes) поддерживается только в мультирегионах `eu`, `global`, `us`. Attempt 1 тоже падал (adaptation не поддерживается), но это лишь WARNING в логах.

---

### Попытка 4 — `chirp_2` + `eu` (мультирегион, 2026-08-04)

**Гипотеза:** Мультирегион `eu` поддерживает multilingual — возможно, там есть `chirp_2`.

**Настройки:**
```
GOOGLE_STT_MODEL=chirp_2
GOOGLE_STT_LOCATION=eu
```

**Результат:** Бот молчит после приветствия.

**Ошибка:**
```
400 The model "chirp_2" does not exist in the location named "eu".
```

**Вывод:** `chirp_2` существует только в специфических регионах (`europe-west4`, `asia-southeast1`), но не в мультирегионах `eu`/`global`/`us`. Замкнутый круг: chirp_2 есть только там, где multilingual запрещен.

---

### Попытка 5 — `chirp_3` + `us-central1` (2026-08-05)

**Гипотеза:** `chirp_3` — более новая модель (GA апрель 2026), 85+ языков, доступна в большем числе регионов.

**Настройки:**
```
GOOGLE_STT_MODEL=chirp_3
GOOGLE_STT_LOCATION=us-central1
```

**Результат:** Бот молчит после приветствия.

**Ошибка:**
```
400 The model "chirp_3" does not exist in the location named "us-central1".
```

**Вывод:** Документация о регионах `chirp_3` (europe-west2, europe-west3, us-central1, asia-south1) оказалась неточной. Модель недоступна в проверенном регионе.

---

### Попытка 6 — `chirp_2` + `europe-west4` + uk-UA only (2026-08-05)

**Гипотеза:** Убрать `ru-RU` как альтернативный язык — тогда `europe-west4` не будет блокировать multilingual, и attempt 2 пройдет.

**Обоснование:** 70% клиентов — украиноязычные. `chirp_2` — универсальная модель, вероятно достаточно хорошо понимает русский без явного указания.

**Настройки:**
```
GOOGLE_STT_MODEL=chirp_2
GOOGLE_STT_LOCATION=europe-west4
GOOGLE_STT_ALTERNATIVE_LANGUAGES=   (пусто)
GOOGLE_STT_ENDPOINTING_SENSITIVITY=SHORT
```

**Результат:** Бот отвечает. Attempt 1 (с хинтами) всё ещё падает, attempt 2 (без хинтов) проходит.

**Проблема — фрагментация финалов:**  
`chirp_2` в отличие от `latest_long` выдаёт 2-3 финала на одну фразу с интервалом до 10 секунд (предварительный финал → исправленный финал → финальный). Pipeline обрабатывает каждый как отдельный ход → бот отвечает 2-3 раза на одну фразу пользователя.

**Пример из логов:**
```
05:23:27 STT final: 'Ще не з собою будуть' (conf=0.92) → бот ответил
05:23:39 STT final: 'Шини будуть з собою' (conf=0.99)  → бот ответил снова
05:23:50 STT final: 'Ні шини будуть свої Я' (conf=0.99) → бот ответил ещё раз
```

---

### Попытка 7 — `chirp_2` + `europe-west4` + STANDARD endpointing + buffer 2.5с (2026-08-05)

**Гипотеза:** STANDARD endpointing (менее агрессивный) + увеличенный буфер слияния уберут лишние финалы.

**Настройки:**
```
GOOGLE_STT_ENDPOINTING_SENSITIVITY=STANDARD
GOOGLE_STT_TRANSCRIPT_BUFFER_SEC=2.5
```

**Результат:** Частично помогло. Буфер сливает пары финалов (`span_ms` до 3549 мс). Но тройные фрагменты с интервалом >2.5с всё равно проскакивают как отдельные ходы.

---

### Попытка 8 — `speech_end_timeout_ms=1500` (2026-08-05)

**Гипотеза:** Если STT будет ждать 1.5с тишины перед финализацией, `chirp_2` успеет собрать полную фразу в один финал (вместо 3 фрагментов).

**Настройки:**
```
GOOGLE_STT_SPEECH_END_TIMEOUT_MS=1500
```

**Результат:** Бот молчит после приветствия.

**Ошибка:**
```
499 The operation was cancelled.
```

**Вывод:** `speech_end_timeout_ms` (реализован через `VoiceActivityTimeout.speech_end_timeout` в gRPC) несовместим с `chirp_2` в `europe-west4` — streaming сессия немедленно отменяется. Та же проблема ранее наблюдалась с `latest_long` (зафиксировано в `config.py`).

---

## Итоговая таблица

| Модель | Локация | Языки | Speech Adaptation | Результат |
|--------|---------|-------|-------------------|-----------|
| `latest_long` | global | uk-UA + ru-RU | ❌ | ✅ Бот работает, хинты не применяются |
| `telephony` | global | uk-UA | — | ❌ uk-UA не поддерживается |
| `chirp_2` | europe-west4 | uk-UA + ru-RU | ❌ | ❌ Multilingual запрещен в регионе |
| `chirp_2` | eu | uk-UA + ru-RU | — | ❌ Модель не существует в eu |
| `chirp_3` | us-central1 | uk-UA + ru-RU | — | ❌ Модель не существует в us-central1 |
| `chirp_2` | europe-west4 | uk-UA only | ❌ | ⚠️ Бот работает, но фрагментация финалов |
| `chirp_2` + speech_end_timeout | europe-west4 | uk-UA only | — | ❌ 499 Cancelled |

---

## Фундаментальное противоречие Google STT v2

```
Нужно одновременно:
  ✓ uk-UA + ru-RU (multilingual)   → только eu / global / us
  ✓ Speech adaptation (phrase hints) → только chirp-модели
  ✓ chirp_2 / chirp_3              → только europe-west4, asia-southeast1 (не eu/global/us)

Пересечение этих трёх требований = пустое множество.
```

`latest_long` в `global` удовлетворяет первому и работает стабильно, но adaptation не поддерживает.

---

## Текущее состояние (2026-08-05)

Возврат к baseline:

```env
GOOGLE_STT_MODEL=latest_long
GOOGLE_STT_LOCATION=global
GOOGLE_STT_ALTERNATIVE_LANGUAGES=ru-RU
GOOGLE_STT_ENDPOINTING_SENSITIVITY=SHORT
GOOGLE_STT_SPEECH_END_TIMEOUT_MS=0
```

Баннер "Підсказки STT тимчасово не застосовуються" в Admin UI остаётся актуальным.

---

## Возможные пути решения в будущем

1. **Дождаться Google:** Google может добавить adaptation поддержку для `latest_long` в `global`, или добавить chirp-модели в мультирегионы `eu`/`global`. Следить за [release notes](https://cloud.google.com/speech-to-text/docs/release-notes).

2. **STT Corrections (уже реализовано):** Автоматические regex-замены через таблицу `stt_correction_suggestions` — частично компенсируют отсутствие phrase hints для частых ошибок.

3. **Whisper (в плане):** Self-hosted Whisper large-v3 на GPU поддерживает украинский язык и не имеет ограничений по adaptation. Feature flag `FF_STT_PROVIDER=whisper` уже реализован.

4. **chirp_2 + uk-UA only (компромисс):** Если фрагментация финалов будет решена на уровне pipeline (например, дедупликация по semantic similarity), можно вернуться к `chirp_2 + europe-west4` без ru-RU. Adaptation всё равно не работает, но модель качественнее распознаёт украинский.
