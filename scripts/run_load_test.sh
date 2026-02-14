#!/usr/bin/env bash
# Run load tests against the Call Center AI.
#
# Usage:
#   ./scripts/run_load_test.sh [profile]
#
# Profiles:
#   normal  - 20 concurrent calls, 30 min (default)
#   peak    - 50 concurrent calls, 15 min
#   stress  - 100 concurrent calls, 10 min
#   quick   - 5 concurrent calls, 2 min (for CI/local verification)
#
# Environment:
#   LOAD_AUDIOSOCKET_HOST  - AudioSocket host (default: 127.0.0.1)
#   LOAD_AUDIOSOCKET_PORT  - AudioSocket port (default: 9092)
#   LOAD_CALL_DURATION     - Call duration in seconds (default: 30)

set -euo pipefail

PROFILE="${1:-normal}"
HOST="${LOAD_HTTP_HOST:-http://localhost:8080}"

case "$PROFILE" in
    normal)
        USERS=20
        RATE=2
        DURATION="30m"
        echo "=== Load Test: Normal (20 concurrent, 30 min) ==="
        ;;
    peak)
        USERS=50
        RATE=5
        DURATION="15m"
        echo "=== Load Test: Peak (50 concurrent, 15 min) ==="
        ;;
    stress)
        USERS=100
        RATE=10
        DURATION="10m"
        echo "=== Load Test: Stress (100 concurrent, 10 min) ==="
        ;;
    quick)
        USERS=5
        RATE=2
        DURATION="2m"
        echo "=== Load Test: Quick (5 concurrent, 2 min) ==="
        ;;
    *)
        echo "Unknown profile: $PROFILE"
        echo "Available: normal, peak, stress, quick"
        exit 1
        ;;
esac

echo "Host: $HOST"
echo "AudioSocket: ${LOAD_AUDIOSOCKET_HOST:-127.0.0.1}:${LOAD_AUDIOSOCKET_PORT:-9092}"
echo ""

locust \
    -f tests/load/locustfile.py \
    --host="$HOST" \
    -u "$USERS" \
    -r "$RATE" \
    -t "$DURATION" \
    --headless \
    --csv="load_test_${PROFILE}_$(date +%Y%m%d_%H%M%S)" \
    --html="load_test_${PROFILE}_$(date +%Y%m%d_%H%M%S).html"

echo ""
echo "=== Load test completed. Results saved to load_test_${PROFILE}_*.csv/html ==="
