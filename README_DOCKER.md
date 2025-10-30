# Docker Setup Guide

This guide explains how to run Contex using Docker and Docker Compose.

## Prerequisites

- Docker 20.10+
- Docker Compose 2.0+

## Quick Start

1. **Clone the repository**:
   ```bash
   git clone https://github.com/contex/contex.git
   cd contex
   ```

2. **Start the services**:
   ```bash
   docker compose up -d
   ```

3. **Verify the services are running**:
   ```bash
   docker compose ps
   ```

4. **Check the logs**:
   ```bash
   docker compose logs -f contex
   ```

5. **Test the service**:
   ```bash
   curl http://localhost:8001/health
   ```

## Services

The Docker Compose setup includes two services:

### Redis
- **Image**: redis:7-alpine
- **Port**: 6379
- **Volume**: redis-data (persistent storage)
- **Features**:
  - AOF (Append-Only File) persistence enabled
  - Health check configured
  - Automatic restart on failure

### Contex
- **Build**: Built from local Dockerfile
- **Port**: 8001
- **Dependencies**: Redis (with health check)
- **Features**:
  - Health check endpoint
  - Resource limits (2GB memory, 2 CPUs)
  - Automatic restart on failure
  - Non-root user for security

## Configuration

Environment variables can be customized in `docker-compose.yml`:

```yaml
environment:
  - REDIS_URL=redis://redis:6379
  - SIMILARITY_THRESHOLD=0.5  # Match threshold (0-1)
  - MAX_MATCHES=10            # Max results per need
```

Or create a `.env` file in the project root:

```bash
cp .env.example .env
# Edit .env with your values
```

## Common Commands

### Start services
```bash
docker compose up -d
```

### Stop services
```bash
docker compose down
```

### Stop and remove volumes (WARNING: deletes all data)
```bash
docker compose down -v
```

### View logs
```bash
# All services
docker compose logs -f

# Specific service
docker compose logs -f contex
docker compose logs -f redis
```

### Restart services
```bash
docker compose restart
```

### Rebuild after code changes
```bash
docker compose build
docker compose up -d
```

### Check resource usage
```bash
docker stats contex-app contex-redis
```

## Health Checks

Both services have health checks configured:

**Redis**:
- Checks: `redis-cli ping`
- Interval: 5 seconds
- Timeout: 3 seconds
- Retries: 5

**Contex**:
- Checks: `curl -f http://localhost:8001/health`
- Interval: 10 seconds
- Timeout: 3 seconds
- Retries: 3

Check health status:
```bash
docker compose ps
```

## Resource Limits

The Contex service has the following resource limits:

**Limits (maximum)**:
- Memory: 2GB
- CPUs: 2.0

**Reservations (guaranteed)**:
- Memory: 1GB
- CPUs: 1.0

These can be adjusted in `docker-compose.yml` based on your workload.

## Data Persistence

Redis data is persisted in a Docker volume named `redis-data`. This volume survives container restarts.

To backup Redis data:
```bash
docker compose exec redis redis-cli BGSAVE
docker cp contex-redis:/data/dump.rdb ./backup-$(date +%Y%m%d).rdb
```

To restore Redis data:
```bash
docker compose down
docker cp ./backup-20250101.rdb contex-redis:/data/dump.rdb
docker compose up -d
```

## Networking

By default, Docker Compose creates a bridge network for the services. The services can communicate using their service names:

- From Contex to Redis: `redis://redis:6379`
- From host to Contex: `http://localhost:8001`
- From host to Redis: `localhost:6379`

## Security

The Contex container runs as a non-root user (`appuser`) for improved security.

For production deployments:
1. Add authentication to Redis (see Redis documentation)
2. Use environment variables for secrets
3. Consider using Docker secrets or external secret management
4. Add a reverse proxy (nginx, Traefik) with TLS
5. Restrict network access using firewall rules

## Troubleshooting

### Container won't start

Check logs:
```bash
docker compose logs contex
```

Common issues:
- **Port already in use**: Change ports in `docker-compose.yml`
- **Out of memory**: Increase Docker memory limit or reduce resource reservations
- **Redis connection failed**: Check Redis container is healthy (`docker compose ps`)

### Model download issues

The first time Contex starts, it downloads the sentence-transformers model (~80MB). This can take a few minutes.

To pre-download the model:
```bash
docker compose run --rm contex python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"
```

### Performance issues

Check resource usage:
```bash
docker stats contex-app
```

If memory or CPU is maxed out, adjust limits in `docker-compose.yml`.

### Data loss after restart

Ensure you're not using `docker compose down -v` which removes volumes. Use `docker compose down` or `docker compose stop` instead.

## Production Deployment

For production deployments, consider:

1. **Use a production-grade Redis setup**:
   - Redis Cluster for high availability
   - Redis Sentinel for automatic failover
   - Or managed Redis (AWS ElastiCache, Redis Cloud, etc.)

2. **Scale Contex horizontally**:
   ```bash
   docker compose up -d --scale contex=3
   ```

   Note: You'll need a load balancer and external Redis.

3. **Add monitoring**:
   - Prometheus metrics
   - Grafana dashboards
   - Health check monitoring
   - Log aggregation (ELK, Loki, etc.)

4. **Use orchestration**:
   - Kubernetes (see Kubernetes section in main README)
   - Docker Swarm
   - Nomad

5. **Implement backup strategy**:
   - Automated Redis backups
   - Regular testing of restore procedures
   - Offsite backup storage

## Development Workflow

For local development:

1. **Run services**:
   ```bash
   docker compose up -d redis  # Start only Redis
   ```

2. **Run Contex locally**:
   ```bash
   pip install -r requirements.txt
   REDIS_URL=redis://localhost:6379 python main.py
   ```

3. **Make changes and test**:
   - Code changes are reflected immediately when running locally
   - No need to rebuild Docker image for each change

4. **Run tests**:
   ```bash
   pytest tests/ -v
   ```

5. **Build and test Docker image**:
   ```bash
   docker compose build contex
   docker compose up -d
   ```

## Additional Resources

- [Main README](README.md)
- [API Documentation](https://docs.contex.dev)
- [Docker Documentation](https://docs.docker.com/)
- [Docker Compose Documentation](https://docs.docker.com/compose/)
