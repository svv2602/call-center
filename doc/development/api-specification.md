# Store API — Спецификация

## Общие сведения

REST API для интеграции голосового ИИ-агента с интернет-магазином шин. API используется агентом для поиска товаров, оформления заказов, записи на шиномонтаж и получения информации.

**Base URL:** `https://store-api.example.com/api/v1`

**Формат:** JSON

**Аутентификация:** Bearer token (API key)

```
Authorization: Bearer <API_KEY>
```

## Общие соглашения

### Пагинация

```json
{
  "data": [...],
  "pagination": {
    "page": 1,
    "per_page": 20,
    "total": 150,
    "total_pages": 8
  }
}
```

### Ошибки

```json
{
  "error": {
    "code": "not_found",
    "message": "Товар не знайдено"
  }
}
```

| HTTP код | Описание |
|----------|----------|
| 200 | Успех |
| 201 | Создано |
| 400 | Некорректный запрос |
| 401 | Не авторизован |
| 404 | Не найдено |
| 422 | Ошибка валидации |
| 500 | Внутренняя ошибка |

---

## Фаза 1 — Каталог и наличие

### GET /tires/search

Поиск шин по параметрам. Все параметры опциональны, можно комбинировать.

**Query Parameters:**

| Параметр | Тип | Описание | Пример |
|----------|-----|----------|--------|
| `width` | integer | Ширина (мм) | 215 |
| `profile` | integer | Профиль (%) | 55 |
| `diameter` | integer | Диаметр (дюймы) | 17 |
| `season` | string | Сезон: `summer`, `winter`, `all_season` | winter |
| `brand` | string | Бренд | Michelin |
| `model` | string | Модель шины | X-Ice North 4 |
| `min_price` | number | Мин. цена за шт. (грн) | 1000 |
| `max_price` | number | Макс. цена за шт. (грн) | 5000 |
| `in_stock` | boolean | Только в наличии | true |
| `sort` | string | Сортировка: `price_asc`, `price_desc`, `popularity` | price_asc |
| `page` | integer | Страница | 1 |
| `per_page` | integer | Кол-во на странице (max 50) | 10 |

**Пример запроса:**
```
GET /tires/search?width=215&profile=55&diameter=17&season=winter&in_stock=true&sort=price_asc&per_page=5
```

**Пример ответа:**
```json
{
  "data": [
    {
      "id": "tire-001",
      "brand": "Continental",
      "model": "IceContact 3",
      "size": "215/55 R17",
      "width": 215,
      "profile": 55,
      "diameter": 17,
      "season": "winter",
      "type": "studded",
      "load_index": "98",
      "speed_index": "T",
      "price": 2800.00,
      "currency": "UAH",
      "in_stock": true,
      "stock_quantity": 12,
      "image_url": "https://...",
      "description": "Зимова шипована шина для легкових автомобілів"
    },
    {
      "id": "tire-002",
      "brand": "Michelin",
      "model": "X-Ice North 4",
      "size": "215/55 R17",
      "width": 215,
      "profile": 55,
      "diameter": 17,
      "season": "winter",
      "type": "studded",
      "load_index": "98",
      "speed_index": "T",
      "price": 3200.00,
      "currency": "UAH",
      "in_stock": true,
      "stock_quantity": 8,
      "image_url": "https://...",
      "description": "Преміальна зимова шипована шина"
    }
  ],
  "pagination": {
    "page": 1,
    "per_page": 5,
    "total": 7,
    "total_pages": 2
  }
}
```

### GET /tires/{id}

Детальная информация о конкретной шине.

**Пример ответа:**
```json
{
  "data": {
    "id": "tire-001",
    "brand": "Continental",
    "model": "IceContact 3",
    "size": "215/55 R17",
    "width": 215,
    "profile": 55,
    "diameter": 17,
    "season": "winter",
    "type": "studded",
    "load_index": "98",
    "speed_index": "T",
    "price": 2800.00,
    "old_price": 3100.00,
    "currency": "UAH",
    "in_stock": true,
    "stock_quantity": 12,
    "image_url": "https://...",
    "description": "Зимова шипована шина для легкових автомобілів",
    "features": [
      "Покращене зчеплення на льоду",
      "Знижений рівень шуму"
    ],
    "compatible_vehicles": [
      "Toyota Camry (2018-2024)",
      "Honda Accord (2018-2024)",
      "Mazda 6 (2018-2024)"
    ]
  }
}
```

### GET /tires/{id}/availability

Проверка наличия товара (детальная, по складам/точкам).

**Пример ответа:**
```json
{
  "data": {
    "product_id": "tire-001",
    "total_quantity": 12,
    "in_stock": true,
    "locations": [
      {
        "warehouse": "Київ-центральний",
        "quantity": 8,
        "available_from": "now"
      },
      {
        "warehouse": "Одеса",
        "quantity": 4,
        "available_from": "now"
      }
    ],
    "next_delivery": null
  }
}
```

### GET /vehicles/tires

Подбор шин по автомобилю (через базу применимости).

**Query Parameters:**

| Параметр | Тип | Описание | Пример |
|----------|-----|----------|--------|
| `make` | string | Марка | Toyota |
| `model` | string | Модель | Camry |
| `year` | integer | Год | 2020 |
| `modification` | string | Модификация (опционально) | 2.5 XLE |
| `season` | string | Сезон | winter |

**Пример запроса:**
```
GET /vehicles/tires?make=Toyota&model=Camry&year=2020&season=winter
```

**Пример ответа:**
```json
{
  "data": {
    "vehicle": {
      "make": "Toyota",
      "model": "Camry",
      "year": 2020,
      "oem_sizes": ["215/55 R17", "235/45 R18"]
    },
    "recommended_tires": [
      {
        "size": "215/55 R17",
        "is_oem": true,
        "tires": [
          {
            "id": "tire-001",
            "brand": "Continental",
            "model": "IceContact 3",
            "size": "215/55 R17",
            "price": 2800.00,
            "in_stock": true,
            "stock_quantity": 12
          }
        ]
      },
      {
        "size": "235/45 R18",
        "is_oem": true,
        "tires": [...]
      }
    ]
  }
}
```

---

## Фаза 2 — Заказы

### GET /orders/search

Поиск заказов по телефону клиента или номеру заказа.

**Query Parameters:**

| Параметр | Тип | Описание |
|----------|-----|----------|
| `phone` | string | Телефон клиента (+380...) |
| `order_number` | string | Номер заказа |
| `status` | string | Фильтр по статусу |

**Пример ответа:**
```json
{
  "data": [
    {
      "id": "ord-12345",
      "order_number": "12345",
      "status": "delivering",
      "status_label": "В доставці",
      "created_at": "2025-03-12T10:00:00Z",
      "total": 12800.00,
      "currency": "UAH",
      "items_summary": "4x Michelin X-Ice North 4 215/55 R17",
      "estimated_delivery": "2025-03-15",
      "tracking_number": "0504000012345"
    }
  ]
}
```

### GET /orders/{id}

Детали заказа.

**Пример ответа:**
```json
{
  "data": {
    "id": "ord-12345",
    "order_number": "12345",
    "status": "delivering",
    "status_label": "В доставці",
    "created_at": "2025-03-12T10:00:00Z",
    "customer": {
      "phone": "+380XXXXXXXXX",
      "name": "Іван Петренко"
    },
    "items": [
      {
        "product_id": "tire-002",
        "name": "Michelin X-Ice North 4 215/55 R17",
        "quantity": 4,
        "price_per_unit": 3200.00,
        "total": 12800.00
      }
    ],
    "delivery": {
      "type": "delivery",
      "city": "Київ",
      "address": "вул. Хрещатик, 1",
      "cost": 200.00,
      "estimated_date": "2025-03-15",
      "tracking_number": "0504000012345"
    },
    "payment": {
      "method": "cod",
      "status": "pending",
      "total": 13000.00
    },
    "status_history": [
      {"status": "created", "at": "2025-03-12T10:00:00Z"},
      {"status": "confirmed", "at": "2025-03-12T10:05:00Z"},
      {"status": "shipped", "at": "2025-03-13T14:00:00Z"},
      {"status": "delivering", "at": "2025-03-14T09:00:00Z"}
    ]
  }
}
```

### POST /orders

Создать черновик заказа.

**Request Body:**
```json
{
  "customer_phone": "+380XXXXXXXXX",
  "customer_name": "Іван Петренко",
  "items": [
    {
      "product_id": "tire-002",
      "quantity": 4
    }
  ],
  "source": "ai_agent",
  "call_id": "uuid-of-call-session"
}
```

**Пример ответа (201):**
```json
{
  "data": {
    "id": "ord-12346",
    "order_number": "12346",
    "status": "draft",
    "items": [
      {
        "product_id": "tire-002",
        "name": "Michelin X-Ice North 4 215/55 R17",
        "quantity": 4,
        "price_per_unit": 3200.00,
        "total": 12800.00
      }
    ],
    "subtotal": 12800.00,
    "currency": "UAH"
  }
}
```

### PATCH /orders/{id}/delivery

Указать способ и адрес доставки.

**Request Body (доставка):**
```json
{
  "delivery_type": "delivery",
  "city": "Київ",
  "address": "вул. Хрещатик, 1"
}
```

**Request Body (самовывоз):**
```json
{
  "delivery_type": "pickup",
  "pickup_point_id": "pp-001"
}
```

**Пример ответа:**
```json
{
  "data": {
    "order_id": "ord-12346",
    "delivery": {
      "type": "delivery",
      "city": "Київ",
      "address": "вул. Хрещатик, 1",
      "cost": 200.00,
      "estimated_days": "2-3"
    },
    "total": 13000.00
  }
}
```

### POST /orders/{id}/confirm

Подтвердить и финализировать заказ.

**Request Body:**
```json
{
  "payment_method": "cod",
  "customer_name": "Іван Петренко",
  "send_sms_confirmation": true
}
```

**Пример ответа:**
```json
{
  "data": {
    "id": "ord-12346",
    "order_number": "12346",
    "status": "confirmed",
    "total": 13000.00,
    "estimated_delivery": "2025-03-17",
    "sms_sent": true
  }
}
```

### GET /delivery/calculate

Рассчитать стоимость доставки.

**Query Parameters:**

| Параметр | Тип | Описание |
|----------|-----|----------|
| `city` | string | Город |
| `items_weight_kg` | number | Вес (кг) |
| `order_total` | number | Сумма заказа (для бесплатной доставки) |

### GET /pickup-points

Список пунктов самовывоза.

**Query Parameters:**

| Параметр | Тип | Описание |
|----------|-----|----------|
| `city` | string | Город |

**Пример ответа:**
```json
{
  "data": [
    {
      "id": "pp-001",
      "name": "Шини XYZ — Позняки",
      "city": "Київ",
      "address": "вул. Здолбунівська, 7а",
      "working_hours": "Пн-Сб 9:00-19:00, Нд 10:00-17:00",
      "phone": "+380441234567"
    }
  ]
}
```

---

## Фаза 3 — Шиномонтаж и база знаний

### GET /fitting/stations

Список точек шиномонтажа.

**Query Parameters:**

| Параметр | Тип | Описание |
|----------|-----|----------|
| `city` | string | Город |

**Пример ответа:**
```json
{
  "data": [
    {
      "id": "fs-001",
      "name": "Шиномонтаж XYZ — Позняки",
      "city": "Київ",
      "district": "Позняки",
      "address": "вул. Здолбунівська, 7а",
      "phone": "+380441234567",
      "working_hours": "Пн-Сб 8:00-20:00, Нд 9:00-18:00",
      "services": ["tire_change", "balancing", "alignment", "storage"]
    }
  ]
}
```

### GET /fitting/stations/{id}/slots

Доступные слоты для записи.

**Query Parameters:**

| Параметр | Тип | Описание |
|----------|-----|----------|
| `date_from` | string | Начало периода (YYYY-MM-DD) |
| `date_to` | string | Конец периода (YYYY-MM-DD) |
| `service_type` | string | Тип услуги |

**Пример ответа:**
```json
{
  "data": {
    "station_id": "fs-001",
    "slots": [
      {
        "date": "2025-03-15",
        "times": [
          {"time": "10:00", "available": true},
          {"time": "11:00", "available": false},
          {"time": "12:00", "available": true},
          {"time": "14:00", "available": true},
          {"time": "16:00", "available": true}
        ]
      },
      {
        "date": "2025-03-16",
        "times": [
          {"time": "09:00", "available": true},
          {"time": "11:00", "available": true},
          {"time": "13:00", "available": true}
        ]
      }
    ]
  }
}
```

### POST /fitting/bookings

Создать запись на шиномонтаж.

**Request Body:**
```json
{
  "station_id": "fs-001",
  "date": "2025-03-15",
  "time": "14:00",
  "service_type": "tire_change",
  "customer_phone": "+380XXXXXXXXX",
  "customer_name": "Іван Петренко",
  "vehicle_info": "Toyota Camry 2020",
  "tire_diameter": 17,
  "linked_order_id": "ord-12346",
  "notes": "Шини з замовлення, будуть на складі",
  "source": "ai_agent",
  "call_id": "uuid-of-call-session"
}
```

**Пример ответа (201):**
```json
{
  "data": {
    "id": "bk-001",
    "station": {
      "name": "Шиномонтаж XYZ — Позняки",
      "address": "вул. Здолбунівська, 7а"
    },
    "date": "2025-03-15",
    "time": "14:00",
    "service_type": "tire_change",
    "estimated_duration_min": 45,
    "price": 800.00,
    "currency": "UAH",
    "sms_sent": true
  }
}
```

### DELETE /fitting/bookings/{id}

Отменить запись.

### PATCH /fitting/bookings/{id}

Перенести запись.

**Request Body:**
```json
{
  "date": "2025-03-16",
  "time": "11:00"
}
```

### GET /fitting/prices

Прайс-лист на услуги шиномонтажа.

**Query Parameters:**

| Параметр | Тип | Описание |
|----------|-----|----------|
| `station_id` | string | ID точки |
| `tire_diameter` | integer | Диаметр шин |

**Пример ответа:**
```json
{
  "data": [
    {
      "service": "tire_change",
      "label": "Заміна шин (4 шт.)",
      "prices_by_diameter": {
        "14": 400,
        "15": 500,
        "16": 600,
        "17": 800,
        "18": 1000,
        "19": 1200,
        "20": 1500
      },
      "currency": "UAH"
    },
    {
      "service": "balancing",
      "label": "Балансування (4 шт.)",
      "prices_by_diameter": {
        "14": 200,
        "15": 250,
        "16": 300,
        "17": 400,
        "18": 500,
        "19": 600,
        "20": 700
      },
      "currency": "UAH"
    }
  ]
}
```

### GET /knowledge/search

Поиск по базе знаний (для экспертных консультаций).

**Query Parameters:**

| Параметр | Тип | Описание |
|----------|-----|----------|
| `query` | string | Поисковый запрос |
| `category` | string | Категория: `brands`, `guides`, `faq`, `comparisons` |
| `limit` | integer | Кол-во результатов (default 5) |

**Пример запроса:**
```
GET /knowledge/search?query=michelin vs continental зима&limit=3
```

**Пример ответа:**
```json
{
  "data": [
    {
      "id": "kb-042",
      "title": "Порівняння Michelin та Continental для зими",
      "category": "comparisons",
      "content": "Michelin X-Ice North 4 та Continental IceContact 3 — дві найпопулярніші зимові шини преміум-сегменту...",
      "relevance_score": 0.95
    },
    {
      "id": "kb-015",
      "title": "Як обрати зимові шини",
      "category": "guides",
      "content": "...",
      "relevance_score": 0.78
    }
  ]
}
```

---

## Фаза 4 — Аналитика

### GET /analytics/calls

Список звонков с фильтрами.

**Query Parameters:**

| Параметр | Тип | Описание |
|----------|-----|----------|
| `date_from` | string | Начало периода |
| `date_to` | string | Конец периода |
| `scenario` | string | Фильтр по сценарию |
| `transferred` | boolean | Только переключённые на оператора |
| `quality_below` | number | Оценка качества ниже порога |
| `page` | integer | Страница |

### GET /analytics/calls/{id}

Полная информация о звонке: транскрипция, метрики, оценка качества, стоимость.

### GET /analytics/summary

Агрегированная статистика.

**Query Parameters:**

| Параметр | Тип | Описание |
|----------|-----|----------|
| `period` | string | `day`, `week`, `month` |
| `date_from` | string | Начало периода |
| `date_to` | string | Конец периода |

**Пример ответа:**
```json
{
  "data": {
    "period": "2025-03-01 to 2025-03-31",
    "total_calls": 12500,
    "avg_duration_seconds": 185,
    "resolved_by_bot_pct": 72.5,
    "transferred_to_operator_pct": 27.5,
    "transfer_reasons": {
      "customer_request": 45,
      "cannot_help": 30,
      "complex_question": 15,
      "negative_emotion": 10
    },
    "scenarios": {
      "tire_search": 40,
      "availability_check": 15,
      "order_status": 20,
      "order_creation": 10,
      "fitting_booking": 8,
      "consultation": 7
    },
    "orders_created": 850,
    "fittings_booked": 620,
    "avg_quality_score": 0.82,
    "total_cost_usd": 1250.00,
    "avg_cost_per_call_usd": 0.10
  }
}
```
