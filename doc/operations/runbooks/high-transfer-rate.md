# Runbook: Высокий процент передач оператору (>50% за час)

**Дата создания:** 2026-02-14  
**Владелец:** Product / Analytics  
**Приоритет:** MEDIUM  

## Описание

Этот runbook срабатывает, когда доля звонков, переданных оператору (`transfer_to_operator`), превышает 50% за последний час. Это указывает на проблемы с качеством работы LLM агента: непонимание запросов, неполнота поиска, проблемы с бронированием или другие сбои логики.

## Симптомы

- Alert в Prometheus: `call_transfer_rate{interval="1h"} > 0.5`
- Alert в Grafana dashboard "Call Center Metrics"
- Увеличение нагрузки на очередь операторов (`operator_queue_length` > 5)
- Снижение CSAT (Customer Satisfaction Score)
- Телеметрия: `tool_call{tool="transfer_to_operator", status="success"}` > 50% от всех успешных инструментов

## Диагностика

### 1. Подтвердить метрику через Prometheus

```bash
# Запросить процент передач за последний час
curl -s "http://localhost:9090/api/v1/query?query=call_transfer_rate%7Binterval%3D%221h%22%7D" | jq '.data.result'

# Альтернативный расчёт вручную (если нет встроенной метрики)
# Получить все transfer_to_operator за час
curl -s "http://localhost:9090/api/v1/query_range?query=increase(tool_call_total%7Btool%3D%22transfer_to_operator%22%7D%5B1h%5D)&start=$(date -d '1 hour ago' +%s)&end=$(date +%s)&step=300" | jq '.data.result'

# Получить общее количество успешных звонков
curl -s "http://localhost:9090/api/v1/query_range?query=increase(call_completed_total%5B1h%5D)&start=$(date -d '1 hour ago' +%s)&end=$(date +%s)&step=300" | jq '.data.result'
```

### 2. Проверить логи Call Processor

```bash
# Получить логи за последний час с временными метками
docker compose logs --timestamps --since 1h call-processor > /tmp/call_processor_logs.log

# Поиск причин передач
grep -i "transfer_to_operator\|reason.*transfer\|unable.*handle" /tmp/call_processor_logs.log | head -20

# Подсчитать частоту передач
grep -c "transfer_to_operator" /tmp/call_processor_logs.log
```

### 3. Проверить ошибки STT/LLM/TTS

```bash
# Ошибки STT (распознавание речи)
docker compose logs call-processor | grep -i "stt.*error\|speech_recognition.*failed" | head -10

# Ошибки LLM (Claude API)
docker compose logs call-processor | grep -i "claude.*error\|llm.*failed\|api.*error" | head -10

# Ошибки TTS (синтез речи)
docker compose logs call-processor | grep -i "tts.*error\|text_to_speech.*failed" | head -10

# Timeout'ы
docker compose logs call-processor | grep -i "timeout\|timed out" | head -10
```

### 4. Проверить доступность сервисов Google Cloud

```bash
# STT (Google Cloud Speech-to-Text)
curl -s -w "\nHTTP %{http_code}\n" \
  -X POST "https://speech.googleapis.com/v2/projects/*/locations/global/recognizers" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $(gcloud auth print-access-token)"

# TTS (Google Cloud Text-to-Speech)
curl -s -w "\nHTTP %{http_code}\n" \
  -X POST "https://texttospeech.googleapis.com/v1/text:synthesize" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $(gcloud auth print-access-token)"
```

### 5. Проверить Claude API

```bash
# Тестовое обращение к Claude API
curl -s -w "\nHTTP %{http_code}\n" \
  -X POST "https://api.anthropic.com/v1/messages" \
  -H "Content-Type: application/json" \
  -H "x-api-key: $ANTHROPIC_API_KEY" \
  -d '{
    "model": "claude-opus-4-6",
    "max_tokens": 100,
    "messages": [{"role": "user", "content": "Привіт"}]
  }'
```

### 6. Проверить качество распознавания

```bash
# Найти примеры неудачного распознавания (confidence < 0.7)
docker compose logs call-processor | grep -i "confidence\|speech_confidence" | awk -F'[=,]' '{print $NF}' | sort -n | head -20

# Примеры непонятых запросов
docker compose logs call-processor | grep -i "did not understand\|unrecognized\|sorry" | head -15
```

### 7. Проверить логику агента

```bash
# Найти примеры, когда агент не смог найти шины
docker compose logs call-processor | grep -i "search.*empty\|no.*results\|not.*found" | head -15

# Примеры ошибок бронирования
docker compose logs call-processor | grep -i "booking.*failed\|slot.*unavailable\|appointment.*error" | head -15
```

## Решение

### Шаг 1: Определить категорию проблемы

Основываясь на результатах диагностики, выявить корневую причину:

```bash
# Если это проблема STT (много ошибок распознавания):
docker compose logs call-processor | grep -c "stt.*error"

# Если это проблема LLM (много timeout'ов Claude API):
docker compose logs call-processor | grep -c "claude.*timeout\|llm.*error"

# Если это проблема поиска (много "no results"):
docker compose logs call-processor | grep -c "search.*empty"
```

### Шаг 2: Проверить системное здоровье

```bash
# Проверить CPU и память Call Processor
docker compose stats call-processor --no-stream

# Проверить Redis (session state)
redis-cli info stats | grep -E "total_commands_processed|instantaneous_ops_per_sec"

# Проверить PostgreSQL
pg_isready -h localhost -p 5432

# Проверить Store API
curl -s http://store-api:8080/health | jq '.'
```

### Шаг 3: Если проблема в STT

```bash
# Может быть перегруз Google Cloud или низкое качество аудио
# Проверить quota usage в Google Cloud Console
# Или увеличить timeout для STT в конфиге:
grep -i "stt.*timeout" /path/to/.env

# Попробовать перезагрузить Call Processor с обновленной конфигурацией
docker compose restart call-processor
```

### Шаг 4: Если проблема в LLM

```bash
# Проверить rate limit Claude API
docker compose logs call-processor | grep -i "rate.*limit\|429"

# Проверить систему prompt'а (может нужно обновление)
grep -i "system.*prompt\|temperature\|max_tokens" /path/to/.env

# Увеличить timeout для Claude API if needed
```

### Шаг 5: Если проблема в логике поиска

```bash
# Проверить Store API за неполными результатами
docker compose logs store-api | grep -i "search\|tire" | head -20

# Может потребоваться улучшение параметров поиска в agent prompts
# Обновить doc/development/api-specification.md и перестроить Docker image
docker compose build call-processor
docker compose restart call-processor
```

### Шаг 6: Проверить очередь операторов

```bash
# Если операторы перегружены, это может быть причиной:
curl -s http://localhost:8080/health/operator-queue | jq '.queue_length'

# Запросить операторов на смену если очередь > 5
```

## Эскалация

### Если процент передач остаётся > 50% после 30 минут:

1. **Product Manager / Analytics**
   - Проверить логи за последние 3 часа (pattern анализ)
   - Выявить спецификку проблемы (STT / LLM / Search / Booking)
   - Приоритизировать fix на основе impact

2. **ML Engineer (для STT/LLM проблем)**
   - Пересмотреть system prompt агента
   - Проверить fine-tuning параметры
   - Может требуется A/B тестирование новых версий prompt'а

3. **Backend Engineer (для Search/Booking проблем)**
   - Пересмотреть параметры API запросов
   - Проверить индексы базы данных
   - Может требуется кэширование популярных запросов

4. **DevOps / SRE**
   - Проверить пороги масштабирования (HPA)
   - Может требуется increase resource limits
   - Активировать инцидент в PagerDuty если rate не снижается

### Контакты эскалации

- **Product Analytics:** @product-analytics
- **ML Engineer (Prompt Specialist):** @ml-engineer
- **Backend API Owner:** @backend-lead
- **SRE On-Call:** @sre-oncall

