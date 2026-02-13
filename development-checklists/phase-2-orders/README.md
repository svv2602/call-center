# Фаза 2 — Заказы: Статус и оформление

## Цель

Добавить возможность проверки статуса существующих заказов и оформления новых заказов через голосового агента. Клиент сможет полностью завершить покупку без участия оператора.

## Пререквизиты

- Фаза 1 (MVP) завершена и стабильно работает
- Store API расширен эндпоинтами для работы с заказами

## Критерии успеха

- [ ] Бот определяет клиента по CallerID
- [ ] Бот находит и озвучивает статус заказа
- [ ] Бот работает с несколькими заказами одного клиента
- [ ] Бот проводит полный цикл оформления заказа
- [ ] Бот подтверждает детали перед финализацией
- [ ] Бот отправляет/инициирует SMS с подтверждением заказа
- [ ] Клиент может отменить оформление на любом этапе
- [ ] Логирование всех действий с заказами (аудит)

## Фазы работы

1. [Идентификация клиента (CallerID)](phase-01-caller-id.md) — ARI интеграция, идентификация по телефону
2. [Tools для заказов](phase-02-order-tools.md) — 4 новых tools, schema, валидация
3. [Store API для заказов](phase-03-store-api-orders.md) — Эндпоинты заказов, Idempotency-Key
4. [Сценарии и промпт](phase-04-scenarios-prompt.md) — Системный промпт фазы 2, сценарии диалога
5. [Тестирование](phase-05-testing.md) — Тесты заказов, безопасность, E2E

## Источник требований

- `doc/development/phase-2-orders.md` — основной
- `doc/development/api-specification.md` — спецификация Store API (фаза 2)
- `doc/technical/data-model.md` — таблицы orders, order_items
- `doc/development/00-overview.md` — канонический список tools

## Новые tools (канонический список)

| Tool | Store API endpoint |
|------|--------------------|
| `get_order_status` | `GET /orders/search`, `GET /orders/{id}` |
| `create_order_draft` | `POST /orders` |
| `update_order_delivery` | `PATCH /orders/{id}/delivery` |
| `confirm_order` | `POST /orders/{id}/confirm` |

## Новые Store API endpoints

| Метод | Endpoint | Описание |
|-------|----------|----------|
| GET | `/api/v1/orders/search` | Поиск заказов по телефону/номеру |
| GET | `/api/v1/orders/{id}` | Детали заказа |
| POST | `/api/v1/orders` | Создать черновик заказа (Idempotency-Key) |
| PATCH | `/api/v1/orders/{id}/delivery` | Указать доставку |
| POST | `/api/v1/orders/{id}/confirm` | Подтвердить заказ (Idempotency-Key) |
| GET | `/api/v1/delivery/calculate` | Рассчитать стоимость доставки |
| GET | `/api/v1/pickup-points` | Список пунктов самовывоза |

## Зависимости от Фазы 1

- AudioSocket сервер работает стабильно
- STT/TTS модули работают
- LLM агент с tool calling работает
- Store Client с circuit breaker и retry работает
- Логирование в PostgreSQL работает

## Начало работы

Для начала или продолжения работы прочитай [PROGRESS.md](PROGRESS.md)
