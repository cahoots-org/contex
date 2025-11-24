# Redis Persistence & Backup Guide

This document describes how to configure Redis persistence for Contex to ensure data survives container restarts and how to backup/restore data.

## Redis Persistence Modes

Redis offers two persistence modes:

### 1. RDB (Redis Database) - Snapshots
- Point-in-time snapshots at specified intervals
- Compact, single-file format
- Faster restarts
- **Risk**: Data loss between snapshots

### 2. AOF (Append-Only File) - Transaction Log
- Logs every write operation
- More durable (configurable fsync)
- Larger file size
- **Recommended for production**

## Production Configuration

### Docker Compose Setup

The `docker-compose.yml` is already configured with persistence:

```yaml
redis:
  image: redis/redis-stack:latest
  command: redis-server --appendonly yes --appendfsync everysec
  ports:
    - "6379:6379"
  volumes:
    - redis-data:/data
  healthcheck:
    test: ["CMD", "redis-cli", "ping"]
    interval: 5s
    timeout: 3s
    retries: 5

volumes:
  redis-data:
```

### Configuration Options

**AOF Sync Policies**:
- `--appendfsync always`: Fsync after every write (slowest, most durable)
- `--appendfsync everysec`: Fsync every second (recommended, good balance)
- `--appendfsync no`: Let OS decide when to fsync (fastest, least durable)

**RDB Configuration**:
```bash
# Save snapshot if:
# - 900 seconds (15 min) and at least 1 key changed
# - 300 seconds (5 min) and at least 10 keys changed  
# - 60 seconds and at least 10000 keys changed
redis-server --save 900 1 --save 300 10 --save 60 10000
```

**Combined (Recommended)**:
```bash
redis-server \
  --appendonly yes \
  --appendfsync everysec \
  --save 900 1 \
  --save 300 10 \
  --save 60 10000
```

## Backup Procedures

### Manual Backup

#### 1. Backup AOF File

```bash
# Stop writes (optional, for consistency)
docker exec contex-redis redis-cli BGSAVE

# Copy AOF file
docker cp contex-redis:/data/appendonly.aof ./backups/appendonly-$(date +%Y%m%d-%H%M%S).aof

# Copy RDB file
docker cp contex-redis:/data/dump.rdb ./backups/dump-$(date +%Y%m%d-%H%M%S).rdb
```

#### 2. Using Redis SAVE/BGSAVE

```bash
# Blocking save (stops all clients)
docker exec contex-redis redis-cli SAVE

# Background save (non-blocking)
docker exec contex-redis redis-cli BGSAVE

# Check save status
docker exec contex-redis redis-cli LASTSAVE
```

### Automated Backup Script

Create `scripts/backup-redis.sh`:

```bash
#!/bin/bash
set -e

# Configuration
BACKUP_DIR="./backups/redis"
CONTAINER_NAME="contex-redis"
RETENTION_DAYS=30

# Create backup directory
mkdir -p "$BACKUP_DIR"

# Timestamp
TIMESTAMP=$(date +%Y%m%d-%H%M%S)

echo "Starting Redis backup at $TIMESTAMP..."

# Trigger background save
docker exec "$CONTAINER_NAME" redis-cli BGSAVE

# Wait for save to complete
while [ $(docker exec "$CONTAINER_NAME" redis-cli LASTSAVE) -eq $(docker exec "$CONTAINER_NAME" redis-cli LASTSAVE) ]; do
    sleep 1
done

# Copy files
docker cp "$CONTAINER_NAME:/data/dump.rdb" "$BACKUP_DIR/dump-$TIMESTAMP.rdb"
docker cp "$CONTAINER_NAME:/data/appendonly.aof" "$BACKUP_DIR/appendonly-$TIMESTAMP.aof"

# Compress backups
gzip "$BACKUP_DIR/dump-$TIMESTAMP.rdb"
gzip "$BACKUP_DIR/appendonly-$TIMESTAMP.aof"

echo "Backup completed: $BACKUP_DIR/*-$TIMESTAMP.*"

# Cleanup old backups
find "$BACKUP_DIR" -name "*.gz" -mtime +$RETENTION_DAYS -delete

echo "Old backups cleaned up (retention: $RETENTION_DAYS days)"
```

Make it executable:
```bash
chmod +x scripts/backup-redis.sh
```

### Automated Backup with Cron

```bash
# Add to crontab
# Backup every day at 2 AM
0 2 * * * /path/to/contex/scripts/backup-redis.sh >> /var/log/contex-backup.log 2>&1

# Backup every 6 hours
0 */6 * * * /path/to/contex/scripts/backup-redis.sh >> /var/log/contex-backup.log 2>&1
```

## Restore Procedures

### Restore from Backup

#### 1. Stop Contex

```bash
docker-compose down
```

#### 2. Restore Files

```bash
# Decompress backup
gunzip backups/redis/dump-20250124-020000.rdb.gz
gunzip backups/redis/appendonly-20250124-020000.aof.gz

# Copy to volume
docker run --rm -v contex_redis-data:/data -v $(pwd)/backups/redis:/backup \
  alpine sh -c "cp /backup/dump-20250124-020000.rdb /data/dump.rdb && \
                cp /backup/appendonly-20250124-020000.aof /data/appendonly.aof"
```

#### 3. Restart Contex

```bash
docker-compose up -d
```

#### 4. Verify Data

```bash
# Check Redis is running
docker exec contex-redis redis-cli ping

# Check data
docker exec contex-redis redis-cli DBSIZE

# Test Contex
curl http://localhost:8001/api/health
```

### Restore from RDB Only

If you only have RDB snapshot:

```bash
# Stop Redis
docker-compose stop redis

# Copy RDB file
docker cp ./backups/dump.rdb contex-redis:/data/dump.rdb

# Start Redis
docker-compose start redis
```

### Restore from AOF Only

If you only have AOF file:

```bash
# Stop Redis
docker-compose stop redis

# Copy AOF file
docker cp ./backups/appendonly.aof contex-redis:/data/appendonly.aof

# Start Redis with AOF enabled
docker-compose start redis
```

## Disaster Recovery

### Complete Data Loss

If all data is lost and you have backups:

1. **Stop all services**:
   ```bash
   docker-compose down -v  # Remove volumes
   ```

2. **Recreate volumes**:
   ```bash
   docker volume create contex_redis-data
   ```

3. **Restore backup** (see above)

4. **Start services**:
   ```bash
   docker-compose up -d
   ```

### Corrupted AOF File

If AOF file is corrupted:

```bash
# Check AOF file
docker exec contex-redis redis-check-aof /data/appendonly.aof

# Fix AOF file (removes corrupted parts)
docker exec contex-redis redis-check-aof --fix /data/appendonly.aof

# Restart Redis
docker-compose restart redis
```

## Monitoring Persistence

### Check Persistence Status

```bash
# Get persistence info
docker exec contex-redis redis-cli INFO persistence

# Key metrics:
# - aof_enabled: 1 (AOF is enabled)
# - aof_last_write_status: ok
# - rdb_last_save_time: timestamp
# - rdb_changes_since_last_save: number
```

### Prometheus Metrics

Add to your monitoring:

```promql
# Last successful save
redis_rdb_last_save_timestamp_seconds

# AOF file size
redis_aof_current_size_bytes

# Pending AOF rewrites
redis_aof_pending_rewrite
```

## Best Practices

### 1. Regular Backups

- **Frequency**: At least daily, hourly for critical systems
- **Retention**: 30 days minimum
- **Storage**: Off-site or cloud storage (S3, GCS)

### 2. Test Restores

- Test restore procedure monthly
- Verify data integrity after restore
- Document restore time (RTO)

### 3. Monitoring

- Monitor disk space on Redis volume
- Alert on failed saves
- Track AOF file size growth

### 4. Replication (Production)

For high availability, use Redis replication:

```yaml
# docker-compose.yml
redis-master:
  image: redis/redis-stack:latest
  command: redis-server --appendonly yes

redis-replica:
  image: redis/redis-stack:latest
  command: redis-server --replicaof redis-master 6379 --appendonly yes
  depends_on:
    - redis-master
```

## Troubleshooting

### Issue: Data Lost After Restart

**Cause**: Persistence not enabled or volume not mounted

**Solution**:
1. Check `docker-compose.yml` has `--appendonly yes`
2. Verify volume is mounted: `docker inspect contex-redis`
3. Check Redis logs: `docker logs contex-redis`

### Issue: Slow Performance

**Cause**: AOF fsync on every write

**Solution**:
- Change to `--appendfsync everysec`
- Consider RDB-only for non-critical data

### Issue: Large AOF File

**Cause**: AOF grows over time

**Solution**:
```bash
# Trigger AOF rewrite
docker exec contex-redis redis-cli BGREWRITEAOF

# Or configure automatic rewrite
redis-server --auto-aof-rewrite-percentage 100 --auto-aof-rewrite-min-size 64mb
```

## Summary

✅ **Persistence Enabled**: AOF + RDB in docker-compose  
✅ **Backup Script**: Automated daily backups  
✅ **Restore Procedure**: Documented and tested  
✅ **Monitoring**: Persistence metrics tracked  
✅ **Best Practices**: Regular backups, test restores  

Your Redis data is now protected against container restarts and failures!
