# Диаграмма развёртывания

## Схема инфраструктуры

```mermaid
graph TB
    subgraph Internet["Интернет"]
        PSTN[PSTN / SIP Trunk]
        GCP_STT[Google Cloud<br/>Speech-to-Text]
        GCP_TTS[Google Cloud<br/>Text-to-Speech]
        CLAUDE[Claude API<br/>Anthropic]
    end

    subgraph Server1["Сервер 1 — Телефония"]
        AST[Asterisk 20<br/>PBX]
    end

    subgraph Server2["Сервер 2 — Call Processor"]
        subgraph Docker["Docker Compose"]
            CP[Call Processor<br/>Python 3.12<br/>:9092 AudioSocket<br/>:8080 API]
            PG[(PostgreSQL 16<br/>:5432)]
            RD[(Redis 7<br/>:6379)]
            PROM[Prometheus<br/>:9090]
            GRAF[Grafana<br/>:3000]
        end
    end

    subgraph Server3["Сервер 3 — Store API"]
        STORE[Store API<br/>REST]
        STORE_DB[(Store DB)]
    end

    PSTN <-->|SIP| AST
    AST <-->|AudioSocket TCP :9092| CP
    CP <-->|gRPC streaming| GCP_STT
    CP <-->|HTTPS| GCP_TTS
    CP <-->|HTTPS| CLAUDE
    CP <-->|REST| STORE
    CP --> PG
    CP --> RD
    CP --> PROM
    PROM --> GRAF
    STORE --> STORE_DB
```

## Варианты развёртывания

### Вариант A: Минимальный (MVP)

Всё на одном сервере (кроме существующего Asterisk).

```mermaid
graph TB
    subgraph ExistingServer["Существующий сервер (Asterisk)"]
        AST[Asterisk 20]
    end

    subgraph NewServer["Новый сервер (4 vCPU, 8GB RAM)"]
        CP[Call Processor]
        PG[(PostgreSQL)]
        RD[(Redis)]
        STORE[Store API]
    end

    AST <-->|AudioSocket TCP<br/>LAN| CP
    CP --> PG
    CP --> RD
    CP --> STORE
```

**Характеристики сервера:**

| Параметр | Значение |
|----------|----------|
| CPU | 4 vCPU |
| RAM | 8 GB |
| Диск | 50 GB SSD |
| ОС | Ubuntu 22.04 LTS |
| Сеть | LAN с сервером Asterisk |

**Стоимость:** ~$40–80/мес (VPS) или own hardware.

**Подходит для:** до 30 одновременных звонков.

---

### Вариант B: Продакшен (масштабируемый)

Разделение компонентов для надёжности и масштабирования.

```mermaid
graph TB
    subgraph AsterisNode["Asterisk Node"]
        AST[Asterisk 20]
        LB_AS[AudioSocket<br/>Load Balancer]
    end

    subgraph AppNodes["Application Nodes (2+)"]
        CP1[Call Processor #1]
        CP2[Call Processor #2]
    end

    subgraph DataNodes["Data Nodes"]
        PG[(PostgreSQL<br/>Primary)]
        PG_R[(PostgreSQL<br/>Replica)]
        RD_C[(Redis Cluster)]
    end

    subgraph MonitoringNodes["Monitoring"]
        PROM[Prometheus]
        GRAF[Grafana]
        ALERT[Alertmanager]
    end

    AST --> LB_AS
    LB_AS --> CP1
    LB_AS --> CP2
    CP1 & CP2 --> PG
    PG --> PG_R
    CP1 & CP2 --> RD_C
    CP1 & CP2 --> PROM
    PROM --> GRAF
    PROM --> ALERT
```

**Подходит для:** 30–200 одновременных звонков.

---

### Вариант C: С self-hosted ML (фаза 4)

Добавление GPU-сервера для Faster-Whisper.

```mermaid
graph TB
    subgraph AppNodes["Application Nodes"]
        CP[Call Processor]
    end

    subgraph GPU_Node["GPU Node (T4/A10)"]
        WHISPER[Faster-Whisper<br/>large-v3<br/>:8443 gRPC]
    end

    subgraph Cloud["Cloud APIs"]
        GCP_TTS[Google TTS]
        CLAUDE[Claude API]
    end

    CP <-->|gRPC| WHISPER
    CP <-->|HTTPS| GCP_TTS
    CP <-->|HTTPS| CLAUDE
```

**GPU-сервер:** NVIDIA T4 (16GB) или A10 — ~$150/мес.

**Экономия:** Google STT $900/мес → GPU $150/мес = **-$750/мес**.

## Docker Compose (Вариант A)

```yaml
services:
  call-processor:
    build: .
    ports:
      - "9092:9092"   # AudioSocket
      - "8080:8080"   # REST API / health
    environment:
      - GOOGLE_APPLICATION_CREDENTIALS=/secrets/gcp-key.json
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
      - STORE_API_URL=http://store-api:3000/api/v1
      - STORE_API_KEY=${STORE_API_KEY}
      - DATABASE_URL=postgresql+asyncpg://callcenter:${POSTGRES_PASSWORD}@postgres:5432/callcenter
      - REDIS_URL=redis://redis:6379/0
      - ARI_URL=http://asterisk:8088/ari
      - ARI_USER=${ARI_USER}
      - ARI_PASSWORD=${ARI_PASSWORD}
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    restart: unless-stopped
    deploy:
      resources:
        limits:
          cpus: "2.0"
          memory: 4G

  postgres:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_DB: callcenter
      POSTGRES_USER: callcenter
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U callcenter -d callcenter"]
      interval: 10s
      timeout: 5s
      retries: 5
    restart: unless-stopped

  redis:
    image: redis:7-alpine
    volumes:
      - redisdata:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5
    restart: unless-stopped

  prometheus:
    image: prom/prometheus:v2.53.0
    volumes:
      - ./prometheus/prometheus.yml:/etc/prometheus/prometheus.yml:ro
      - ./prometheus/alerts.yml:/etc/prometheus/alerts.yml:ro
      - prometheus_data:/prometheus
    ports:
      - "9090:9090"
    command:
      - "--config.file=/etc/prometheus/prometheus.yml"
      - "--storage.tsdb.retention.time=30d"
    restart: unless-stopped

  grafana:
    image: grafana/grafana:11.1.0
    volumes:
      - ./grafana/provisioning:/etc/grafana/provisioning:ro
      - ./grafana/dashboards:/var/lib/grafana/dashboards:ro
      - grafana_data:/var/lib/grafana
    ports:
      - "3000:3000"
    environment:
      GF_SECURITY_ADMIN_PASSWORD: ${GRAFANA_ADMIN_PASSWORD:-admin}
      GF_USERS_ALLOW_SIGN_UP: "false"
    restart: unless-stopped

  celery-worker:
    build: .
    command: celery -A src.tasks.celery_app worker -l info -Q quality,stats -c 2
    environment:
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
      - DATABASE_URL=postgresql+asyncpg://callcenter:${POSTGRES_PASSWORD}@postgres:5432/callcenter
      - CELERY_BROKER_URL=redis://redis:6379/1
      - CELERY_RESULT_BACKEND=redis://redis:6379/1
      - QUALITY_LLM_MODEL=claude-haiku-4-5-20251001
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    restart: unless-stopped

  celery-beat:
    build: .
    command: celery -A src.tasks.celery_app beat -l info
    environment:
      - CELERY_BROKER_URL=redis://redis:6379/1
      - CELERY_RESULT_BACKEND=redis://redis:6379/1
      - DATABASE_URL=postgresql+asyncpg://callcenter:${POSTGRES_PASSWORD}@postgres:5432/callcenter
    depends_on:
      redis:
        condition: service_healthy
    restart: unless-stopped

  alertmanager:
    image: prom/alertmanager:v0.27.0
    ports:
      - "9093:9093"
    volumes:
      - ./alertmanager/config.yml:/etc/alertmanager/alertmanager.yml:ro
    environment:
      TELEGRAM_BOT_TOKEN: ${TELEGRAM_BOT_TOKEN}
      TELEGRAM_CHAT_ID: ${TELEGRAM_CHAT_ID}
    command:
      - "--config.file=/etc/alertmanager/alertmanager.yml"
    restart: unless-stopped

volumes:
  pgdata:
  redisdata:
  prometheus_data:
  grafana_data:
```

> **Примечание:** Admin UI реализован как минимальная HTML-оболочка (`admin-ui/index.html`). Полноценный SPA (React/Vue) — задача для следующего этапа. Все функции доступны через REST API.

## Защита AudioSocket-канала

AudioSocket между Asterisk и Call Processor передаёт аудио в открытом виде (PCM over TCP). Рекомендации по защите:

| Сценарий | Рекомендация |
|----------|-------------|
| Asterisk и Call Processor на одном сервере | `127.0.0.1` — безопасно, трафик не покидает хост |
| Asterisk и Call Processor в одной LAN | WireGuard-туннель между серверами (минимальная задержка, шифрование) |
| Asterisk и Call Processor в разных сетях | **Обязательно:** VPN / WireGuard / SSH-туннель. TCP без шифрования по интернету недопустим |

**Пример WireGuard-конфигурации (Asterisk → Call Processor):**

```ini
# /etc/wireguard/wg0.conf на сервере Asterisk
[Interface]
PrivateKey = <asterisk-private-key>
Address = 10.0.0.1/24

[Peer]
PublicKey = <call-processor-public-key>
Endpoint = <call-processor-ip>:51820
AllowedIPs = 10.0.0.2/32
```

В dialplan Asterisk адрес AudioSocket меняется на WireGuard-адрес:

```ini
AudioSocket(${UNIQUE_ID},10.0.0.2:9092)
```

## Сетевые требования

| Соединение | Протокол | Порт | Направление | Требования |
|------------|----------|------|-------------|------------|
| Asterisk → Call Processor | TCP | 9092 | LAN | Задержка < 5ms |
| Call Processor → Google STT | gRPC (HTTPS) | 443 | Internet | Стабильный канал |
| Call Processor → Google TTS | HTTPS | 443 | Internet | — |
| Call Processor → Claude API | HTTPS | 443 | Internet | — |
| Call Processor → Store API | HTTP(S) | 3000 | LAN | Задержка < 10ms |
| Call Processor → PostgreSQL | TCP | 5432 | LAN | — |
| Call Processor → Redis | TCP | 6379 | LAN | — |
| Grafana (UI) | HTTPS | 3000 | Internet | За reverse proxy |

**Минимальная пропускная способность интернета:** 10 Mbit/s (с запасом для 50 одновременных потоков STT).

## Бэкапы

| Что | Как | Частота |
|-----|-----|---------|
| PostgreSQL | pg_dump → S3 | Ежедневно |
| Redis | RDB snapshot | Ежечасно |
| Конфигурация | Git репозиторий | При изменении |
| Секреты (API keys) | Encrypted vault | При изменении |

## Отказоустойчивость Asterisk (SPOF)

На MVP-этапе Asterisk — single point of failure. План резервирования:

### Фаза 1–2 (MVP): мониторинг

- Мониторинг доступности Asterisk через Prometheus + AMI exporter
- Алерт при недоступности (Telegram, < 1 мин)
- Документированная процедура ручного восстановления (RTO < 15 мин)

### Фаза 3+ (продакшен): резервирование

```mermaid
graph TB
    subgraph SIP["SIP Provider"]
        TRUNK[SIP Trunk<br/>failover routing]
    end

    subgraph Primary["Primary"]
        AST1[Asterisk #1<br/>Active]
    end

    subgraph Standby["Standby"]
        AST2[Asterisk #2<br/>Hot Standby]
    end

    TRUNK -->|primary| AST1
    TRUNK -.->|failover| AST2
    AST1 & AST2 --> CP[Call Processor]
```

- **SIP-уровень:** failover на стороне SIP-провайдера (два endpoint-а)
- **Конфигурация:** синхронизация через rsync / Ansible
- **Переключение:** автоматическое на стороне SIP trunk (по таймауту SIP INVITE)

## Health Checks

| Сервис | Endpoint | Проверка |
|--------|----------|----------|
| Call Processor | `GET /health` | AudioSocket listening, DB connected, Redis connected |
| Call Processor | `GET /health/ready` | + Google STT reachable, Claude API reachable |
| PostgreSQL | `pg_isready` | Принимает подключения |
| Redis | `redis-cli ping` | PONG |
