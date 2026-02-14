# Runbook: Переполнение очереди операторов (>5)

**Дата создания:** 2026-02-14  
**Владелец:** Operations / Contact Center Manager  
**Приоритет:** MEDIUM  

## Описание

Очередь операторов содержит звонки, ожидающие передачи реальному сотруднику. Когда количество вызовов в очереди превышает 5, это указывает на недостаток операторского покрытия или проблемы с системой распределения звонков (router). Большая очередь приводит к увеличению времени ожидания (queue wait time) и снижению CSAT.

## Симптомы

- Alert в Prometheus: `operator_queue_length > 5`
- Alert в Grafana: "Operator Queue Status"
- Метрика `call_queue_wait_time_seconds` растёт выше 30 сек
- Клиенты слышат долгое ожидание на hold'е
- Logи Call Processor: `WARNING: Queue length exceeded` или `ALERT: Operator queue critical`
- Метрика `transfer_to_operator_success_rate` может быть низкой (timeout'ы)

## Диагностика

### 1. Проверить текущую длину очереди

```bash
# Получить текущее значение очереди
curl -s http://localhost:8080/health/operator-queue | jq '.queue_length'

# Или через Prometheus
curl -s "http://localhost:9090/api/v1/query?query=operator_queue_length" | jq '.data.result'

# Проверить максимум за час
curl -s "http://localhost:9090/api/v1/query?query=max_over_time(operator_queue_length%5B1h%5D)" | jq '.data.result'
```

### 2. Проверить время ожидания в очереди

```bash
# Среднее время ожидания
curl -s "http://localhost:9090/api/v1/query?query=avg(call_queue_wait_time_seconds)" | jq '.data.result'

# p95 время ожидания
curl -s "http://localhost:9090/api/v1/query?query=histogram_quantile(0.95,call_queue_wait_time_seconds)" | jq '.data.result'

# Проверить, сколько звонков timeout'нуло в очереди
curl -s "http://localhost:9090/api/v1/query?query=increase(queue_timeout_total%5B1h%5D)" | jq '.data.result'
```

### 3. Проверить доступность операторов

```bash
# Количество активных операторов
curl -s http://localhost:8080/health/operators | jq '.available_operators'

# Список операторов с их статусом
curl -s http://localhost:8080/api/v1/operators | jq '.[] | {name, status, calls_handled}'

# Проверить, кто online
curl -s http://localhost:8080/api/v1/operators/online | jq '.count'

# Проверить, кто на paused
curl -s http://localhost:8080/api/v1/operators/paused | jq '.count'
```

### 4. Проверить маршрутизацию звонков

```bash
# Последние логи маршрутизатора
docker compose logs --tail=50 call-router | grep -i "queue\|route\|transfer"

# Проверить конфигурацию маршрутизации
grep -i "routing\|queue.*size\|operator.*timeout" /path/to/.env

# Проверить метрики маршрутизации
curl -s "http://localhost:9090/api/v1/query?query=increase(routing_transfers_total%5B1h%5D)" | jq '.data.result'
```

### 5. Проверить Call Processor

```bash
# Логи Call Processor последние 30 минут
docker compose logs --timestamps --since 30m call-processor | tail -50

# Поиск операторских ошибок
docker compose logs call-processor | grep -i "operator\|transfer\|queue" | tail -30

# Проверить метрику успешных передач
curl -s "http://localhost:9090/api/v1/query?query=rate(transfer_to_operator_total%5B5m%5D)" | jq '.data.result'
```

### 6. Проверить VoIP шлюз (Asterisk)

```bash
# Состояние каналов в Asterisk
docker exec asterisk asterisk -rx "core show channels concise" | head -20

# Проверить очередь в Asterisk
docker exec asterisk asterisk -rx "queue show" 

# Логи Asterisk
docker compose logs --tail=50 asterisk | grep -i "queue\|agent\|channel"
```

### 7. Проверить интеграцию с PBX

```bash
# Проверить соединение с external PBX (если используется)
curl -s -w "\nHTTP %{http_code}\n" http://pbx-server:5060/status

# Проверить логи PBX
# (требует SSH доступ к PBX серверу)

# Проверить SIP регистрации
curl -s http://localhost:8080/api/v1/sip/registrations | jq '.[]'
```

## Решение

### Шаг 1: Срочно создать alert операторам

```bash
# Отправить уведомление операторам о переполнении очереди
# Это может быть SMS, email, или push notification
curl -s -X POST http://localhost:8080/api/v1/alerts \
  -H "Content-Type: application/json" \
  -d '{
    "type": "queue_overflow",
    "severity": "HIGH",
    "message": "Очередь переполнена. Требуются дополнительные операторы на смену."
  }'

# Отправить Slack notification
curl -X POST https://hooks.slack.com/services/YOUR/WEBHOOK/URL \
  -d '{
    "text": "⚠️ Очередь операторов переполнена: '$(curl -s http://localhost:8080/health/operator-queue | jq .queue_length)' звонков в ожидании"
  }'
```

### Шаг 2: Быстро увеличить количество операторов

```bash
# Активировать резервных операторов (manual action через Contact Center UI)
# Или programmatically:
curl -s -X POST http://localhost:8080/api/v1/operators/activate-reserve \
  -H "Content-Type: application/json" \
  -d '{
    "count": 2,
    "duration_minutes": 120
  }'

# Проверить, что операторы активированы
sleep 10
curl -s http://localhost:8080/api/v1/operators/online | jq '.count'
```

### Шаг 3: Проверить, что все операторы действительно online

```bash
# Может быть операторы на pause или away
curl -s http://localhost:8080/api/v1/operators | jq '.[] | select(.status != "available")'

# Если операторы на pause без причины:
curl -s -X POST http://localhost:8080/api/v1/operators/resume-all \
  -H "Content-Type: application/json" \
  -d '{
    "reason": "queue_overflow"
  }'
```

### Шаг 4: Проверить маршрутизацию

```bash
# Может быть проблема с распределением на операторов
# Проверить логи маршрутизатора
docker compose logs --timestamps --since 5m call-router | grep -i "error\|failed"

# Перезагрузить маршрутизатор
docker compose restart call-router

# Проверить, что очередь начала обрабатываться
sleep 5
curl -s http://localhost:8080/health/operator-queue | jq '.queue_length'
```

### Шаг 5: Если очередь всё ещё растёт

```bash
# Может быть проблема с Call Processor (не может обработать быстро)
docker compose stats call-processor --no-stream

# Если CPU/memory использование высокое, перезагрузить
docker compose restart call-processor

# Проверить, есть ли ошибки при transfer'е
docker compose logs call-processor | grep -i "transfer.*error\|operator.*failed" | tail -20
```

### Шаг 6: Если нет свободных операторов

```bash
# Это критичная ситуация - может потребоваться отказать звоникам
# Или перенаправить на автоответчик

# Включить IVR bypass (автоматические ответы без агента)
curl -s -X POST http://localhost:8080/api/v1/ivr/bypass-mode \
  -H "Content-Type: application/json" \
  -d '{
    "enabled": true,
    "message": "Все операторы заняты. Пожалуйста, позвоните позже."
  }'

# Или отправить в callback очередь
curl -s -X POST http://localhost:8080/api/v1/callbacks/queue \
  -H "Content-Type: application/json" \
  -d '{
    "call_id": "...",
    "callback_time": "2026-02-14T12:30:00Z"
  }'
```

### Шаг 7: Проверить метрики после восстановления

```bash
# Проверить, что очередь уменьшилась
curl -s http://localhost:8080/health/operator-queue | jq '.queue_length'

# Проверить время ожидания
curl -s "http://localhost:9090/api/v1/query?query=avg(call_queue_wait_time_seconds)" | jq '.data.result'

# Проверить, что операторы обрабатывают звонки
curl -s "http://localhost:9090/api/v1/query?query=rate(transfer_to_operator_total%5B1m%5D)" | jq '.data.result'
```

## Эскалация

### Если очередь остаётся > 5 после 10 минут:

1. **Contact Center Manager (On-Call)**
   - Решить о необходимости overtime для операторов
   - Может потребоваться вызвать дополнительный shift
   - Может потребоваться перенаправление звонков на другой колл-центр

2. **Operations Team Lead**
   - Мониторить очередь в реальном времени
   - Может потребоваться включить emergency protocols
   - Может потребоваться временно отключить некритичные функции

3. **DevOps / Technical Lead**
   - Проверить, нет ли технических проблем (router/asterisk/pbx)
   - Может потребоваться перезагрузка компонентов
   - Может потребоваться временно увеличить capacity

4. **Executive / Director**
   - Если это peak time и операторов действительно недостаточно
   - Может потребоваться management solution (staffing)
   - Может требуется публичный statement о задержках

### Контакты эскалации

- **Contact Center Manager On-Call:** @cc-manager-oncall
- **Operations Team Lead:** @ops-lead
- **DevOps On-Call:** @devops-oncall
- **Call Center Director:** @cc-director

### Пороги автоматической эскалации

| Метрика | Пороги | Действие |
|---------|--------|---------|
| Queue Length | 5-10 | Alert в Slack |
| Queue Length | 10-20 | Notify CC Manager + activate reserve operators |
| Queue Length | >20 | Notify Director + enable emergency protocol |
| Queue Wait Time | 30-60s | Alert yellow |
| Queue Wait Time | >60s | Alert red + escalate |

