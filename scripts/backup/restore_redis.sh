#!/usr/bin/env bash
# Restore Redis from a RDB backup file.
#
# Usage: ./scripts/backup/restore_redis.sh <backup_file.rdb.gz>
#
# Stops Redis, replaces dump.rdb, restarts Redis.

set -euo pipefail

BACKUP_FILE="${1:-}"

if [ -z "$BACKUP_FILE" ]; then
    echo "Usage: $0 <backup_file.rdb.gz>"
    echo "Example: $0 /var/backups/callcenter/redis/redis_2026-02-14_041500.rdb.gz"
    exit 1
fi

if [ ! -f "$BACKUP_FILE" ]; then
    echo "ERROR: Backup file not found: $BACKUP_FILE"
    exit 1
fi

echo "=== Redis Restore ==="
echo "Backup file: $BACKUP_FILE"
echo "WARNING: This will overwrite current Redis data!"
echo "Note: Redis data is transient (sessions with TTL). Safe to skip if not needed."
echo ""

COMPOSE_CMD="docker compose"
if ! $COMPOSE_CMD version &>/dev/null; then
    COMPOSE_CMD="docker-compose"
fi

echo "1. Stopping Redis..."
$COMPOSE_CMD stop redis

echo "2. Extracting backup..."
TEMP_RDB=$(mktemp /tmp/dump.rdb.XXXXXX)
if [[ "$BACKUP_FILE" == *.gz ]]; then
    gunzip -c "$BACKUP_FILE" > "$TEMP_RDB"
else
    cp "$BACKUP_FILE" "$TEMP_RDB"
fi

echo "3. Copying RDB into Redis container volume..."
$COMPOSE_CMD cp "$TEMP_RDB" redis:/data/dump.rdb
rm -f "$TEMP_RDB"

echo "4. Restarting Redis..."
$COMPOSE_CMD start redis

echo "5. Waiting for Redis to load data..."
sleep 3
$COMPOSE_CMD exec redis redis-cli ping

echo ""
echo "=== Redis restore completed successfully ==="
