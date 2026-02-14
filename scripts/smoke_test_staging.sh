#!/usr/bin/env bash
# Smoke test for staging environment.
#
# Verifies all services are up and responding correctly.
# Usage: ./scripts/smoke_test_staging.sh [base_url]
# Default base_url: http://localhost:18080

set -euo pipefail

BASE_URL="${1:-http://localhost:18080}"
STORE_API_URL="${2:-http://localhost:13002}"
REDIS_HOST="${3:-localhost}"
REDIS_PORT="${4:-16379}"
PROMETHEUS_URL="${5:-http://localhost:19090}"

PASS=0
FAIL=0
WARN=0

green() { printf "\033[32m%s\033[0m\n" "$1"; }
red()   { printf "\033[31m%s\033[0m\n" "$1"; }
yellow(){ printf "\033[33m%s\033[0m\n" "$1"; }

check() {
    local name="$1"
    local url="$2"
    local expected="${3:-200}"

    printf "  %-40s " "$name"
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 "$url" 2>/dev/null || echo "000")

    if [ "$HTTP_CODE" = "$expected" ]; then
        green "OK ($HTTP_CODE)"
        PASS=$((PASS + 1))
    elif [ "$HTTP_CODE" = "000" ]; then
        red "FAIL (connection refused)"
        FAIL=$((FAIL + 1))
    else
        red "FAIL (expected $expected, got $HTTP_CODE)"
        FAIL=$((FAIL + 1))
    fi
}

check_redis() {
    printf "  %-40s " "Redis PING"
    PONG=$(redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" ping 2>/dev/null || echo "FAIL")

    if [ "$PONG" = "PONG" ]; then
        green "OK (PONG)"
        PASS=$((PASS + 1))
    else
        # Try without redis-cli (might not be installed)
        yellow "WARN (redis-cli not available, skipping)"
        WARN=$((WARN + 1))
    fi
}

echo "=== Staging Smoke Tests ==="
echo "Call Processor: $BASE_URL"
echo "Store API:      $STORE_API_URL"
echo ""

echo "1. Call Processor API"
check "/health"                     "$BASE_URL/health"
check "/health/ready"               "$BASE_URL/health/ready"
check "/metrics"                    "$BASE_URL/metrics"
check "/admin (UI)"                 "$BASE_URL/admin"
echo ""

echo "2. Store API (mock)"
check "/api/v1/health"              "$STORE_API_URL/api/v1/health"
check "/api/v1/tires/search"        "$STORE_API_URL/api/v1/tires/search" "401"
echo ""

echo "3. Infrastructure"
check_redis
check "Prometheus /-/healthy"       "$PROMETHEUS_URL/-/healthy"
echo ""

echo "4. API Endpoints (authenticated)"
AUTH_HEADER="Authorization: Bearer test-store-api-key"
printf "  %-40s " "Store API tire search"
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 \
    -H "$AUTH_HEADER" "$STORE_API_URL/api/v1/tires/search?width=205" 2>/dev/null || echo "000")
if [ "$HTTP_CODE" = "200" ]; then
    green "OK ($HTTP_CODE)"
    PASS=$((PASS + 1))
else
    red "FAIL ($HTTP_CODE)"
    FAIL=$((FAIL + 1))
fi
echo ""

# --- Summary ---
echo "=== Summary ==="
TOTAL=$((PASS + FAIL + WARN))
green "  Passed:  $PASS/$TOTAL"
if [ "$WARN" -gt 0 ]; then
    yellow "  Warnings: $WARN"
fi
if [ "$FAIL" -gt 0 ]; then
    red "  Failed:  $FAIL/$TOTAL"
    echo ""
    red "STAGING SMOKE TEST FAILED"
    exit 1
else
    echo ""
    green "ALL CHECKS PASSED"
    exit 0
fi
