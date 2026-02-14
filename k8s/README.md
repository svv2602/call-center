# Kubernetes Deployment — Call Center AI

## Prerequisites

- `kubectl` configured and connected to a Kubernetes cluster (v1.26+)
- Access to a container registry (Docker Hub, GitHub Container Registry, ECR, etc.)
- Application container images built and pushed to the registry
- Google Cloud service account JSON key for STT/TTS

## Directory Structure

```
k8s/
  namespace.yml          # Namespace definition
  configmap.yml          # Non-secret application configuration
  secrets.yml            # Secret templates (placeholder values only)
  call-processor/        # Call Processor Deployment + Service
  postgresql/            # PostgreSQL StatefulSet
  redis/                 # Redis Deployment
  monitoring/            # Prometheus, Grafana, Flower
```

## Deployment Steps

### 1. Create the Namespace

```bash
kubectl apply -f k8s/namespace.yml
```

### 2. Create Secrets

**Do NOT apply `secrets.yml` directly** — it contains only placeholder values.
Instead, create secrets from literal values or files:

```bash
# Application secrets
kubectl create secret generic call-center-secrets \
  --namespace=call-center \
  --from-literal=ANTHROPIC_API_KEY='sk-ant-api03-...' \
  --from-literal=STORE_API_KEY='your-store-api-key' \
  --from-literal=POSTGRES_PASSWORD='strong-db-password' \
  --from-literal=ADMIN_JWT_SECRET='random-jwt-secret-256bit' \
  --from-literal=GRAFANA_ADMIN_PASSWORD='grafana-password' \
  --from-literal=TELEGRAM_BOT_TOKEN='123456:ABC-DEF...' \
  --from-literal=TELEGRAM_CHAT_ID='-1001234567890' \
  --from-literal=ARI_USER='ari_user' \
  --from-literal=ARI_PASSWORD='ari-strong-password' \
  --from-literal=FLOWER_BASIC_AUTH='flower_user:flower_password' \
  --from-literal=SMTP_HOST='smtp.example.com' \
  --from-literal=SMTP_PORT='587' \
  --from-literal=SMTP_USER='smtp-user@example.com' \
  --from-literal=SMTP_PASSWORD='smtp-password'

# Google Cloud credentials (mounted as a file)
kubectl create secret generic google-credentials \
  --namespace=call-center \
  --from-file=credentials.json=/path/to/your/service-account.json
```

### 3. Apply ConfigMap

```bash
kubectl apply -f k8s/configmap.yml
```

### 4. Deploy Infrastructure

```bash
kubectl apply -f k8s/postgresql/
kubectl apply -f k8s/redis/
```

Wait for infrastructure pods to become ready:

```bash
kubectl wait --namespace=call-center \
  --for=condition=Ready pod \
  --selector=app.kubernetes.io/name=postgresql \
  --timeout=120s

kubectl wait --namespace=call-center \
  --for=condition=Ready pod \
  --selector=app.kubernetes.io/name=redis \
  --timeout=60s
```

### 5. Deploy Application

```bash
kubectl apply -f k8s/call-processor/
```

### 6. Deploy Monitoring

```bash
kubectl apply -f k8s/monitoring/
```

### 7. Verify

```bash
kubectl get pods -n call-center
kubectl get svc -n call-center
kubectl logs -n call-center -l app.kubernetes.io/name=call-processor --tail=50
```

## Kustomize Usage

If you add a `kustomization.yaml` at the root of `k8s/`, you can deploy everything at once:

```bash
kubectl apply -k k8s/
```

Example `k8s/kustomization.yaml`:

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization

namespace: call-center

resources:
  - namespace.yml
  - configmap.yml
  # Do NOT include secrets.yml — create secrets via kubectl commands above
  - postgresql/
  - redis/
  - call-processor/
  - monitoring/
```

For environment-specific overlays (staging, production), create overlay directories:

```
k8s/
  base/            # Move shared manifests here
  overlays/
    staging/
      kustomization.yaml
      configmap-patch.yml
    production/
      kustomization.yaml
      configmap-patch.yml
```

## Secrets Management

**Never commit real secret values to version control.**

For production environments, consider:

- **Sealed Secrets** (Bitnami) — encrypts secrets in-cluster, safe to commit encrypted manifests
- **External Secrets Operator** — syncs secrets from AWS Secrets Manager, HashiCorp Vault, GCP Secret Manager
- **SOPS + age/GPG** — encrypts secret files at rest, decrypted during CI/CD

The `secrets.yml` file in this directory serves only as a template/reference for which secrets are required by the application.

## Production Recommendations

- **PostgreSQL**: Use a managed database service (AWS RDS, GCP Cloud SQL, Azure Database for PostgreSQL) instead of running PostgreSQL in Kubernetes. This provides automated backups, failover, and maintenance.
- **Redis**: Use a managed Redis service (AWS ElastiCache, GCP Memorystore, Azure Cache for Redis) for high availability and persistence guarantees.
- **Ingress**: Configure an Ingress controller (nginx-ingress, Traefik, or cloud-native ALB/NLB) for external access to the admin panel and monitoring dashboards.
- **TLS**: Use cert-manager with Let's Encrypt for automatic TLS certificate provisioning.
- **Resource Limits**: Set CPU/memory requests and limits on all Deployments to ensure cluster stability.
- **Pod Disruption Budgets**: Define PDBs for critical workloads (call-processor) to maintain availability during node maintenance.
- **Horizontal Pod Autoscaler**: Configure HPA for call-processor based on CPU utilization or custom metrics (active calls).
- **Network Policies**: Restrict pod-to-pod communication to only what is required (e.g., call-processor to PostgreSQL, Redis, and store-api).
