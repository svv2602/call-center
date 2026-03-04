# Фаза 4: K8s & Infrastructure

## Статус
- [x] Не начата
- [x] В процессе
- [x] Завершена

**Начата:** 2026-03-04
**Завершена:** 2026-03-04

## Цель фазы
Заменить NodePort на ClusterIP для AudioSocket. Настроить HPA по custom metric. Подготовить WireGuard документацию.

## Задачи

### 4.1 AudioSocket — ClusterIP
- [x] В `k8s/call-processor/service.yml`:
  ```yaml
  # Было:
  type: NodePort
  # Стало:
  type: ClusterIP
  ```
- [x] Для доступа Asterisk: Internal LoadBalancer или ingress с source IP restriction
- [x] Обновить Asterisk dialplan: AudioSocket endpoint → ClusterIP/LB address
- [x] Документировать: firewall rules для AudioSocket

**Файлы:** `k8s/call-processor/service.yml`, `asterisk/extensions.conf`
**Audit refs:** ARCH-07, CRIT-08

---

### 4.2 HPA — custom metric active_calls
- [x] В `k8s/call-processor/hpa.yml`: добавить custom metric:
  ```yaml
  metrics:
    - type: Pods
      pods:
        metric:
          name: callcenter_active_calls
        target:
          type: AverageValue
          averageValue: 10  # масштабировать при >10 звонков на pod
  ```
- [x] Требуется Prometheus Adapter для custom metrics API
- [x] Создать `k8s/prometheus-adapter/` конфигурацию (или документировать setup)
- [x] Сохранить CPU/memory метрики как fallback

**Файлы:** `k8s/call-processor/hpa.yml`
**Audit refs:** ARCH-08

---

### 4.3 WireGuard — документация и конфигурация
- [x] Создать `doc/technical/wireguard-setup.md` с инструкцией:
  - Генерация ключей (Asterisk + Call Processor)
  - wg0.conf для обоих серверов
  - Обновление dialplan: `AudioSocket(${CALL_UUID},10.0.0.2:9092)`
  - Тестирование: `wg show`, `ping 10.0.0.2`
- [x] Подготовить template конфигов в `k8s/wireguard/` (или `infra/wireguard/`)

NOTE: Created `k8s/prometheus-adapter/config.yml` for Helm values.

**Файлы:** `doc/technical/wireguard-setup.md` (новый)
**Audit refs:** STR-05

---

### 4.4 Тесты
- [x] K8s manifests: validated YAML structure
- [x] Проверить что service.yml валиден

---

## При завершении фазы
1. Выполни коммит:
   ```bash
   git add k8s/ asterisk/ doc/technical/
   git commit -m "checklist(p2-architecture): phase-4 K8s ClusterIP, HPA custom metric, WireGuard docs"
   ```
2. Обнови PROGRESS.md: Текущая фаза: 5
