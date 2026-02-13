# Фаза 3 — Сервисы: Шиномонтаж и консультации

## Цель

Расширить возможности агента: запись на шиномонтаж с выбором даты/времени/точки обслуживания, а также экспертные консультации по подбору шин через RAG (база знаний).

## Пререквизиты

- Фаза 2 (заказы) завершена и стабильно работает
- Store API расширен эндпоинтами для записи на шиномонтаж
- Агент наработал статистику типичных вопросов из фаз 1–2

## Критерии успеха

- [ ] Бот записывает клиента на шиномонтаж (полный цикл)
- [ ] Бот показывает доступные даты и время
- [ ] Бот отменяет/переносит запись
- [ ] Бот озвучивает стоимость монтажа
- [ ] Бот проводит экспертную консультацию, используя базу знаний
- [ ] Бот связывает заказ шин с записью на монтаж
- [ ] Бот проводит комплексный сценарий: подбор → заказ → монтаж
- [ ] База знаний содержит минимум 20 статей (бренды, FAQ, гайды)
- [ ] Векторный поиск по базе знаний работает корректно

## Фазы работы

1. [Tools шиномонтажа](phase-01-fitting-tools.md) — Tools: stations, slots, booking, cancel, price
2. [Fitting API](phase-02-fitting-api.md) — Store API: fitting endpoints, prices
3. [База знаний (RAG)](phase-03-knowledge-base.md) — pgvector, embeddings, RAG pipeline
4. [Комплексные сценарии](phase-04-complex-scenarios.md) — Связка подбор→заказ→монтаж, промпт
5. [Тестирование](phase-05-testing.md) — Тесты RAG, booking, цепочки сценариев

## Источник требований

- `doc/development/phase-3-services.md` — основной
- `doc/development/api-specification.md` — спецификация Store API (фаза 3)
- `doc/technical/data-model.md` — таблицы fitting_stations, fitting_bookings, knowledge_*
- `doc/development/00-overview.md` — канонический список tools

## Новые tools (канонический список)

| Tool | Store API endpoint |
|------|--------------------|
| `get_fitting_stations` | `GET /fitting/stations` |
| `get_fitting_slots` | `GET /fitting/stations/{id}/slots` |
| `book_fitting` | `POST /fitting/bookings` |
| `search_knowledge_base` | `GET /knowledge/search` |

## Новые Store API endpoints

| Метод | Endpoint | Описание |
|-------|----------|----------|
| GET | `/api/v1/fitting/stations` | Список точек шиномонтажа |
| GET | `/api/v1/fitting/stations/{id}/slots` | Доступные слоты |
| POST | `/api/v1/fitting/bookings` | Создать запись |
| DELETE | `/api/v1/fitting/bookings/{id}` | Отменить запись |
| PATCH | `/api/v1/fitting/bookings/{id}` | Перенести запись |
| GET | `/api/v1/fitting/prices` | Прайс-лист на услуги |
| GET | `/api/v1/knowledge/search` | Поиск по базе знаний |

## Зависимости от Фазы 2

- CallerID идентификация работает
- Все tools из фаз 1-2 работают
- Store Client поддерживает расширение endpoints
- Заказы оформляются полностью

## Начало работы

Для начала или продолжения работы прочитай [PROGRESS.md](PROGRESS.md)
