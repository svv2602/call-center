# Фаза 2: Автоматическая оценка качества

## Статус
- [ ] Не начата
- [ ] В процессе
- [x] Завершена

**Начата:** 2026-02-14
**Завершена:** 2026-02-14

## Цель фазы

Реализовать автоматическую оценку качества каждого звонка через фоновую задачу (Celery). LLM анализирует транскрипцию и выставляет оценки по критериям. Проблемные звонки попадают в очередь на просмотр.

## Задачи

### 2.0 ОБЯЗАТЕЛЬНО: Анализ и планирование

#### A. Анализ существующего кода
- [x] Проверить таблицу calls (поле quality_score)
- [x] Изучить требования к качеству из `doc/development/phase-4-analytics.md`
- [x] Проверить наличие Celery в зависимостях

**Команды для поиска:**
```bash
grep -rn "quality\|score\|celery\|Celery" src/
grep -rn "quality_score" migrations/
```

#### B. Анализ зависимостей
- [x] Celery + Redis (broker) для фоновых задач
- [x] Claude Haiku для экономичной оценки
- [x] Таблица calls.quality_score уже есть

**Новые абстракции:** Нет
**Новые env variables:** `CELERY_BROKER_URL`, `QUALITY_LLM_MODEL`
**Новые tools:** Нет
**Миграции БД:** Расширение calls (если нужны доп. поля для качества)

**Референс-модуль:** `doc/development/phase-4-analytics.md` — секция 4.3

**Цель:** Определить критерии качества и pipeline оценки.

**Заметки для переиспользования:** calls.quality_score FLOAT уже существует в migration 001.

---

### 2.1 Celery setup

- [x] Добавить Celery в зависимости проекта
- [x] Настроить Celery с Redis как broker
- [x] Создать `src/tasks/celery_app.py` — конфигурация Celery
- [x] Добавить Celery worker в docker-compose.yml
- [x] Задача запускается автоматически после завершения звонка

**Файлы:** `src/tasks/celery_app.py`, `docker-compose.yml`
**Заметки:** celery[redis]>=5.4.0 добавлен в pyproject.toml. Celery worker и beat в docker-compose. Redis db=1 для broker (db=0 для sessions).

---

### 2.2 Quality Evaluator

- [x] Создать `src/tasks/quality_evaluator.py`
- [x] Критерии оценки:
  - `bot_greeted_properly` — бот поздоровался
  - `bot_understood_intent` — бот правильно понял намерение
  - `bot_used_correct_tool` — бот вызвал правильный инструмент
  - `bot_provided_accurate_info` — информация корректна
  - `bot_confirmed_before_action` — подтвердил перед оформлением
  - `bot_was_concise` — ответы не слишком длинные
  - `call_resolved_without_human` — решено без оператора
  - `customer_seemed_satisfied` — клиент не выражал недовольства
- [x] LLM вызов: Claude Haiku анализирует транскрипцию
- [x] Результат: оценка 0-1 по каждому критерию + общий score
- [x] Сохранение в calls.quality_score и доп. JSONB поле

**Файлы:** `src/tasks/quality_evaluator.py`
**Заметки:** Claude Haiku для экономии. JSON-ответ парсится, средний score по 8 критериям. Retry до 3 раз при ошибках API.

---

### 2.3 Выявление проблем

- [x] Звонки с quality_score < 0.5 → помечаются для ручного просмотра
- [x] Паттерн: >30% переключений по сценарию за день → алерт
- [x] Частые переспрашивания STT → алерт (проблема с распознаванием)
- [x] Сохранение результатов анализа для аналитики

**Файлы:** `src/tasks/quality_evaluator.py`
**Заметки:** needs_review=True при score < threshold. Алерт HighTransferRate покрывает паттерн переключений. quality_details JSONB сохраняет полную детализацию.

---

### 2.4 Миграция БД для аналитики

- [x] Создать `migrations/versions/005_add_analytics.py`
- [x] Таблица `daily_stats`: stat_date, total_calls, resolved_by_bot, transferred, avg_duration, avg_quality_score, total_cost_usd, scenario_breakdown (JSONB), transfer_reasons (JSONB)
- [x] Доп. поля в calls: quality_details (JSONB) — детализация по критериям
- [x] Cron-задача (Celery beat) для ежедневного расчёта daily_stats

**Файлы:** `migrations/versions/005_add_analytics.py`
**Заметки:** daily_stats с ON CONFLICT upsert. hourly_distribution JSONB добавлен. Celery beat запускает calculate_daily_stats в 01:00 по Киеву.

---

### 2.5 API endpoints для качества

- [x] `GET /analytics/quality` — отчёт по качеству (агрегация quality_score)
- [x] `GET /analytics/calls?quality_below=0.5` — фильтр проблемных звонков
- [x] `GET /analytics/calls/{id}` — детали с quality_details

**Файлы:** `src/api/analytics.py`
**Заметки:** FastAPI router с pagination, фильтрами по scenario/date/quality/transferred. GET /analytics/summary для daily_stats.

---

## При завершении фазы

1. Убедись, что все задачи отмечены [x]
2. Измени статус фазы: [x] Завершена
3. Заполни дату "Завершена: YYYY-MM-DD"
4. Выполни коммит:
   ```bash
   git add .
   git commit -m "checklist(phase-4-analytics): phase-2 quality evaluation completed"
   ```
5. Обнови PROGRESS.md
6. Открой следующую фазу: `phase-03-ab-testing.md`
