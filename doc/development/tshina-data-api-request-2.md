# Запрос к команде tshina: данные для консультации голосового бота (сводный, v2)

**От:** команда Call Center AI (голосовой бот сетей Твоя Шина и Про Колесо)
**Дата:** 2026-10-26
**Заменяет и дополняет:** [запрос от 2026-09-29](tshina-data-api-request.md) — его часть выполнена (`Modules/DataApi`, `/api/v1`), статус ниже.

## Зачем

С 27 сентября ваш веб-консультант сильно поумнел: факты о моделях со сверенными цитатами, омологация, знания о брендах, честные места в тестах, акции. Голосовой бот консультирует тех же покупателей по телефону, но знает о шинах гораздо меньше. Сейчас он может сказать только размер, цену, наличие и буквы EU-этикетки. Вопрос «чим Turanza 6 краща за T005?» или «хто виробник Ріданс?» бот переводит на менеджера или отвечает общими словами.

Логику мы переносим к себе сами: проверки ответа, ранжирование по потребности, места в тестах. Но данные лежат у вас, и наружу их отдаёт только `/api/v1`. Просим несколько новых ресурсов **только на чтение** в том же модуле и по тем же правилам.

## Часть 1. Что уже работает (запрос 2026-09-29)

| Ресурс | Статус у вас | Статус у нас |
|---|---|---|
| `GET /api/v1/eu-labels` | ✅ работает | синхронизируем, этикетки идут в подбор и сравнение |
| `GET /api/v1/tire-tests` | ✅ работает | синхронизируем; в ответах бота с этого прогона — места в тестах по запросу клиента |
| `GET /api/v1/vehicles/{brands,models,modifications,tire-sizes,disk-sizes}` | ✅ работает | синхронизируем, по справочнику подбираем размер по машине |

**Что осталось по нему:**

1. **Критерии тестов.** `catalog_tire_test_criteria_scores` пока пустая. Как только она начнёт заполняться, данные пойдут через `placements[].criteria` без доработок. Для нас это самое ценное в тестах: «найкраща на мокрій дорозі в тесті ADAC».
2. **Шкала источников.** У всех `TireTestSource`, кроме `ams`, `scale_min`/`scale_max` равны `null`, поэтому `higher_is_better = null`. Направление шкалы мы не угадываем. Заполните хотя бы adac и autobild: там шкала «меньше — лучше».
3. **Новые источники тестов** (tyrereviews, testshin, ACE, AMS). Правильно ли мы поняли, что их тесты публикуются в те же `catalog_tire_tests` и сами попадут в фид? Есть ли у них `source_key` первоисточника?

## Часть 2. Новые ресурсы

Общие требования те же, что в [запросе 2026-09-29](tshina-data-api-request.md#общие-требования-для-всех-трёх-эндпоинтов):
- `GET`, JSON;
- `updated_since` (ISO-8601 с зоной) + `cursor` / `limit` ≤ 5000;
- ответ `{items, next_cursor, server_time}`;
- удаления через `data_api_deletions` с `deleted: true`;
- авторизация, IP-allowlist и лимит — как у текущего `/api/v1`.

Локализованные тексты отдаются объектом `{"ua": …, "ru": …}`. Мы говорим по-украински; русский нужен, потому что клиент часто спрашивает по-русски.

**Ключи.** Мы стыкуемся по `sku` (`catalog_products.sku`) и коду модели 1С (`catalog_product_models.sync1c_model_id`). Ни одна таблица ниже их не хранит напрямую, поэтому просим join на вашей стороне: в каждом ресурсе о товаре или модели должны быть `sku` или `model_1c_id`, а не ваши внутренние `product_id` / `product_model_id`.

Приоритет: **P1** — нужно для ближайшего прогона, **P2** — заметно улучшит ответы, **P3** — по возможности.

### 2.1 Структурные атрибуты шин — P1

`GET /api/v1/tire-attributes`

Сейчас шипы, XL и RunFlat мы вытаскиваем регулярками из свободного текста `description` из 1С. «Под шип» от «шип» отличаем с трудом, а по `tire_products.studded` (false на всех строках) ничего определить нельзя. У вас эти атрибуты уже разобраны (`catalog_product_values` + денормализованные колонки `catalog_products`).

```json
{
  "sku": "00000044795",
  "model_1c_id": "000012345",
  "season": "winter",
  "studded": "pod_ship",
  "is_xl": true,
  "is_runflat": false,
  "is_snow_marker": true,
  "vehicle_type": "passenger",
  "reinforcement": "reinfxl",
  "load_index": "103",
  "speed_index": "V",
  "country": {"ua": "Польща", "ru": "Польша"},
  "year": 2025,
  "updated_at": "2026-10-20T09:00:00Z",
  "deleted": false
}
```

| Поле | Источник у вас | Правило |
|---|---|---|
| `season` | атрибут `seasonality` (11) | `summer` / `winter` / `all_season` |
| `studded` | легаси-атрибут `studded` (26): `ship` / `no_ship` / `pod_ship` | только у него есть «под шип». `is_studded` (38) покрывает около 3 тыс. товаров. Если оба есть и расходятся, что считать правдой? |
| `is_xl`, `is_runflat`, `is_snow_marker` | атрибуты 37, 36, 39 | bool; `null`, если значения нет |
| `vehicle_type` | `type` (12): `legkovye` / `legkogruzovye` / `gruzovye` | `passenger` / `light_truck` / `truck` (по нему мы определяем шины C/LT) |
| `reinforcement` | `tire_reinforcement` (35) | slug как есть |
| `load_index`, `speed_index` | колонки `catalog_products` | строки как есть |
| `country`, `year` | `country_of_manufacture_id`, `year_of_manufacture` | |

**Что это даёт боту:** не называть липучку шипованной, честно говорить «під шип», отличать XL, отвечать «де вироблено / якого року».

### 2.2 Карточки знаний о моделях — P1

`GET /api/v1/model-knowledge`

Источник: `tire_model_knowledge`, только `status = 'ok'` и `schema_version = 1`. Локально 1036 из 1079.

```json
{
  "model_1c_id": "000012345",
  "brand": "Bridgestone",
  "model": "Turanza 6",
  "schema_version": 1,
  "card": {
    "purpose":     [{"text": {"ua": "…", "ru": "…"}, "source": "https://…"}],
    "conditions":  [ … ],
    "features":    [{"name": "…", "text": {"ua": "…", "ru": "…"}, "source": "…"}],
    "strengths":   [ … ],
    "limitations": [ … ],
    "fits":        [ … ]
  },
  "built_at": "2026-10-18T03:00:00Z",
  "updated_at": "2026-10-18T03:00:00Z",
  "deleted": false
}
```

- `quote`, `quote_match` и стоимость сборки (`llm_model`, токены, `cost_usd`) нам не нужны. Отдавайте только пункты, прошедшие сверку цитаты, как их читает `ModelKnowledgeFacts`.
- `catalog` (агрегаты по SKU) не нужен: этикетки и тесты мы берём из своих фидов.
- Строка, которая вышла из `ok` (`no_source`, `llm_failed`), приходит как `deleted: true`. Иначе у нас останется устаревшая карточка.
- **Что это даёт боту:** короткий устный ответ «для кого ця модель, сильні й слабкі сторони» на основе ваших сверенных фактов, а не выдумки LLM.

### 2.3 Факты консультанта (справочные темы) — P2

`GET /api/v1/knowledge-facts`

Источник: `tire_consultant_knowledge_facts` + `_fact_translations` + `_knowledge_topics`. Отдавать только `is_active = 1`.

```json
{
  "id": 412,
  "topic": {"slug": "tire-xl-reinforced", "title": {"ua": "…", "ru": "…"}},
  "model_1c_id": null,
  "origin": "article",
  "text": {"ua": "…", "ru": "…"},
  "keywords": ["xl", "посилена"],
  "updated_at": "2026-10-23T12:00:00Z",
  "deleted": false
}
```

- **Вопрос.** У 17 из 66 тем `is_active = 1`, но `is_published = 0`. Какой флаг означает, что факт можно говорить клиенту?
- Деактивированный факт (`deactivated_reason`) приходит как `deleted: true`.
- У `_fact_translations` нет `updated_at`. Правка перевода должна поднимать `updated_at` факта, иначе инкремент её не увидит.
- Локально фактов пока 0, а на проде, по нашим данным, в очереди около 460 предложений. Ресурс полезен по мере того, как их принимают.

### 2.4 Омологация (OE-метки) — P2

Два ресурса: справочник кодов и привязку к SKU.

`GET /api/v1/ommology-codes`. Источник: `tire_ommology_codes` + `catalog_ommology_atomic_map` → `catalog_ommology_brand_map` → `catalog_ommology_brands` (+ translations). Локально 208 кодов.

```json
{
  "code": "MO1",
  "car_brand": {"key": "mercedes", "title": {"ua": "Mercedes-Benz", "ru": "Mercedes-Benz"}},
  "description": "…",
  "updated_at": "…",
  "deleted": false
}
```

Код, для которого марка не определилась, отдавайте с `car_brand: null` и не пропускайте.

`GET /api/v1/product-ommology`. Источник: `catalog_product_ommology` → `catalog_products.sku`. Локально 1046 привязок.

```json
{"sku": "00000044795", "codes": ["MO1", "*"], "updated_at": "…", "deleted": false}
```

- Одна запись на SKU со всеми кодами. Изменение набора кодов поднимает `updated_at`.
- Атрибут `ommology` (25) дублирует таблицу. Какой источник канонический?
- **Что это даёт боту:** «ця модель має заводський допуск Mercedes (MO1)» при подборе по машине. Это сильный аргумент, который сейчас пропадает.

### 2.5 Знания о брендах шин — P2

`GET /api/v1/brand-facts`

Источник: `brand_knowledge_overrides` с `entity_type = 'tire_brand'` и (если согласны) карточки `tire_brand_knowledge` (локально 60 строк, все `ok`).

```json
{
  "brand": {"slug": "bridgestone", "title": "Bridgestone"},
  "fields": {
    "parent_company":    {"ua": "…", "ru": "…", "confidence": "medium"},
    "country_of_origin": {"ua": "Японія", "ru": "Япония", "confidence": "medium"},
    "founded_year":      {"value": "1931", "confidence": "medium"},
    "tire_technologies": {"ua": "…", "ru": "…", "confidence": "low"}
  },
  "card": { "positioning": […], "origin": […], "technologies": […], "lines": […], "strengths": […] },
  "updated_at": "…",
  "deleted": false
}
```

- Поля из `FIELD_KEYS_WHITELIST` для шин: `parent_company, founded_year, country_of_origin, headquarters_city, tire_technologies, oe_partners, brand_story, positioning, usp`.
- `confidence` нужен обязательно: `low` мы не озвучиваем.
- **Что это даёт боту:** правильный ответ на «хто виробник / чия це марка / де роблять», в том числе для марок с опечаткой.

### 2.6 Алиасы брендов — P3

`GET /api/v1/brand-aliases`

Источник: как в `FacetAliasesRepository::getBrandAliases` — `sync1c_tire_attribute_synonyms` (`target_kind = 'brand'`, `status = 'approved'`, `applied_at` не null) плюс канонические `brands.slug` / `brand_translations.title`.

```json
{"alias": "мішлен", "brand": {"slug": "michelin", "title": "Michelin"}, "updated_at": "…", "deleted": false}
```

**Что это даёт боту:** распознавание речи выдаёт марки в кириллице и с ошибками («ріданс», «бріджстоун»). Сейчас у нас около 60 своих основ, у вас больше тысячи проверенных написаний.

### 2.7 Акции и гарантийные программы — P3

`GET /api/v1/promotions`

Источник: `special_offers` (`status = 'published'`) + `special_offer_translations`, привязка к SKU через `catalog_products.active_special_offers`.

```json
{
  "id": 473,
  "type": "warranty_program",
  "title": {"ua": "…", "ru": "…"},
  "storefront_text": {"ua": "…", "ru": "…"},
  "start_at": "2026-01-01T00:00:00Z",
  "end_at": "2026-12-31T23:59:59Z",
  "conditions": {"warranty_min_qty": 4, "warranty_excludes_parts": "…", "details_url": "https://…"},
  "skus": ["00000044795", "…"],
  "updated_at": "…",
  "deleted": false
}
```

- `skus` пересчитывается вашим `PriceEngine`. Его пересчёт должен поднимать `updated_at` акции, иначе инкремент не увидит новые товары.
- Акция, у которой прошёл `end_at` или которую сняли с публикации, приходит как `deleted: true`.
- У `special_offer_translations` нет `updated_at`, так что правка текста должна поднимать `updated_at` акции.
- **Что это даёт боту:** «на Michelin діє розширена гарантія до кінця року» только тогда, когда это действительно так. Сейчас условия акций мы вносим руками.

### 2.8 Справочник моделей — P3

У вас уже есть `GET /api/sync1c/models` (15c6401ac). Но он отдаёт только модели с фото, не учитывает статус публикации и живёт на токене 1С.

Просим либо `GET /api/v1/tire-models` по общим правилам:

```json
{"model_1c_id": "000012345", "brand": {"slug": "bridgestone", "title": "Bridgestone"}, "name": "Turanza 6",
 "type": "tyres", "season": "summer", "is_published": true, "updated_at": "…", "deleted": false}
```

…либо доступ нашим токеном к `/api/sync1c/models` с параметром «все модели, не только с фото».

**Что это даёт боту:** распознавание названной модели («близак шість», «туранза») как сущности до поиска. Сейчас бот узнаёт модель, только если она уже есть в выдаче.

## Вопросы к вам

1. Сроки и порядок. Нам важнее всего 2.1 и 2.2; остальное — когда будет удобно.
2. Факты консультанта (2.3): какой флаг означает «можно говорить клиенту» — `is_active` или `is_published`?
3. Шипы (2.1): `studded` (легаси) или `is_studded` — что канонично, если они расходятся?
4. Омологация (2.4): что канонично — таблица `catalog_product_ommology` или атрибут `ommology`?
5. Можно ли поднимать `updated_at` родительской записи при правке переводов (2.3, 2.7), где у таблиц переводов нет своего `updated_at`? Если нет — для этих ресурсов нам хватит еженедельного полного снимка.
6. Шкала тестов (часть 1, п.2): когда сможете заполнить `scale_min`/`scale_max` для adac и autobild?

## Что делаем мы

- Клиенты новых ресурсов пишем по этому документу заранее, на моках, и включаем, когда ресурс появится. Расписание то же, что у текущего синка: ночной инкремент и еженедельная сверка по полному снимку (`src/integrations/tshina_sync.py`).
- Тексты от вас (`model-knowledge`, `knowledge-facts`, `brand-facts`) бот озвучивает **только как есть**. Если LLM в ответе добавит что-то сверх этого, проверка в коде заменит такое предложение. Поля с `confidence: low` не озвучиваются.
- Если ваш API недоступен, у нас ничего не ломается: остаются последние данные, и через 48 часов срабатывает алерт `TshinaDataSyncStale`.
- Цифры в документе взяты из локальной копии вашей базы (2026-10-26), на проде они могут отличаться.
