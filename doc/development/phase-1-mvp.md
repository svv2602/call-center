# Фаза 1 — MVP: Подбор шин и проверка наличия

## Цель фазы

Запустить минимально работающую систему: клиент звонит, ИИ-агент принимает звонок, помогает подобрать шины, проверяет наличие и при необходимости переключает на оператора.

## Сценарии MVP

### 1.1 Подбор шин

**Триггер:** клиент говорит что-то вроде "Мені потрібні шини на..." / "Підберіть шини..."

**Поток:**
```
Клиент: "Мені потрібні зимові шини на Тойоту Камрі 2020 року"
Агент:  → tool call: search_tires(vehicle="Toyota Camry", year=2020, season="winter")
        → API возвращает список
Агент: "Для Тойоти Камрі 2020 року є такі варіанти зимових шин:
        1. Michelin X-Ice North 4 215/55 R17 — 3200 грн/шт, є в наявності
        2. Continental IceContact 3 215/55 R17 — 2800 грн/шт, є в наявності
        3. Nokian Hakkapeliitta 10 215/55 R17 — 3500 грн/шт, під замовлення
        Що вас цікавить?"
```

**Альтернативные входы:**
- По размеру: "Мені потрібні шини 215/55 R17"
- По бренду: "Є шини Michelin на 17 радіус?"
- Неполная информация → агент задаёт уточняющие вопросы

### 1.2 Проверка наличия

**Триггер:** "Чи є в наявності..." / "Перевірте наявність..."

**Поток:**
```
Клиент: "Чи є в наявності Michelin Pilot Sport 5 225/45 R18?"
Агент:  → tool call: check_availability(query="Michelin Pilot Sport 5 225/45 R18")
        → API: в наличии 8 шт, цена 4200 грн
Агент: "Так, Michelin Pilot Sport 5 225/45 R18 є в наявності — 8 штук,
        ціна 4200 гривень за штуку. Бажаєте оформити замовлення?"
```

### 1.3 Переключение на оператора

**Триггеры:**
- Прямая просьба: "З'єднайте з оператором"
- Агент не может помочь (3+ неудачных попытки)
- Клиент раздражён / негативные эмоции
- Вопрос вне компетенции агента

**Поток:**
```
Агент: "Зараз з'єдную вас з оператором. Залишайтесь на лінії."
       → tool call: transfer_to_operator(reason="customer_request")
       → Asterisk ARI: transfer to operator queue
```

## Технические задачи

### 1.1 Настройка Asterisk AudioSocket

**Задача:** настроить Asterisk dialplan для перенаправления входящих звонков на AudioSocket-сервер.

**Asterisk dialplan (extensions.conf):**
```ini
[incoming]
exten => _X.,1,NoOp(Incoming call from ${CALLERID(num)})
 same => n,Answer()
 same => n,Set(CHANNEL(audioreadformat)=slin16)
 same => n,Set(CHANNEL(audiowriteformat)=slin16)
 same => n,AudioSocket(${UNIQUE_ID},127.0.0.1:9092)
 same => n,Hangup()

[operators]
exten => _X.,1,NoOp(Transfer to operator queue)
 same => n,Queue(operator-queue,t,,,60)
 same => n,Hangup()
```

**Результат:** Asterisk передаёт аудио-поток (16kHz, 16-bit, signed linear PCM) через TCP на порт 9092.

### 1.2 AudioSocket сервер (Python)

**Задача:** TCP-сервер, принимающий соединения от Asterisk AudioSocket.

**Протокол AudioSocket:**
- Каждый пакет: `[type:1byte][length:2bytes(BE)][payload:N bytes]`
- Типы: `0x01` = UUID, `0x10` = audio, `0x00` = hangup, `0xFF` = error
- Аудио: 16kHz, 16-bit signed linear PCM, little-endian

**Ключевые моменты:**
- Каждый звонок — отдельная asyncio Task
- При подключении получаем UUID канала Asterisk
- Двунаправленный поток: читаем аудио клиента, пишем аудио ответа
- Graceful shutdown при hangup

### 1.3 STT интеграция (Google Cloud Speech-to-Text)

**Задача:** streaming-распознавание украинской речи в реальном времени.

**Конфигурация:**
```python
config = cloud_speech.StreamingRecognitionConfig(
    config=cloud_speech.RecognitionConfig(
        encoding=cloud_speech.RecognitionConfig.AudioEncoding.LINEAR16,
        sample_rate_hertz=16000,
        language_code="uk-UA",
        model="latest_long",
        enable_automatic_punctuation=True,
    ),
    interim_results=True,  # промежуточные результаты для быстрого отклика
)
```

**Ключевые моменты:**
- Streaming API — отправляем аудио-чанки по мере поступления
- `interim_results=True` — получаем промежуточные результаты (для индикации что бот "слушает")
- Обработка `is_final=True` — отправляем финальный текст в LLM
- Обработка пауз (VAD) — определение конца фразы клиента
- Таймаут тишины: 10 секунд → "Ви ще на лінії?"

### 1.4 LLM агент

**Задача:** агент на базе Claude с tool calling для ведения диалога.

**Системный промпт (ключевые элементы):**
```
Ти — голосовий асистент інтернет-магазину шин [НАЗВАНИЕ].
Ти спілкуєшся українською мовою, ввічливо та професійно.

Твої можливості:
- Підбір шин за автомобілем, розміром або брендом
- Перевірка наявності товару
- Переключення на оператора

Правила:
- Відповідай коротко і чітко (це телефонна розмова, не чат)
- Не вигадуй інформацію — використовуй тільки дані з інструментів
- Називай ціни у гривнях
- Якщо не можеш допомогти — запропонуй з'єднати з оператором
- Максимум 2-3 речення у відповіді
```

**Tools для MVP:**

```python
tools = [
    {
        "name": "search_tires",
        "description": "Поиск шин в каталоге магазина",
        "input_schema": {
            "type": "object",
            "properties": {
                "vehicle_make": {"type": "string", "description": "Марка авто"},
                "vehicle_model": {"type": "string", "description": "Модель авто"},
                "vehicle_year": {"type": "integer", "description": "Год выпуска"},
                "width": {"type": "integer", "description": "Ширина шины (мм)"},
                "profile": {"type": "integer", "description": "Профиль шины (%)"},
                "diameter": {"type": "integer", "description": "Диаметр (дюймы)"},
                "season": {
                    "type": "string",
                    "enum": ["summer", "winter", "all_season"],
                },
                "brand": {"type": "string"},
            },
        },
    },
    {
        "name": "check_availability",
        "description": "Проверка наличия конкретного товара",
        "input_schema": {
            "type": "object",
            "properties": {
                "product_id": {"type": "string"},
                "query": {"type": "string", "description": "Текстовый запрос"},
            },
        },
    },
    {
        "name": "transfer_to_operator",
        "description": "Переключить клиента на живого оператора",
        "input_schema": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "enum": [
                        "customer_request",
                        "cannot_help",
                        "complex_question",
                        "negative_emotion",
                    ],
                },
                "summary": {
                    "type": "string",
                    "description": "Краткое описание запроса клиента для оператора",
                },
            },
            "required": ["reason", "summary"],
        },
    },
]
```

### 1.5 TTS интеграция (Google Cloud TTS)

**Задача:** синтез украинской речи для ответов агента.

**Конфигурация:**
```python
voice = texttospeech.VoiceSelectionParams(
    language_code="uk-UA",
    name="uk-UA-Standard-A",  # или Neural2 для лучшего качества
    ssml_gender=texttospeech.SsmlVoiceGender.FEMALE,
)

audio_config = texttospeech.AudioConfig(
    audio_encoding=texttospeech.AudioEncoding.LINEAR16,
    sample_rate_hertz=16000,
    speaking_rate=1.0,  # нормальная скорость
)
```

**Ключевые моменты:**
- Формат аудио: LINEAR16, 16kHz — совпадает с AudioSocket
- Кэширование частых фраз ("Добрий день!", "Зачекайте, будь ласка")
- Streaming TTS для длинных ответов (отправка по предложениям)

### 1.6 Pipeline: связка STT → LLM → TTS

**Задача:** оркестрация потока данных в реальном времени.

```
AudioSocket (аудио вход)
    │
    ▼
STT Streaming (Google) ──interim──► [можно: "Так, шукаю..."]
    │
    │ is_final=True
    ▼
LLM Agent (Claude)
    │
    ├── text response ──► TTS ──► AudioSocket (аудио выход)
    │
    └── tool_call ──► Store API ──► LLM (продолжение) ──► TTS
```

**Управление очередностью:**
- Пока агент "говорит" (TTS играет) — STT продолжает слушать, но в буфер
- Если клиент перебивает (barge-in) — прервать TTS, обработать новый ввод
- Между концом речи клиента и началом ответа — цель < 1.5 секунды

### 1.7 Store API (MVP endpoints)

Подробное описание в [api-specification.md](./api-specification.md). Для MVP нужны:

| Метод | Endpoint | Описание |
|-------|----------|----------|
| GET | `/api/v1/tires/search` | Поиск шин по параметрам |
| GET | `/api/v1/tires/{id}/availability` | Наличие конкретного товара |
| GET | `/api/v1/vehicles/tires` | Подбор шин по автомобилю |

## Инфраструктура MVP

### Docker Compose

```yaml
services:
  call-processor:
    build: .
    ports:
      - "9092:9092"   # AudioSocket
      - "8080:8080"   # API мониторинга
    environment:
      - GOOGLE_APPLICATION_CREDENTIALS=/secrets/gcp-key.json
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
      - STORE_API_URL=http://store-api:3000
    depends_on:
      - postgres
      - redis

  postgres:
    image: postgres:16
    volumes:
      - pgdata:/var/lib/postgresql/data

  redis:
    image: redis:7-alpine
```

## Критерии приёмки MVP

- [ ] Бот принимает звонок и здоровается на украинском
- [ ] Бот распознаёт речь на украинском языке
- [ ] Бот подбирает шины по запросу (авто или размер)
- [ ] Бот проверяет наличие товара
- [ ] Бот отвечает голосом на украинском
- [ ] Бот переключает на оператора по запросу
- [ ] Бот переключает на оператора при невозможности помочь
- [ ] Задержка ответа < 2 секунд (от конца речи клиента до начала ответа)
- [ ] Логирование всех звонков (транскрипция + метаданные)
- [ ] Работа при 10 одновременных звонках

## Оценка затрат (на 500 звонков/день)

| Сервис | Расчёт | Стоимость/мес |
|--------|--------|---------------|
| Google STT | ~2.5 мин/звонок × 500 × 30 = 37500 мин | ~$900 |
| Google TTS | ~1 мин/звонок × 500 × 30 = 15000 мин | ~$240 |
| Claude API | ~4 запроса/звонок × 500 × 30 = 60000 запросов | ~$300–600 |
| Сервер | 4 vCPU, 8GB RAM | ~$40–80 |
| **Итого** | | **~$1500–1800** |

> Оптимизация в фазе 4: self-hosted Whisper может снизить STT-расходы на 80%.
