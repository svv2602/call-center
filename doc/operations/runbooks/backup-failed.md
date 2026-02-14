# Runbook: Резервная копия не удалась или проверка не пройдена

**Дата создания:** 2026-02-14  
**Владелец:** DevOps / Database Administrator  
**Приоритет:** CRITICAL  

## Описание

Резервные копии (backups) критичны для восстановления данных после сбоев. Система выполняет ежедневные полные резервные копии PostgreSQL и Redis. Если backup не удался или проверка целостности (verification) не пройдена, это угроза для восстанавливаемости системы. Необходимо срочно действовать.

## Симптомы

- Alert в Prometheus: `backup_status = "failed"` или `backup_verification_failed = 1`
- Alert в PagerDuty: "Backup Failed" с уровнем CRITICAL
- Логи: `ERROR: Backup failed` или `CRITICAL: Backup verification failed`
- Диск почти полный (может быть причина отказа): `disk_usage_percent > 90`
- Отсутствуют свежие файлы backup'а в хранилище (`/backups`, S3, GCS, и т.д.)
- Метрика `last_successful_backup_timestamp` не обновляется более 24 часов

## Диагностика

### 1. Проверить статус последнего backup'а

```bash
# Проверить timestamp последнего успешного backup'а
curl -s "http://localhost:9090/api/v1/query?query=last_successful_backup_timestamp" | jq '.data.result'

# Проверить, когда был последний попыток
curl -s "http://localhost:9090/api/v1/query?query=backup_last_attempt_timestamp" | jq '.data.result'

# Проверить статус backup'а (успех/ошибка)
curl -s "http://localhost:9090/api/v1/query?query=backup_status" | jq '.data.result'
```

### 2. Проверить логи backup'а

```bash
# Логи backup service (если работает в Docker)
docker compose logs --tail=100 backup-service

# Или проверить systemd logs (если это Linux service)
journalctl -u call-center-backup -n 100 --no-pager

# Поиск ошибок в логах
docker compose logs backup-service | grep -i "error\|failed\|critical"

# Полный лог последнего запуска
docker compose logs backup-service 2>&1 | tail -200
```

### 3. Проверить наличие файлов backup'а

```bash
# Локальное хранилище
ls -lah /backups/

# Проверить размер файлов
du -sh /backups/*

# Если backup'ы на S3
aws s3 ls s3://call-center-backups/ --recursive

# Если на GCS
gsutil ls -l gs://call-center-backups/

# Проверить дату последнего backup'а
ls -lt /backups/ | head -5
```

### 4. Проверить свободное место на диске

```bash
# Проверить свободное место
df -h /backups

# Проверить использование
du -sh /backups

# Проверить inode (может быть проблема с inode exhaustion)
df -i /backups
```

### 5. Проверить PostgreSQL

```bash
# Проверить, что PostgreSQL работает
pg_isready -h localhost -p 5432

# Проверить размер базы данных
docker exec -it call-center-postgres-1 psql -U postgres -c "SELECT datname, pg_size_pretty(pg_database_size(datname)) FROM pg_database ORDER BY pg_database_size(datname) DESC;"

# Проверить соединения (может быть слишком много открытых)
docker exec -it call-center-postgres-1 psql -U postgres -c "SELECT datname, count(*) FROM pg_stat_activity GROUP BY datname;"

# Проверить логи PostgreSQL
docker compose logs postgres | grep -i "error\|failed" | tail -20
```

### 6. Проверить Redis

```bash
# Проверить, что Redis работает
redis-cli ping

# Проверить размер Redis
redis-cli INFO memory

# Проверить, много ли ключей
redis-cli DBSIZE

# Проверить, есть ли проблемы с синхронизацией
redis-cli INFO replication
```

### 7. Проверить конфигурацию backup'а

```bash
# Проверить переменные окружения для backup
grep -i "backup\|s3\|gcs" /path/to/.env

# Проверить credentials для облачного хранилища
# Ищите ошибки аутентификации в логах
docker compose logs backup-service | grep -i "auth\|permission\|denied\|credential"

# Проверить, имеется ли доступ к хранилищу
# Для S3:
aws s3 ls s3://call-center-backups/ --region us-east-1

# Для GCS:
gsutil ls gs://call-center-backups/
```

### 8. Проверить статус проверки целостности (verification)

```bash
# Запустить проверку вручную
call-center-admin db verify-backup /backups/latest.sql.gz

# Или через CLI
docker exec call-center-backup-service backup-verify /backups/latest.sql.gz

# Проверить лог проверки
docker compose logs backup-service | grep -i "verify\|verification"
```

## Решение

### Шаг 1: Немедленно проверить наличие места на диске

```bash
# Проверить занято/свободно
df -h /backups

# Если диск более 90% занят, срочно удалить старые backup'ы
# ВНИМАНИЕ: Убедитесь, что вы не удаляете нужные backup'ы!

# Список backup'ов отсортирован по дате
ls -lrt /backups/ | head -20

# Удалить backup'ы старше 30 дней (если есть более новые)
find /backups -name "*.sql.gz" -mtime +30 -delete

# После очистки проверить место
df -h /backups
```

### Шаг 2: Убедиться, что PostgreSQL здоров

```bash
# Проверить статус
docker compose ps postgres

# Если не запущен:
docker compose start postgres

# Дождаться, пока стартует
sleep 10

# Проверить доступность
pg_isready -h localhost -p 5432

# Если есть ошибки в логах:
docker compose logs postgres | tail -50
```

### Шаг 3: Убедиться, что Redis здоров

```bash
# Проверить статус
docker compose ps redis

# Если не запущен:
docker compose start redis

# Дождаться, пока стартует
sleep 5

# Проверить доступность
redis-cli ping

# Если есть ошибки в логах:
docker compose logs redis | tail -30
```

### Шаг 4: Перезагрузить backup service

```bash
# Перезагрузить сервис
docker compose restart backup-service

# Дождаться перезагрузки
sleep 10

# Проверить логи
docker compose logs --tail=20 backup-service

# Проверить, что метрика обновилась
curl -s "http://localhost:9090/api/v1/query?query=backup_status" | jq '.data.result'
```

### Шаг 5: Запустить backup вручную

```bash
# Если automatic backup не работает, запустить вручную
docker exec call-center-backup-service backup-now

# Или через CLI
call-center-admin db backup

# Дождаться завершения (может занять минуты/часы)
# Проверить логи
docker compose logs -f backup-service | grep -i "progress\|complete\|error"

# Проверить, был ли создан файл
ls -lah /backups/ | head -5
```

### Шаг 6: Проверить целостность backup'а

```bash
# Проверить последний backup файл
BACKUP_FILE=$(ls -t /backups/*.sql.gz 2>/dev/null | head -1)

# Запустить проверку
call-center-admin db verify-backup "$BACKUP_FILE"

# Если файл повреждён, нужно сделать новый backup
# Если файл OK, проблема в процессе verification
```

### Шаг 7: Если проблема в credentials облачного хранилища

```bash
# Проверить AWS credentials
aws sts get-caller-identity

# Если нет доступа, обновить credentials в .env
# И перезагрузить backup service
docker compose restart backup-service

# Аналогично для GCS:
gcloud auth list
gcloud config get-value project

# Проверить bucket permissions
aws s3api get-bucket-versioning --bucket call-center-backups
```

### Шаг 8: Если ничего не помогает

```bash
# Может потребоваться разовая резервная копия в боковое хранилище
# Экспортировать PostgreSQL вручную
docker exec call-center-postgres-1 pg_dump -U postgres -d call_center -F custom -f /tmp/manual_backup.dump

# Скопировать на безопасное место
cp /tmp/manual_backup.dump /secure-backup-location/

# Экспортировать Redis
docker exec call-center-redis-1 redis-cli --rdb /tmp/redis_backup.rdb

# Скопировать
cp /tmp/redis_backup.rdb /secure-backup-location/

# Проверить файлы
ls -lah /secure-backup-location/
```

## Эскалация

### Если backup не восстановлен после 30 минут:

1. **Database Administrator**
   - Проверить логи PostgreSQL более детально
   - Может требуется VACUUM или ANALYZE
   - Может потребоваться manual dump с низким IO impact

2. **DevOps Engineer**
   - Проверить хранилище (S3/GCS) permissions
   - Может потребоваться обновить credentials
   - Может потребоваться change storage backend

3. **Storage / Infrastructure Team**
   - Проверить, достаточно ли disk space на инфраструктуре
   - Может потребоваться add new storage
   - Может потребоваться optimize backup strategy (incremental backups)

4. **SRE Lead / CTO**
   - Активировать CRITICAL инцидент
   - Может потребоваться notification внешнему хранилищу (S3, GCS)
   - Может потребоваться external backup service

### Контакты эскалации

- **Database Administrator:** @dba-lead
- **DevOps On-Call:** @devops-oncall
- **Storage / Infrastructure:** @storage-team
- **SRE Lead:** @sre-lead
- **CTO (для CRITICAL issues):** @cto

### Восстановление из backup'а (если требуется)

```bash
# Восстановить PostgreSQL из backup'а
# ОСТОРОЖНО: Это перезапишет текущие данные!

# 1. Остановить Call Processor (чтобы избежать записей)
docker compose stop call-processor

# 2. Остановить PostgreSQL
docker compose stop postgres

# 3. Очистить данные PostgreSQL
docker volume rm call_center_postgres_data

# 4. Запустить PostgreSQL (создаст пустой инстанс)
docker compose up -d postgres
sleep 10

# 5. Восстановить из backup'а
docker exec -i call-center-postgres-1 pg_restore -U postgres -d call_center < /backups/backup.dump

# 6. Проверить восстановление
docker exec call-center-postgres-1 psql -U postgres -d call_center -c "SELECT COUNT(*) FROM calls;"

# 7. Запустить Call Processor
docker compose up -d call-processor

# 8. Проверить, что система работает
curl -s http://localhost:8080/health | jq '.'
```

