# Манифест привязки статей к тенантам (сетям)

> Служебный файл, **не статья базы знаний**. Не импортировать.
> `scripts/seed_knowledge.py` берёт `knowledge_seed/**/*.md` и затянет этот файл как статью `general` — при seed-скрипте его исключить (или удалить созданную статью «Манифест привязки статей к тенантам (сетям)»).

Статьи с `tenant_id IS NULL` (shared) видны обеим сетям. Сетевые статьи должны иметь `tenant_id` своей сети — поиск фильтрует `tenant_id IS NULL OR tenant_id = :tenant` (`src/knowledge/search.py`).

Правило для shared-статей: ни одного сетевого условия (бесплатная/платная доставка, расширенная гарантия, монтаж, хранение) — только «умови залежать від мережі».

## Привязка

Область: `delivery/`, `warranty/`, `returns/`, `policies/`. Остальные папки не размечены (см. «Вне манифеста»).

| Файл | Тенант |
|---|---|
| `delivery/01_delivery_sposoby_dostavky.md` | shared |
| `delivery/02_delivery_oplaata_zamovlennya.md` | shared |
| `delivery/03_delivery_vidstezhennya_zamovlennya.md` | shared |
| `delivery/04_delivery_dostavka_velykogabarytna.md` | shared |
| `delivery/05_delivery_poshkodzhennya_pry_dostavtsi.md` | shared |
| `delivery/06_delivery_tvoya_shina.md` | `tvoya-shina` |
| `delivery/07_delivery_prokoleso.md` | `prokoleso` |
| `warranty/01_warranty_zagalni_umovy.md` | shared |
| `warranty/02_warranty_yak_podaty_pretenziyu.md` | shared |
| `warranty/03_warranty_garantiya_po_brendakh.md` | shared |
| `warranty/04_warranty_shcho_ne_ye_garantiynym.md` | shared |
| `warranty/05_warranty_dorozhnya_garantiya.md` | shared |
| `warranty/06_warranty_tvoya_shina.md` | `tvoya-shina` |
| `warranty/07_warranty_prokoleso.md` | `prokoleso` |
| `returns/01_returns_umovy_povernennya.md` | shared |
| `returns/02_returns_yak_oformyty_povernennya.md` | shared |
| `returns/03_returns_povernennya_neякisnoho_tovaru.md` | shared |
| `returns/04_returns_obmin_na_inshyy_rozmir.md` | shared |
| `returns/05_returns_vidmova_vid_zamovlennya.md` | shared |
| `policies/01_policies_konfidentsiynist.md` | shared |
| `policies/02_policies_tsinoutvorennya.md` | shared |
| `policies/03_policies_yakist_ta_oryhinal.md` | shared |
| `policies/04_policies_obsluhovuvannya_kliентiv.md` | shared |
| `policies/05_policies_ekologichna_politika.md` | shared |
| `policies/06_policies_zakonodavstvo_shyny.md` | shared |
| `policies/07_policies_aktsii_loyalnist.md` | shared |
| `policies/08_policies_tvoya_shina.md` | `tvoya-shina` |
| `policies/09_policies_prokoleso.md` | `prokoleso` |

Итого: 22 shared, 3 `tvoya-shina`, 3 `prokoleso`.

## Как привязать `tenant_id` при импорте

`POST /knowledge/articles/import` (`src/api/knowledge.py:154`) и `scripts/seed_knowledge.py` **не принимают `tenant_id`** — статья создаётся с `tenant_id = NULL`, то есть видна обеим сетям. Кроме того, оба пути **пропускают файл, если статья с таким же title уже есть** — переписанный текст существующих статей реимпортом не обновится.

1. UUID тенантов (сверить slug — в коде встречаются и `tvoya-shina`, и `tshina`):
   ```sql
   SELECT id, slug, name FROM tenants WHERE slug IN ('tvoya-shina', 'prokoleso');
   ```
2. **Сетевые статьи (6 файлов)** — создавать сразу с `tenant_id`, чтобы ни на минуту не оказаться shared:
   ```
   POST /knowledge/articles
   {"title": "<H1 файла>", "category": "<delivery|warranty|policies>", "content": "<тело файла без H1>", "tenant_id": "<uuid сети>"}
   ```
   Если всё же импортированы через `/knowledge/articles/import` — сразу после импорта:
   ```
   PATCH /knowledge/articles/{id}
   {"tenant_id": "<uuid сети>"}
   ```
3. **Shared-статьи, уже существующие в БД** (22 файла, title не менялись) — обновить текст:
   ```
   PATCH /knowledge/articles/{id}
   {"content": "<тело файла без H1>"}
   ```
   и убедиться, что `tenant_id` у них NULL (`PATCH` с `"tenant_id": ""` сбрасывает в NULL).
4. Проверка:
   ```sql
   SELECT a.title, a.category, t.slug
   FROM knowledge_articles a LEFT JOIN tenants t ON t.id = a.tenant_id
   WHERE a.category IN ('delivery', 'warranty', 'returns', 'policies')
   ORDER BY a.category, a.title;
   ```
   Ожидается: 6 строк с slug (по 3 на сеть), остальные — NULL.

## Вне манифеста

Папки `fitting/`, `procedures/`, `general/`, `faq/`, `guides/`, `brands/`, `comparisons/` не размечены. В `fitting/` (монтаж, хранение) — услуги только Твоя Шина, а статьи shared; в `procedures/03_procedures_perevirka_nayavnosti.md` — «зарезервувати шини на 24 години», что противоречит решению владельца (резерва нет). Требует отдельного чеклиста.
