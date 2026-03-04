# Phase 01 — DB Pool Consolidation

## Commit message
```
perf: consolidate API router DB engines into shared pool
```

## Tasks

- [x] 1.1 Create `src/api/database.py` with shared `get_engine()` that returns a single `AsyncEngine` (pool_size=5, max_overflow=10, pool_pre_ping=True)
- [x] 1.2 Update all API routers to import from `src.api.database` instead of creating their own engines (analytics, auth, admin_users, customers, export, fitting_hints, knowledge, llm_costs, onec_data, operators, prompts, sandbox, scraper, stt_config, system, tenants, test_phones, training_dialogues, training_safety, training_templates, training_tools, tts_config, vehicles, middleware/audit)
- [x] 1.3 Update `src/main.py` shutdown handler to dispose the shared engine instead of iterating modules
- [x] 1.4 Update the main.py `_db_engine` creation (if any) to use `pool_size=5, max_overflow=10` instead of `max_size=3`
