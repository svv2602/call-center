#!/usr/bin/env bash
# Verify backup integrity for all system components.
#
# Usage: ./scripts/backup/test_backup.sh [backup_dir]
# Default backup_dir: /var/backups/callcenter

set -euo pipefail

BACKUP_DIR="${1:-/var/backups/callcenter}"
ERRORS=0

echo "=== Backup Verification ==="
echo "Backup directory: $BACKUP_DIR"
echo ""

# --- PostgreSQL backups ---
echo "1. PostgreSQL backups:"
PG_DIR="$BACKUP_DIR"
PG_LATEST=$(find "$PG_DIR" -maxdepth 1 -name "callcenter_*.sql.gz" -type f 2>/dev/null | sort -r | head -1)

if [ -z "$PG_LATEST" ]; then
    echo "   FAIL: No PostgreSQL backups found in $PG_DIR"
    ERRORS=$((ERRORS + 1))
else
    echo "   Latest: $(basename "$PG_LATEST")"

    # Check integrity
    if gunzip -t "$PG_LATEST" 2>/dev/null; then
        echo "   OK: gzip integrity verified"
    else
        echo "   FAIL: Corrupt gzip file"
        ERRORS=$((ERRORS + 1))
    fi

    # Check freshness (< 26 hours)
    BACKUP_AGE=$(( $(date +%s) - $(stat -c %Y "$PG_LATEST" 2>/dev/null || stat -f %m "$PG_LATEST") ))
    HOURS=$((BACKUP_AGE / 3600))
    if [ "$BACKUP_AGE" -lt 93600 ]; then
        echo "   OK: Fresh (${HOURS}h ago)"
    else
        echo "   WARN: Stale (${HOURS}h ago, expected <26h)"
        ERRORS=$((ERRORS + 1))
    fi

    # Check size (> 1KB)
    SIZE=$(stat -c %s "$PG_LATEST" 2>/dev/null || stat -f %z "$PG_LATEST")
    if [ "$SIZE" -gt 1024 ]; then
        echo "   OK: Size $(numfmt --to=iec "$SIZE" 2>/dev/null || echo "${SIZE}B")"
    else
        echo "   WARN: Suspiciously small (${SIZE} bytes)"
        ERRORS=$((ERRORS + 1))
    fi
fi
echo ""

# --- Redis backups ---
echo "2. Redis backups:"
REDIS_DIR="$BACKUP_DIR/redis"
REDIS_LATEST=$(find "$REDIS_DIR" -maxdepth 1 -name "redis_*.rdb.gz" -type f 2>/dev/null | sort -r | head -1)

if [ -z "$REDIS_LATEST" ]; then
    echo "   INFO: No Redis backups found (Redis data is transient — may be OK)"
else
    echo "   Latest: $(basename "$REDIS_LATEST")"
    if gunzip -t "$REDIS_LATEST" 2>/dev/null; then
        echo "   OK: gzip integrity verified"
    else
        echo "   FAIL: Corrupt gzip file"
        ERRORS=$((ERRORS + 1))
    fi
fi
echo ""

# --- Knowledge base backups ---
echo "3. Knowledge base backups:"
KB_DIR="$BACKUP_DIR/knowledge"
KB_LATEST=$(find "$KB_DIR" -maxdepth 1 -name "knowledge_*.tar.gz" -type f 2>/dev/null | sort -r | head -1)

if [ -z "$KB_LATEST" ]; then
    echo "   INFO: No knowledge base backups found"
else
    echo "   Latest: $(basename "$KB_LATEST")"
    if tar -tzf "$KB_LATEST" >/dev/null 2>&1; then
        echo "   OK: tar.gz integrity verified"
    else
        echo "   FAIL: Corrupt archive"
        ERRORS=$((ERRORS + 1))
    fi
fi
echo ""

# --- Summary ---
echo "=== Summary ==="
if [ "$ERRORS" -eq 0 ]; then
    echo "All checks passed."
    exit 0
else
    echo "FAILED: $ERRORS error(s) found."
    exit 1
fi
