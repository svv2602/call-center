# Голдсет голосового бота

Кейсы — реплики клиента + ожидания по каждому ходу. Харнесс `scripts/run_goldset.py` прогоняет кейс через реальную LLM-петлю с мок-tools (`src/sandbox/agent_runner.py` — промпт, tools и оверрайды тенанта собираются как в живом звонке) **в каждой сети, которую кейс называет**. Обе сети идут через один промпт, поэтому всё, что зависит от условий сети, пишется как `network: both` с раздельными ожиданиями.

## Запуск

```bash
# бесплатно: загрузить и проверить кейсы
python -m scripts.run_goldset --validate

# в контейнере call-processor (БД для промпта, ключи провайдеров):
python -m scripts.run_goldset --network both               # печатает оценку стоимости и выходит
python -m scripts.run_goldset --network prokoleso --case 'pk_*' --yes   # платный прогон
python -m scripts.run_goldset --include-pending --yes --json-out /tmp/goldset.json
```

- Платный прогон — только с `--yes`; без него скрипт печатает план и оценку стоимости.
- Провайдер — `--provider <router key>`, по умолчанию `tenants.config.agent_provider_override` сети (ТШ — `openai-gpt41-mini`).
- Отчёт: «Зелёних кейсів N/M» по прогонам кейс × мережа, разбивка по сетям, число кейсов, зелёных во всех своих сетях, провалы по ассертам и красные ходы с ответом бота.

## Формат

`cases/*.yaml` — список кейсов в файле. Полная схема — `schema.json` (проверяет `parse_case` в харнессе; синхронность ключей пинит `tests/unit/test_goldset_assertions.py`).

```yaml
- id: delivery_cost_ua
  network: both                  # или [tvoya-shina] / [prokoleso]
  sales_enabled: true            # под какой tenants.config.sales_enabled написан кейс
  pending: 'wave-3-G-sales-scope-switch: причина'   # пропуск с причиной, не молча
  source: tshina:delivery_cost_lviv_ua              # | prod:<id звонка> | owner:<решение>
  mocks: {search_tires: {...}}   # ответ tool вместо дефолтного мока sandbox
  turns:
    - user: скільки коштує доставка до Львова?
      expect: {must_not_contain: ['якому місті']}
      expect_by_network:         # сливается с expect: списки складываются, скаляры заменяются
        tvoya-shina: {must_contain: ['безкоштовн']}
        prokoleso:   {must_contain: ['тариф\w*\s+перевізник']}
```

Ходы играются по порядку с общей историей; ход без `expect` — только контекст.

## Ассерты

| ключ | что проверяет |
|---|---|
| `must_contain` | каждый regex найден в ответе (без учёта регистра); ua и ru формы — через `\|` |
| `must_not_contain` | ни один regex не найден |
| `tool_called` | каждый спек выполнен вызовом на этом ходу: `name` или `{name, args: {arg: regex}}` |
| `tool_not_called` | ни одного вызова из списка |
| `transfer_reason` | `transfer_to_operator` с ровно этим `reason` |
| `max_sentences` | предложений в ответе не больше N (ответ озвучивается) |
| `network_leak` | **по умолчанию включён на каждом ходу**: нет фраз другой сети (название, доставка, услуги, расширенная гарантия). Отключить — `network_leak: false` |

Ошибка хода (`agent.last_error`) — отдельный провал `turn_error`.

`network_leak` пока берёт фразы из статического `NETWORK_ONLY_PHRASES` в харнессе. **TODO (волна 1-A):** строить список из `NetworkPolicy` каждого тенанта — единственная точка переключения `foreign_phrases()`.

Из 11 классов ассертов tshina перенесены `KeyPhrasesPresent` → `must_contain`, `ForbiddenPhrases` → `must_not_contain`, `IntentMatches` → `transfer_reason`/`tool_called` (у голоса нет intent-выхода — намерение видно по tool call). Карточки, EU-label, источники RAG, фильтры сайта — сайт-специфичны и не переносились (таблица — в чеклисте `wave-1-C-goldset-harness/tshina-transferability.md`).

## Флаг продаж

`sales_enabled` из кейса харнесс кладёт в `tenant["config"]` перед сборкой агента. Сборка промпта его пока не читает (волны 1-A / 3-G) — поэтому все `sales_enabled: true` кейсы помечены `pending` на волну, после которой ожидание станет достижимым. Кейсы `scope_today.yaml` (`sales_enabled: false`) гоняются уже сейчас — это базлайн инкумбента.

## Добавить кейс

1. Реальная формулировка из прода — `source: prod:<первые 8 символов id>`, без имён и телефонов.
2. Условие сети — `network: both` + `expect_by_network`.
3. Ожидание недостижимо до волны — `pending: '<чеклист>: <почему>'`.
4. `python -m scripts.run_goldset --validate`.
