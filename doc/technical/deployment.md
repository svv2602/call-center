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
version: "3.9"

services:
  call-processor:
    build:
      context: .
      dockerfile: Dockerfile
    ports:
      - "9092:9092"   # AudioSocket
      - "8080:8080"   # REST API / health
    environment:
      - DATABASE_URL=postgresql://app:secret@postgres:5432/callcenter
      - REDIS_URL=redis://redis:6379/0
      - GOOGLE_APPLICATION_CREDENTIALS=/secrets/gcp-service-account.json
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
      - STORE_API_URL=http://store-api:3000/api/v1
      - ASTERISK_ARI_URL=http://asterisk-host:8088/ari
      - ASTERISK_ARI_USER=ari_user
      - ASTERISK_ARI_PASSWORD=${ARI_PASSWORD}
    volumes:
      - ./secrets:/secrets:ro
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
      POSTGRES_USER: app
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    volumes:
      - pgdata:/var/lib/postgresql/data
    ports:
      - "127.0.0.1:5432:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U app -d callcenter"]
      interval: 5s
      timeout: 3s
      retries: 5
    restart: unless-stopped

  redis:
    image: redis:7-alpine
    ports:
      - "127.0.0.1:6379:6379"
    volumes:
      - redisdata:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 5
    restart: unless-stopped

  prometheus:
    image: prom/prometheus:latest
    volumes:
      - ./monitoring/prometheus.yml:/etc/prometheus/prometheus.yml:ro
      - promdata:/prometheus
    ports:
      - "127.0.0.1:9090:9090"
    restart: unless-stopped

  grafana:
    image: grafana/grafana:latest
    volumes:
      - grafanadata:/var/lib/grafana
      - ./monitoring/grafana/dashboards:/etc/grafana/provisioning/dashboards:ro
    ports:
      - "3000:3000"
    environment:
      - GF_SECURITY_ADMIN_PASSWORD=${GRAFANA_PASSWORD}
    restart: unless-stopped

volumes:
  pgdata:
  redisdata:
  promdata:
  grafanadata:
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
