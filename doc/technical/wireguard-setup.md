# WireGuard VPN: Asterisk ↔ Call Processor

## Зачем

Протокол AudioSocket не поддерживает TLS. Без шифрования аудиоданные передаются
в открытом виде между Asterisk и Call Processor. WireGuard создаёт зашифрованный
туннель (ChaCha20-Poly1305) с минимальным overhead (~60 байт на пакет).

## Схема

```
Asterisk (10.0.0.1/32) ──── WireGuard tunnel ──── Call Processor (10.0.0.2/32)
  wg0: 10.0.0.1                                     wg0: 10.0.0.2
  UDP :51820                                         UDP :51820
```

## 1. Генерация ключей

На **каждом** сервере:

```bash
# Asterisk server
wg genkey | tee /etc/wireguard/private.key | wg pubkey > /etc/wireguard/public.key
chmod 600 /etc/wireguard/private.key

# Call Processor server
wg genkey | tee /etc/wireguard/private.key | wg pubkey > /etc/wireguard/public.key
chmod 600 /etc/wireguard/private.key
```

## 2. Конфигурация Asterisk (`/etc/wireguard/wg0.conf`)

```ini
[Interface]
Address = 10.0.0.1/32
PrivateKey = <ASTERISK_PRIVATE_KEY>
ListenPort = 51820

[Peer]
# Call Processor
PublicKey = <CALL_PROCESSOR_PUBLIC_KEY>
AllowedIPs = 10.0.0.2/32
Endpoint = <CALL_PROCESSOR_PUBLIC_IP>:51820
PersistentKeepalive = 25
```

## 3. Конфигурация Call Processor (`/etc/wireguard/wg0.conf`)

```ini
[Interface]
Address = 10.0.0.2/32
PrivateKey = <CALL_PROCESSOR_PRIVATE_KEY>
ListenPort = 51820

[Peer]
# Asterisk
PublicKey = <ASTERISK_PUBLIC_KEY>
AllowedIPs = 10.0.0.1/32
Endpoint = <ASTERISK_PUBLIC_IP>:51820
PersistentKeepalive = 25
```

## 4. Запуск и включение

```bash
# На обоих серверах:
sudo systemctl enable --now wg-quick@wg0
```

## 5. Обновление Asterisk dialplan

В `extensions.conf` изменить адрес AudioSocket:

```ini
; Было:
; same => n,AudioSocket(${CALL_UUID},192.168.11.53:9092)

; Стало (через WireGuard):
same => n,AudioSocket(${CALL_UUID},10.0.0.2:9092)
```

## 6. Тестирование

```bash
# На Asterisk:
sudo wg show
ping -c 3 10.0.0.2

# На Call Processor:
sudo wg show
ping -c 3 10.0.0.1

# Проверка AudioSocket через туннель:
nc -zv 10.0.0.2 9092
```

## 7. Firewall

```bash
# На обоих серверах: разрешить WireGuard UDP
sudo ufw allow 51820/udp

# На Call Processor: AudioSocket только через WireGuard
sudo ufw allow from 10.0.0.1 to any port 9092 proto tcp
sudo ufw deny 9092/tcp  # запретить прямой доступ
```

## 8. K8s вариант

При развёртывании Call Processor в Kubernetes используйте sidecar контейнер
с WireGuard или DaemonSet. AudioSocket Service должен быть ClusterIP
(не NodePort) — см. `k8s/call-processor/service.yml`.

Asterisk подключается к WireGuard endpoint ноды, трафик маршрутизируется
внутри кластера через ClusterIP.

## Мониторинг

```bash
# Статус туннеля:
sudo wg show

# Метрики (latest handshake, transfer):
sudo wg show wg0 latest-handshakes
sudo wg show wg0 transfer
```

Добавить проверку в Prometheus через `wireguard_exporter` (порт 9586)
для алертинга при потере туннеля.
