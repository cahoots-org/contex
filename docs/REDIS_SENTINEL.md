# Redis Sentinel Configuration for High Availability

This guide covers Redis Sentinel setup for Contex to achieve high availability with automatic failover.

## 📋 Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Configuration](#configuration)
  - [Standalone Mode](#standalone-mode-default)
  - [Sentinel Mode](#sentinel-mode-ha)
- [Deployment](#deployment)
  - [Kubernetes Deployment](#kubernetes-deployment)
  - [Helm Chart Deployment](#helm-chart-deployment)
  - [Docker Compose](#docker-compose)
- [Monitoring](#monitoring)
- [Troubleshooting](#troubleshooting)

## Overview

Redis Sentinel provides high availability for Redis through:

- **Monitoring**: Continuous health checks of master and replica instances
- **Automatic Failover**: Promotes replica to master when master fails
- **Service Discovery**: Clients discover current master through Sentinel
- **Notifications**: Alerts when failover occurs

### Benefits

- **Zero Downtime**: Automatic failover typically completes in <30 seconds
- **Data Safety**: Synchronous replication ensures no data loss (with proper configuration)
- **Scalability**: Read replicas distribute load
- **Disaster Recovery**: Geographic distribution possible

## Architecture

### Standalone Mode (Default)

```
┌─────────────┐
│   Contex    │
│  Service    │
└──────┬──────┘
       │
       ▼
┌─────────────┐
│    Redis    │
│  Standalone │
└─────────────┘
```

- Simple setup, single point of failure
- Suitable for development and testing
- Lower resource requirements

### Sentinel Mode (High Availability)

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   Contex    │────▶│  Sentinel 1 │     │  Sentinel 2 │
│  Service    │     │             │     │             │
└─────────────┘     └──────┬──────┘     └──────┬──────┘
       │                   │                    │
       │                   ▼                    ▼
       │            ┌─────────────────────────────────┐
       └───────────▶│     Redis Master (mymaster)     │
                    └──────┬──────────────────────────┘
                           │
             ┌─────────────┴─────────────┐
             ▼                           ▼
    ┌────────────────┐         ┌────────────────┐
    │ Redis Replica 1│         │ Redis Replica 2│
    └────────────────┘         └────────────────┘
```

- Multiple Sentinel nodes (minimum 3 recommended)
- Automatic failover when master fails
- Quorum-based decision making
- Production-grade reliability

## Configuration

### Standalone Mode (Default)

Standalone mode is the default configuration, suitable for development and testing.

**Environment Variables:**

```bash
# Redis Mode
REDIS_MODE=standalone

# Redis Connection
REDIS_URL=redis://redis:6379
REDIS_MAX_CONNECTIONS=50
REDIS_SOCKET_TIMEOUT=5
REDIS_SOCKET_CONNECT_TIMEOUT=5
REDIS_SOCKET_KEEPALIVE=true
REDIS_RETRY_ON_TIMEOUT=true
REDIS_HEALTH_CHECK_INTERVAL=30

# Optional: Redis Password
# REDIS_PASSWORD=your_password
```

### Sentinel Mode (HA)

For production deployments requiring high availability.

**Environment Variables:**

```bash
# Redis Mode
REDIS_MODE=sentinel

# Sentinel Configuration
REDIS_SENTINEL_HOSTS=sentinel-0:26379,sentinel-1:26379,sentinel-2:26379
REDIS_SENTINEL_MASTER=mymaster
REDIS_DB=0

# Connection Settings (optional, uses defaults if not set)
REDIS_MAX_CONNECTIONS=50
REDIS_SOCKET_TIMEOUT=5
REDIS_SOCKET_CONNECT_TIMEOUT=5
REDIS_SOCKET_KEEPALIVE=true
REDIS_HEALTH_CHECK_INTERVAL=30

# Optional: Passwords
# REDIS_SENTINEL_PASSWORD=sentinel_password
# REDIS_PASSWORD=redis_password
```

**Configuration Parameters:**

| Parameter | Description | Default | Required |
|-----------|-------------|---------|----------|
| `REDIS_MODE` | Connection mode: `standalone` or `sentinel` | `standalone` | Yes |
| `REDIS_SENTINEL_HOSTS` | Comma-separated Sentinel addresses | - | Yes (Sentinel) |
| `REDIS_SENTINEL_MASTER` | Master instance name | `mymaster` | Yes (Sentinel) |
| `REDIS_SENTINEL_PASSWORD` | Sentinel auth password | - | No |
| `REDIS_PASSWORD` | Redis auth password | - | No |
| `REDIS_DB` | Redis database number | `0` | No |
| `REDIS_MAX_CONNECTIONS` | Max connection pool size | `50` | No |
| `REDIS_SOCKET_TIMEOUT` | Socket timeout (seconds) | `5` | No |
| `REDIS_HEALTH_CHECK_INTERVAL` | Health check interval (seconds) | `30` | No |

## Deployment

### Kubernetes Deployment

#### 1. Deploy Redis with Sentinel

Create `redis-sentinel.yaml`:

```yaml
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: redis-sentinel-config
data:
  sentinel.conf: |
    sentinel monitor mymaster redis-master 6379 2
    sentinel down-after-milliseconds mymaster 5000
    sentinel parallel-syncs mymaster 1
    sentinel failover-timeout mymaster 10000

---
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: redis-master
spec:
  serviceName: redis-master
  replicas: 1
  selector:
    matchLabels:
      app: redis
      role: master
  template:
    metadata:
      labels:
        app: redis
        role: master
    spec:
      containers:
      - name: redis
        image: redis:7-alpine
        ports:
        - containerPort: 6379
        volumeMounts:
        - name: data
          mountPath: /data
  volumeClaimTemplates:
  - metadata:
      name: data
    spec:
      accessModes: [ "ReadWriteOnce" ]
      resources:
        requests:
          storage: 10Gi

---
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: redis-replica
spec:
  serviceName: redis-replica
  replicas: 2
  selector:
    matchLabels:
      app: redis
      role: replica
  template:
    metadata:
      labels:
        app: redis
        role: replica
    spec:
      containers:
      - name: redis
        image: redis:7-alpine
        command:
        - redis-server
        - --replicaof
        - redis-master
        - "6379"
        ports:
        - containerPort: 6379
        volumeMounts:
        - name: data
          mountPath: /data
  volumeClaimTemplates:
  - metadata:
      name: data
    spec:
      accessModes: [ "ReadWriteOnce" ]
      resources:
        requests:
          storage: 10Gi

---
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: redis-sentinel
spec:
  serviceName: redis-sentinel
  replicas: 3
  selector:
    matchLabels:
      app: redis-sentinel
  template:
    metadata:
      labels:
        app: redis-sentinel
    spec:
      containers:
      - name: sentinel
        image: redis:7-alpine
        command:
        - redis-sentinel
        - /etc/redis/sentinel.conf
        ports:
        - containerPort: 26379
        volumeMounts:
        - name: config
          mountPath: /etc/redis
      volumes:
      - name: config
        configMap:
          name: redis-sentinel-config

---
apiVersion: v1
kind: Service
metadata:
  name: redis-master
spec:
  type: ClusterIP
  ports:
  - port: 6379
    targetPort: 6379
  selector:
    app: redis
    role: master

---
apiVersion: v1
kind: Service
metadata:
  name: redis-sentinel
spec:
  type: ClusterIP
  clusterIP: None
  ports:
  - port: 26379
    targetPort: 26379
  selector:
    app: redis-sentinel
```

#### 2. Update Contex ConfigMap

Edit `k8s/base/configmap.yaml`:

```yaml
data:
  redis_mode: "sentinel"
  redis_sentinel_hosts: "redis-sentinel-0.redis-sentinel:26379,redis-sentinel-1.redis-sentinel:26379,redis-sentinel-2.redis-sentinel:26379"
  redis_sentinel_master: "mymaster"
  redis_db: "0"
```

#### 3. Update Contex Deployment

Uncomment Sentinel environment variables in `k8s/base/deployment.yaml`:

```yaml
- name: REDIS_SENTINEL_HOSTS
  valueFrom:
    configMapKeyRef:
      name: contex-config
      key: redis_sentinel_hosts
- name: REDIS_SENTINEL_MASTER
  valueFrom:
    configMapKeyRef:
      name: contex-config
      key: redis_sentinel_master
- name: REDIS_DB
  valueFrom:
    configMapKeyRef:
      name: contex-config
      key: redis_db
```

#### 4. Deploy

```bash
# Deploy Redis Sentinel
kubectl apply -f redis-sentinel.yaml

# Wait for Redis to be ready
kubectl wait --for=condition=ready pod -l app=redis --timeout=120s
kubectl wait --for=condition=ready pod -l app=redis-sentinel --timeout=120s

# Deploy/Update Contex
kubectl apply -k k8s/base/

# Verify Contex can connect
kubectl logs -f deployment/contex
```

### Helm Chart Deployment

Update `values.yaml` for high availability:

```yaml
contex:
  redis:
    mode: "sentinel"
    sentinel:
      enabled: true
      hosts: "contex-redis-node-0.contex-redis-headless:26379,contex-redis-node-1.contex-redis-headless:26379,contex-redis-node-2.contex-redis-headless:26379"
      master: "mymaster"
      password: ""
    password: ""

# Enable Redis Sentinel
redis:
  enabled: true
  architecture: replication
  auth:
    enabled: false
  master:
    persistence:
      enabled: true
      size: 8Gi
  replica:
    replicaCount: 2
    persistence:
      enabled: true
      size: 8Gi
  sentinel:
    enabled: true
    quorum: 2
```

Deploy:

```bash
# Install with Sentinel
helm install contex ./helm/contex -f values-ha.yaml

# Or upgrade existing deployment
helm upgrade contex ./helm/contex -f values-ha.yaml
```

### Docker Compose

Create `docker-compose.sentinel.yml`:

```yaml
version: '3.8'

services:
  redis-master:
    image: redis:7-alpine
    command: redis-server --appendonly yes
    ports:
      - "6379:6379"
    volumes:
      - redis-master-data:/data

  redis-replica-1:
    image: redis:7-alpine
    command: redis-server --replicaof redis-master 6379 --appendonly yes
    depends_on:
      - redis-master
    volumes:
      - redis-replica-1-data:/data

  redis-replica-2:
    image: redis:7-alpine
    command: redis-server --replicaof redis-master 6379 --appendonly yes
    depends_on:
      - redis-master
    volumes:
      - redis-replica-2-data:/data

  sentinel-1:
    image: redis:7-alpine
    command: >
      sh -c "echo 'sentinel monitor mymaster redis-master 6379 2
             sentinel down-after-milliseconds mymaster 5000
             sentinel parallel-syncs mymaster 1
             sentinel failover-timeout mymaster 10000' > /tmp/sentinel.conf &&
             redis-sentinel /tmp/sentinel.conf"
    depends_on:
      - redis-master
    ports:
      - "26379:26379"

  sentinel-2:
    image: redis:7-alpine
    command: >
      sh -c "echo 'sentinel monitor mymaster redis-master 6379 2
             sentinel down-after-milliseconds mymaster 5000
             sentinel parallel-syncs mymaster 1
             sentinel failover-timeout mymaster 10000' > /tmp/sentinel.conf &&
             redis-sentinel /tmp/sentinel.conf"
    depends_on:
      - redis-master
    ports:
      - "26380:26379"

  sentinel-3:
    image: redis:7-alpine
    command: >
      sh -c "echo 'sentinel monitor mymaster redis-master 6379 2
             sentinel down-after-milliseconds mymaster 5000
             sentinel parallel-syncs mymaster 1
             sentinel failover-timeout mymaster 10000' > /tmp/sentinel.conf &&
             redis-sentinel /tmp/sentinel.conf"
    depends_on:
      - redis-master
    ports:
      - "26381:26379"

  contex:
    image: ghcr.io/cahoots-org/contex:latest
    environment:
      - REDIS_MODE=sentinel
      - REDIS_SENTINEL_HOSTS=sentinel-1:26379,sentinel-2:26379,sentinel-3:26379
      - REDIS_SENTINEL_MASTER=mymaster
      - API_KEY_SALT=your_secure_salt_here
    ports:
      - "8001:8001"
    depends_on:
      - sentinel-1
      - sentinel-2
      - sentinel-3

volumes:
  redis-master-data:
  redis-replica-1-data:
  redis-replica-2-data:
```

Run:

```bash
docker-compose -f docker-compose.sentinel.yml up -d
```

## Monitoring

### Health Check

Contex automatically tests the Redis connection on startup. Check logs:

```bash
# Kubernetes
kubectl logs -f deployment/contex | grep -i redis

# Docker
docker logs contex | grep -i redis
```

Expected output (Sentinel mode):

```
Connected to Redis via Sentinel | master_name=mymaster master_host=10.0.1.5 master_port=6379 sentinel_count=3
```

### Prometheus Metrics

Monitor Redis connection health with Prometheus:

```promql
# Redis connection count
contex_redis_connections

# Redis operation latency
histogram_quantile(0.95, rate(contex_redis_operation_duration_seconds_bucket[5m]))

# Check for connection errors
increase(contex_redis_connection_errors_total[5m])
```

### Grafana Dashboard

The Contex Reliability dashboard includes Redis monitoring panels:

- Redis connection count with alerts (>45 = pool exhaustion)
- Redis operation duration (p95)
- Memory usage

### Sentinel Status

Check Sentinel status directly:

```bash
# Kubernetes
kubectl exec -it redis-sentinel-0 -- redis-cli -p 26379 SENTINEL masters
kubectl exec -it redis-sentinel-0 -- redis-cli -p 26379 SENTINEL replicas mymaster

# Docker
docker exec sentinel-1 redis-cli -p 26379 SENTINEL masters
```

## Troubleshooting

### Connection Failures

**Symptom**: `Failed to connect to Redis via Sentinel`

**Solutions**:

1. **Verify Sentinel hosts are reachable**:
   ```bash
   # From Contex pod
   kubectl exec -it contex-xxxxx -- nc -zv sentinel-0 26379
   ```

2. **Check Sentinel configuration**:
   ```bash
   kubectl exec -it redis-sentinel-0 -- redis-cli -p 26379 SENTINEL masters
   ```

3. **Verify master name matches**:
   - `REDIS_SENTINEL_MASTER` must match Sentinel config
   - Default is "mymaster"

4. **Check network policies**:
   - Ensure Contex pods can reach Sentinel on port 26379
   - Ensure Contex pods can reach Redis master on port 6379

### Failover Not Working

**Symptom**: Master fails but Sentinel doesn't promote replica

**Solutions**:

1. **Check quorum**:
   ```bash
   # Quorum must be ≤ number of Sentinels
   kubectl exec redis-sentinel-0 -- redis-cli -p 26379 SENTINEL masters
   ```

2. **Verify Sentinel count**:
   - Minimum 3 Sentinels recommended
   - Quorum typically set to (N/2)+1

3. **Check Sentinel logs**:
   ```bash
   kubectl logs redis-sentinel-0
   ```

### Split Brain

**Symptom**: Multiple masters exist simultaneously

**Prevention**:

1. **Use odd number of Sentinels** (3, 5, 7)
2. **Set appropriate quorum** ((N/2)+1)
3. **Configure proper timeouts**:
   ```
   sentinel down-after-milliseconds mymaster 5000
   sentinel failover-timeout mymaster 10000
   ```

### Slow Failover

**Symptom**: Failover takes >60 seconds

**Solutions**:

1. **Reduce down-after-milliseconds**:
   ```
   sentinel down-after-milliseconds mymaster 3000
   ```

2. **Check network latency**:
   ```bash
   # From Sentinel to Redis
   kubectl exec sentinel-0 -- ping redis-master
   ```

3. **Increase Sentinel resources**:
   ```yaml
   resources:
     limits:
       cpu: 200m
       memory: 256Mi
   ```

### Connection Pool Exhausted

**Symptom**: `REDIS_MAX_CONNECTIONS exceeded`

**Solutions**:

1. **Increase pool size**:
   ```yaml
   REDIS_MAX_CONNECTIONS=100
   ```

2. **Check for connection leaks**:
   ```promql
   contex_redis_connections
   ```

3. **Scale Contex pods** if load is high

### Authentication Failures

**Symptom**: `NOAUTH Authentication required`

**Solutions**:

1. **Set Redis password**:
   ```yaml
   REDIS_PASSWORD=your_password
   ```

2. **Set Sentinel password** (if Sentinel has auth):
   ```yaml
   REDIS_SENTINEL_PASSWORD=sentinel_password
   ```

3. **Verify password in Redis**:
   ```bash
   kubectl exec redis-master-0 -- redis-cli AUTH your_password PING
   ```

## Best Practices

### Production Deployment

1. **Use 3+ Sentinels** across different availability zones
2. **Enable persistence** on master and replicas
3. **Configure auth passwords** for security
4. **Set resource limits** to prevent resource starvation
5. **Monitor failover metrics** with alerts
6. **Test failover regularly** (chaos engineering)
7. **Use anti-affinity rules** to spread pods across nodes

### Security

1. **Enable Redis AUTH**:
   ```yaml
   redis:
     auth:
       enabled: true
       password: "strong_password"
   ```

2. **Use network policies** to restrict access
3. **Encrypt connections** with TLS (Redis 6+)
4. **Rotate passwords** regularly
5. **Use Kubernetes Secrets** for passwords, not ConfigMaps

### Performance

1. **Tune connection pool**:
   - Set `REDIS_MAX_CONNECTIONS` based on expected load
   - Monitor pool usage with metrics

2. **Configure timeouts**:
   - `REDIS_SOCKET_TIMEOUT`: Query timeout
   - `REDIS_SOCKET_CONNECT_TIMEOUT`: Connection timeout

3. **Enable TCP keepalive**:
   ```yaml
   REDIS_SOCKET_KEEPALIVE=true
   ```

4. **Use local replicas** for read operations (future enhancement)

## References

- [Redis Sentinel Documentation](https://redis.io/docs/manual/sentinel/)
- [Redis High Availability](https://redis.io/topics/sentinel)
- [Bitnami Redis Helm Chart](https://github.com/bitnami/charts/tree/main/bitnami/redis)
- [Contex Health Monitoring](./METRICS.md)
