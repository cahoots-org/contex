# Contex Operational Runbooks

This document contains operational runbooks for common scenarios when running Contex in production.

## Table of Contents

1. [Incident Response](#incident-response)
2. [Redis Operations](#redis-operations)
3. [OpenSearch Operations](#opensearch-operations)
4. [Scaling Operations](#scaling-operations)
5. [Deployment Procedures](#deployment-procedures)
6. [Backup and Recovery](#backup-and-recovery)
7. [Performance Troubleshooting](#performance-troubleshooting)
8. [Security Incidents](#security-incidents)

---

## Incident Response

### High Error Rate Alert

**Symptoms:**
- Error rate above threshold (default: 1%)
- `contex_errors_total` metric increasing
- Sentry alerts firing

**Investigation Steps:**

1. **Check Grafana dashboards:**
   ```
   Navigate to: Grafana > Contex Reliability Dashboard
   Look at: Error Rate panel, HTTP Errors by Status Code
   ```

2. **Check application logs:**
   ```bash
   # Kubernetes
   kubectl logs -l app=contex --tail=100 -f

   # Docker Compose
   docker compose logs contex --tail=100 -f
   ```

3. **Check dependent services:**
   ```bash
   # Redis health
   redis-cli ping

   # OpenSearch health
   curl -s http://localhost:9200/_cluster/health | jq .
   ```

4. **Check Sentry for error details:**
   - Review recent issues in Sentry dashboard
   - Look for common error patterns
   - Check error distribution across endpoints

**Resolution:**

- If Redis is down: See [Redis Failover](#redis-failover)
- If OpenSearch is down: See [OpenSearch Recovery](#opensearch-recovery)
- If high latency: See [High Latency Investigation](#high-latency-investigation)
- If OOM: See [Memory Issues](#memory-issues)

---

### High Latency Alert

**Symptoms:**
- P95/P99 latency above threshold
- Request timeout errors
- Slow response times reported by users

**Investigation Steps:**

1. **Check latency breakdown in Grafana:**
   ```
   Navigate to: Grafana > Contex Performance Dashboard
   Look at: Latency Distribution, P95/P99 over time
   ```

2. **Check Redis latency:**
   ```bash
   redis-cli --latency-history
   redis-cli info stats | grep -E "(instantaneous|keyspace)"
   ```

3. **Check OpenSearch latency:**
   ```bash
   curl -s "http://localhost:9200/_cat/nodes?v&h=name,cpu,load_1m,heap.percent"
   ```

4. **Check embedding cache hit rate:**
   ```
   Look at: embedding_cache_hits_total vs embedding_cache_misses_total
   Low hit rate = more embedding computations = higher latency
   ```

**Resolution:**

- Scale up replicas if CPU bound
- Increase embedding cache size if cache miss rate is high
- Add Redis replicas if Redis is the bottleneck
- Optimize OpenSearch indices if search is slow

---

### Service Unavailable (5xx Errors)

**Symptoms:**
- HTTP 500/502/503/504 errors
- Health checks failing
- Pods crashing or restarting

**Investigation Steps:**

1. **Check pod status:**
   ```bash
   kubectl get pods -l app=contex
   kubectl describe pod <pod-name>
   ```

2. **Check recent events:**
   ```bash
   kubectl get events --sort-by='.lastTimestamp' | grep contex
   ```

3. **Check resource usage:**
   ```bash
   kubectl top pods -l app=contex
   ```

4. **Check degradation status:**
   ```bash
   curl -s http://localhost:8001/health | jq '.degradation'
   ```

**Resolution:**

- If OOMKilled: Increase memory limits
- If CrashLoopBackOff: Check logs for startup errors
- If Redis unavailable: Check Redis health, Contex will auto-degrade
- If ImagePullBackOff: Check image registry credentials

---

## Redis Operations

### Redis Failover

**When Sentinel Detects Master Failure:**

Sentinel automatically handles failover. Monitor with:

```bash
# Check Sentinel status
redis-cli -p 26379 SENTINEL master mymaster
redis-cli -p 26379 SENTINEL slaves mymaster

# Check which node is master
redis-cli -p 26379 SENTINEL get-master-addr-by-name mymaster
```

**Manual Failover (if needed):**

```bash
# Trigger manual failover
redis-cli -p 26379 SENTINEL failover mymaster

# Verify new master
redis-cli -p 26379 SENTINEL get-master-addr-by-name mymaster
```

**Contex Response:**

- Contex will automatically reconnect to the new master
- Brief connectivity errors during failover (typically < 30 seconds)
- Check `degradation_mode` metric - should return to NORMAL after recovery

### Redis Memory Issues

**Symptoms:**
- `redis_memory_used_bytes` approaching `redis_memory_max_bytes`
- OOM errors in Redis logs
- Eviction happening (`evicted_keys` counter increasing)

**Investigation:**

```bash
# Check memory usage
redis-cli info memory

# Check key distribution
redis-cli --bigkeys

# Check memory per key type
redis-cli memory doctor
```

**Resolution:**

1. **Immediate:** Increase `maxmemory` if possible
   ```bash
   redis-cli CONFIG SET maxmemory 2gb
   ```

2. **Short-term:** Enable eviction policy
   ```bash
   redis-cli CONFIG SET maxmemory-policy allkeys-lru
   ```

3. **Long-term:**
   - Scale Redis cluster
   - Review data retention policies
   - Implement TTLs on cached data

### Redis Connection Pool Exhaustion

**Symptoms:**
- "Connection pool exhausted" errors
- Increasing latency
- Timeouts on Redis operations

**Investigation:**

```bash
# Check connected clients
redis-cli info clients

# Check connection pool metrics in Prometheus
# Look at: redis_connected_clients
```

**Resolution:**

1. **Increase pool size:**
   ```bash
   export REDIS_MAX_CONNECTIONS=100
   ```

2. **Check for connection leaks:**
   - Review code for unclosed connections
   - Ensure proper connection cleanup in error paths

3. **Scale horizontally:**
   - Add more Contex replicas to distribute connections

---

## OpenSearch Operations

### OpenSearch Recovery

**Symptoms:**
- Hybrid search failing
- Yellow/Red cluster health
- Index errors

**Investigation:**

```bash
# Check cluster health
curl -s http://localhost:9200/_cluster/health?pretty

# Check node status
curl -s http://localhost:9200/_cat/nodes?v

# Check index status
curl -s http://localhost:9200/_cat/indices?v

# Check shard allocation
curl -s http://localhost:9200/_cat/shards?v
```

**Resolution for Yellow Status:**

```bash
# Wait for shard recovery (usually automatic)
curl -s http://localhost:9200/_cat/recovery?v

# If stuck, force shard allocation
curl -X POST "localhost:9200/_cluster/reroute?retry_failed=true"
```

**Resolution for Red Status:**

```bash
# Identify problem indices
curl -s http://localhost:9200/_cat/indices?v&health=red

# Try to recover index
curl -X POST "localhost:9200/<index-name>/_open"

# If data is lost, recreate index
curl -X DELETE "localhost:9200/<index-name>"
# Then restart Contex to recreate
```

### OpenSearch Index Maintenance

**Re-index for Better Performance:**

```bash
# Create new index with updated settings
curl -X PUT "localhost:9200/contex-contexts-v2" -H 'Content-Type: application/json' -d'{
  "settings": {
    "number_of_shards": 3,
    "number_of_replicas": 1
  }
}'

# Reindex data
curl -X POST "localhost:9200/_reindex" -H 'Content-Type: application/json' -d'{
  "source": {"index": "contex-contexts"},
  "dest": {"index": "contex-contexts-v2"}
}'

# Update alias
curl -X POST "localhost:9200/_aliases" -H 'Content-Type: application/json' -d'{
  "actions": [
    {"remove": {"index": "contex-contexts", "alias": "contex"}},
    {"add": {"index": "contex-contexts-v2", "alias": "contex"}}
  ]
}'
```

---

## Scaling Operations

### Horizontal Scaling

**When to Scale:**
- CPU utilization consistently > 70%
- Request latency increasing
- Queue depth growing

**Kubernetes:**

```bash
# Scale manually
kubectl scale deployment contex --replicas=5

# Or configure HPA
kubectl apply -f - <<EOF
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: contex-hpa
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: contex
  minReplicas: 2
  maxReplicas: 10
  metrics:
  - type: Resource
    resource:
      name: cpu
      target:
        type: Utilization
        averageUtilization: 70
EOF
```

**Docker Compose:**

```bash
docker compose up -d --scale contex=5
```

### Vertical Scaling

**When to Scale:**
- Memory pressure (OOM kills)
- Single-request operations timing out
- Embedding computations too slow

**Kubernetes:**

```yaml
# Update deployment resources
resources:
  limits:
    cpu: "4"
    memory: "4Gi"
  requests:
    cpu: "2"
    memory: "2Gi"
```

---

## Deployment Procedures

### Rolling Update

**Pre-deployment Checklist:**
- [ ] All tests passing
- [ ] Security scan clean
- [ ] Staging environment validated
- [ ] Monitoring dashboards open
- [ ] Rollback plan ready

**Kubernetes Rolling Update:**

```bash
# Update image
kubectl set image deployment/contex contex=contex:v1.2.0

# Monitor rollout
kubectl rollout status deployment/contex

# Check pods
kubectl get pods -l app=contex -w
```

**Rollback:**

```bash
# Immediate rollback
kubectl rollout undo deployment/contex

# Rollback to specific revision
kubectl rollout history deployment/contex
kubectl rollout undo deployment/contex --to-revision=2
```

### Blue-Green Deployment

**Setup:**

```bash
# Deploy green version
kubectl apply -f deployment-green.yaml

# Verify green is healthy
kubectl get pods -l app=contex,version=green

# Switch traffic
kubectl patch service contex -p '{"spec":{"selector":{"version":"green"}}}'

# Monitor for issues
# If problems occur, switch back:
kubectl patch service contex -p '{"spec":{"selector":{"version":"blue"}}}'

# Cleanup old version after validation
kubectl delete deployment contex-blue
```

---

## Backup and Recovery

### Redis Backup

**RDB Snapshot:**

```bash
# Trigger immediate backup
redis-cli BGSAVE

# Check backup status
redis-cli LASTSAVE

# Backup files are in Redis data directory
# Default: /data/dump.rdb
```

**AOF Backup:**

```bash
# Trigger AOF rewrite
redis-cli BGREWRITEAOF

# Backup the AOF file
cp /data/appendonly.aof /backup/appendonly-$(date +%Y%m%d).aof
```

**Kubernetes CronJob for Backups:**

```yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: redis-backup
spec:
  schedule: "0 2 * * *"  # Daily at 2 AM
  jobTemplate:
    spec:
      template:
        spec:
          containers:
          - name: backup
            image: redis:7-alpine
            command:
            - /bin/sh
            - -c
            - |
              redis-cli -h redis BGSAVE
              sleep 10
              cp /data/dump.rdb /backup/dump-$(date +%Y%m%d).rdb
          restartPolicy: OnFailure
```

### Redis Recovery

**From RDB:**

```bash
# Stop Redis
redis-cli SHUTDOWN

# Replace dump.rdb
cp /backup/dump-20240101.rdb /data/dump.rdb

# Start Redis
redis-server
```

**From AOF:**

```bash
# Stop Redis
redis-cli SHUTDOWN

# Replace appendonly.aof
cp /backup/appendonly-20240101.aof /data/appendonly.aof

# Check and fix AOF if corrupted
redis-check-aof --fix /data/appendonly.aof

# Start Redis
redis-server --appendonly yes
```

---

## Performance Troubleshooting

### Memory Issues

**Symptoms:**
- OOMKilled pods
- High memory usage
- Slow garbage collection

**Investigation:**

```bash
# Check container memory
kubectl top pods -l app=contex

# Check Python memory profile (if profiling enabled)
curl http://localhost:8001/debug/memory
```

**Resolution:**

1. **Increase memory limits**
2. **Enable memory profiling to identify leaks**
3. **Review embedding cache size**
4. **Check for connection pool leaks**

### CPU Issues

**Symptoms:**
- High CPU utilization
- Slow embedding generation
- Request queuing

**Investigation:**

```bash
# Check CPU usage
kubectl top pods -l app=contex

# Profile with py-spy (if available)
py-spy record -o profile.svg --pid <pid>
```

**Resolution:**

1. **Scale horizontally** - add more replicas
2. **Enable embedding cache** - reduce recomputation
3. **Use batch operations** - more efficient processing
4. **Consider GPU acceleration** for embeddings

### Slow Queries

**Investigation:**

```bash
# Check OpenSearch slow log
curl -X PUT "localhost:9200/contex-contexts/_settings" -H 'Content-Type: application/json' -d'{
  "index.search.slowlog.threshold.query.warn": "2s",
  "index.search.slowlog.threshold.query.info": "1s"
}'

# View slow log
docker logs opensearch | grep slowlog
```

**Resolution:**

1. **Optimize queries** - reduce result size
2. **Add indices** - improve search performance
3. **Increase OpenSearch resources**
4. **Enable query caching**

---

## Security Incidents

### Suspected Breach

**Immediate Actions:**

1. **Isolate affected systems:**
   ```bash
   # Scale down to prevent further access
   kubectl scale deployment contex --replicas=0
   ```

2. **Preserve evidence:**
   ```bash
   # Capture logs
   kubectl logs -l app=contex --all-containers > incident-logs.txt

   # Capture network state
   kubectl get networkpolicies -o yaml > network-state.yaml
   ```

3. **Rotate credentials:**
   ```bash
   # Rotate API key salt
   kubectl create secret generic contex-secrets \
     --from-literal=API_KEY_SALT=$(openssl rand -base64 32) \
     --dry-run=client -o yaml | kubectl apply -f -

   # Rotate Redis password
   # Rotate Vault tokens
   # Rotate AWS credentials
   ```

4. **Notify stakeholders:**
   - Security team
   - Management
   - Affected users (if applicable)

### API Key Compromise

**Steps:**

1. **Invalidate compromised keys:**
   ```bash
   # Delete from Redis
   redis-cli DEL "api_key:<compromised_hash>"
   ```

2. **Rotate API key salt** (invalidates ALL keys):
   ```bash
   export API_KEY_SALT=$(openssl rand -base64 32)
   # Redeploy Contex
   ```

3. **Issue new keys to legitimate users**

4. **Review access logs:**
   ```bash
   # Check for suspicious activity
   grep "api_key" /var/log/contex/*.log | grep -v 200
   ```

### Secret Exposure

**If secrets are exposed in logs/commits:**

1. **Rotate immediately:**
   - All API keys
   - Database passwords
   - Vault tokens
   - AWS credentials

2. **Audit access:**
   ```bash
   # Check who had access to logs
   # Review git history
   git log --oneline --all -- '*secret*' '*.env*'
   ```

3. **Clean up:**
   ```bash
   # Remove from git history if committed
   git filter-branch --force --index-filter \
     "git rm --cached --ignore-unmatch <secret-file>" \
     --prune-empty --tag-name-filter cat -- --all
   ```

4. **Update .gitignore and pre-commit hooks**

---

## Monitoring Quick Reference

### Key Metrics to Watch

| Metric | Warning | Critical | Action |
|--------|---------|----------|--------|
| Error Rate | > 0.1% | > 1% | Check logs, investigate errors |
| P99 Latency | > 500ms | > 2s | Scale or optimize |
| CPU Usage | > 70% | > 90% | Scale horizontally |
| Memory Usage | > 70% | > 90% | Scale vertically or fix leaks |
| Redis Connections | > 80% pool | > 95% pool | Increase pool or scale |
| Cache Hit Rate | < 70% | < 50% | Increase cache size |
| Degradation Mode | DEGRADED | UNAVAILABLE | Check Redis health |

### Useful Commands

```bash
# Quick health check
curl -s http://localhost:8001/health | jq .

# Metrics endpoint
curl -s http://localhost:8001/metrics | head -100

# Redis quick check
redis-cli ping && redis-cli info keyspace

# OpenSearch quick check
curl -s http://localhost:9200/_cluster/health | jq '.status'

# Pod resource usage
kubectl top pods -l app=contex

# Recent logs
kubectl logs -l app=contex --tail=50 --since=5m
```

---

## Contact Information

- **On-Call:** [Your On-Call System]
- **Slack Channel:** #contex-ops
- **Escalation:** [Your Escalation Path]
- **Documentation:** [Internal Wiki Link]
