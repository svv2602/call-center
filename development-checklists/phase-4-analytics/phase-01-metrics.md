# Фаза 1: Метрики и дашборды

## Статус
- [ ] Не начата
- [ ] В процессе
- [ ] Завершена

**Начата:** -
**Завершена:** -

## Цель фазы

Расширить Prometheus метрики, настроить Grafana дашборды для реального времени и агрегированной аналитики. Настроить алерты.

## Задачи

### 1.0 ОБЯЗАТЕЛЬНО: Анализ и планирование

#### A. Анализ существующего кода
- [ ] Проверить существующие метрики Prometheus (из phase-1 MVP logging)
- [ ] Изучить требования к метрикам из `doc/development/phase-4-analytics.md`
- [ ] Проверить NFR алерты из `doc/technical/nfr.md`

**Команды для поиска:**
```bash
grep -rn "prometheus\|Counter\|Histogram\|Gauge\|Summary" src/
grep -rn "metrics\|METRICS" src/
```

#### B. Анализ зависимостей
- [ ] Prometheus уже собирает базовые метрики (из phase-1)
- [ ] Нужна Grafana (Docker Compose)
- [ ] Нужны агрегированные метрики из PostgreSQL

**Новые абстракции:** Нет
**Новые env variables:** `GRAFANA_URL`, `GRAFANA_ADMIN_PASSWORD`
**Новые tools:** Нет
**Миграции БД:** `005_add_analytics.py` — daily_stats

#### C. Проверка архитектуры
- [ ] Prometheus → Grafana для визуализации
- [ ] PostgreSQL → Grafana (через datasource) для агрегированных метрик
- [ ] Алерты: Alertmanager → Telegram/Slack

**Референс-модуль:** `doc/development/phase-4-analytics.md` — секции 4.1, алерты

**Цель:** Определить полный набор метрик и дашбордов.

**Заметки для переиспользования:** Существующие метрики из phase-1

---

### 1.1 Расширение Prometheus метрик

- [ ] Добавить агрегированные метрики (если не созданы в phase-1):
  - `calls_total` (Counter) — общее количество звонков
  - `calls_resolved_by_bot_total` (Counter) — решено ботом
  - `calls_transferred_total` (Counter, labels: reason) — переключения по причинам
  - `orders_created_total` (Counter) — заказы через бота
  - `fittings_booked_total` (Counter) — записи на монтаж
  - `call_cost_usd` (Histogram) — стоимость звонка
- [ ] Метрики по сценариям: `call_scenario_total` (Counter, labels: scenario)
- [ ] Метрики очереди операторов: `operator_queue_length` (Gauge)

**Файлы:** `src/monitoring/metrics.py`
**Заметки:** -

---

### 1.2 Docker Compose: Grafana + Prometheus

- [ ] Добавить Grafana в docker-compose.yml
- [ ] Настроить Prometheus datasource в Grafana (provisioning)
- [ ] Настроить PostgreSQL datasource в Grafana
- [ ] Добавить Alertmanager для алертов

**Файлы:** `docker-compose.yml`, `grafana/provisioning/`
**Заметки:** -

---

### 1.3 Grafana дашборд: реальное время

- [ ] Панель: Активные звонки (Gauge)
- [ ] Панель: Очередь на оператора (Gauge)
- [ ] Панель: Средняя задержка ответа (Graph, p50/p95/p99)
- [ ] Панель: STT/LLM/TTS latency breakdown (Stacked graph)
- [ ] Панель: Ошибки по типам (Counter rate)
- [ ] Панель: Текущая нагрузка (звонков/минуту)

**Файлы:** `grafana/dashboards/realtime.json`
**Заметки:** -

---

### 1.4 Grafana дашборд: агрегированная аналитика

- [ ] Панель: Звонков за день/неделю/месяц (Bar chart)
- [ ] Панель: % решено ботом vs переключено на оператора (Pie)
- [ ] Панель: Распределение по сценариям (Pie)
- [ ] Панель: Средняя стоимость звонка (Graph)
- [ ] Панель: Распределение по времени суток (Heatmap)
- [ ] Панель: Заказы и записи на монтаж (Counter)
- [ ] Источник: PostgreSQL (таблица daily_stats или прямые запросы к calls)

**Файлы:** `grafana/dashboards/analytics.json`
**Заметки:** -

---

### 1.5 Алерты

- [ ] Высокий % переключений: >50% за последний час → Telegram
- [ ] Ошибки STT/TTS/LLM: >5 за 10 минут → Telegram
- [ ] Задержка ответа: p95 > 3 секунды → Prometheus alert
- [ ] Нет активных операторов: очередь > 5 → Telegram/SMS
- [ ] Аномальный расход API: >200% от среднедневного → Email
- [ ] Prompt injection подозрение: аномальные tool calls → Telegram
- [ ] Настроить Alertmanager → Telegram webhook

**Файлы:** `prometheus/alerts.yml`, `alertmanager/config.yml`
**Заметки:** Список алертов из `doc/development/phase-4-analytics.md`

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
