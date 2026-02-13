# Фаза 2: Автоматическая оценка качества

## Статус
- [ ] Не начата
- [ ] В процессе
- [ ] Завершена

**Начата:** -
**Завершена:** -

## Цель фазы

Реализовать автоматическую оценку качества каждого звонка через фоновую задачу (Celery). LLM анализирует транскрипцию и выставляет оценки по критериям. Проблемные звонки попадают в очередь на просмотр.

## Задачи

### 2.0 ОБЯЗАТЕЛЬНО: Анализ и планирование

#### A. Анализ существующего кода
- [ ] Проверить таблицу calls (поле quality_score)
- [ ] Изучить требования к качеству из `doc/development/phase-4-analytics.md`
- [ ] Проверить наличие Celery в зависимостях

**Команды для поиска:**
```bash
grep -rn "quality\|score\|celery\|Celery" src/
grep -rn "quality_score" migrations/
```

#### B. Анализ зависимостей
- [ ] Celery + Redis (broker) для фоновых задач
- [ ] Claude Haiku для экономичной оценки
- [ ] Таблица calls.quality_score уже есть

**Новые абстракции:** Нет
**Новые env variables:** `CELERY_BROKER_URL`, `QUALITY_LLM_MODEL`
**Новые tools:** Нет
**Миграции БД:** Расширение calls (если нужны доп. поля для качества)

**Референс-модуль:** `doc/development/phase-4-analytics.md` — секция 4.3

**Цель:** Определить критерии качества и pipeline оценки.

**Заметки для переиспользования:** -

---

### 2.1 Celery setup

- [ ] Добавить Celery в зависимости проекта
- [ ] Настроить Celery с Redis как broker
- [ ] Создать `src/tasks/celery_app.py` — конфигурация Celery
- [ ] Добавить Celery worker в docker-compose.yml
- [ ] Задача запускается автоматически после завершения звонка

**Файлы:** `src/tasks/celery_app.py`, `docker-compose.yml`
**Заметки:** -

---

### 2.2 Quality Evaluator

- [ ] Создать `src/tasks/quality_evaluator.py`
- [ ] Критерии оценки:
  - `bot_greeted_properly` — бот поздоровался
  - `bot_understood_intent` — бот правильно понял намерение
  - `bot_used_correct_tool` — бот вызвал правильный инструмент
  - `bot_provided_accurate_info` — информация корректна
  - `bot_confirmed_before_action` — подтвердил перед оформлением
  - `bot_was_concise` — ответы не слишком длинные
  - `call_resolved_without_human` — решено без оператора
  - `customer_seemed_satisfied` — клиент не выражал недовольства
- [ ] LLM вызов: Claude Haiku анализирует транскрипцию
- [ ] Результат: оценка 0-1 по каждому критерию + общий score
- [ ] Сохранение в calls.quality_score и доп. JSONB поле

**Файлы:** `src/tasks/quality_evaluator.py`
**Заметки:** Claude Haiku для экономии

---

### 2.3 Выявление проблем

- [ ] Звонки с quality_score < 0.5 → помечаются для ручного просмотра
- [ ] Паттерн: >30% переключений по сценарию за день → алерт
- [ ] Частые переспрашивания STT → алерт (проблема с распознаванием)
- [ ] Сохранение результатов анализа для аналитики

**Файлы:** `src/tasks/quality_evaluator.py`
**Заметки:** -

---

### 2.4 Миграция БД для аналитики

- [ ] Создать `migrations/versions/005_add_analytics.py`
- [ ] Таблица `daily_stats`: stat_date, total_calls, resolved_by_bot, transferred, avg_duration, avg_quality_score, total_cost_usd, scenario_breakdown (JSONB), transfer_reasons (JSONB)
- [ ] Доп. поля в calls: quality_details (JSONB) — детализация по критериям
- [ ] Cron-задача (Celery beat) для ежедневного расчёта daily_stats

**Файлы:** `migrations/versions/005_add_analytics.py`
**Заметки:** -

---

### 2.5 API endpoints для качества

- [ ] `GET /analytics/quality` — отчёт по качеству (агрегация quality_score)
- [ ] `GET /analytics/calls?quality_below=0.5` — фильтр проблемных звонков
- [ ] `GET /analytics/calls/{id}` — детали с quality_details

**Файлы:** `src/api/routes.py` или `src/store_client/client.py`
**Заметки:** -

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
