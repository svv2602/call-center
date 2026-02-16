# Настройка Asterisk для Call Center AI

Инструкция по подключению существующего Asterisk 20 к системе Call Center AI.

## Содержимое папки

```
asterisk/
  README.md             — эта инструкция
  extensions.conf       — dialplan (входящие → AudioSocket, transfer)
  ari.conf              — конфигурация ARI (REST-интерфейс)
  http.conf             — HTTP-сервер для ARI
  queues.conf           — очередь операторов
```

---

## 1. Пререквизиты

### Asterisk-сервер

- **Asterisk 20** (с поддержкой AudioSocket)
- Модули: `res_audiosocket`, `res_ari`, `res_http_websocket`, `app_queue`
- Настроенный SIP-транк к провайдеру (входящие звонки)

### Call Processor (отдельный сервер)

- Docker 24+, Docker Compose v2+
- Сетевой доступ к Asterisk (LAN, latency < 5ms)
- Порт 9092 открыт для входящих TCP-соединений от Asterisk

### Сеть

| Соединение | Протокол | Порт | Требование |
|---|---|---|---|
| Asterisk → Call Processor | TCP | 9092 | AudioSocket, latency < 5ms |
| Call Processor → Asterisk | HTTP | 8088 | ARI (redirect, caller info) |

---

## 2. Проверка модулей Asterisk

```bash
# Подключиться к CLI
asterisk -rvvv

# Проверить наличие модулей
module show like audiosocket
module show like res_ari
module show like res_http
module show like app_queue
```

Если `res_audiosocket` отсутствует:

```bash
# Загрузить модуль
module load res_audiosocket.so

# Убедиться что модуль в autoload (не в noload)
# /etc/asterisk/modules.conf — НЕ должно быть строки:
#   noload => res_audiosocket.so
```

---

## 3. Установка конфигурации

### 3.1 Dialplan — `extensions.conf`

Скопировать файл из этой папки:

```bash
# Бэкап текущего dialplan
cp /etc/asterisk/extensions.conf /etc/asterisk/extensions.conf.bak.$(date +%Y%m%d)

# Добавить контексты (НЕ перезаписывать — дописать в конец!)
cat asterisk/extensions.conf >> /etc/asterisk/extensions.conf
```

Или вручную добавить контексты `[incoming]` и `[transfer-to-operator]` из файла `extensions.conf`.

**Важно:** В строке `AudioSocket()` указать IP-адрес сервера Call Processor:

```ini
; Локальная разработка (один сервер):
AudioSocket(${UNIQUE_ID},127.0.0.1:9092)

; Два сервера в LAN:
AudioSocket(${UNIQUE_ID},192.168.1.100:9092)

; Через WireGuard (см. раздел 5):
AudioSocket(${UNIQUE_ID},10.0.0.2:9092)
```

### 3.2 ARI — `ari.conf`

```bash
cp asterisk/ari.conf /etc/asterisk/ari.conf
```

Отредактировать пароль в `/etc/asterisk/ari.conf`:

```ini
password = <сгенерировать-надёжный-пароль>
```

Этот же пароль указать в `.env` на сервере Call Processor:

```bash
ARI_URL=http://<asterisk-ip>:8088/ari
ARI_USER=callcenter
ARI_PASSWORD=<тот-же-пароль>
```

### 3.3 HTTP-сервер — `http.conf`

```bash
cp asterisk/http.conf /etc/asterisk/http.conf
```

По умолчанию ARI слушает на `127.0.0.1:8088`. Если Call Processor на другом сервере — поменять `bindaddr` на LAN-адрес Asterisk или `0.0.0.0` (с firewall!).

### 3.4 Очередь операторов — `queues.conf`

```bash
cp asterisk/queues.conf /etc/asterisk/queues.conf
```

Добавить в очередь SIP-номера операторов:

```ini
member => PJSIP/operator1
member => PJSIP/operator2
```

### 3.5 Привязка входящего SIP-транка к контексту

В конфигурации SIP-транка (`pjsip.conf` или `sip.conf`) убедиться что входящие звонки попадают в контекст `incoming`:

```ini
; pjsip.conf — endpoint для SIP-транка
[trunk-provider]
type = endpoint
context = incoming        ; ← должен совпадать с контекстом в dialplan
...
```

---

## 4. Применение конфигурации

```bash
# Перезагрузить dialplan
asterisk -rx "dialplan reload"

# Перезагрузить ARI
asterisk -rx "module reload res_ari.so"

# Перезагрузить HTTP
asterisk -rx "module reload res_http_websocket.so"

# Перезагрузить очереди
asterisk -rx "module reload app_queue.so"
```

Для полной перезагрузки (если много изменений):

```bash
systemctl restart asterisk
```

---

## 5. Защита AudioSocket (WireGuard)

AudioSocket передаёт аудио **без шифрования** (plain TCP). Если Asterisk и Call Processor на разных серверах — необходим VPN.

| Сценарий | Рекомендация |
|---|---|
| Один сервер | `127.0.0.1` — безопасно |
| Один LAN | WireGuard (рекомендуется) |
| Разные сети / интернет | **Обязательно** WireGuard/VPN |

### Настройка WireGuard

**На обоих серверах:**

```bash
apt install wireguard
wg genkey | tee privatekey | wg pubkey > publickey
```

**Asterisk-сервер** — `/etc/wireguard/wg0.conf`:

```ini
[Interface]
PrivateKey = <asterisk-private-key>
Address = 10.0.0.1/24
ListenPort = 51820

[Peer]
PublicKey = <call-processor-public-key>
Endpoint = <call-processor-external-ip>:51820
AllowedIPs = 10.0.0.2/32
PersistentKeepalive = 25
```

**Call Processor** — `/etc/wireguard/wg0.conf`:

```ini
[Interface]
PrivateKey = <call-processor-private-key>
Address = 10.0.0.2/24
ListenPort = 51820

[Peer]
PublicKey = <asterisk-public-key>
Endpoint = <asterisk-external-ip>:51820
AllowedIPs = 10.0.0.1/32
PersistentKeepalive = 25
```

**Активировать на обоих серверах:**

```bash
systemctl enable wg-quick@wg0
systemctl start wg-quick@wg0

# Проверить туннель
ping 10.0.0.2   # с Asterisk
ping 10.0.0.1   # с Call Processor
```

**Обновить адреса:**

- `extensions.conf`: `AudioSocket(${UNIQUE_ID},10.0.0.2:9092)`
- `.env` на Call Processor: `ARI_URL=http://10.0.0.1:8088/ari`

---

## 6. Firewall

### На сервере Asterisk

```bash
# SIP (только от IP провайдера)
ufw allow from <sip-provider-ip> to any port 5060 proto udp
ufw allow from <sip-provider-ip> to any port 10000:20000 proto udp

# WireGuard (если используется)
ufw allow from <call-processor-ip> to any port 51820 proto udp

# ARI (только от Call Processor)
ufw allow from <call-processor-lan-ip> to any port 8088 proto tcp

# Запретить всё остальное
ufw enable
```

### На сервере Call Processor

```bash
# AudioSocket (только от Asterisk)
ufw allow from <asterisk-lan-ip> to any port 9092 proto tcp

# WireGuard (если используется)
ufw allow from <asterisk-ip> to any port 51820 proto udp

ufw enable
```

---

## 7. Безопасность SIP

### fail2ban

```bash
apt install fail2ban
```

Создать `/etc/fail2ban/jail.d/asterisk.conf`:

```ini
[asterisk]
enabled  = true
filter   = asterisk
action   = iptables-allports[name=asterisk, protocol=all]
logpath  = /var/log/asterisk/messages
maxretry = 3
findtime = 600
bantime  = 3600
```

```bash
systemctl enable fail2ban
systemctl restart fail2ban
```

### Дополнительно

- SIP-пароли: минимум 20 символов, случайные
- IP whitelist для SIP-транка (только адреса провайдера)
- Отключить гостевые SIP-звонки: `allowguest=no` (sip.conf) / `allow_transfer=no` для неавторизованных (pjsip.conf)

---

## 8. Проверка

### 8.1 Проверить что Call Processor запущен и слушает

```bash
# На сервере Call Processor
ss -tlnp | grep 9092
# Ожидание: LISTEN на 0.0.0.0:9092 или :::9092
```

### 8.2 Проверить ARI

```bash
# С сервера Call Processor
curl -s -u callcenter:<ari-password> http://<asterisk-ip>:8088/ari/asterisk/info | head -20
# Ожидание: JSON с информацией об Asterisk
```

### 8.3 Проверить dialplan

```bash
# На Asterisk
asterisk -rx "dialplan show incoming"
# Ожидание: контекст incoming с AudioSocket

asterisk -rx "dialplan show transfer-to-operator"
# Ожидание: контекст transfer-to-operator с Queue
```

### 8.4 Проверить очередь операторов

```bash
asterisk -rx "queue show operators"
# Ожидание: очередь с подключёнными операторами
```

### 8.5 Тестовый звонок

1. Позвонить на входящий номер
2. Бот должен ответить: *"Добрий день! Вас вітає автоматизована система..."*
3. Сказать: *"Мені потрібні зимові шини на Тойоту Камрі"*
4. Бот должен вызвать `search_tires` и предложить варианты

### 8.6 Проверить transfer на оператора

1. Во время звонка сказать: *"Переключіть мене на оператора"*
2. Бот должен подтвердить и вызвать `transfer_to_operator`
3. Звонок должен попасть в очередь `operators`

---

## 9. Troubleshooting

### AudioSocket: "Connection refused" на порт 9092

| Причина | Проверка | Решение |
|---|---|---|
| Call Processor не запущен | `ss -tlnp \| grep 9092` | `docker compose up -d` |
| Порт занят | `lsof -i :9092` | Остановить конфликтующий процесс |
| Firewall | `iptables -L -n \| grep 9092` | Открыть порт |
| Неправильный IP в dialplan | Проверить `extensions.conf` | Исправить адрес |

### Нет аудио (соединение есть, звук не идёт)

- Проверить формат: `Set(CHANNEL(audioreadformat)=slin16)` в dialplan
- Убедиться что `Answer()` вызван **до** `AudioSocket()`
- Проверить логи: `docker compose logs -f call-processor | grep audio`

### Аудио искажено

- Sample rate должен быть 16kHz в обе стороны
- PCM: 16-bit signed, little-endian
- Убедиться что нет двойной конвертации кодеков

### Transfer на оператора не работает

- Проверить ARI: `curl -u callcenter:<pass> http://<ip>:8088/ari/asterisk/info`
- Проверить контекст: `asterisk -rx "dialplan show transfer-to-operator"`
- Проверить очередь: `asterisk -rx "queue show operators"`
- Проверить логи ARI в Call Processor

### Эхо при разговоре

- Включить echo cancellation: `echocancel=yes` в `sip.conf`
- Проверить что TTS-аудио не попадает обратно в STT (mute на время воспроизведения)

---

## 10. Откат

Если система работает нестабильно — быстрое переключение всего трафика на операторов:

```bash
# 1. В extensions.conf — заменить AudioSocket на Queue
# [incoming]
# exten => _X.,1,Answer()
#  same => n,Queue(operators,t,,,120)
#  same => n,Hangup()

# 2. Применить
asterisk -rx "dialplan reload"

# Время отката: < 1 минуты
```

Для возврата к AI-агенту — раскомментировать AudioSocket обратно и перезагрузить dialplan.

---

## Протокол AudioSocket (справка)

```
┌──────────┬───────────────┬────────────────┐
│ Type (1B)│ Length (2B BE) │ Payload (N B)  │
└──────────┴───────────────┴────────────────┘
```

| Type | Описание |
|---|---|
| `0x01` | UUID канала (call_id) — первый пакет при соединении |
| `0x10` | Аудио данные (PCM 16kHz, 16-bit LE) |
| `0x00` | Hangup (клиент повесил трубку) |
| `0xFF` | Ошибка |

Поток двунаправленный: Asterisk отправляет голос клиента, Call Processor отвечает синтезированным TTS.
