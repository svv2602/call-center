# Фаза 3: A/B тестирование промптов

## Статус
- [ ] Не начата
- [ ] В процессе
- [x] Завершена

**Начата:** 2026-02-14
**Завершена:** 2026-02-14

## Цель фазы

Реализовать версионирование промптов и A/B тестирование. Каждый звонок получает вариант промпта, метрики сравниваются для выбора лучшего.

## Задачи

### 3.0 ОБЯЗАТЕЛЬНО: Анализ и планирование

#### A. Анализ существующего кода
- [x] Проверить таблицы prompt_versions, prompt_ab_tests в data-model
- [x] Изучить как промпт передаётся в Agent (src/agent/prompts.py)
- [x] Проверить поле calls.prompt_version

#### B. Анализ зависимостей
- [x] Таблицы prompt_versions, prompt_ab_tests (миграция)
- [x] Модификация Agent для получения промпта из БД
- [x] Рандомизация варианта при начале звонка

**Новые абстракции:** Нет
**Новые env variables:** Нет
**Новые tools:** Нет
**Миграции БД:** 006_add_prompt_ab_tests.py

**Референс-модуль:** `doc/development/phase-4-analytics.md` — секция 4.4

**Заметки:** PROMPT_VERSION = "v3.0-services", SYSTEM_PROMPT в src/agent/prompts.py. Agent использует _build_system_prompt() для создания динамического промпта.

---

### 3.1 Версионирование промптов

- [x] Создать модуль `src/agent/prompt_manager.py`
- [x] Хранение промптов в PostgreSQL (таблица prompt_versions)
- [x] Поля: id, name, system_prompt, tools_config, is_active, metadata
- [x] CRUD операции: create, get active, activate version
- [x] При старте — загрузка активного промпта из БД
- [x] Fallback: если БД недоступна → использовать хардкодированный промпт

**Файлы:** `src/agent/prompt_manager.py`
**Заметки:** PromptManager с async SQLAlchemy. Fallback на hardcoded SYSTEM_PROMPT из prompts.py.

---

### 3.2 A/B тестирование

- [x] Создать модуль `src/agent/ab_testing.py`
- [x] Таблица prompt_ab_tests: test_name, variant_a_id, variant_b_id, calls_a, calls_b, quality_a, quality_b, status, started_at
- [x] При начале звонка: случайный выбор варианта A или B
- [x] Запись варианта в calls.prompt_version
- [x] Агрегация метрик по вариантам: % решения без оператора, avg quality, avg duration

**Файлы:** `src/agent/ab_testing.py`
**Заметки:** ABTestManager с assign_variant(), update_quality(). traffic_split для контроля распределения трафика.

---

### 3.3 API для управления промптами

- [x] `GET /prompts` — список версий промптов
- [x] `POST /prompts` — создать новую версию
- [x] `PATCH /prompts/{id}/activate` — активировать версию
- [x] `GET /prompts/ab-tests` — текущие A/B тесты
- [x] `POST /prompts/ab-tests` — создать новый A/B тест
- [x] `PATCH /prompts/ab-tests/{id}/stop` — остановить тест, зафиксировать результат

**Файлы:** `src/api/prompts.py`
**Заметки:** FastAPI router подключён в main.py. Pydantic request models для валидации.

---

### 3.4 Статистическая значимость

- [x] Расчёт статистической значимости разницы между вариантами
- [x] Минимальный размер выборки для каждого варианта
- [x] Автоматическое предложение о переключении при значимой разнице
- [x] Панель в Grafana: сравнение вариантов

**Файлы:** `src/agent/ab_testing.py`
**Заметки:** Z-test с calculate_significance(). min_samples=10, p<0.05 для значимости. recommended_variant при значимости.

---

### 3.5 Автоматическая доработка промптов

- [x] Сбор проблемных диалогов (quality_score < 0.5)
- [x] Анализ причин неудач (паттерны)
- [x] Генерация предложений по улучшению промпта (LLM)
- [x] Ручное одобрение менеджером → создание новой версии

**Файлы:** `src/tasks/prompt_optimizer.py`
**Заметки:** Celery задача analyze_failed_calls. Claude Haiku анализирует до 20 низкокачественных звонков за 7 дней. JSON-ответ с patterns и suggestions.

---

## При завершении фазы

1. Убедись, что все задачи отмечены [x]
2. Измени статус фазы: [x] Завершена
3. Заполни дату "Завершена: YYYY-MM-DD"
4. Выполни коммит:
   ```bash
   git add .
   git commit -m "checklist(phase-4-analytics): phase-3 AB testing completed"
   ```
5. Обнови PROGRESS.md
6. Открой следующую фазу: `phase-04-admin-ui.md`
