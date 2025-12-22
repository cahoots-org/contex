# Contex Operational Runbooks

This document contains operational runbooks for common scenarios when running Contex in production.

## Table of Contents

1. [Incident Response](#incident-response)
2. [PostgreSQL Operations](#postgresql-operations)
3. [Redis Operations](#redis-operations)
4. [OpenSearch Operations](#opensearch-operations)
5. [Scaling Operations](#scaling-operations)
6. [Deployment Procedures](#deployment-procedures)
7. [Backup and Recovery](#backup-and-recovery)
8. [Performance Troubleshooting](#performance-troubleshooting)
9. [Security Incidents](#security-incidents)

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
   # PostgreSQL health
   psql -U contex -d contex -c "SELECT 1"

   # Redis health (pub/sub only)
   redis-cli ping

   # OpenSearch health
   curl -s http://localhost:9200/_cluster/health | jq .
   ```

4. **Check Sentry for error details:**
   - Review recent issues in Sentry dashboard
   - Look for common error patterns
   - Check error distribution across endpoints

**Resolution:**

- If PostgreSQL is down: See [PostgreSQL Recovery](#postgresql-recovery)
- If Redis is down: See [Redis Issues](#redis-pubsub-issues)
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

2. **Check PostgreSQL latency:**
   ```bash
   # Check active queries
   psql -c "SELECT pid, now() - pg_stat_activity.query_start AS duration, query
            FROM pg_stat_activity
            WHERE state = 'active' ORDER BY duration DESC;"

   # Check connection count
   psql -c "SELECT count(*) FROM pg_stat_activity WHERE datname = 'contex';"
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
- Add connection pool capacity if PostgreSQL connections are exhausted
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

4. **Check health endpoint:**
   ```bash
   curl -s http://localhost:8001/api/v1/health | jq .
   ```

**Resolution:**

- If OOMKilled: Increase memory limits
- If CrashLoopBackOff: Check logs for startup errors
- If PostgreSQL unavailable: Check database health
- If ImagePullBackOff: Check image registry credentials

---

## PostgreSQL Operations

### PostgreSQL Recovery

**Symptoms:**
- Connection errors to PostgreSQL
- "could not connect to server" errors
- Database queries timing out

**Investigation:**

```bash
# Check PostgreSQL container status
docker compose ps postgres

# Check PostgreSQL logs
docker compose logs postgres --tail=100

# Test connection
psql "postgresql://contex:contex_password@localhost:5432/contex" -c "SELECT 1"

# Check active connections
psql -c "SELECT count(*), state FROM pg_stat_activity GROUP BY state;"
```

**Resolution:**

1. **If container is down:**
   ```bash
   docker compose up -d postgres
   # Wait for healthy status
   docker compose ps postgres
   ```

2. **If too many connections:**
   ```bash
   # Terminate idle connections
   psql -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity
            WHERE datname = 'contex' AND state = 'idle'
            AND query_start < now() - interval '5 minutes';"
   ```

3. **If disk space issue:**
   ```bash
   # Check disk usage
   docker exec contex-postgres-1 df -h /var/lib/postgresql/data

   # Vacuum to reclaim space
   psql -c "VACUUM FULL;"
   ```

### PostgreSQL Connection Pool Exhaustion

**Symptoms:**
- "connection pool exhausted" errors
- Increasing latency
- Timeouts on database operations

**Investigation:**

```bash
# Check active connections
psql -c "SELECT count(*), usename, application_name
         FROM pg_stat_activity
         WHERE datname = 'contex'
         GROUP BY usename, application_name;"

# Check max connections setting
psql -c "SHOW max_connections;"
```

**Resolution:**

1. **Increase pool size:**
   ```bash
   export DATABASE_POOL_SIZE=20
   export DATABASE_MAX_OVERFLOW=40
   ```

2. **Check for connection leaks:**
   - Review code for unclosed sessions
   - Ensure proper `async with db.session()` usage

3. **Scale horizontally:**
   - Add more Contex replicas to distribute connections

### PostgreSQL Performance Tuning

**Check slow queries:**

```sql
-- Enable pg_stat_statements if not already
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;

-- Find slowest queries
SELECT query, mean_exec_time, calls, total_exec_time
FROM pg_stat_statements
ORDER BY mean_exec_time DESC
LIMIT 10;
```

**Check table bloat:**

```sql
-- Check table sizes
SELECT schemaname, tablename,
       pg_size_pretty(pg_total_relation_size(schemaname || '.' || tablename))
FROM pg_tables
WHERE schemaname = 'public'
ORDER BY pg_total_relation_size(schemaname || '.' || tablename) DESC;

-- Run vacuum if needed
VACUUM ANALYZE embeddings;
VACUUM ANALYZE events;
```

**Check index usage:**

```sql
-- Find unused indexes
SELECT schemaname, tablename, indexname, idx_scan
FROM pg_stat_user_indexes
WHERE idx_scan = 0;
```

---

## Redis Operations

Redis is used only for pub/sub notifications in Contex. Data is stored in PostgreSQL.

### Redis Pub/Sub Issues

**Symptoms:**
- Agent notifications not being delivered
- Pub/sub messages not arriving

**Investigation:**

```bash
# Check Redis status
redis-cli ping

# Check pub/sub channels
redis-cli PUBSUB CHANNELS "*"

# Monitor pub/sub activity
redis-cli MONITOR
```

**Resolution:**

```bash
# Restart Redis if needed
docker compose restart redis

# Check Contex is connected
docker compose logs contex | grep -i redis
```

### Redis Memory (Pub/Sub Buffer)

Even for pub/sub only, Redis uses memory for message buffers:

```bash
# Check memory usage
redis-cli info memory

# Check client output buffers
redis-cli info clients
```

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

### PostgreSQL Backup

**Logical Backup (pg_dump):**

```bash
# Full database backup
pg_dump -h localhost -U contex -Fc contex > backup-$(date +%Y%m%d).dump

# Backup specific tables
pg_dump -h localhost -U contex -t events -t embeddings contex > tables-backup.sql
```

**Docker Volume Backup:**

```bash
# Backup volume
docker run --rm -v contex_postgres-data:/data -v $(pwd):/backup \
  alpine tar czf /backup/postgres-backup-$(date +%Y%m%d).tar.gz /data
```

**Automated Backup CronJob (Kubernetes):**

```yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: postgres-backup
spec:
  schedule: "0 2 * * *"  # Daily at 2 AM
  jobTemplate:
    spec:
      template:
        spec:
          containers:
          - name: backup
            image: postgres:16
            command:
            - /bin/sh
            - -c
            - |
              pg_dump -h postgres -U contex -Fc contex > /backup/contex-$(date +%Y%m%d).dump
          restartPolicy: OnFailure
```

### PostgreSQL Recovery

**From pg_dump:**

```bash
# Restore to existing database
pg_restore -h localhost -U contex -d contex -c backup.dump

# Restore to new database
createdb contex_restored
pg_restore -h localhost -U contex -d contex_restored backup.dump
```

**From Volume Backup:**

```bash
# Stop containers
docker compose down

# Restore volume
docker run --rm -v contex_postgres-data:/data -v $(pwd):/backup \
  alpine sh -c "rm -rf /data/* && tar xzf /backup/postgres-backup.tar.gz -C /"

# Start containers
docker compose up -d
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

# Check PostgreSQL memory
psql -c "SELECT pg_size_pretty(sum(pg_total_relation_size(schemaname || '.' || tablename)))
         FROM pg_tables WHERE schemaname = 'public';"
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

# Check PostgreSQL slow queries
psql -c "SELECT query, mean_exec_time FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 5;"
```

**Resolution:**

1. **Optimize queries** - reduce result size
2. **Add indices** - improve search performance
3. **Increase database resources**
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

   # Rotate PostgreSQL password
   # Rotate Redis password
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
   # Delete from database
   psql -c "DELETE FROM api_keys WHERE key_id = '<compromised_key_id>';"
   ```

2. **Rotate API key salt** (invalidates ALL keys):
   ```bash
   export API_KEY_SALT=$(openssl rand -base64 32)
   # Redeploy Contex
   ```

3. **Issue new keys to legitimate users**

4. **Review audit logs:**
   ```sql
   -- Check for suspicious activity
   SELECT * FROM audit_events
   WHERE actor_id = '<compromised_key_id>'
   ORDER BY timestamp DESC LIMIT 100;
   ```

### Secret Exposure

**If secrets are exposed in logs/commits:**

1. **Rotate immediately:**
   - All API keys
   - Database passwords
   - Redis password
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
| DB Connections | > 80% pool | > 95% pool | Increase pool or scale |
| Cache Hit Rate | < 70% | < 50% | Increase cache size |

### Useful Commands

```bash
# Quick health check
curl -s http://localhost:8001/api/v1/health | jq .

# Metrics endpoint
curl -s http://localhost:8001/api/v1/metrics | head -100

# PostgreSQL quick check
psql -c "SELECT 1" && psql -c "SELECT count(*) FROM events;"

# Redis quick check (pub/sub)
redis-cli ping

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
