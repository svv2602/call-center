#!/bin/sh
# Substitute env vars in alertmanager config template, then start alertmanager.
# Only ALERTMANAGER_* vars are substituted to avoid breaking Go template syntax
# (e.g. {{ .Labels.alertname }}).
envsubst '${ALERTMANAGER_TELEGRAM_BOT_TOKEN} ${ALERTMANAGER_TELEGRAM_CHAT_ID}' \
  < /etc/alertmanager/alertmanager.yml.tmpl \
  > /etc/alertmanager/alertmanager.yml

exec /bin/alertmanager --config.file=/etc/alertmanager/alertmanager.yml "$@"
