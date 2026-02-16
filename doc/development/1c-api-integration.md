# Интеграция с 1С API — Спецификация реальных эндпоинтов

## Общие сведения

Реальные API-эндпоинты 1С-системы магазина. Используются для синхронизации каталога, проверки остатков, создания заказов и записи на шиномонтаж.

**Сервер:** `http://192.168.11.9`

**Аутентификация:** HTTP Basic Auth
```
Username: web_service
Password: 44332211
```

**Торговые сети:** `ProKoleso`, `Tshina` — используем обе.

---

## REST-эндпоинты

### 1. Номенклатура (каталог товаров)

**Base:** `GET /Trade/hs/site/get_wares`

#### Вариант 1 — Инкрементальная выгрузка (только изменённые товары)

```
GET /Trade/hs/site/get_wares/?TradingNetwork=ProKoleso
GET /Trade/hs/site/get_wares/?TradingNetwork=Tshina
```

После успешной обработки необходимо **подтвердить получение** (иначе те же товары вернутся повторно):

```
GET /Trade/hs/site/get_wares/?TradingNetwork=ProKoleso&ConfirmationOfReceipt
GET /Trade/hs/site/get_wares/?TradingNetwork=Tshina&ConfirmationOfReceipt
```

**Использование:** Sync job (периодический, напр. каждые 5 мин) → обновление PostgreSQL.

#### Вариант 2 — Полная выгрузка

```
GET /Trade/hs/site/get_wares/?UploadingAll
GET /Trade/hs/site/get_wares/?UploadingAll&limit=100
```

**Использование:** Первичная загрузка каталога или полная пересинхронизация.

#### Вариант 3 — По коду товара (SKU)

```
GET /Trade/hs/site/get_wares/?sku=00-00001688
```

**Использование:** Получение актуальных данных конкретного товара в реальном времени.

#### Структура ответа get_wares

Данные полностью структурированы — парсинг названий не нужен.

**Пример ответа:**

```json
{
  "success": true,
  "data": [
    {
      "type": "000000001",
      "model_id": "000167134",
      "model": "Winter1",
      "manufacturer_id": "000022523",
      "manufacturer": "Tigar",
      "seasonality": "Зимняя",
      "tread_pattern_type": "Направленный",
      "product": [
        {
          "sku": "00000019835",
          "art": "",
          "text": "155/70 R13 Tigar Winter1 [75T]",
          "diametr": "13",
          "size_id": "26",
          "size": "155/70R13",
          "profile_height": "70",
          "profile_width": "155",
          "speed_rating": "T",
          "load_rating": "75",
          "reinforcement_type_id": "",
          "reinforcement_type": "",
          "reinforcement_type2_id": "",
          "reinforcement_type2": "",
          "reinforcement_type3_id": "",
          "reinforcement_type3": "",
          "ommology_id": "",
          "ommology": "",
          "studded": "",
          "tire_insulation": "",
          "tire_rim_peeling": "",
          "tire_ply": ""
        }
      ]
    }
  ],
  "errors": []
}
```

**Маппинг полей модели (верхний уровень `data[]`):**

| Поле 1С | Тип | Наше поле | Описание |
|---------|-----|-----------|----------|
| `type` | string | `type_id` | ID категории товара |
| `model_id` | string | `model_id` | ID модели в 1С |
| `model` | string | `model` | Название модели ("Winter1") |
| `manufacturer_id` | string | `manufacturer_id` | ID производителя в 1С |
| `manufacturer` | string | `brand` | Бренд ("Tigar") |
| `seasonality` | string | `season` | Сезонность ("Зимняя", "Летняя", "Всесезонная") |
| `tread_pattern_type` | string | `tread_type` | Тип протектора ("Направленный", "Симметричный", ...) |

**Маппинг полей товара (`data[].product[]`):**

| Поле 1С | Тип | Наше поле | Описание |
|---------|-----|-----------|----------|
| `sku` | string | `id` / `sku` | Уникальный код товара |
| `art` | string | `article` | Артикул (может быть пустым) |
| `text` | string | `display_name` | Полное название для отображения |
| `diametr` | string→int | `diameter` | Диаметр (дюймы) |
| `size` | string | `size` | Размер строкой ("155/70R13") |
| `profile_height` | string→int | `profile` | Профиль (%) |
| `profile_width` | string→int | `width` | Ширина (мм) |
| `speed_rating` | string | `speed_index` | Индекс скорости ("T") |
| `load_rating` | string | `load_index` | Индекс нагрузки ("75") |
| `reinforcement_type` | string | `reinforcement` | Усиление (XL, C, ...) |
| `studded` | string→bool | `studded` | Шипованная |
| `tire_insulation` | string | `run_flat` | Технология RunFlat? |
| `tire_ply` | string | `ply` | Слойность |

> **Примечание:** Числовые поля (`diametr`, `profile_height`, `profile_width`) приходят как строки — нужна конвертация при синхронизации. Пустые строки в boolean-полях (`studded`) трактуются как `false`.

---

### 2. Остатки и цены

**Base:** `GET /Trade/hs/site/get_stock`

```
GET /Trade/hs/site/get_stock/ProKoleso
GET /Trade/hs/site/get_stock/Tshina
```

**Параметры:** `TradingNetwork` (обязательный) — передаётся как часть пути.

**Использование:** `check_availability` tool — проверка наличия и цен. Можно кэшировать в Redis (TTL 1-5 мин).

#### Структура ответа get_stock

```json
{
  "success": true,
  "TradingNetwork": "ProKoleso",
  "data": [
    {
      "sku": "00000000023",
      "price": "2540",
      "stock": "8",
      "foreign_product": "1",
      "price_tshina": "2540",
      "year_issue": "25-24",
      "country": "Сербія"
    },
    {
      "sku": "00000000028",
      "price": "4657",
      "stock": "16",
      "foreign_product": "1",
      "price_tshina": "4657",
      "year_issue": "26р02тиж",
      "country": "Іспанія"
    }
  ]
}
```

**Маппинг полей:**

| Поле 1С | Тип | Наше поле | Описание |
|---------|-----|-----------|----------|
| `sku` | string | `sku` | Код товара (связь с get_wares `product[].sku`) |
| `price` | string→int | `price` | Цена в грн (для сети из URL) |
| `stock` | string→int | `stock_quantity` | Количество в наличии |
| `foreign_product` | string→bool | `is_foreign` | Импортный товар ("1" = да) |
| `price_tshina` | string→int | `price_tshina` | Цена для сети Tshina (присутствует и в ответе ProKoleso) |
| `year_issue` | string | `year_of_manufacture` | Год/неделя выпуска (формат неоднородный, см. ниже) |
| `country` | string | `country_of_origin` | Страна производства |

> **Примечание: `year_issue`** — формат неоднородный: `"25-24"` (вероятно 2025 год, 24 неделя) vs `"26р02тиж"` (2026 рік, 02 тиждень). Хранить как строку, при отображении клиенту упрощать до года.

> **Примечание: `price_tshina`** — цена для Tshina присутствует даже в ответе ProKoleso. Возможно, достаточно одного запроса к любой сети для получения обеих цен. Нужна проверка — есть ли `price_prokoleso` в ответе Tshina.

---

### 3. Остатки по складам ERP

```
GET /Trade/hs/site/stockgoods
```

**Параметры:** нет.

**Использование:** Детальная информация по складам (для ответа клиенту "есть на складе в Киеве").

---

### 4. Создание заказа

```
POST /Trade/hs/site/zakaz
```

**Content-Type:** `application/json`

**Пример тела запроса:**

```json
{
  "number": "C-74914",
  "date": "20260116010737",
  "klient": "Юрий",
  "phone": "0989897789",
  "email": "1973gorbi@gmail.com",
  "fizlico": "ФизЛицо",
  "contact_klient": "Юрий",
  "payment": "2",
  "bank_order_id": null,
  "prepayment_bank_order_id": "",
  "prepayment": "",
  "number_payments": "",
  "delivery": "Точки выдачи",
  "point": "000000053",
  "city": "Київ",
  "city_id": "8d5a980d-391c-11dd-90d9-001a92567626",
  "branch": "",
  "street": "",
  "street_id": "",
  "house": "",
  "flat": "",
  "comment": "",
  "topic": "",
  "promocode": "",
  "store": "tshina",
  "no_callback": "",
  "user_link": "",
  "disc_card": "",
  "fio": "",
  "guid": "",
  "order_channel": "ADS",
  "bought_at_TSC": true,
  "TSC": "нет",
  "tovar": [
    {
      "articul": "",
      "id": "00000035988",
      "count": 4,
      "sum": 60364,
      "sum_delivery": 0,
      "sum_cash_delivery": "",
      "sum_commission": null,
      "sum_discount": ""
    }
  ],
  "prepayment_order": ""
}
```

**Описание полей:**

| Поле | Тип | Описание | Для AI-агента |
|------|-----|----------|---------------|
| `number` | string | Номер заказа (генерируем, формат `AI-XXXXX`) | Генерируем сами |
| `date` | string | Дата создания `YYYYMMDDHHmmss` | Текущее время |
| `klient` | string | Имя клиента | Из диалога |
| `phone` | string | Телефон клиента | CallerID или из диалога |
| `email` | string | Email клиента | Из диалога (опционально) |
| `fizlico` | string | Тип клиента | `"ФизЛицо"` (всегда для звонков) |
| `contact_klient` | string | Контактное лицо | = `klient` |
| `payment` | string | Способ оплаты (код, см. справочник ниже) | Для AI-агента: `"1"` (наличные) или `"2"` (безнал) |
| `delivery` | string | Тип доставки (см. справочник ниже) | `"Точки выдачи"` или `"NovaPost"` |
| `point` | string | ID точки выдачи (из справочника 1С) | При самовывозе |
| `city` | string | Город | Из диалога |
| `city_id` | string | GUID города в 1С | Из справочника |
| `street`, `house`, `flat` | string | Адрес доставки | При доставке на адрес |
| `store` | string | Торговая сеть | `"tshina"` / `"prokoleso"` |
| `order_channel` | string | Канал заказа | `"AI_AGENT"` |
| `bought_at_TSC` | boolean | Куплено в шинном центре | Определяется по контексту |
| `tovar` | array | Товары | Из корзины |
| `tovar[].id` | string | SKU товара (из get_wares) | Из результатов поиска |
| `tovar[].count` | integer | Количество | Из диалога |
| `tovar[].sum` | integer | Сумма в гривнах (целое число, без копеек) | Цена × количество |

#### Справочник: способы оплаты (`payment`)

| Код | Значение | Для AI-агента |
|-----|----------|---------------|
| `"1"` | Готівковий (наличные) | Да — оплата при получении |
| `"2"` | Безготівковий (безналичный) | Да — оплата картой при получении |
| `"3"` | monopay | Нет — онлайн-оплата |
| `"4"` | Приват ОЧ | Нет — онлайн-оплата |
| `"5"` | Приват МР | Нет — онлайн-оплата |
| `"6"` | Liqpay | Нет — онлайн-оплата |
| `"7"` | Monobank | Нет — онлайн-оплата |

> **Для AI-агента:** По телефону реально предложить только `"1"` (наличные при получении) или `"2"` (безнал/карта при получении). Онлайн-оплата (3-7) требует ссылку — можно отправить SMS, но это отдельный flow.

#### Справочник: способы доставки (`delivery`)

| Значение | Описание | Дополнительные поля |
|----------|----------|---------------------|
| `"Точки выдачи"` | Самовывоз из точки выдачи | `point` (ID точки) |
| `"NovaPost"` | Доставка Новой Почтой | `city`, `city_id`, `branch` (отделение НП) |

> **Формат суммы:** `tovar[].sum` — целое число в **гривнах** (без копеек). Пример: 60364 грн за 4 шины ≈ 15091 грн/шт.

> **TODO:** Статус заказа — эндпоинт будет добавлен позже со стороны 1С.

---

## SOAP-сервис шиномонтажа

**WSDL:** `http://192.168.11.9/Trade/ws/TireAssemblyExchange.1cws?wsdl`

**Namespace:** `http://www.1c.ru/SSL/TireAssemblyExchange`

**Binding:** SOAP 1.1 и SOAP 1.2 (document/literal)

### Операции

#### 1. GetGrafikStations — Список станций

**Запрос:** без параметров.

**Ответ:** строка (JSON/XML со списком станций).

**Маппинг:** → `get_fitting_stations` tool.

---

#### 2. GetStationSchedule — Расписание станции

**Запрос:**

| Поле | Тип | Описание |
|------|-----|----------|
| `StationID` | string (nillable) | ID станции (null = все станции) |
| `DataBig` | dateTime | Начало периода |
| `DataEnd` | dateTime | Конец периода |

**Ответ:** Массив строк с полями:
- `StationID` — ID станции
- `Data` — дата (date)
- `Time` — время (time)
- `Period` — полная дата-время (dateTime)
- `Quantity` — количество доступных слотов (int)

**Маппинг:** → `get_fitting_slots` tool.

---

#### 3. GetTireRecording — Запись на шиномонтаж

**Запрос:**

| Поле | Тип | Обязательное | Описание |
|------|-----|---|----------|
| `Person` | string | Да | ФИО клиента |
| `PhoneNumber` | string | Да | Телефон |
| `AutoType` | string | Да | Тип/марка авто |
| `AutoNumber` | string | Да | Госномер авто |
| `StoreTires` | boolean | Нет | Хранение шин |
| `StationID` | string | Да | ID станции |
| `Date` | date | Да | Дата записи |
| `Time` | time | Да | Время записи |
| `Status` | string | Нет | Статус |
| `Comment` | string | Нет | Комментарий |
| `NumberContract` | string | Нет | Номер договора |
| `CallBack` | boolean | Нет | Нужен обратный звонок |
| `ClientMode` | integer | Нет | Режим клиента |
| `СheckBalance` | boolean | Нет | Проверка баланса |
| `IdTelegram` | string | Нет | ID в Telegram |
| `IdViber` | string | Нет | ID в Viber |

**Ответ:** `Result` (boolean) + `GUID` (string — ID записи для последующей отмены).

**Маппинг:** → `book_fitting` tool.

---

#### 4. GetTireBooking — Предварительная бронь слота

**Запрос:**

| Поле | Тип | Описание |
|------|-----|----------|
| `StationID` | string | ID станции |
| `Date` | date | Дата |
| `Time` | time | Время |
| `Comment` | string (nillable) | Комментарий |

**Ответ:** `Result` (boolean) + `GUID` (string).

**Использование:** Можно использовать для промежуточной блокировки слота, пока агент собирает данные клиента (имя, авто).

---

#### 5. GetListOfEntries — Список записей клиента

**Запрос:**

| Поле | Тип | Описание |
|------|-----|----------|
| `PhoneNumber` | string | Телефон клиента |
| `StationID` | string (nillable) | ID станции (опционально) |
| `Date` | string (nillable) | Дата (опционально) |
| `Time` | string (nillable) | Время (опционально) |

**Ответ:** Массив строк с полями:
- `StationID`, `Data`, `Time`, `Period`
- `Customer` — имя клиента
- `AutoType`, `AutoNumber` — авто
- `NumberContract` — номер договора
- `GUID` — ID записи

**Маппинг:** Дополнительная функция — проверка существующих записей по CallerID.

---

#### 6. GetCancelRecords — Отмена записи

**Запрос:**

| Поле | Тип | Описание |
|------|-----|----------|
| `GUID` | string | ID записи (из GetTireRecording/GetTireBooking) |
| `UPP` | boolean (nillable) | Флаг (уточнить назначение) |

**Ответ:** `Result` (boolean).

**Маппинг:** → `cancel_fitting` tool.

---

#### 7. GetOziv — Отзыв клиента

**Запрос:**

| Поле | Тип | Описание |
|------|-----|----------|
| `GUID` | string | ID записи |
| `Otvet` | string | Текст отзыва |

**Использование:** Бонусная функция (Фаза 4) — сбор отзывов после посещения.

---

#### 8. GetVremPerem — Временные переменные

**Запрос:** `Save` (boolean), `StrokaJSON` (string).

**Использование:** Вероятно, внутренняя служебная операция 1С. Пока не используем.

---

## Маппинг: 1С API → LLM Agent Tools

| LLM Tool | Фаза | 1С Эндпоинт | Протокол | Данные для агента |
|----------|------|-------------|----------|-------------------|
| `search_tires` | MVP | `get_wares` + PostgreSQL | REST | Поиск по синхронизированному каталогу |
| `check_availability` | MVP | `get_stock/{Network}` | REST | Остатки + цены в реальном времени |
| `transfer_to_operator` | MVP | — | Asterisk ARI | Внутренний (не 1С) |
| `get_order_status` | 2 | **TODO** (будет позже) | REST? | Ждём эндпоинт от 1С |
| `create_order_draft` | 2 | `zakaz` | REST POST | Создание заказа в 1С |
| `update_order_delivery` | 2 | (часть `zakaz`) | REST POST | Включено в тело заказа |
| `confirm_order` | 2 | (часть `zakaz`?) | REST POST | **TODO:** уточнить flow |
| `get_fitting_stations` | 3 | `GetGrafikStations` | SOAP | Список станций |
| `get_fitting_slots` | 3 | `GetStationSchedule` | SOAP | Расписание/слоты |
| `book_fitting` | 3 | `GetTireRecording` | SOAP | Полная запись |
| `cancel_fitting` | 3 | `GetCancelRecords` | SOAP | Отмена по GUID |
| `search_knowledge_base` | 3 | — | Локальный RAG | pgvector (не 1С) |

### Дополнительные операции 1С (не в канонических tools, но полезны)

| Операция | Использование |
|----------|---------------|
| `GetTireBooking` (SOAP) | Предварительная бронь слота пока собираем данные клиента |
| `GetListOfEntries` (SOAP) | Проверка существующих записей по CallerID |
| `GetOziv` (SOAP) | Сбор отзывов (Фаза 4) |

---

## Архитектура интеграции

```
                    ┌─────────────────────────────────────────────┐
                    │              Call Processor                   │
                    │                                               │
                    │   LLM Agent (tools)                           │
                    │     │                                         │
                    │     ├── search_tires ──► PostgreSQL (синхр.)  │
                    │     │                       ▲                 │
                    │     │                       │ Sync Job        │
                    │     │                       │ (get_wares +    │
                    │     │                       │  ConfirmReceipt)│
                    │     │                       │                 │
                    │     ├── check_availability ─┼──► 1С REST      │
                    │     │                       │   get_stock     │
                    │     │                       │   stockgoods    │
                    │     │                       │                 │
                    │     ├── create_order_draft ──┼──► 1С REST     │
                    │     │                       │   POST zakaz    │
                    │     │                       │                 │
                    │     ├── get_fitting_* ──────┼──► 1С SOAP     │
                    │     ├── book_fitting ───────┼──► 1С SOAP     │
                    │     └── cancel_fitting ─────┼──► 1С SOAP     │
                    │                             │                 │
                    └─────────────────────────────┼─────────────────┘
                                                  │
                                                  ▼
                                        ┌─────────────────┐
                                        │   1С Сервер      │
                                        │  192.168.11.9    │
                                        │                   │
                                        │  REST: /Trade/hs/ │
                                        │  SOAP: /Trade/ws/ │
                                        │                   │
                                        │  Basic Auth:      │
                                        │  web_service /    │
                                        │  44332211         │
                                        └─────────────────┘
```

### Стратегия: Sync + Real-time

- **Каталог (get_wares):** Периодическая синхронизация в PostgreSQL. Поиск (`search_tires`) работает по локальной БД — быстро, с фильтрами.
- **Остатки (get_stock):** Запрос в реальном времени к 1С при `check_availability`. Можно кэшировать в Redis на 1-5 мин.
- **Заказы (zakaz):** Прямой POST в 1С при создании.
- **Шиномонтаж:** Все операции в реальном времени через SOAP.

---

## Открытые вопросы (TODO)

| # | Вопрос | Статус |
|---|--------|--------|
| ~~1~~ | ~~Структура ответа `get_wares`~~ | Решён — данные полностью структурированы |
| ~~2~~ | ~~Коды `payment`~~ | Решён — 7 кодов, для агента: 1 (наличные), 2 (безнал) |
| ~~3~~ | ~~Формат `sum` в заказе~~ | Решён — гривны, целое число (60364 грн за 4 шт) |
| ~~4~~ | ~~Значения `delivery`~~ | Решён — "Точки выдачи", "NovaPost" |
| 5 | **Справочник `city_id`** — откуда брать GUID городов? | Ждёт уточнения |
| 6 | **Справочник `point`** — откуда получать список точек выдачи? | Ждёт уточнения |
| 7 | **Статус заказа** — эндпоинт будет добавлен позже со стороны 1С | Ждём 1С |
| 8 | **`order_channel`** — какой код использовать для AI-агента? (предлагаем `"AI_AGENT"`) | Ждёт согласования |
| ~~9~~ | ~~Сетевой доступ~~ | Решён — прямой доступ к 192.168.11.9, Basic Auth (web_service / 44332211) |
| 10 | **`UPP` в GetCancelRecords** — что означает этот флаг? | Ждёт уточнения |
| ~~11~~ | ~~Структура ответа `get_stock`~~ | Решён — sku, price, stock, country, year_issue |
