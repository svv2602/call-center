# Фаза 2: Observability

## Статус
- [x] Не начата
- [x] В процессе
- [x] Завершена

**Начата:** 2026-02-14
**Завершена:** 2026-02-14

## Цель фазы
Экспортировать Grafana дашборды в JSON и хранить в репозитории. Обеспечить reproducibility и version control для мониторинга. После этой фазы дашборды можно восстановить из кода, а изменения отслеживаются через git.

## Задачи

### 2.0 ОБЯЗАТЕЛЬНО: Анализ и планирование

#### A. Анализ существующего кода
- [x] Изучить `grafana/` — текущая конфигурация Prometheus, Grafana (путь `grafana/`, не `monitoring/`)
- [x] Изучить `docker-compose*.yml` — как подключены сервисы мониторинга
- [x] Изучить `src/monitoring/metrics.py` — все зарегистрированные метрики
- [x] Определить какие дашборды нужны (system, application, business)

**Команды для поиска:**
```bash
# Мониторинг
ls grafana/
# Метрики
grep -rn "Counter\|Gauge\|Histogram\|Summary" src/monitoring/metrics.py
# Docker compose
grep -n "grafana\|prometheus" docker-compose*.yml
```

#### B. Анализ зависимостей
- [x] Нужны ли новые абстракции (Protocol)? — Нет
- [x] Нужны ли новые env variables? — Нет (Grafana provisioning через volume mount)
- [x] Нужны ли миграции БД? — Нет

**Новые env variables:** нет
**Миграции БД:** нет

#### C. Проверка архитектуры
- [x] Grafana provisioning: dashboards в `grafana/dashboards/`
- [x] Provisioning config в `grafana/provisioning/dashboards/dashboards.yml`
- [x] Docker compose volume mount для автоматической загрузки

**Референс-модуль:** `grafana/` (существующая структура)

**Цель:** Понять существующие паттерны проекта ПЕРЕД написанием кода.

**Заметки для переиспользования:** Проект использует `grafana/` (не `monitoring/grafana/`). Существующие дашборды: realtime.json, analytics.json, operators.json. Provisioning и volume mounts уже настроены в docker-compose.yml.

---

### 2.1 Grafana provisioning configuration

- [x] Создать `grafana/provisioning/dashboards/dashboards.yml` — уже существует
- [x] Убедиться что `grafana/provisioning/datasources/` настроен на Prometheus — настроен (Prometheus + PostgreSQL)
- [x] Обновить `docker-compose*.yml` — volume mount уже настроены (./grafana/provisioning, ./grafana/dashboards)
- [x] Проверить что Grafana автоматически загружает дашборды при старте — provisioning config указывает на /var/lib/grafana/dashboards

**Файлы:** `grafana/provisioning/`, `docker-compose.yml`
**Заметки:** Provisioning и volume mounts были настроены ранее в production-readiness чеклисте.

---

### 2.2 Создание дашбордов

- [x] **System Overview** (`grafana/dashboards/system-overview.json`):
  - CPU, Memory, Disk (gauge panels с thresholds)
  - Network I/O (timeseries: receive + transmit)
  - PostgreSQL connections, query latency (p95)
  - Redis memory, connections, ops/sec
- [x] **Application Metrics** (`grafana/dashboards/application-metrics.json`):
  - Active calls (stat), call rate (stat)
  - Call duration (histogram p50/p95)
  - STT/LLM/TTS latency (p50, p95, p99) — 3 отдельных timeseries
  - Circuit breaker state (stat с value mappings)
  - Store API errors by status_code
  - WebSocket connections (stat)
  - Rate limit events (timeseries)
  - TTS cache hit rate (gauge)
  - Backup status (stat by component)
- [x] **Business Dashboard** (`grafana/dashboards/business-dashboard.json`):
  - Calls today (stat), bot resolution rate (gauge)
  - Orders created, fittings booked (stat)
  - Calls per hour (timeseries)
  - Resolution: Bot vs Operator (piechart donut)
  - Tool usage distribution (piechart by tool_name)
  - Transfer reasons (barchart by reason)
  - Average call cost (timeseries)
  - Scenario distribution (piechart by scenario)

**Файлы:** `grafana/dashboards/`
**Заметки:** Все дашборды — валидный JSON, schemaVersion 39, datasource uid = "prometheus".

---

## При завершении фазы

Выполни следующие действия:

1. Убедись, что все задачи отмечены [x]
2. Измени статус фазы:
   - [x] Завершена
3. Заполни дату "Завершена: YYYY-MM-DD"
4. Выполни коммит:
   ```bash
   git add .
   git commit -m "checklist(post-production-improvements): phase-2 observability completed"
   ```
5. Обнови PROGRESS.md:
   - Текущая фаза: 3
   - Добавь запись в историю
6. Открой следующую фазу и продолжи работу
