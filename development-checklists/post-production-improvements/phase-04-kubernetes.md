# Фаза 4: Kubernetes

## Статус
- [ ] Не начата
- [ ] В процессе
- [ ] Завершена

**Начата:** -
**Завершена:** -

## Цель фазы
Создать Kubernetes manifests для развёртывания приложения с auto-scaling, health checks и resilience. После этой фазы приложение можно развернуть в K8s кластере одной командой.

## Задачи

### 4.0 ОБЯЗАТЕЛЬНО: Анализ и планирование

#### A. Анализ существующего кода
- [ ] Изучить `docker-compose.yml` — все сервисы, порты, volumes, env variables
- [ ] Изучить `docker-compose.staging.yml` — staging конфигурация
- [ ] Изучить `Dockerfile` — как собирается приложение
- [ ] Изучить health check endpoints: `/health`, `/health/ready`
- [ ] Изучить `src/config.py` — все env variables для конфигурации

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
- [ ] Какие сервисы нужны: app, PostgreSQL, Redis, Prometheus, Grafana
- [ ] Какие volumes/PV нужны: PostgreSQL data, Redis data, Grafana dashboards
- [ ] Какие secrets нужны: DB credentials, API keys, JWT secret
- [ ] Какие ConfigMaps нужны: app config, Prometheus config, Grafana provisioning

**Компоненты K8s:**
- Deployments: call-processor, admin-api
- StatefulSets: PostgreSQL, Redis
- Services: ClusterIP для внутренних, LoadBalancer/Ingress для внешних
- Ingress: admin UI, API
- HPA: call-processor (по CPU/memory)
- Secrets: db-credentials, api-keys, jwt-secret
- ConfigMaps: app-config, prometheus-config

#### C. Проверка архитектуры
- [ ] Namespace: `call-center`
- [ ] Resource limits для каждого pod
- [ ] Readiness/liveness probes на basis health endpoints
- [ ] Anti-affinity для HA
- [ ] PDB (PodDisruptionBudget) для zero-downtime updates

**Референс-модуль:** `docker-compose.yml` (все сервисы и их конфигурация)

**Цель:** Понять существующие паттерны проекта ПЕРЕД написанием кода.

**Заметки для переиспользования:** -

---

### 4.1 Namespace и базовые ресурсы

- [ ] Создать `k8s/namespace.yml` — namespace `call-center`
- [ ] Создать `k8s/secrets.yml` — шаблон для secrets (с placeholder-ами, не реальные значения)
- [ ] Создать `k8s/configmap.yml` — ConfigMap с app конфигурацией
- [ ] Создать `k8s/README.md` — инструкции по развёртыванию

**Файлы:** `k8s/`
**Заметки:** Secrets шаблон — placeholder values, реальные через `kubectl create secret` или sealed-secrets

---

### 4.2 Application Deployments

- [ ] `k8s/call-processor/deployment.yml` — Call Processor (AudioSocket + API):
  - Replicas: 2 (min)
  - Resources: requests 256Mi/250m, limits 512Mi/500m
  - Liveness: `/health` (HTTP, period 30s)
  - Readiness: `/health/ready` (HTTP, period 10s)
  - Env from ConfigMap + Secrets
  - Port: 8080 (API), 9092 (AudioSocket)
- [ ] `k8s/call-processor/service.yml` — ClusterIP для API, NodePort/LoadBalancer для AudioSocket
- [ ] `k8s/call-processor/hpa.yml` — HPA: min 2, max 10, target CPU 70%
- [ ] `k8s/call-processor/pdb.yml` — PDB: minAvailable 1

**Файлы:** `k8s/call-processor/`
**Заметки:** AudioSocket требует TCP pass-through, не HTTP routing

---

### 4.3 Infrastructure StatefulSets

- [ ] `k8s/postgresql/statefulset.yml` — PostgreSQL 16:
  - PVC: 10Gi
  - Resources: requests 256Mi/250m, limits 1Gi/1000m
  - Liveness: `pg_isready`
  - InitDB scripts
- [ ] `k8s/postgresql/service.yml` — ClusterIP
- [ ] `k8s/redis/statefulset.yml` — Redis 7:
  - PVC: 1Gi
  - Resources: requests 128Mi/100m, limits 256Mi/250m
  - Liveness: `redis-cli ping`
- [ ] `k8s/redis/service.yml` — ClusterIP

**Файлы:** `k8s/postgresql/`, `k8s/redis/`
**Заметки:** Для production рекомендуется managed services (RDS, ElastiCache). StatefulSets — fallback.

---

### 4.4 Ingress и Monitoring

- [ ] `k8s/ingress.yml` — Ingress для admin UI и API:
  - TLS termination
  - Path-based routing: `/api/*`, `/admin/*`, `/ws`
  - Annotations для nginx/traefik
- [ ] `k8s/monitoring/prometheus-deployment.yml` — Prometheus с ServiceMonitor
- [ ] `k8s/monitoring/grafana-deployment.yml` — Grafana с provisioned dashboards
- [ ] `k8s/kustomization.yml` — Kustomize для управления всеми ресурсами

**Файлы:** `k8s/`, `k8s/monitoring/`
**Заметки:** -

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
