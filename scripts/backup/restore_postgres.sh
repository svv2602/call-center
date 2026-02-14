#!/usr/bin/env bash
# Restore PostgreSQL from a compressed backup file.
#
# Usage: ./scripts/backup/restore_postgres.sh <backup_file.sql.gz>
#
# Stops call-processor and celery workers before restore,
# restarts them after completion.

set -euo pipefail

BACKUP_FILE="${1:-}"

if [ -z "$BACKUP_FILE" ]; then
    echo "Usage: $0 <backup_file.sql.gz>"
    echo "Example: $0 /var/backups/callcenter/callcenter_2026-02-14_040000.sql.gz"
    exit 1
fi

if [ ! -f "$BACKUP_FILE" ]; then
    echo "ERROR: Backup file not found: $BACKUP_FILE"
    exit 1
fi

echo "=== PostgreSQL Restore ==="
echo "Backup file: $BACKUP_FILE"
echo "WARNING: This will overwrite the current database!"
echo ""

# Determine compose command
COMPOSE_CMD="docker compose"
if ! $COMPOSE_CMD version &>/dev/null; then
    COMPOSE_CMD="docker-compose"
fi

echo "1. Stopping application services..."
$COMPOSE_CMD stop call-processor celery-worker celery-beat 2>/dev/null || true

echo "2. Restoring database from backup..."
if [[ "$BACKUP_FILE" == *.gz ]]; then
    gunzip -c "$BACKUP_FILE" | $COMPOSE_CMD exec -T postgres psql -U callcenter -d callcenter
else
    $COMPOSE_CMD exec -T postgres psql -U callcenter -d callcenter < "$BACKUP_FILE"
fi

echo "3. Restarting application services..."
$COMPOSE_CMD start call-processor celery-worker celery-beat

echo ""
echo "=== Restore completed successfully ==="
echo "Restored from: $BACKUP_FILE"
