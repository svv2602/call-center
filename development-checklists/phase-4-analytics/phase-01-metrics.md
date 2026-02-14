# Фаза 1: Метрики и дашборды

## Статус
- [ ] Не начата
- [ ] В процессе
- [x] Завершена

**Начата:** 2026-02-14
**Завершена:** 2026-02-14

## Цель фазы

Расширить Prometheus метрики, настроить Grafana дашборды для реального времени и агрегированной аналитики. Настроить алерты.

## Задачи

### 1.0 ОБЯЗАТЕЛЬНО: Анализ и планирование

#### A. Анализ существующего кода
- [x] Проверить существующие метрики Prometheus (из phase-1 MVP logging)
- [x] Изучить требования к метрикам из `doc/development/phase-4-analytics.md`
- [x] Проверить NFR алерты из `doc/technical/nfr.md`

**Команды для поиска:**
```bash
grep -rn "prometheus\|Counter\|Histogram\|Gauge\|Summary" src/
grep -rn "metrics\|METRICS" src/
```

#### B. Анализ зависимостей
- [x] Prometheus уже собирает базовые метрики (из phase-1)
- [x] Нужна Grafana (Docker Compose)
- [x] Нужны агрегированные метрики из PostgreSQL

**Новые абстракции:** Нет
**Новые env variables:** `GRAFANA_URL`, `GRAFANA_ADMIN_PASSWORD`
**Новые tools:** Нет
**Миграции БД:** `005_add_analytics.py` — daily_stats

#### C. Проверка архитектуры
- [x] Prometheus → Grafana для визуализации
- [x] PostgreSQL → Grafana (через datasource) для агрегированных метрик
- [x] Алерты: Alertmanager → Telegram/Slack

**Референс-модуль:** `doc/development/phase-4-analytics.md` — секции 4.1, алерты

**Цель:** Определить полный набор метрик и дашбордов.

**Заметки для переиспользования:** Существующие метрики из phase-1

---

### 1.1 Расширение Prometheus метрик

- [x] Добавить агрегированные метрики (если не созданы в phase-1):
  - `calls_total` (Counter) — общее количество звонков
  - `calls_resolved_by_bot_total` (Counter) — решено ботом
  - `calls_transferred_total` (Counter, labels: reason) — переключения по причинам
  - `orders_created_total` (Counter) — заказы через бота
  - `fittings_booked_total` (Counter) — записи на монтаж
  - `call_cost_usd` (Histogram) — стоимость звонка
- [x] Метрики по сценариям: `call_scenario_total` (Counter, labels: scenario)
- [x] Метрики очереди операторов: `operator_queue_length` (Gauge)

**Файлы:** `src/monitoring/metrics.py`
**Заметки:** calls_total и transfers_to_operator_total уже существовали из phase-1. Добавлены: calls_resolved_by_bot_total, orders_created_total, fittings_booked_total, call_cost_usd, call_scenario_total, operator_queue_length.

---

### 1.2 Docker Compose: Grafana + Prometheus

- [x] Добавить Grafana в docker-compose.yml
- [x] Настроить Prometheus datasource в Grafana (provisioning)
- [x] Настроить PostgreSQL datasource в Grafana
- [x] Добавить Alertmanager для алертов

**Файлы:** `docker-compose.yml`, `grafana/provisioning/`
**Заметки:** Grafana 11.1.0, Prometheus 2.53.0, Alertmanager 0.27.0. Provisioning через YAML-файлы. PostgreSQL datasource с динамическим паролем.

---

### 1.3 Grafana дашборд: реальное время

- [x] Панель: Активные звонки (Gauge)
- [x] Панель: Очередь на оператора (Gauge)
- [x] Панель: Средняя задержка ответа (Graph, p50/p95/p99)
- [x] Панель: STT/LLM/TTS latency breakdown (Stacked graph)
- [x] Панель: Ошибки по типам (Counter rate)
- [x] Панель: Текущая нагрузка (звонков/минуту)

**Файлы:** `grafana/dashboards/realtime.json`
**Заметки:** 6 панелей, uid=callcenter-realtime, refresh=10s, histogram_quantile для percentiles.

---

### 1.4 Grafana дашборд: агрегированная аналитика

- [x] Панель: Звонков за день/неделю/месяц (Bar chart)
- [x] Панель: % решено ботом vs переключено на оператора (Pie)
- [x] Панель: Распределение по сценариям (Pie)
- [x] Панель: Средняя стоимость звонка (Graph)
- [x] Панель: Распределение по времени суток (Heatmap)
- [x] Панель: Заказы и записи на монтаж (Counter)
- [x] Источник: PostgreSQL (таблица daily_stats или прямые запросы к calls)

**Файлы:** `grafana/dashboards/analytics.json`
**Заметки:** 8 панелей, uid=callcenter-analytics, refresh=5m, template variable $time_grouping, PostgreSQL datasource для daily_stats table.

---

### 1.5 Алерты

- [x] Высокий % переключений: >50% за последний час → Telegram
- [x] Ошибки STT/TTS/LLM: >5 за 10 минут → Telegram
- [x] Задержка ответа: p95 > 3 секунды → Prometheus alert
- [x] Нет активных операторов: очередь > 5 → Telegram/SMS
- [x] Аномальный расход API: >200% от среднедневного → Email
- [x] Prompt injection подозрение: аномальные tool calls → Telegram
- [x] Настроить Alertmanager → Telegram webhook

**Файлы:** `prometheus/alerts.yml`, `alertmanager/config.yml`
**Заметки:** 10 alert rules: HighTransferRate, PipelineErrorsHigh, HighResponseLatency, HighSTTLatency, HighLLMLatency, HighTTSLatency, OperatorQueueOverflow, AbnormalAPISpend, SuspiciousToolCalls, CircuitBreakerOpen.

---

## При завершении фазы

1. Убедись, что все задачи отмечены [x]
2. Измени статус фазы: [x] Завершена
3. Заполни дату "Завершена: YYYY-MM-DD"
4. Выполни коммит:
   ```bash
   git add .
   git commit -m "checklist(phase-4-analytics): phase-1 metrics and dashboards completed"
   ```
5. Обнови PROGRESS.md
6. Открой следующую фазу: `phase-02-quality.md`
