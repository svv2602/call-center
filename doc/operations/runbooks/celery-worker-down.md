# Runbook: Celery Worker не отвечает

**Дата создания:** 2026-02-14  
**Владелец:** DevOps / SRE  
**Приоритет:** HIGH  

## Описание

Celery workers обрабатывают асинхронные задачи: запись логов, отправка уведомлений, аналитика, создание отчётов. Если worker'ы не отвечают, накапливается очередь задач (RabbitMQ / Redis), и system метрики начинают расти. Это может привести к потере данных и проблемам с аналитикой.

## Симптомы

- Alert в Prometheus: `celery_worker_status = 0` (не доступен)
- Alert: `celery_queue_length > 100` (очередь растёт)
- Команда `docker compose ps` показывает, что контейнер `celery-worker` не запущен или в состоянии `Restarting`
- Логи Call Processor: `WARNING: Unable to reach Celery worker` или `celery task timeout`
- Метрика Prometheus: `celery_worker_pool_size = 0`

## Диагностика

### 1. Проверить статус контейнера

```bash
# Список контейнеров
docker compose ps celery-worker

# Ожидаемый статус: "Up" или похожий, НЕ "Exited" или "Restarting"
```

### 2. Проверить logs Celery Worker

```bash
# Последние 50 строк логов
docker compose logs --tail=50 celery-worker

# Последние логи с временными метками
docker compose logs --timestamps --since 10m celery-worker

# Поиск ошибок
docker compose logs celery-worker | grep -i "error\|critical\|failed"
```

### 3. Проверить здоровье Celery Worker через API

```bash
# Health check endpoint
curl -s http://localhost:8080/health/celery | jq '.'

# Ожидаемый ответ: {"status": "healthy", "worker_count": 2, "queue_length": 5}
```

### 4. Проверить очередь задач

```bash
# Если используется RabbitMQ
curl -s -u guest:guest http://localhost:15672/api/queues/%2F | jq '.[] | {name: .name, messages: .messages}'

# Если используется Redis
redis-cli LLEN celery

# Проверить состояние Redis
redis-cli ping
```

### 5. Проверить активные воркеры

```bash
# Список активных воркеров через Celery CLI (если доступен)
docker exec call-center-celery-worker-1 celery -A src.tasks.celery_app inspect active

# Или через API
curl -s http://localhost:8080/health/celery/workers | jq '.'
```

### 6. Проверить системные ресурсы

```bash
# CPU и память контейнера
docker compose stats celery-worker --no-stream

# Проверить лимиты
docker inspect call-center-celery-worker-1 | grep -A 10 "MemoryLimit\|CpuShares"
```

### 7. Проверить RabbitMQ (если используется)

```bash
# Статус RabbitMQ
curl -s -u guest:guest http://localhost:15672/api/overview | jq '{status: .status, messages: .queue_totals}'

# Проверить очереди
curl -s -u guest:guest http://localhost:15672/api/queues | jq '.[] | {name, messages}'

# Логи RabbitMQ
docker compose logs --tail=30 rabbitmq
```

## Решение

### Шаг 1: Перезагрузить Celery Worker

```bash
# Мягкая перезагрузка (graceful restart)
docker compose restart celery-worker

# Дождаться, пока контейнер запустится
sleep 5
docker compose ps celery-worker

# Проверить логи после перезагрузки
docker compose logs --tail=20 celery-worker
```

### Шаг 2: Если контейнер всё ещё не запускается

```bash
# Проверить более подробно логи ошибки
docker compose logs celery-worker 2>&1 | tail -100

# Выискать специфичную ошибку:
# - ModuleNotFoundError: проблема с import'ами
# - ConnectionError: проблема с RabbitMQ/Redis
# - MemoryError: недостаточно памяти
# - PermissionError: проблема с доступом к файлам
```

### Шаг 3: Если проблема в подключении к брокеру

```bash
# Проверить RabbitMQ (если используется)
docker compose ps rabbitmq
docker compose logs --tail=20 rabbitmq

# Перезагрузить RabbitMQ
docker compose restart rabbitmq

# Проверить Redis (если используется как брокер)
docker compose ps redis
redis-cli ping

# Перезагрузить Redis
docker compose restart redis

# После перезагрузки брокера:
docker compose restart celery-worker
```

### Шаг 4: Если проблема в ресурсах

```bash
# Проверить использование памяти
docker compose stats celery-worker

# Если используется близко к лимиту, увеличить лимит
# В docker-compose.yml:
# celery-worker:
#   deploy:
#     resources:
#       limits:
#         memory: 2G  (вместо 1G)

# Пересобрать и перезагрузить
docker compose up -d

# Проверить статус
docker compose ps celery-worker
```

### Шаг 5: Если проблема в коде Celery worker

```bash
# Проверить, нет ли синтаксических ошибок
docker compose logs celery-worker | grep -i "syntax\|import\|module"

# Если есть ошибка импорта:
# 1. Проверить, что все зависимости установлены
# 2. Пересобрать Docker image
docker compose build celery-worker

# 3. Перезагрузить
docker compose up -d celery-worker
```

### Шаг 6: Если очередь переполнена

```bash
# Проверить размер очереди
redis-cli LLEN celery

# Если очередь очень большая (> 1000), может быть нужно очистить:
# ВНИМАНИЕ: Это потеряет все незавершённые задачи!
redis-cli DEL celery

# Проверить, что очередь очищена
redis-cli LLEN celery

# Перезагрузить worker
docker compose restart celery-worker
```

### Шаг 7: Проверить метрики после восстановления

```bash
# Проверить, что worker восстановился
curl -s http://localhost:8080/health/celery | jq '.status'

# Проверить, что очередь обрабатывается
curl -s "http://localhost:9090/api/v1/query?query=celery_queue_length" | jq '.data.result'

# Проверить метрику worker'ов
curl -s "http://localhost:9090/api/v1/query?query=celery_worker_pool_size" | jq '.data.result'
```

## Эскалация

### Если проблема не решена после 10 минут:

1. **DevOps Engineer**
   - Проверить Docker daemon health
   - Проверить disk space (может быть полный диск)
   - Может потребоваться перезагрузка Docker демона
   - Проверить логи ОС (systemd journal)

2. **Backend / Celery Owner**
   - Может быть deadlock в коде Celery задач
   - Проверить логику обработки задач
   - Может потребоваться code-level debugging
   - Может потребоваться профилирование памяти

3. **Platform / Infrastructure Lead**
   - Может потребоваться увеличение ресурсов (CPU/RAM)
   - Может потребоваться добавить больше worker'ов
   - Может потребоваться изменить стратегию обработки задач

4. **SRE Lead**
   - Активировать инцидент в PagerDuty
   - Может потребоваться failover на backup систему
   - Может требуется manual intervention на production

### Контакты эскалации

- **Celery Owner / Backend Lead:** @backend-lead
- **DevOps On-Call:** @devops-oncall
- **Infrastructure Lead:** @infra-lead
- **SRE Lead:** @sre-lead

