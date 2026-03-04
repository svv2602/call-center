# Фаза 1: History & Context

## Проблема 1: History trim before summarize
**Файлы:** `src/agent/agent.py` (lines 191-198), `src/agent/streaming_loop.py` (lines 191-198)

Сейчас history trim (удаление сообщений 2-6) выполняется ДО summarize_old_messages(). Это значит, что ранний контекст разговора (имя клиента, тема, параметры авто) удаляется безвозвратно, а summarize() получает уже обрезанную историю.

**Исправление:** Поменять порядок — сначала summarize, потом trim. summarize() сама заменяет старые сообщения компактным резюме, после чего trim уже не нужен для большинства случаев, но оставляем как safety net.

### Задачи
- [x] **1.1** В `src/agent/agent.py`: переставить блок summarize (line 198) ПЕРЕД блоком trim (lines 191-195)
- [x] **1.2** В `src/agent/streaming_loop.py`: переставить блок summarize (line 198) ПЕРЕД блоком trim (lines 192-195)

## Проблема 2: Pattern search threshold too low
**Файл:** `src/core/pipeline.py` (line 452)

Текущий порог `min_similarity=0.6` слишком низкий — в промпт попадают нерелевантные паттерны, которые путают LLM.

**Исправление:** Поднять порог до 0.72.

### Задачи
- [x] **1.3** В `src/core/pipeline.py`: изменить `min_similarity=0.6` на `min_similarity=0.72` (line 452)
- [x] **1.4** В `src/sandbox/patterns.py`: изменить default параметр `min_similarity: float = 0.75` на `min_similarity: float = 0.72` для consistency

## Проблема 3: Tenant fallback invisible
**Файл:** `src/main.py` (lines 658-673)

При fallback на первый активный тенант (когда нет CALLED_EXTEN) — только DEBUG-лог. В продакшене это незаметно.

**Исправление:** Добавить WARNING-лог при fallback, изменить "Tenant DB lookup failed" с DEBUG на WARNING.

### Задачи
- [x] **1.5** В `src/main.py`: добавить `logger.warning("Tenant fallback to first active tenant: call=%s", channel_uuid)` в блоке else (line 658-668), после SQL-запроса
- [x] **1.6** В `src/main.py`: изменить `logger.debug("Tenant DB lookup failed"...)` на `logger.warning("Tenant DB lookup failed"...)` (line 673)

---

**Коммит:** `fix(agent): summarize before trim, raise pattern threshold, tenant fallback warning`
