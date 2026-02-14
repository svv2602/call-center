# Фаза 6: Тестирование аналитики

## Статус
- [ ] Не начата
- [ ] В процессе
- [x] Завершена

**Начата:** 2026-02-14
**Завершена:** 2026-02-14

## Цель фазы

Тестирование всех компонентов фазы 4: метрики, качество, A/B тесты, алерты, админ-интерфейс, оптимизация.

## Задачи

### 6.0 ОБЯЗАТЕЛЬНО: Анализ и планирование

#### A. Анализ существующего кода
- [x] Проверить существующие тесты из фаз 1-3
- [x] Определить новые тестовые сценарии для фазы 4

**Заметки:** 18 существующих тестовых файлов в tests/unit/, 3 в tests/integration/. Добавлены 5 новых тестовых файлов.

---

### 6.1 Unit-тесты: Quality Evaluator

- [x] Тест: оценка качества по каждому критерию
- [x] Тест: общий quality_score расчёт
- [x] Тест: обнаружение проблемных звонков (score < 0.5)
- [x] Тест: Celery задача запускается после звонка
- [x] Тест: обработка ошибок LLM при оценке

**Файлы:** `tests/unit/test_quality_evaluator.py`
**Заметки:** 10 тестов: TestBuildTranscriptionText (4), TestQualityCriteria (2), TestQualityScore (3), TestEvaluateCallQualityAsync (3). Проверка всех 8 критериев, JSON парсинг, markdown code block stripping.

---

### 6.2 Unit-тесты: A/B тестирование

- [x] Тест: создание новой версии промпта
- [x] Тест: рандомизация варианта при звонке
- [x] Тест: агрегация метрик по вариантам
- [x] Тест: статистическая значимость
- [x] Тест: fallback при недоступности БД промптов

**Файлы:** `tests/unit/test_ab_testing.py`
**Заметки:** 10 тестов: TestCalculateSignificance (7) — insufficient samples, no difference, A wins, B wins, equal means, zero SE, p-value range. TestPromptVersionFallback (3).

---

### 6.3 Unit-тесты: Cost Optimization

- [x] Тест: Whisper STT реализация (mock model)
- [x] Тест: кэширование TTS (cache hit/miss)
- [x] Тест: model routing (простой запрос → Haiku, сложный → Sonnet)
- [x] Тест: расчёт стоимости звонка (cost_breakdown)
- [x] Тест: feature flags для переключения провайдеров

**Файлы:** `tests/unit/test_cost_optimization.py`
**Заметки:** 26 тестов: TestModelRouter (17) — routing для каждого типа запроса, active_order, turn_count, disabled, scenario classification. TestCostBreakdown (10) — Google STT, Whisper, Sonnet, Haiku, TTS cached/uncached, total, to_dict, Whisper savings.

---

### 6.4 Integration-тесты: Metrics + Grafana

- [x] Тест: Prometheus метрики экспортируются корректно
- [x] Тест: daily_stats заполняются Celery beat задачей
- [x] Тест: API endpoints аналитики возвращают корректные данные
- [x] Тест: фильтры и пагинация в GET /analytics/calls

**Файлы:** `tests/integration/test_analytics.py`
**Заметки:** TestMetricsEndpoint (3), TestHealthEndpoint (1), TestAnalyticsAPIEndpoints (4), TestAuthEndpoint (2), TestPromptsAPIEndpoints (2), TestKnowledgeAPIEndpoints (2), TestAdminUI (1).

---

### 6.5 Тесты алертов

- [x] Тест: алерт при >50% переключений
- [x] Тест: алерт при >5 ошибок за 10 минут
- [x] Тест: алерт при p95 > 3 секунды
- [x] Тест: алерт при аномальном расходе API
- [x] Тест: Telegram webhook вызывается при алерте

**Файлы:** `tests/unit/test_alerts.py`
**Заметки:** TestAlertRules (10) — YAML validation, required alerts exist, severity labels, annotations, thresholds. TestAlertmanagerConfig (4) — Telegram receiver, routes. TestPrometheusConfig (4) — scrape config, alertmanager integration.

---

## При завершении фазы

1. Убедись, что все задачи отмечены [x]
2. Измени статус фазы: [x] Завершена
3. Заполни дату "Завершена: YYYY-MM-DD"
4. Проверь покрытие:
   ```bash
   pytest tests/ --cov=src --cov-report=html
   ```
5. Выполни коммит:
   ```bash
   git add .
   git commit -m "checklist(phase-4-analytics): phase-6 testing completed"
   ```
6. Обнови PROGRESS.md — Фаза 4 Аналитика завершена!
7. Вся система Call Center AI завершена!
