# Kubernetes Deployment Guide

This guide covers deploying Contex to Kubernetes using either raw manifests or Helm.

## Prerequisites

- Kubernetes cluster (1.24+)
- kubectl configured
- Helm 3.x (for Helm deployment)
- Docker image built and pushed to registry

## Quick Start

### Option 1: Helm (Recommended)

```bash
# Add Helm repository (if published)
helm repo add contex https://charts.contex.example.com
helm repo update

# Install with default values
helm install contex contex/contex

# Or install from local chart
helm install contex ./helm/contex

# Install with custom values
helm install contex ./helm/contex -f my-values.yaml
```

### Option 2: Kustomize

```bash
# Deploy to development
kubectl apply -k k8s/overlays/dev

# Deploy to production
kubectl apply -k k8s/overlays/prod
```

### Option 3: Raw Manifests

```bash
# Create namespace
kubectl create namespace contex

# Apply manifests
kubectl apply -f k8s/base/ -n contex
```

## Configuration

### Using Helm

Edit `values.yaml` or create your own:

```yaml
# my-values.yaml
replicaCount: 3

contex:
  redis:
    url: "redis://my-redis:6379"
  similarity:
    threshold: 0.6
  logging:
    level: DEBUG

ingress:
  enabled: true
  hosts:
    - host: contex.mycompany.com
      paths:
        - path: /
          pathType: Prefix

resources:
  limits:
    cpu: 2000m
    memory: 4Gi
  requests:
    cpu: 500m
    memory: 1Gi
```

Install with custom values:
```bash
helm install contex ./helm/contex -f my-values.yaml
```

### Using ConfigMap

For raw manifests, edit `k8s/base/configmap.yaml`:

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: contex-config
data:
  redis_url: "redis://my-redis:6379"
  similarity_threshold: "0.6"
  log_level: "DEBUG"
```

## Secrets Management

### Create Secrets

```bash
# Generate random salt
API_KEY_SALT=$(openssl rand -hex 32)

# Create secret
kubectl create secret generic contex-secrets \
  --from-literal=api-key-salt=$API_KEY_SALT \
  -n contex
```

### Using External Secrets Operator

```yaml
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
metadata:
  name: contex-secrets
spec:
  refreshInterval: 1h
  secretStoreRef:
    name: aws-secrets-manager
    kind: SecretStore
  target:
    name: contex-secrets
  data:
  - secretKey: api-key-salt
    remoteRef:
      key: contex/api-key-salt
```

## PostgreSQL Setup

PostgreSQL with pgvector is the primary database for all persistent data.

### Option 1: Bundled PostgreSQL (Helm)

```yaml
# values.yaml
postgresql:
  enabled: true
  image:
    repository: pgvector/pgvector
    tag: pg16
  auth:
    database: contex
    username: contex
    password: contex_password
  primary:
    persistence:
      enabled: true
      size: 20Gi
```

### Option 2: External PostgreSQL

```yaml
# values.yaml
postgresql:
  enabled: false

contex:
  database:
    url: "postgresql+asyncpg://user:pass@external-postgres:5432/contex"
```

### Option 3: Cloud-Managed PostgreSQL

For production, use managed services (RDS, Cloud SQL, Azure Database) with pgvector:

```yaml
contex:
  database:
    url: "postgresql+asyncpg://user:pass@db.example.com:5432/contex?sslmode=require"
```

> **Note:** Ensure pgvector extension is enabled in your managed PostgreSQL instance.

## Redis Setup (Pub/Sub Only)

Redis is only used for pub/sub notifications. All persistent data is in PostgreSQL.

### Option 1: Bundled Redis (Helm)

```yaml
# values.yaml
redis:
  enabled: true
  architecture: standalone
  master:
    persistence:
      enabled: true
      size: 10Gi
```

### Option 2: External Redis

```yaml
# values.yaml
redis:
  enabled: false

contex:
  redis:
    url: "redis://external-redis:6379"
```

### Option 3: Redis Cluster (Pub/Sub Only)

Redis is only used for pub/sub notifications. For high availability:

```yaml
contex:
  redis:
    url: "redis://redis-cluster:6379"
```

> **Note:** All persistent data is stored in PostgreSQL, not Redis. Redis is only used for real-time pub/sub notifications.

## Ingress Configuration

### NGINX Ingress

```yaml
# values.yaml
ingress:
  enabled: true
  className: nginx
  annotations:
    cert-manager.io/cluster-issuer: "letsencrypt-prod"
    nginx.ingress.kubernetes.io/rate-limit: "100"
  hosts:
    - host: contex.example.com
      paths:
        - path: /
          pathType: Prefix
  tls:
    - secretName: contex-tls
      hosts:
        - contex.example.com
```

### Traefik Ingress

```yaml
ingress:
  enabled: true
  className: traefik
  annotations:
    traefik.ingress.kubernetes.io/router.middlewares: "default-ratelimit@kubernetescrd"
```

## Monitoring

### Prometheus

The chart includes a ServiceMonitor for Prometheus Operator:

```yaml
# values.yaml
serviceMonitor:
  enabled: true
  interval: 15s
```

Or create manually:
```bash
kubectl apply -f k8s/base/servicemonitor.yaml
```

### Grafana Dashboards

Import the Contex dashboard:
```bash
kubectl create configmap contex-dashboard \
  --from-file=dashboard.json \
  -n monitoring
```

## Scaling

### Manual Scaling

```bash
# Scale deployment
kubectl scale deployment contex --replicas=5 -n contex
```

### Horizontal Pod Autoscaler

HPA is enabled by default in Helm:

```yaml
# values.yaml
autoscaling:
  enabled: true
  minReplicas: 2
  maxReplicas: 10
  targetCPUUtilizationPercentage: 70
  targetMemoryUtilizationPercentage: 80
```

Check HPA status:
```bash
kubectl get hpa -n contex
```

### Vertical Pod Autoscaler

```yaml
apiVersion: autoscaling.k8s.io/v1
kind: VerticalPodAutoscaler
metadata:
  name: contex-vpa
spec:
  targetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: contex
  updatePolicy:
    updateMode: "Auto"
```

## Health Checks

The deployment includes liveness and readiness probes:

```yaml
livenessProbe:
  httpGet:
    path: /api/health/live
    port: 8001
  initialDelaySeconds: 30
  periodSeconds: 10

readinessProbe:
  httpGet:
    path: /api/health/ready
    port: 8001
  initialDelaySeconds: 10
  periodSeconds: 5
```

Test probes:
```bash
# Get pod name
POD=$(kubectl get pod -l app=contex -n contex -o jsonpath='{.items[0].metadata.name}')

# Test liveness
kubectl exec $POD -n contex -- curl -f http://localhost:8001/api/health/live

# Test readiness
kubectl exec $POD -n contex -- curl -f http://localhost:8001/api/health/ready
```

## Troubleshooting

### Check Pod Status

```bash
kubectl get pods -n contex
kubectl describe pod <pod-name> -n contex
kubectl logs <pod-name> -n contex
```

### Check Events

```bash
kubectl get events -n contex --sort-by='.lastTimestamp'
```

### Debug Container

```bash
kubectl exec -it <pod-name> -n contex -- /bin/sh
```

### Common Issues

#### Pods Not Starting

**Symptom**: Pods stuck in `Pending` or `CrashLoopBackOff`

**Solutions**:
1. Check resource availability:
   ```bash
   kubectl describe nodes
   ```

2. Check events:
   ```bash
   kubectl describe pod <pod-name> -n contex
   ```

3. Check logs:
   ```bash
   kubectl logs <pod-name> -n contex
   ```

#### Redis Connection Failed

**Symptom**: Logs show "Failed to connect to Redis"

**Solutions**:
1. Verify Redis is running:
   ```bash
   kubectl get pods -l app=redis -n contex
   ```

2. Check Redis URL in ConfigMap:
   ```bash
   kubectl get configmap contex-config -n contex -o yaml
   ```

3. Test Redis connectivity:
   ```bash
   kubectl exec <contex-pod> -n contex -- redis-cli -h redis ping
   ```

#### High Memory Usage

**Symptom**: Pods being OOMKilled

**Solutions**:
1. Increase memory limits:
   ```yaml
   resources:
     limits:
       memory: 4Gi
   ```

2. Check for memory leaks in logs

3. Enable memory profiling

## Upgrades

### Helm Upgrade

```bash
# Upgrade with new values
helm upgrade contex ./helm/contex -f my-values.yaml

# Rollback if needed
helm rollback contex
```

### Rolling Update

```bash
# Update image
kubectl set image deployment/contex contex=contex:0.3.0 -n contex

# Check rollout status
kubectl rollout status deployment/contex -n contex

# Rollback if needed
kubectl rollout undo deployment/contex -n contex
```

## Backup & Restore

All persistent data is stored in PostgreSQL. See [Database Setup](DATABASE.md) for full backup documentation.

### Backup PostgreSQL Data

```bash
# Using pg_dump
kubectl exec -it postgres-0 -n contex -- pg_dump -U contex contex > backup.sql

# Using compressed format
kubectl exec -it postgres-0 -n contex -- pg_dump -U contex -Fc contex > backup.dump
```

### Restore PostgreSQL Data

```bash
# Copy backup to pod and restore
kubectl cp backup.dump contex/postgres-0:/tmp/backup.dump
kubectl exec -it postgres-0 -n contex -- pg_restore -U contex -d contex /tmp/backup.dump
```

> **Note:** Redis is only used for pub/sub and does not require backup.

## Production Checklist

- [ ] Resource limits configured
- [ ] HPA enabled and tested
- [ ] PostgreSQL persistence enabled
- [ ] Secrets properly managed
- [ ] Ingress with TLS configured
- [ ] Monitoring and alerting set up
- [ ] Backup strategy in place
- [ ] Pod disruption budget configured
- [ ] Network policies applied
- [ ] Security context configured

## Advanced Configuration

### Pod Disruption Budget

```yaml
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: contex-pdb
spec:
  minAvailable: 1
  selector:
    matchLabels:
      app: contex
```

### Network Policy

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: contex-netpol
spec:
  podSelector:
    matchLabels:
      app: contex
  policyTypes:
  - Ingress
  - Egress
  ingress:
  - from:
    - podSelector:
        matchLabels:
          app: nginx-ingress
    ports:
    - protocol: TCP
      port: 8001
  egress:
  - to:
    - podSelector:
        matchLabels:
          app: redis
    ports:
    - protocol: TCP
      port: 6379
```

## Summary

✅ **Multiple deployment options**: Helm, Kustomize, raw manifests
✅ **Production-ready**: Health checks, autoscaling, monitoring
✅ **Secure**: Secrets management, security contexts
✅ **Scalable**: HPA, resource limits, PostgreSQL with pgvector
✅ **Observable**: Prometheus metrics, structured logging

Your Contex deployment is ready for production!
