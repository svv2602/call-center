# P0: Transfer to Operator

## Цель
Реализовать реальное переключение звонка на оператора через Asterisk ARI, заменив текущий нефункциональный флаг. Исправить все тексты, которые ложно обещают оператора.

## Критерии успеха
- [x] `transfer_to_operator()` выполняет ARI redirect на очередь операторов
- [x] AudioSocket не закрывается до подтверждения transfer
- [x] `ERROR_TEXT` не обещает оператора (заменён на «спробуйте ще раз»)
- [x] Asterisk `queues.conf` содержит хотя бы одного оператора
- [x] Fallback при ARI unavailable: сообщение клиенту + hangup
- [x] Тесты покрывают transfer success/failure/timeout
- [ ] pytest tests/ проходит (требует БД)

## Фазы работы
1. **Подготовка** — анализ ARI кода, Asterisk конфигурации
2. **ARI Transfer** — подключение ARI client к transfer tool
3. **Error Text & Fallbacks** — исправление текстов, fallback логика

## Источник требований
- `audit/prompts/report/01-critical-risks.md` (CRIT-01)
- `audit/prompts/report/03-bot-scenarios.md` (BOT-01, BOT-03)
- `audit/prompts/report/07-strategic.md` (STR-01)

## Правила переиспользования кода

### Где искать:
```
src/core/asterisk_ari.py   # Существующий ARI client (transfer_to_queue — dead code)
src/main.py                # transfer_to_operator() tool handler
src/core/pipeline.py       # Pipeline lifecycle, session state transitions
src/agent/prompts.py       # ERROR_TEXT, TRANSFER_TEXT, все UX-тексты
src/config.py              # ARISettings (url, user, password)
asterisk/extensions.conf   # [transfer-to-operator] context
asterisk/queues.conf       # Очередь операторов
```

## Начало работы
Для начала или продолжения работы прочитай PROGRESS.md
