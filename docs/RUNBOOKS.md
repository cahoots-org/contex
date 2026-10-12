# Operational Runbooks

Procedures for the `docker compose` deployment in this repository. Commands run
from the repository root. Postgres isn't published to the host, so database
commands go through `docker compose exec`.

## Health and logs

```bash
# Liveness
curl -s http://localhost:8001/health

# Recent logs
docker compose logs contex --tail=100 -f

# Database reachable
docker compose exec postgres psql -U contex -d contex -c "SELECT 1"

# Live subscription updates need the LISTEN connection
docker compose exec postgres psql -U contex -d contex \
  -c "SELECT pid, state FROM pg_stat_activity WHERE application_name = 'contex-notifier'"
```

If the server won't start, the first error in `docker compose logs contex` names
the setting it rejected (protected mode, `BOOTSTRAP_ADMIN_KEY`, a secret under
16 characters, or a database it can't reach or migrate).

## Database connections

Each Contex process opens a pool of 5 connections that grows to 15 under load,
plus one LISTEN connection. The pool size is fixed in code. If Postgres runs
out of connections, compare its limit with what Contex holds:

```bash
docker compose exec postgres psql -U contex -d contex -c "SHOW max_connections"
docker compose exec postgres psql -U contex -d contex \
  -c "SELECT application_name, state, count(*) FROM pg_stat_activity WHERE datname = 'contex' GROUP BY 1, 2"
```

## Slow queries

```bash
docker compose exec postgres psql -U contex -d contex -c "
  SELECT pid, now() - query_start AS duration, left(query, 120)
  FROM pg_stat_activity WHERE state = 'active' ORDER BY duration DESC"

docker compose exec postgres psql -U contex -d contex -c "VACUUM ANALYZE embeddings; VACUUM ANALYZE events;"
```

## Upgrade and rollback

The `contex` service is built from the checkout, and it migrates the database
to the newest schema at boot.

Take a [backup](#backup-and-restore) first, then:

```bash
git pull
docker compose up -d --build contex
docker compose logs contex --tail=100 -f
```

Older code won't start on a database migrated past it, so rolling back means
restoring the pre-upgrade backup before rebuilding:

```bash
docker compose stop contex
docker compose exec -T postgres pg_restore -U contex -d contex --clean --if-exists < contex-YYYYMMDD.dump
git checkout <previous-tag>
docker compose up -d --build contex
```

## Backup and restore

```bash
# Backup
docker compose exec -T postgres pg_dump -U contex -Fc contex > contex-$(date +%Y%m%d).dump

# Restore into the running database
docker compose stop contex
docker compose exec -T postgres pg_restore -U contex -d contex --clean --if-exists < contex-YYYYMMDD.dump
docker compose start contex
```

## API keys

Manage keys with the CLI inside the container (or with `python -m src.cli`
wherever `DATABASE_URL` points at the database).

```bash
docker compose exec contex python -m src.cli keys list
docker compose exec contex python -m src.cli keys create --name ingest --role publisher --project my-app
docker compose exec contex python -m src.cli keys revoke <key_id>
```

**A key leaked:** revoke it, then create a replacement with the same role,
tenant and projects.

**The admin key is lost:** create a new one with `keys create --role admin`.

**Rotating `API_KEY_PEPPER`:** every existing key stops working. Set the new
pepper in `.env`, run `docker compose up -d contex`, then revoke the old keys and
create new ones with the CLI.
