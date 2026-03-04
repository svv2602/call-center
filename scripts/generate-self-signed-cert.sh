#!/usr/bin/env bash
# Generate self-signed TLS certificate for internal deployment.
# Output: nginx/ssl/server.crt, nginx/ssl/server.key
# Usage: ./scripts/generate-self-signed-cert.sh [HOSTNAME]

set -euo pipefail

HOSTNAME="${1:-localhost}"
SSL_DIR="$(dirname "$0")/../nginx/ssl"

mkdir -p "$SSL_DIR"

openssl req -x509 -nodes -days 365 \
    -newkey rsa:2048 \
    -keyout "$SSL_DIR/server.key" \
    -out "$SSL_DIR/server.crt" \
    -subj "/C=UA/ST=Kyiv/L=Kyiv/O=CallCenterAI/CN=$HOSTNAME" \
    -addext "subjectAltName=DNS:$HOSTNAME,DNS:localhost,IP:127.0.0.1"

chmod 600 "$SSL_DIR/server.key"
chmod 644 "$SSL_DIR/server.crt"

echo "Self-signed certificate generated:"
echo "  Certificate: $SSL_DIR/server.crt"
echo "  Private key: $SSL_DIR/server.key"
echo "  Hostname:    $HOSTNAME"
echo "  Valid for:   365 days"
