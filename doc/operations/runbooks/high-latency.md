# Runbook: Высокая задержка (p95 > 2.5s)

**Дата создания:** 2026-02-14  
**Владелец:** DevOps / Performance Engineering  
**Приоритет:** HIGH  

## Описание

Этот runbook применяется, когда 95-й percentile времени ответа (p95) превышает 2.5 секунды. Звонок содержит три критичные точки: STT (распознавание речи), LLM (Claude API), TTS (синтез речи). Высокая задержка может быть в любой из этих компонент или в orchestration layer (Call Processor).

## Симптомы

- Alert в Prometheus: `call_latency_p95_seconds > 2.5`
- Alert в Grafana: "Call Processing Latency"
- Клиенты жалуются на задержку в ответах агента (перерывы в разговоре)
- Метрики показывают увеличение `call_duration_seconds`
- AudioSocket buffer underruns (прерывистый звук)

## Диагностика

### 1. Проверить общую задержку

```bash
# Получить p95, p99 latency за последний час
curl -s "http://localhost:9090/api/v1/query?query=histogram_quantile(0.95,call_latency_seconds)" | jq '.data.result'
curl -s "http://localhost:9090/api/v1/query?query=histogram_quantile(0.99,call_latency_seconds)" | jq '.data.result'

# Получить средню задержку
curl -s "http://localhost:9090/api/v1/query?query=avg(call_latency_seconds)" | jq '.data.result'
```

### 2. Определить компоненту с высокой задержкой

```bash
# STT latency (Speech-to-Text)
curl -s "http://localhost:9090/api/v1/query?query=histogram_quantile(0.95,stt_latency_seconds)" | jq '.data.result'

# LLM latency (Claude API)
curl -s "http://localhost:9090/api/v1/query?query=histogram_quantile(0.95,llm_latency_seconds)" | jq '.data.result'

# TTS latency (Text-to-Speech)
curl -s "http://localhost:9090/api/v1/query?query=histogram_quantile(0.95,tts_latency_seconds)" | jq '.data.result'

# Tool call latency (Store API)
curl -s "http://localhost:9090/api/v1/query?query=histogram_quantile(0.95,tool_call_latency_seconds)" | jq '.data.result'
```

### 3. Проверить логи Call Processor

```bash
# Получить логи за последний час
docker compose logs --timestamps --since 1h call-processor > /tmp/call_processor_logs.log

# Поиск медленных операций STT
grep -i "stt.*latency\|speech.*duration" /tmp/call_processor_logs.log | awk '{print $NF}' | sort -n | tail -20

# Поиск медленных операций LLM
grep -i "claude.*latency\|llm.*duration\|api.*ms" /tmp/call_processor_logs.log | awk '{print $NF}' | sort -n | tail -20

# Поиск медленных операций TTS
grep -i "tts.*latency\|synthesis.*duration" /tmp/call_processor_logs.log | awk '{print $NF}' | sort -n | tail -20

# Поиск медленных tool call'ов
grep -i "tool_call.*latency\|store.*api.*ms" /tmp/call_processor_logs.log | awk '{print $NF}' | sort -n | tail -20
```

### 4. Проверить системные ресурсы

```bash
# CPU и память Call Processor
docker compose stats call-processor --no-stream

# Сетевые задержки
docker compose logs call-processor | grep -i "network\|latency\|timeout" | head -20

# Проверить использование диска (может быть I/O wait)
df -h

# Проверить iowait
iostat -x 1 5
```

### 5. Проверить Redis (session state)

```bash
# Redis performance
redis-cli --latency -i 1

# Проверить размер ключей в Redis
redis-cli INFO memory | grep -E "used_memory|used_memory_peak|mem_fragmentation_ratio"

# Проверить медленные команды
redis-cli slowlog get 10
```

### 6. Проверить PostgreSQL (логирование)

```bash
# Проверить медленные запросы (если включен log_min_duration_statement)
# В логах PostgreSQL
docker compose logs postgres | grep "duration:" | awk '{print $(NF-1)}' | sort -n | tail -20

# Проверить активные запросы
docker exec -it call-center-postgres-1 psql -U postgres -c "SELECT pid, query, query_start, state FROM pg_stat_activity WHERE state != 'idle';"

# Проверить индексы на таблице calls
docker exec -it call-center-postgres-1 psql -U postgres -c "\d calls"
```

### 7. Проверить Store API задержку

```bash
# Пиковая задержка на Store API
curl -s "http://localhost:9090/api/v1/query?query=histogram_quantile(0.95,store_api_latency_seconds)" | jq '.data.result'

# Количество ошибок Store API
curl -s "http://localhost:9090/api/v1/query?query=increase(store_api_errors_total%5B1h%5D)" | jq '.data.result'

# Проверить logи Store API
docker compose logs --tail=50 store-api
```

### 8. Проверить Google Cloud сервисы

```bash
# STT: проверить throughput
curl -s "http://localhost:9090/api/v1/query?query=rate(stt_requests_total%5B1m%5D)" | jq '.data.result'

# TTS: проверить throughput
curl -s "http://localhost:9090/api/v1/query?query=rate(tts_requests_total%5B1m%5D)" | jq '.data.result'

# Проверить quota usage в Google Cloud Console (требует доступ к GCP)
```

## Решение

### Шаг 1: Определить медленный компонент

```bash
# Сравнить p95 latencies для каждого компонента
echo "STT:"
curl -s "http://localhost:9090/api/v1/query?query=histogram_quantile(0.95,stt_latency_seconds)" | jq '.data.result[0].value[1]'

echo "LLM:"
curl -s "http://localhost:9090/api/v1/query?query=histogram_quantile(0.95,llm_latency_seconds)" | jq '.data.result[0].value[1]'

echo "TTS:"
curl -s "http://localhost:9090/api/v1/query?query=histogram_quantile(0.95,tts_latency_seconds)" | jq '.data.result[0].value[1]'
```

### Шаг 2: Если проблема в STT

```bash
# Может быть перегруз Google Cloud API или сетевой задержкой
# Проверить параметр streaming в конфигурации:
grep -i "stt.*streaming\|stt.*interimResults" /path/to/.env

# Попробовать отключить interimResults (может снизить latency)
# Обновить конфиг и перезагрузить:
docker compose restart call-processor
```

### Шаг 3: Если проблема в LLM (Claude API)

```bash
# Проверить параметры запроса:
grep -i "max_tokens\|temperature" /path/to/.env

# Может быть высокая рекурсия в thinking (o1/extended thinking)
# Попробовать снизить max_tokens в system prompt:
# Обновить и перестроить:
docker compose build call-processor
docker compose restart call-processor

# Может быть перегруз Claude API - проверить rate limit
curl -s "http://localhost:9090/api/v1/query?query=claude_api_rate_limit_remaining" | jq '.data.result'
```

### Шаг 4: Если проблема в TTS

```bash
# Может быть очередь синтезирования
# Проверить параметры в конфигурации:
grep -i "tts.*voice\|tts.*audio_encoding" /path/to/.env

# Может быть долгие текст-парагрфы
# Попробовать сократить LLM ответы через prompt:
# Обновить и перестроить:
docker compose build call-processor
docker compose restart call-processor
```

### Шаг 5: Если проблема в Redis

```bash
# Очистить старые ключи
redis-cli FLUSHDB --ASYNC

# Или перезагрузить Redis контейнер
docker compose restart redis

# После очистки проверить снова
docker compose stats call-processor --no-stream
```

### Шаг 6: Если проблема в PostgreSQL

```bash
# Пересчитать статистику
docker exec -it call-center-postgres-1 psql -U postgres -c "ANALYZE calls; ANALYZE call_turns; ANALYZE call_tool_calls;"

# Может потребоваться VACUUM
docker exec -it call-center-postgres-1 psql -U postgres -c "VACUUM calls; VACUUM call_turns; VACUUM call_tool_calls;"

# Если это не помогло, может потребоваться добавить индекс
docker exec -it call-center-postgres-1 psql -U postgres -c "CREATE INDEX CONCURRENTLY idx_calls_created_at ON calls(created_at DESC) WHERE status = 'completed';"
```

### Шаг 7: Если проблема системная (высокая load)

```bash
# Увеличить количество Call Processor инстансов
# В docker-compose.yml:
# deploy:
#   replicas: 2  (или больше)

docker compose up -d --scale call-processor=2

# Проверить, что load распределён
docker compose ps
```

## Эскалация

### Если p95 latency остаётся > 2.5s после 15 минут:

1. **Performance Engineer**
   - Профилировать Call Processor (CPU, memory, network)
   - Может потребоваться code-level optimization
   - Проверить asyncio event loop для bottlenecks

2. **Backend Engineer (для Store API latency)**
   - Проверить query plans в PostgreSQL
   - Может потребоваться добавить кэш (Redis)
   - Может потребоваться оптимизация API endpoints

3. **Google Cloud / Third-party Services Owner**
   - Проверить quota, rate limits
   - Может потребоваться upgrade в плане (higher tier)
   - Может требуется контакт с support Google Cloud

4. **DevOps / Infrastructure**
   - Может потребоваться вертикальное масштабирование (больше CPU/RAM)
   - Может потребоваться горизонтальное масштабирование (load balancer)
   - Может потребоваться tuning ОС параметров

### Контакты эскалации

- **Performance Engineer:** @performance-eng
- **SRE On-Call:** @sre-oncall
- **Google Cloud TAM:** @gcp-support
- **DevOps Lead:** @devops-lead

