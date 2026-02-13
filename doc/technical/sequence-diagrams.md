# Диаграммы последовательности

## 1. Основной поток: входящий звонок

```mermaid
sequenceDiagram
    participant C as Клиент
    participant AST as Asterisk PBX
    participant AS as AudioSocket Server
    participant SM as Session Manager
    participant STT as Google STT
    participant LLM as Claude Agent
    participant TTS as Google TTS
    participant API as Store API

    C->>AST: Входящий звонок (SIP)
    AST->>AST: Answer() + slin16
    AST->>AS: TCP connect (AudioSocket)
    AS->>AS: Parse UUID packet (0x01)
    AS->>SM: create_session(uuid)
    SM->>AST: ARI: get CallerID
    AST-->>SM: +380XXXXXXXXX

    Note over AS,TTS: Приветствие
    SM->>TTS: synthesize("Добрий день!...")
    TTS-->>SM: audio bytes
    SM->>AS: write audio (0x10)
    AS->>AST: audio → клиенту
    AST->>C: Голос бота

    Note over C,API: Диалог
    C->>AST: Речь клиента
    AST->>AS: audio packets (0x10)
    AS->>STT: streaming audio chunks

    loop Streaming Recognition
        STT-->>SM: interim transcript
    end

    STT-->>SM: final transcript: "Мені потрібні зимові шини на Камрі"
    SM->>LLM: messages + tools
    LLM-->>SM: tool_use: search_tires(...)
    SM->>API: GET /tires/search?...
    API-->>SM: [{tire1}, {tire2}, ...]
    SM->>LLM: tool_result
    LLM-->>SM: text: "Для Камрі є такі варіанти..."
    SM->>TTS: synthesize(text)
    TTS-->>SM: audio bytes
    SM->>AS: write audio
    AS->>AST: audio → клиенту
    AST->>C: Голос бота с ответом
```

## 2. Оформление заказа

```mermaid
sequenceDiagram
    participant C as Клиент
    participant SM as Session Manager
    participant LLM as Claude Agent
    participant API as Store API
    participant SMS as SMS Gateway

    Note over C,SMS: После подбора шин

    C->>SM: "Так, хочу замовити ці, 4 штуки"
    SM->>LLM: user message
    LLM-->>SM: tool_use: create_order_draft(items, phone)
    SM->>API: POST /orders
    API-->>SM: {order_id, subtotal: 12800}
    SM->>LLM: tool_result
    LLM-->>SM: "Замовлення: 4 шини, 12800 грн. Доставка чи самовивіз?"

    SM->>C: [TTS] "Доставка чи самовивіз?"
    C->>SM: "Доставка, Київ, Хрещатик 1"
    SM->>LLM: user message
    LLM-->>SM: tool_use: update_order_delivery(...)
    SM->>API: PATCH /orders/{id}/delivery
    API-->>SM: {delivery_cost: 200, total: 13000}
    SM->>LLM: tool_result
    LLM-->>SM: "Доставка 200 грн, разом 13000. Оплата?"

    SM->>C: [TTS] "Оплата при отриманні чи онлайн?"
    C->>SM: "При отриманні"
    SM->>LLM: user message
    LLM-->>SM: "Підтверджую: 4 шини Michelin, 13000 грн, доставка Київ. Все вірно?"

    SM->>C: [TTS] Подтверждение
    C->>SM: "Так, підтверджую"
    SM->>LLM: user message
    LLM-->>SM: tool_use: confirm_order(order_id, payment="cod")
    SM->>API: POST /orders/{id}/confirm
    API-->>SM: {order_number: "12346", confirmed: true}
    API->>SMS: Отправить SMS подтверждение
    SM->>LLM: tool_result
    LLM-->>SM: "Замовлення 12346 оформлено! SMS надіслано."
    SM->>C: [TTS] Финальное сообщение
```

## 3. Переключение на оператора

```mermaid
sequenceDiagram
    participant C as Клиент
    participant AST as Asterisk PBX
    participant SM as Session Manager
    participant LLM as Claude Agent

    C->>SM: "З'єднайте мене з оператором"
    SM->>LLM: user message
    LLM-->>SM: tool_use: transfer_to_operator(reason="customer_request", summary="...")

    SM->>C: [TTS] "Зараз з'єдную з оператором. Зачекайте."

    SM->>AST: ARI: POST /channels/{id}/redirect<br/>extension=transfer-to-operator
    AST->>AST: Queue(operators)

    Note over AST: Клиент слышит музыку ожидания

    AST->>C: Соединение с оператором
    SM->>SM: Логирование: transferred, reason, summary
```

## 4. Barge-in (клиент перебивает бота)

```mermaid
sequenceDiagram
    participant C as Клиент
    participant AS as AudioSocket
    participant SM as Session Manager
    participant STT as Google STT
    participant TTS as TTS Engine

    Note over SM,TTS: Бот говорит длинный ответ
    SM->>TTS: synthesize("Для вашого авто є такі варіанти...")
    TTS-->>SM: audio chunk 1
    SM->>AS: play audio chunk 1
    TTS-->>SM: audio chunk 2

    Note over C,AS: Клиент начинает говорить
    C->>AS: speech audio detected
    AS->>STT: audio
    STT-->>SM: interim: "а скільки..."

    SM->>SM: BARGE-IN detected!
    SM->>AS: stop playback (flush output buffer)
    SM->>TTS: cancel remaining chunks

    STT-->>SM: final: "А скільки коштує доставка?"
    SM->>SM: Продолжить обработку нового вопроса
```

## 5. Обработка ошибок

```mermaid
sequenceDiagram
    participant C as Клиент
    participant SM as Session Manager
    participant STT as Google STT
    participant LLM as Claude Agent
    participant API as Store API
    participant AST as Asterisk

    C->>SM: Речь клиента
    SM->>STT: audio stream

    alt STT Error
        STT-->>SM: Error (service unavailable)
        SM->>C: [TTS] "Вибачте, у мене технічні труднощі. З'єдную з оператором."
        SM->>AST: ARI transfer to operator
    end

    alt Store API Error
        SM->>API: GET /tires/search
        API-->>SM: 500 Internal Server Error
        SM->>LLM: tool_result: {error: "service unavailable"}
        LLM-->>SM: "На жаль, зараз не можу перевірити наявність. Спробуйте за кілька хвилин або з'єдную з оператором."
    end

    alt LLM Error
        SM->>LLM: messages
        LLM-->>SM: Error (timeout/rate limit)
        SM->>SM: retry (1 attempt)
        alt Retry failed
            SM->>C: [TTS] "Вибачте за затримку. З'єдную з оператором."
            SM->>AST: ARI transfer
        end
    end

    alt Client Silence (10s)
        SM->>SM: silence_timeout
        SM->>C: [TTS] "Ви ще на лінії?"
        alt Silence again (10s)
            SM->>C: [TTS] "Дякую за дзвінок. До побачення!"
            SM->>AST: Hangup
        end
    end
```

## 6. Запись на шиномонтаж

```mermaid
sequenceDiagram
    participant C as Клиент
    participant SM as Session Manager
    participant LLM as Claude Agent
    participant API as Store API

    C->>SM: "Хочу записатися на шиномонтаж"
    SM->>LLM: user message
    LLM-->>SM: "В якому місті вам зручно?"

    SM->>C: [TTS] Вопрос о городе
    C->>SM: "Київ"
    SM->>LLM: user message
    LLM-->>SM: tool_use: get_fitting_stations(city="Київ")
    SM->>API: GET /fitting/stations?city=Київ
    API-->>SM: [station1, station2, station3]
    SM->>LLM: tool_result
    LLM-->>SM: "Є 3 точки: 1)... 2)... 3)..."

    SM->>C: [TTS] Список точек
    C->>SM: "Позняки"
    SM->>LLM: user message
    LLM-->>SM: tool_use: get_fitting_slots(station_id="3")
    SM->>API: GET /fitting/stations/3/slots
    API-->>SM: available slots
    SM->>LLM: tool_result
    LLM-->>SM: "Найближчі слоти: завтра 10:00, 14:00..."

    SM->>C: [TTS] Доступные слоты
    C->>SM: "Завтра о 14"
    SM->>LLM: user message
    LLM-->>SM: tool_use: book_fitting(station_id="3", date, time, phone)
    SM->>API: POST /fitting/bookings
    API-->>SM: {booking_id, confirmed}
    SM->>LLM: tool_result
    LLM-->>SM: "Записав вас на завтра, 14:00, Здолбунівська 7а"
    SM->>C: [TTS] Подтверждение записи
```
