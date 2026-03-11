# Архитектура системы промптов и памяти агента

**Дата:** 2026-03-11 (обновлено: 2026-03-11 по результатам code review)
**Версия промпта:** v4.0-guided (код + DB для A/B тестов)

## 1. Модульная архитектура промпта

Промпт не монолитный — он собирается из 8+ модулей в `src/agent/prompts.py`:

| Модуль | Размер | Когда грузится |
|--------|--------|----------------|
| `_MOD_CORE` (идентичность, правила) | ~1800 tok | **Всегда**, с 1-го терна |
| Compact router (маршрутизатор сценариев) | ~500 tok | Терн 1, пока тема не определена |
| Scenario module (шины/заказы/шиномонтаж/...) | ~2000-4000 tok | **После детекции темы** по ключевым словам |
| Order stage injection | ~200-500 tok | Только когда есть активный заказ |
| Patterns (из DB) | ~300-900 tok | Каждый терн, по similarity |
| Customer profile, history, storage | ~200-500 tok | Если есть данные о клиенте |
| Safety rules, few-shot examples | ~300-500 tok | Всегда |
| Promotions | ~100-300 tok | Если есть активные акции |

**Итого на терн**: ~5500-8500 токенов system prompt.

## 2. Что происходит на каждом терне

```
Реплика клиента (STT)
    │
    ├─ 1. Keyword matching → определение сценария (tire_search, order, fitting...)
    │     → если новый сценарий — добавляется модуль в active_scenarios
    │
    ├─ 2. Pattern search (pgvector, cosine ≥ 0.72, top 3)
    │     → matched patterns → секция "Інструкції з досвіду"
    │
    ├─ 3. Order stage detection (если есть заказ)
    │     → инъекция стадийных инструкций
    │
    ├─ 4. build_system_prompt_with_context() — сборка system prompt
    │     → стабильные секции первыми (для prompt caching)
    │     → динамические секции после
    │
    ├─ 5. History compression (если > 10 сообщений)
    │     → light: tool_result → "[ок]" stub
    │     → heavy: суммаризация старых сообщений
    │
    └─ 6. LLM call (system + history + tools)
          → streaming → TTS → AudioSocket
```

## 3. Память разговора

### Два уровня

- **Redis session** (`CallSession`, TTL 30 мин) — сериализуемое состояние: `tools_called`, `active_scenarios`, стадия заказа, tenant. Переживает рестарт процесса.
- **In-memory `_llm_history`** — полная история сообщений (user/assistant/tool_use/tool_result). **Не сохраняется в Redis** — при крэше теряется.

### Компрессия истории (`history_compressor.py`)

- Старые `tool_result` заменяются на `[ок]` (экономия 50-200 tok каждый)
- При >10 **сообщений** (не пар) — суммаризация, с сохранением первых 3 реплик + последней цитаты клиента
- **Критично**: никогда не разрывает пары `tool_use`/`tool_result` (иначе API 400)
- Потолок: 25 сообщений после trim

## 4. Влияние на скорость и точность

### Скорость

- Терн 1: ~5500 input tokens (compact router) → быстрый ответ
- Терн 5+: ~8500+ input tokens (полный сценарий + история) → медленнее
- Prompt caching (стабильные секции первыми): экономит ~90% стоимости кэшированных токенов (latency-эффект зависит от провайдера)
- Компрессия: без неё к 10-му терну было бы 15-20k tokens истории

### Точность

- Модульность: агент видит только релевантный сценарий, а не все инструкции сразу → меньше confusion
- Patterns: ситуативные фиксы из реальных звонков → лечат конкретные ошибки без раздувания основного промпта
- Фильтрация tools: по стадии заказа агент видит только релевантные инструменты → меньше ложных tool calls
- Компромисс: тяжёлая компрессия теряет детали ранних терновых → агент может переспросить то, что уже обсуждалось

## 5. DB-промпты vs код

По умолчанию system prompt собирается **из кода** (`prompts.py`, версия v4.0-guided). Таблица `prompt_versions` в БД используется для **A/B тестов** — `prompt_manager.py` читает/активирует версии, `ab_testing.py` распределяет звонки между вариантами. В штатном режиме все DB-версии `is_active=false`, и агент работает на кодовом промпте.

## 6. Ключевые файлы

| Файл | Назначение |
|------|-----------|
| `src/agent/prompts.py` | Модульная сборка промпта, форматирование контекста |
| `src/agent/agent.py` | LLMAgent, маршрутизация tool calls, blocking path |
| `src/agent/streaming_loop.py` | Streaming path, real-time audio |
| `src/agent/history_compressor.py` | Стратегии компрессии истории |
| `src/core/call_session.py` | Стейт-машина сессии (Redis) |
| `src/core/pipeline.py` | Оркестрация звонка, flow каждого терна |
| `src/sandbox/patterns.py` | Поиск и инъекция паттернов (pgvector) |
| `src/agent/prompt_manager.py` | Управление DB-версиями промптов |
| `src/agent/ab_testing.py` | A/B тестирование промптов |

## 7. Схема сборки system prompt

```
build_system_prompt_with_context()
    │
    ├─ [STABLE — кэшируется]
    │   ├─ _MOD_CORE (идентичность, базовые правила)
    │   ├─ Safety rules (из DB conversation_patterns type=safety)
    │   ├─ Few-shot examples
    │   └─ Promotions
    │
    ├─ [STABLE-per-call — не меняется в рамках звонка]
    │   ├─ Current date + season hint
    │   ├─ Customer profile + caller history
    │   └─ Storage contracts
    │
    └─ [DYNAMIC — меняется каждый терн]
        ├─ Scenario modules (по active_scenarios)
        ├─ Order stage injection
        ├─ Call context (CallerID, order_id)
        └─ Matched patterns (top 3, cosine ≥ 0.72)
```
