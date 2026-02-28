#!/usr/bin/env bash
# Add Grafana reverse proxy to Nginx config.
# Run with: sudo bash scripts/setup_nginx_grafana.sh

set -euo pipefail

NGINX_CONF="/etc/nginx/sites-available/default"

if grep -q 'location /grafana/' "$NGINX_CONF"; then
    echo "Grafana proxy already configured in $NGINX_CONF"
    exit 0
fi

# Insert Grafana location block before the catch-all "location /"
sed -i '/location \/ {/i \
    location /grafana/ {\
        proxy_pass http://127.0.0.1:3000;\
        proxy_set_header Host $host;\
        proxy_set_header X-Real-IP $remote_addr;\
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\
        proxy_set_header X-Forwarded-Proto $scheme;\
        proxy_http_version 1.1;\
        proxy_set_header Upgrade $http_upgrade;\
        proxy_set_header Connection "upgrade";\
        proxy_hide_header X-Frame-Options;\
    }\
' "$NGINX_CONF"

# Test and reload
nginx -t && systemctl reload nginx
echo "Grafana proxy configured at /grafana/"
