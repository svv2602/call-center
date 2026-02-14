# Runbooks (Процедуры реагирования на инциденты)

Этот каталог содержит **runbook'и** — процедуры пошагового реагирования на операционные инциденты. Каждый runbook имеет структуру:

- **Описание** — что это и почему это важно
- **Симптомы** — как узнать, что есть проблема
- **Диагностика** — команды для выявления корневой причины
- **Решение** — пошаговые действия для восстановления
- **Эскалация** — когда и к кому обращаться, если не помогает

## Список runbook'ов

### 1. **circuit-breaker-open.md** — Открытый circuit breaker Store API
**Приоритет:** HIGH | **Владелец:** DevOps / SRE

Store API circuit breaker открылся (fail_max=5 ошибок подряд). Agent не может обрабатывать запросы поиска, проверки наличия, создания заказов.

**Быстрое решение:**
```bash
docker compose restart call-processor  # Reset breaker
```

---

### 2. **high-transfer-rate.md** — Высокий процент передач оператору (>50% за час)
**Приоритет:** MEDIUM | **Владелец:** Product / Analytics

Более 50% звонков передаётся оператору. Указывает на проблемы с STT, LLM, поиском или бронированием.

**Быстрая диагностика:**
```bash
# Проверить метрику
curl -s "http://localhost:9090/api/v1/query?query=call_transfer_rate%7Binterval%3D%221h%22%7D"

# Проверить логи
docker compose logs --timestamps --since 1h call-processor | head -50
```

---

### 3. **high-latency.md** — Высокая задержка (p95 > 2.5s)
**Приоритет:** HIGH | **Владелец:** DevOps / Performance Engineering

95-й percentile ответа превышает 2.5 секунды. Может быть в STT, LLM, TTS или Store API.

**Быстрая диагностика:**
```bash
# Сравнить latencies
curl -s "http://localhost:9090/api/v1/query?query=histogram_quantile(0.95,stt_latency_seconds)"
curl -s "http://localhost:9090/api/v1/query?query=histogram_quantile(0.95,llm_latency_seconds)"
curl -s "http://localhost:9090/api/v1/query?query=histogram_quantile(0.95,tts_latency_seconds)"
```

---

### 4. **celery-worker-down.md** — Celery Worker не отвечает
**Приоритет:** HIGH | **Владелец:** DevOps / SRE

Celery worker не обрабатывает асинхронные задачи (логи, аналитика, уведомления). Очередь растёт.

**Быстрое решение:**
```bash
docker compose restart celery-worker

# Проверить health
curl -s http://localhost:8080/health/celery
```

---

### 5. **operator-queue-overflow.md** — Переполнение очереди операторов (>5)
**Приоритет:** MEDIUM | **Владелец:** Operations / Contact Center Manager

Более 5 звонков ждут оператора. Время ожидания растёт, клиенты недовольны.

**Быстрое действие:**
```bash
# Проверить очередь и операторов
curl -s http://localhost:8080/health/operator-queue
curl -s http://localhost:8080/api/v1/operators/online

# Активировать резервных операторов (через UI или API)
```

---

### 6. **backup-failed.md** — Резервная копия не удалась или проверка не пройдена
**Приоритет:** CRITICAL | **Владелец:** DevOps / Database Administrator

Ежедневный backup не выполнен или не прошёл проверку целостности. Система не защищена от потери данных.

**Быстрое решение:**
```bash
# Проверить место на диске
df -h /backups

# Запустить backup вручную
docker exec call-center-backup-service backup-now

# Проверить целостность
call-center-admin db verify-backup /backups/latest.sql.gz
```

---

## Как использовать runbook'и

### При получении Alert'а:

1. **Прочитайте Симптомы** — убедитесь, что это правильный runbook
2. **Следуйте Диагностике** — выполните команды для выявления причины
3. **Действуйте по Решению** — применяйте шаги в порядке
4. **Проверяйте результаты** — убедитесь, что проблема решена
5. **Эскалируйте при необходимости** — если не помогает, привлеките специалиста

### Пример сценария:

```
Alert: "call_transfer_rate > 0.5"
           ↓
Открыть: high-transfer-rate.md
           ↓
Прочитать Симптомы ✓
           ↓
Выполнить Диагностику (команды bash)
           ↓
Определить причину (STT/LLM/Search)
           ↓
Следовать пошаговому Решению
           ↓
Проверить метрики после fix'а
           ↓
Если всё ещё > 50% → Эскалировать
```

---

## Общие команды диагностики

```bash
# Статус контейнеров
docker compose ps

# Логи компонента (последние 50 строк)
docker compose logs --tail=50 call-processor

# Временные метки в логах (последний час)
docker compose logs --timestamps --since 1h call-processor

# Очищенные логи (без timestamp'ов)
docker compose logs call-processor | tail -100

# Health check'и
curl -s http://localhost:8080/health | jq '.'
curl -s http://localhost:8080/health/celery
curl -s http://localhost:8080/health/operator-queue

# Prometheus queries (примеры)
curl -s "http://localhost:9090/api/v1/query?query=up" | jq '.data.result'

# Redis
redis-cli ping
redis-cli INFO memory | head -10

# PostgreSQL
pg_isready -h localhost -p 5432
```

---

## Контакты эскалации

| Роль | Slack | Номер |
|------|-------|-------|
| **DevOps On-Call** | @devops-oncall | +38 (050) XXX-XX-XX |
| **SRE Lead** | @sre-lead | +38 (050) XXX-XX-XX |
| **Backend Lead** | @backend-lead | +38 (050) XXX-XX-XX |
| **Contact Center Manager** | @cc-manager-oncall | +38 (050) XXX-XX-XX |
| **CTO (CRITICAL)** | @cto | +38 (050) XXX-XX-XX |

---

## Метрики и Thresholds

| Метрика | Yellow | Red | Runbook |
|---------|--------|-----|---------|
| `store_api_circuit_breaker_state` | OPEN | — | circuit-breaker-open |
| `call_transfer_rate (1h)` | > 0.4 | > 0.5 | high-transfer-rate |
| `call_latency_p95_seconds` | > 2.0 | > 2.5 | high-latency |
| `celery_worker_status` | degraded | down | celery-worker-down |
| `operator_queue_length` | 3 | > 5 | operator-queue-overflow |
| `backup_status` | warning | failed | backup-failed |

---

## Дата создания

**2026-02-14** — Первая версия runbook'ов для production monitoring

## Версионирование

При обновлении runbook'а добавляйте запись в конец файла:

```markdown
### v2.0 (2026-03-01)
- Added new diagnostic step for Redis Cluster
- Updated escalation contacts
```

