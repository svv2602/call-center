# Runbook: Открытый circuit breaker Store API

**Дата создания:** 2026-02-14  
**Владелец:** DevOps / SRE  
**Приоритет:** HIGH  

## Описание

Circuit breaker защищает Call Processor от отказа Store API. Когда количество ошибок достигает порога (fail_max=5), circuit breaker открывается и блокирует дальнейшие запросы на 30 секунд (timeout). Это указывает на серьёзную проблему с доступностью Store API или сетевой связностью.

## Симптомы

- Agent отказывается обрабатывать запросы поиска шин (`search_tires`), проверки наличия (`check_availability`), создания заказов (`create_order_draft`)
- В логах Call Processor: `CircuitBreakerError: Circuit breaker is OPEN`
- Клиент слышит: "Вибачте, система тимчасово недоступна, спробуйте пізніше"
- Метрика Prometheus: `store_api_circuit_breaker_state{state="open"}` = 1

## Диагностика

### 1. Проверить статус circuit breaker

```bash
# Запрос метрик Prometheus (локально на 9090)
curl -s "http://localhost:9090/api/v1/query?query=store_api_circuit_breaker_state" | jq '.data.result'

# Ожидаемый ответ: state="open" с value=1
```

### 2. Проверить логи Call Processor

```bash
# Последние 50 строк логов (Docker Compose)
docker compose logs --tail=50 call-processor

# Поиск ошибок circuit breaker
docker compose logs call-processor | grep -i "circuit.*breaker\|circuitbreakerror"
```

### 3. Проверить доступность Store API

```bash
# Базовая проверка здоровья (health endpoint)
curl -s -w "\nHTTP %{http_code}\n" http://store-api:8080/health

# Проверить конкретный эндпоинт поиска шин
curl -s -w "\nHTTP %{http_code}\n" \
  -X GET "http://store-api:8080/api/v1/tires/search?width=205&profile=55&diameter=16" \
  -H "Authorization: Bearer $STORE_API_KEY"
```

### 4. Проверить сетевую связность

```bash
# Пинг Store API
ping -c 5 store-api

# Проверка DNS
nslookup store-api

# Проверка открытого порта
nc -zv store-api 8080
```

### 5. Проверить метрики Store API

```bash
# Если Store API имеет собственную метрику здоровья
curl -s http://store-api:9091/metrics | grep -i "request\|error\|latency"
```

### 6. Проверить логи Store API

```bash
# Если Store API работает в Docker
docker compose logs --tail=100 store-api

# Поиск HTTP 5xx ошибок в логах
docker compose logs store-api | grep -E "5[0-9]{2}"
```

## Решение

### Шаг 1: Быстрая перезагрузка Call Processor

Если Store API вернулся в норму, перезагрузка Call Processor сбросит circuit breaker:

```bash
# Мягкая перезагрузка (graceful restart)
docker compose restart call-processor

# Проверить, что контейнер запустился
docker compose ps call-processor
```

### Шаг 2: Если Store API всё ещё недоступен

```bash
# Проверить статус Store API в Docker
docker compose ps store-api

# Перезагрузить Store API
docker compose restart store-api

# Дождаться, пока Health Check пройдёт (может быть 10-30 секунд)
sleep 20
curl -s http://store-api:8080/health
```

### Шаг 3: Проверить конфигурацию circuit breaker

Убедитесь, что параметры circuit breaker в `.env` или конфиге соответствуют требованиям:

```bash
# Просмотр конфигурации
grep -i "circuit\|breaker\|fail_max\|timeout" /path/to/.env

# Ожидаемые значения:
# CIRCUIT_BREAKER_FAIL_MAX=5
# CIRCUIT_BREAKER_TIMEOUT=30
```

### Шаг 4: Проверить логи за последние 5 минут

```bash
# Получить логи с временной меткой
docker compose logs --timestamps --since 5m call-processor

# Подсчитать количество ошибок
docker compose logs call-processor | grep -c "error\|ERROR\|Error"
```

### Шаг 5: Если Store API работает нормально, но circuit breaker остаётся открытым

```bash
# Может потребоваться полная перезагрузка
docker compose down
docker compose up -d

# Проверить статус
docker compose logs --follow --tail=20 call-processor
```

## Эскалация

### Если проблема не решена после 5 минут:

1. **Инженер Backend (Store API)**
   - Проверить логи Store API за аномалиями
   - Проверить БД Store API (connexion pool, locks, slow queries)
   - Проверить сетевой балансировщик нагрузки

2. **Инженер DevOps**
   - Проверить мониторинг инфраструктуры (CPU, память, сеть)
   - Проверить firewall правила между Call Processor и Store API
   - Проверить DNS разрешение

3. **Главный инженер (SRE Lead)**
   - Может потребоваться rollback последней версии Store API
   - Проверить логи развёртывания (deployment logs)
   - Активировать PagerDuty инцидент

### Контакты эскалации

- **Store API Owner:** @backend-lead
- **DevOps On-Call:** @devops-oncall
- **SRE Lead:** @sre-lead

