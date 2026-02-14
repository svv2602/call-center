# Фаза 4: Kubernetes

## Статус
- [x] Не начата
- [x] В процессе
- [x] Завершена

**Начата:** 2026-02-14
**Завершена:** 2026-02-14

## Цель фазы
Создать Kubernetes manifests для развёртывания приложения с auto-scaling, health checks и resilience. После этой фазы приложение можно развернуть в K8s кластере одной командой.

## Задачи

### 4.0 ОБЯЗАТЕЛЬНО: Анализ и планирование

#### A. Анализ существующего кода
- [x] Изучить `docker-compose.yml` — все сервисы, порты, volumes, env variables
- [x] Изучить `docker-compose.staging.yml` — staging конфигурация
- [x] Изучить `Dockerfile` — как собирается приложение
- [x] Изучить health check endpoints: `/health`, `/health/ready`
- [x] Изучить `src/config.py` — все env variables для конфигурации

**Команды для поиска:**
```bash
# Docker
cat Dockerfile
cat docker-compose.yml
# Health checks
grep -rn "health\|ready\|liveness" src/api/
# Все env variables
grep -rn "os.environ\|getenv\|ENV" src/config.py
# Порты
grep -n "port\|PORT" docker-compose.yml
```

#### B. Анализ зависимостей
- [x] Какие сервисы нужны: app, PostgreSQL, Redis, Prometheus, Grafana
- [x] Какие volumes/PV нужны: PostgreSQL data, Redis data, Grafana dashboards
- [x] Какие secrets нужны: DB credentials, API keys, JWT secret
- [x] Какие ConfigMaps нужны: app config, Prometheus config, Grafana provisioning

**Компоненты K8s:**
- Deployments: call-processor, admin-api, celery-worker, celery-beat
- StatefulSets: PostgreSQL, Redis
- Services: ClusterIP для внутренних, NodePort для AudioSocket
- Ingress: admin UI, API, Grafana
- HPA: call-processor (по CPU/memory)
- Secrets: call-center-secrets, google-credentials
- ConfigMaps: call-center-config, prometheus-config, prometheus-alerts, grafana-provisioning-*

#### C. Проверка архитектуры
- [x] Namespace: `call-center`
- [x] Resource limits для каждого pod
- [x] Readiness/liveness probes на basis health endpoints
- [x] Anti-affinity для HA
- [x] PDB (PodDisruptionBudget) для zero-downtime updates

**Референс-модуль:** `docker-compose.yml` (все сервисы и их конфигурация)

**Цель:** Понять существующие паттерны проекта ПЕРЕД написанием кода.

**Заметки для переиспользования:** Ports: 8080 (API/metrics), 9092 (AudioSocket). Health endpoints: /health (liveness), /health/ready (readiness). 10 docker-compose services mapped to K8s resources.

---

### 4.1 Namespace и базовые ресурсы

- [x] Создать `k8s/namespace.yml` — namespace `call-center`
- [x] Создать `k8s/secrets.yml` — шаблон для secrets (с placeholder-ами, не реальные значения)
- [x] Создать `k8s/configmap.yml` — ConfigMap с app конфигурацией
- [x] Создать `k8s/README.md` — инструкции по развёртыванию

**Файлы:** `k8s/`
**Заметки:** Secrets шаблон — placeholder values (base64 "change-me"), реальные через `kubectl create secret`. README содержит пошаговую инструкцию деплоя, Kustomize usage, рекомендации по production (managed DB/Redis).

---

### 4.2 Application Deployments

- [x] `k8s/call-processor/deployment.yml` — Call Processor (AudioSocket + API):
  - Replicas: 2 (min)
  - Resources: requests 256Mi/250m, limits 512Mi/500m
  - Liveness: `/health` (HTTP, period 30s)
  - Readiness: `/health/ready` (HTTP, period 10s)
  - Env from ConfigMap + Secrets
  - Port: 8080 (API), 9092 (AudioSocket)
- [x] `k8s/call-processor/service.yml` — ClusterIP для API, NodePort для AudioSocket
- [x] `k8s/call-processor/hpa.yml` — HPA: min 2, max 10, target CPU 70%
- [x] `k8s/call-processor/pdb.yml` — PDB: minAvailable 1

**Файлы:** `k8s/call-processor/`
**Заметки:** Также созданы celery-worker-deployment.yml (1 replica, 2Gi limit) и celery-beat-deployment.yml (1 replica, 512Mi limit). Pod anti-affinity для HA. GCP credentials mounted as volume.

---

### 4.3 Infrastructure StatefulSets

- [x] `k8s/postgresql/statefulset.yml` — PostgreSQL 16:
  - PVC: 10Gi
  - Resources: requests 256Mi/250m, limits 1Gi/1000m
  - Liveness: `pg_isready`
  - InitDB scripts
- [x] `k8s/postgresql/service.yml` — ClusterIP
- [x] `k8s/redis/statefulset.yml` — Redis 7:
  - PVC: 1Gi
  - Resources: requests 128Mi/100m, limits 256Mi/250m
  - Liveness: `redis-cli ping`
- [x] `k8s/redis/service.yml` — ClusterIP

**Файлы:** `k8s/postgresql/`, `k8s/redis/`
**Заметки:** Для production рекомендуется managed services (RDS, ElastiCache). StatefulSets — fallback.

---

### 4.4 Ingress и Monitoring

- [x] `k8s/ingress.yml` — Ingress для admin UI и API:
  - TLS termination
  - Path-based routing: `/api/*`, `/admin/*`, `/ws`
  - Annotations для nginx (WebSocket support, timeouts)
- [x] `k8s/monitoring/prometheus-deployment.yml` — Prometheus с ConfigMaps (prometheus.yml + alerts.yml), PVC 10Gi, Service
- [x] `k8s/monitoring/grafana-deployment.yml` — Grafana с provisioning ConfigMaps (dashboards + datasources), PVC 5Gi, Service
- [x] `k8s/kustomization.yml` — Kustomize для управления всеми ресурсами

**Файлы:** `k8s/`, `k8s/monitoring/`
**Заметки:** Prometheus включает полный набор alert rules (13 alerts). Grafana provisioning для dashboards и datasources (Prometheus + PostgreSQL). Kustomize объединяет все 17 ресурсов.

---

## При завершении фазы

Выполни следующие действия:

1. Убедись, что все задачи отмечены [x]
2. Измени статус фазы:
   - [x] Завершена
3. Заполни дату "Завершена: YYYY-MM-DD"
4. Выполни коммит:
   ```bash
   git add .
   git commit -m "checklist(post-production-improvements): phase-4 kubernetes completed"
   ```
5. Обнови PROGRESS.md:
   - Текущая фаза: 5
   - Добавь запись в историю
6. Открой следующую фазу и продолжи работу
