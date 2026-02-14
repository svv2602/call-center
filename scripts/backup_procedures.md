# Процедуры Backup и Restore для Call Center AI

## Обзор

Данный документ описывает процедуры резервного копирования и восстановления для системы Call Center AI. Система состоит из нескольких компонентов, требующих разных подходов к backup.

## Компоненты системы

1. **PostgreSQL** - основное хранилище данных (звонки, транскрипции, аналитика)
2. **Redis** - кэш сессий (временные данные, TTL 1800s)
3. **Файловая система** - логи, конфигурации, знания базы знаний
4. **Конфигурации** - Docker Compose, переменные окружения, секреты

## Стратегия backup

### Уровень 1: Ежедневные полные backup
- PostgreSQL: полный dump всех данных
- Конфигурации: версионирование в git
- Файлы знаний: синхронизация с git

### Уровень 2: Почасовые инкрементальные backup
- WAL архивы PostgreSQL (Point-in-Time Recovery)
- Логи приложения (ротация по размеру/времени)

### Уровень 3: Репликация в реальном времени
- PostgreSQL streaming replication (hot standby)
- Redis replication (master-slave)

## PostgreSQL Backup

### Полный backup (pg_dump)

```bash
#!/bin/bash
# scripts/backup_postgres.sh

BACKUP_DIR="/backup/postgres"
DATE=$(date +%Y%m%d_%H%M%S)
RETENTION_DAYS=30

# Создаем директорию если не существует
mkdir -p $BACKUP_DIR

# Полный dump всех баз данных
docker compose exec -T postgres pg_dumpall -U callcenter \
  --clean \
  --if-exists \
  --verbose \
  > "$BACKUP_DIR/full_backup_$DATE.sql"

# Сжимаем backup
gzip "$BACKUP_DIR/full_backup_$DATE.sql"

# Удаляем старые backup (старше RETENTION_DAYS)
find $BACKUP_DIR -name "*.sql.gz" -mtime +$RETENTION_DAYS -delete

echo "Backup создан: $BACKUP_DIR/full_backup_$DATE.sql.gz"
```

### Инкрементальный backup (WAL архивирование)

В `docker-compose.yml` добавить:

```yaml
postgres:
  image: pgvector/pgvector:pg16
  volumes:
    - pgdata:/var/lib/postgresql/data
    - ./backup/wal_archive:/wal_archive
  environment:
    POSTGRES_DB: callcenter
    POSTGRES_USER: callcenter
    POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    # WAL архивирование
    POSTGRES_WAL_LEVEL: replica
    POSTGRES_ARCHIVE_MODE: on
    POSTGRES_ARCHIVE_COMMAND: 'test ! -f /wal_archive/%f && cp %p /wal_archive/%f'
```

### Восстановление из backup

```bash
#!/bin/bash
# scripts/restore_postgres.sh

BACKUP_FILE="$1"

if [ -z "$BACKUP_FILE" ]; then
  echo "Использование: $0 <backup_file.sql.gz>"
  exit 1
fi

# Останавливаем приложение
docker compose stop call-processor celery-worker celery-beat

# Восстанавливаем из backup
gunzip -c "$BACKUP_FILE" | docker compose exec -T postgres psql -U callcenter

# Запускаем приложение
docker compose start call-processor celery-worker celery-beat

echo "Восстановление завершено из: $BACKUP_FILE"
```

### Point-in-Time Recovery (PITR)

```bash
# 1. Восстановить последний полный backup
gunzip -c full_backup_20250214_120000.sql.gz | psql -U callcenter

# 2. Применить WAL архивы до нужного времени
cat /wal_archive/000000010000000000000001 \
    /wal_archive/000000010000000000000002 \
    | psql -U callcenter

# Или использовать pg_restore с --recovery-target-time
```

## Redis Backup

### RDB snapshot backup

```bash
#!/bin/bash
# scripts/backup_redis.sh

BACKUP_DIR="/backup/redis"
DATE=$(date +%Y%m%d_%H%M%S)
RETENTION_DAYS=7

mkdir -p $BACKUP_DIR

# Сохраняем snapshot
docker compose exec -T redis redis-cli SAVE

# Копируем dump.rdb
docker compose cp redis:/data/dump.rdb "$BACKUP_DIR/redis_dump_$DATE.rdb"

# Сжимаем
gzip "$BACKUP_DIR/redis_dump_$DATE.rdb"

# Очищаем старые backup
find $BACKUP_DIR -name "*.rdb.gz" -mtime +$RETENTION_DAYS -delete

echo "Redis backup создан: $BACKUP_DIR/redis_dump_$DATE.rdb.gz"
```

### Восстановление Redis

```bash
#!/bin/bash
# scripts/restore_redis.sh

BACKUP_FILE="$1"

if [ -z "$BACKUP_FILE" ]; then
  echo "Использование: $0 <backup_file.rdb.gz>"
  exit 1
fi

# Останавливаем Redis
docker compose stop redis

# Восстанавливаем dump
gunzip -c "$BACKUP_FILE" > ./redis_data/dump.rdb

# Запускаем Redis
docker compose start redis

echo "Redis восстановлен из: $BACKUP_FILE"
```

## Backup файловой системы

### Логи приложения

```bash
#!/bin/bash
# scripts/backup_logs.sh

BACKUP_DIR="/backup/logs"
DATE=$(date +%Y%m%d)
RETENTION_DAYS=90

mkdir -p $BACKUP_DIR

# Архивируем логи за последние 24 часа
find ./logs -name "*.log" -mtime -1 -exec tar -czf "$BACKUP_DIR/logs_$DATE.tar.gz" {} +

# Ротация старых backup
find $BACKUP_DIR -name "*.tar.gz" -mtime +$RETENTION_DAYS -delete

echo "Логи заархивированы: $BACKUP_DIR/logs_$DATE.tar.gz"
```

### База знаний (knowledge_base)

```bash
#!/bin/bash
# scripts/backup_knowledge.sh

BACKUP_DIR="/backup/knowledge"
DATE=$(date +%Y%m%d_%H%M%S)

mkdir -p $BACKUP_DIR

# Архивируем базу знаний
tar -czf "$BACKUP_DIR/knowledge_base_$DATE.tar.gz" \
  ./knowledge_base/ \
  ./scripts/load_knowledge_base.py

echo "База знаний заархивирована: $BACKUP_DIR/knowledge_base_$DATE.tar.gz"
```

## Конфигурации и секреты

### Версионирование в git

```bash
# Важные файлы для backup:
git add \
  docker-compose.yml \
  docker-compose.dev.yml \
  .env.example \
  alembic.ini \
  pyproject.toml \
  admin-ui/ \
  asterisk/ \
  grafana/ \
  prometheus/ \
  alertmanager/

git commit -m "Backup конфигураций $(date +%Y%m%d)"
```

### Секреты (.env файлы)

**ВАЖНО:** Никогда не коммитьте файлы с реальными секретами в git!

```bash
#!/bin/bash
# scripts/backup_secrets.sh

BACKUP_DIR="/backup/secrets"
DATE=$(date +%Y%m%d_%H%M%S)
ENCRYPTION_KEY="your-encryption-key-here"

mkdir -p $BACKUP_DIR

# Архивируем и шифруем секреты
tar -czf - .env .env.local .env.production 2>/dev/null | \
  openssl enc -aes-256-cbc -salt -pass pass:$ENCRYPTION_KEY \
  > "$BACKUP_DIR/secrets_$DATE.tar.gz.enc"

echo "Секреты зашифрованы: $BACKUP_DIR/secrets_$DATE.tar.gz.enc"
```

## Автоматизация backup

### Cron jobs

```bash
# /etc/crontab
# Ежедневно в 2:00 - полный backup PostgreSQL
0 2 * * * root /opt/call-center/scripts/backup_postgres.sh

# Каждый час - инкрементальный backup WAL
0 * * * * root /opt/call-center/scripts/backup_wal.sh

# Ежедневно в 3:00 - backup Redis
0 3 * * * root /opt/call-center/scripts/backup_redis.sh

# Ежедневно в 4:00 - backup логов
0 4 * * * root /opt/call-center/scripts/backup_logs.sh

# Еженедельно в воскресенье 1:00 - backup базы знаний
0 1 * * 0 root /opt/call-center/scripts/backup_knowledge.sh
```

### Мониторинг backup

Добавить в Prometheus:

```yaml
# prometheus/alerts.yml
groups:
  - name: backup_alerts
    rules:
      - alert: BackupFailed
        expr: time() - file_mtime_seconds{file="/backup/postgres/latest_backup"} > 86400
        for: 5m
        labels:
          severity: critical
        annotations:
          summary: "Backup не выполнялся более 24 часов"
          description: "Последний backup был {{ $value }} секунд назад"
```

## Процедура восстановления (Disaster Recovery)

### Полное восстановление системы

1. **Восстановить инфраструктуру:**
   ```bash
   # Восстановить конфигурации из git
   git clone <repository> /opt/call-center
   cd /opt/call-center
   
   # Восстановить секреты
   openssl enc -d -aes-256-cbc -in secrets_backup.tar.gz.enc -pass pass:$ENCRYPTION_KEY | tar -xzf -
   
   # Запустить инфраструктуру
   docker compose up -d postgres redis
   ```

2. **Восстановить данные:**
   ```bash
   # PostgreSQL
   ./scripts/restore_postgres.sh /backup/postgres/full_backup_latest.sql.gz
   
   # Redis (опционально, данные временные)
   ./scripts/restore_redis.sh /backup/redis/redis_dump_latest.rdb.gz
   
   # База знаний
   tar -xzf /backup/knowledge/knowledge_base_latest.tar.gz
   ```

3. **Запустить приложение:**
   ```bash
   docker compose up -d
   ```

### Восстановление отдельных компонентов

#### Только PostgreSQL
```bash
# Остановить только call-processor
docker compose stop call-processor

# Восстановить базу данных
./scripts/restore_postgres.sh /path/to/backup.sql.gz

# Запустить call-processor
docker compose start call-processor
```

#### Только Redis
```bash
# Redis можно просто перезапустить (данные временные)
docker compose restart redis
# Или восстановить из backup если нужно
./scripts/restore_redis.sh /path/to/backup.rdb.gz
```

## Тестирование backup

### Регулярное тестирование восстановления

1. **Ежемесячно:** Восстановление на тестовом стенде
2. **Ежеквартально:** Полное DR тестирование
3. **При изменении схемы:** Проверка совместимости backup

### Скрипт проверки backup

```bash
#!/bin/bash
# scripts/test_backup.sh

echo "=== Тестирование backup ==="

# Проверяем существование последних backup
echo "1. Проверка PostgreSQL backup..."
if [ -f "/backup/postgres/$(ls -t /backup/postgres/*.sql.gz | head -1)" ]; then
  echo "   ✓ Последний backup существует"
else
  echo "   ✗ Backup не найден"
  exit 1
fi

echo "2. Проверка целостности backup..."
gunzip -t "/backup/postgres/$(ls -t /backup/postgres/*.sql.gz | head -1)"
if [ $? -eq 0 ]; then
  echo "   ✓ Backup не поврежден"
else
  echo "   ✗ Backup поврежден"
  exit 1
fi

echo "3. Проверка свежести backup..."
BACKUP_AGE=$(( $(date +%s) - $(stat -c %Y "/backup/postgres/$(ls -t /backup/postgres/*.sql.gz | head -1)") ))
if [ $BACKUP_AGE -lt 86400 ]; then  # Меньше 24 часов
  echo "   ✓ Backup свежий ($((BACKUP_AGE/3600)) часов назад)"
else
  echo "   ✗ Backup устарел ($((BACKUP_AGE/3600)) часов назад)"
  exit 1
fi

echo "=== Все проверки пройдены ==="
```

## Рекомендации по хранению backup

1. **Правило 3-2-1:**
   - 3 копии данных
   - 2 разных типа носителей
   - 1 копия в другом месте

2. **Хранилища:**
   - Локально: быстрый доступ для восстановления
   - Облако (S3/Backblaze): защита от локальных катастроф
   - Лента/холодное хранилище: долгосрочное архивирование

3. **Шифрование:**
   - Все backup за пределами trusted zone должны быть зашифрованы
   - Использовать разные ключи для разных окружений

## Ответственность

- **Владелец backup:** Системный администратор
- **Частота проверки:** Еженедельно
- **Период хранения:**
  - PostgreSQL: 30 дней (полные), 7 дней (WAL)
  - Redis: 7 дней
  - Логи: 90 дней
  - Конфигурации: бессрочно (в git)

## Контакты при инцидентах

- Техническая поддержка: support@example.com
- Аварийный номер: +380 XX XXX XX XX
- Slack канал: #backup-alerts