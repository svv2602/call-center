# P2: Architecture Improvements

## Цель
Стратегические архитектурные улучшения: per-tenant circuit breaker, session persistence, K8s fixes, Redis consolidation, idempotency-key fix, OpenTelemetry, WireGuard, PyJWT.

## Критерии успеха
- [ ] Circuit breaker per-tenant (сбой одного Store API не блокирует всех)
- [ ] Idempotency-Key переиспользуется на retry (нет дублирующих заказов)
- [ ] Session сохраняется в Redis mid-call (recovery при crash)
- [ ] Dual history fix: farewell использует `_llm_history`
- [ ] K8s AudioSocket: ClusterIP (не NodePort)
- [ ] K8s HPA: custom metric `active_calls`
- [ ] Redis: единый shared client (не 20+ независимых)
- [ ] `pytest tests/ -x -q` проходит

## Фазы работы
1. **Store Client** — per-tenant circuit breaker, idempotency-key fix
2. **Session Persistence** — mid-call save в Redis
3. **Pipeline Fixes** — dual history, farewell fix
4. **K8s & Infrastructure** — ClusterIP, HPA custom metrics, WireGuard
5. **Redis & JWT** — shared Redis client, PyJWT migration

## Источник требований
- `audit/prompts/report/02-architecture.md` (ARCH-02, ARCH-03, ARCH-06..10)
- `audit/prompts/report/07-strategic.md` (STR-05, STR-07, STR-10, STR-11)

## Правила переиспользования кода

### Где искать:
```
src/store_client/client.py       # Circuit breaker (module-level), Idempotency-Key
src/core/call_session.py         # Session class, Redis serialization
src/main.py:726-727              # Session save/delete
src/core/pipeline.py:250,655     # _llm_history, farewell
src/api/auth.py:53               # Redis.from_url() пример
src/api/middleware/rate_limit.py  # Ещё один Redis client
k8s/call-processor/service.yml   # NodePort → ClusterIP
k8s/call-processor/hpa.yml       # HPA CPU → custom metric
```

## Начало работы
Для начала или продолжения работы прочитай PROGRESS.md
