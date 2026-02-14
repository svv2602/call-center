# Фаза 2: Observability

## Статус
- [ ] Не начата
- [ ] В процессе
- [ ] Завершена

**Начата:** -
**Завершена:** -

## Цель фазы
Экспортировать Grafana дашборды в JSON и хранить в репозитории. Обеспечить reproducibility и version control для мониторинга. После этой фазы дашборды можно восстановить из кода, а изменения отслеживаются через git.

## Задачи

### 2.0 ОБЯЗАТЕЛЬНО: Анализ и планирование

#### A. Анализ существующего кода
- [ ] Изучить `monitoring/` — текущая конфигурация Prometheus, Grafana
- [ ] Изучить `docker-compose*.yml` — как подключены сервисы мониторинга
- [ ] Изучить `src/monitoring/metrics.py` — все зарегистрированные метрики
- [ ] Определить какие дашборды нужны (system, application, business)

**Команды для поиска:**
```bash
# Мониторинг
ls monitoring/
# Метрики
grep -rn "Counter\|Gauge\|Histogram\|Summary" src/monitoring/metrics.py
# Docker compose
grep -n "grafana\|prometheus" docker-compose*.yml
```

#### B. Анализ зависимостей
- [ ] Нужны ли новые абстракции (Protocol)? — Нет
- [ ] Нужны ли новые env variables? — Нет (Grafana provisioning через volume mount)
- [ ] Нужны ли миграции БД? — Нет

**Новые env variables:** нет
**Миграции БД:** нет

#### C. Проверка архитектуры
- [ ] Grafana provisioning: dashboards в `monitoring/grafana/dashboards/`
- [ ] Provisioning config в `monitoring/grafana/provisioning/dashboards/`
- [ ] Docker compose volume mount для автоматической загрузки

**Референс-модуль:** `monitoring/` (существующая структура)

**Цель:** Понять существующие паттерны проекта ПЕРЕД написанием кода.

**Заметки для переиспользования:** -

---

### 2.1 Grafana provisioning configuration

- [ ] Создать `monitoring/grafana/provisioning/dashboards/default.yml` — datasource provisioning config
- [ ] Убедиться что `monitoring/grafana/provisioning/datasources/` настроен на Prometheus
- [ ] Обновить `docker-compose*.yml` — volume mount для dashboards и provisioning
- [ ] Проверить что Grafana автоматически загружает дашборды при старте

**Файлы:** `monitoring/grafana/provisioning/`, `docker-compose.yml`
**Заметки:** -

---

### 2.2 Создание дашбордов

- [ ] **System Overview** (`monitoring/grafana/dashboards/system-overview.json`):
  - CPU, Memory, Disk, Network
  - Container metrics (Docker)
  - PostgreSQL connections, query latency
  - Redis memory, connections, ops/sec
- [ ] **Application Metrics** (`monitoring/grafana/dashboards/application-metrics.json`):
  - Active calls (gauge)
  - Call duration (histogram)
  - STT/TTS/LLM latency (p50, p95, p99)
  - Error rates по компонентам
  - Circuit breaker state
  - WebSocket connections
  - Rate limit events
- [ ] **Business Dashboard** (`monitoring/grafana/dashboards/business-dashboard.json`):
  - Calls per hour/day
  - Resolution rate (AI vs operator transfer)
  - Tool usage distribution
  - Average quality score
  - Top reasons for operator transfer

**Файлы:** `monitoring/grafana/dashboards/`
**Заметки:** Каждый дашборд — отдельный JSON файл. Использовать переменные для datasource.

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
