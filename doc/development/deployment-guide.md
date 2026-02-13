# Руководство по развёртыванию

## Пререквизиты

### Серверы

| Сервер | Назначение | Минимальные характеристики |
|--------|------------|---------------------------|
| Сервер Asterisk | Уже существует | Asterisk 20, AudioSocket модуль |
| Сервер Call Processor | Новый | 4 vCPU, 8GB RAM, 50GB SSD, Ubuntu 22.04 |

### Аккаунты и ключи

| Сервис | Что нужно |
|--------|-----------|
| Google Cloud | Проект + Service Account с ролями `Speech-to-Text User`, `Text-to-Speech User` |
| Anthropic | API key (https://console.anthropic.com) |

### Софт на сервере Call Processor

- Docker 24+
- Docker Compose v2+
- Git

## Шаг 1: Клонирование репозитория

```bash
git clone <repo-url> /opt/call-center
cd /opt/call-center
```

## Шаг 2: Настройка секретов

```bash
cp .env.example .env
```

Отредактировать `.env`:

```bash
# PostgreSQL
POSTGRES_PASSWORD=<strong-password>

# Google Cloud
# Положить JSON-файл сервисного аккаунта в ./secrets/
# GOOGLE_APPLICATION_CREDENTIALS задан в docker-compose.yml

# Anthropic
ANTHROPIC_API_KEY=sk-ant-...

# Asterisk ARI
ARI_PASSWORD=<ari-password>

# Grafana
GRAFANA_PASSWORD=<admin-password>

# Store API
STORE_API_URL=http://store-api:3000/api/v1
STORE_API_KEY=<api-key>
```

```bash
mkdir -p secrets
# Скопировать JSON-ключ Google Cloud в secrets/
cp /path/to/gcp-service-account.json secrets/gcp-service-account.json
chmod 600 secrets/gcp-service-account.json
```

## Шаг 3: Запуск

```bash
docker compose up -d
```

Проверка:

```bash
# Все контейнеры запущены
docker compose ps

# Health check
curl http://localhost:8080/health

# Логи
docker compose logs -f call-processor
```

## Шаг 4: Настройка Asterisk

На сервере Asterisk добавить в `/etc/asterisk/extensions.conf`:

```ini
[incoming]
exten => _X.,1,NoOp(AI Call Center: ${CALLERID(num)})
 same => n,Answer()
 same => n,Set(CHANNEL(audioreadformat)=slin16)
 same => n,Set(CHANNEL(audiowriteformat)=slin16)
 same => n,AudioSocket(${UNIQUE_ID},<call-processor-ip>:9092)
 same => n,Hangup()

[transfer-to-operator]
exten => _X.,1,NoOp(Transfer to operator: ${CALLERID(num)})
 same => n,Queue(operators,t,,,120)
 same => n,Hangup()
```

Настроить ARI в `/etc/asterisk/ari.conf`:

```ini
[general]
enabled = yes
pretty = yes

[ari_user]
type = user
read_only = no
password = <ari-password>
```

Перезагрузить:

```bash
asterisk -rx "dialplan reload"
asterisk -rx "module reload res_ari.so"
```

## Шаг 5: Проверка

### Тестовый звонок

1. Позвонить на тестовый номер
2. Бот должен ответить: "Добрий день! ..."
3. Сказать: "Мені потрібні зимові шини на Тойоту Камрі"
4. Бот должен предложить варианты

### Проверка компонентов

```bash
# AudioSocket слушает
ss -tlnp | grep 9092

# PostgreSQL
docker compose exec postgres psql -U app -d callcenter -c "SELECT 1"

# Redis
docker compose exec redis redis-cli ping

# Grafana
# Открыть в браузере: http://<server-ip>:3000
```

## Обновление

```bash
cd /opt/call-center
git pull
docker compose build call-processor
docker compose up -d call-processor
```

## Откат

```bash
docker compose down call-processor
git checkout <previous-tag>
docker compose build call-processor
docker compose up -d call-processor
```
