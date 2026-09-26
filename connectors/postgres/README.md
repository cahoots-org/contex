# Postgres connector

Bulk-loads Postgres tables into a Contex project. v1 is a snapshot importer —
run it once (or on a cron) and every row is upserted by a stable key, so
re-runs are idempotent.

## Install

```
pip install -r connectors/postgres/requirements.txt
pip install -r requirements.txt   # installs the base connector framework
```

## Configure

Copy `connector.yaml.example` to `connector.yaml` and fill in your values:

```yaml
contex:
  url: http://localhost:8001/mcp
  project_id: my-app
  service_account_token: ${CONTEX_TOKEN}   # omit when AUTH_ENABLED=false

source:
  dsn: postgresql://readonly@db.internal:5432/app

batch_size: 500
include_binary: false

tables:
  exclude: ["audit.*", "public.sessions"]

columns:
  exclude: ["*.password", "*.*_token"]

key_columns:          # for tables without a primary key
  public.events: event_id
```

`${VAR}` references in the file are expanded from the environment.

## Run

```
python -m connectors.postgres --config connector.yaml
```

Add `--log-level DEBUG` for verbose query-level output.

## Run the container image

The published image `ghcr.io/cahoots-org/contex-connector-postgres` is fully
env-driven — no config file needed:

```bash
docker run --rm \
  -e CONTEX_URL=http://contex:8001/mcp \
  -e CONTEX_PROJECT_ID=my-app \
  -e CONTEX_TOKEN=svc_... \
  -e POSTGRES_DSN=postgresql://readonly@db.internal:5432/app \
  ghcr.io/cahoots-org/contex-connector-postgres:latest
```

| Env var | Maps to | Notes |
|---|---|---|
| `CONTEX_URL` | `contex.url` | MCP endpoint |
| `CONTEX_PROJECT_ID` | `contex.project_id` | Target project |
| `CONTEX_TOKEN` | `contex.service_account_token` | Optional; omit if auth is off |
| `POSTGRES_DSN` | `source.dsn` | Read-only DSN for the source database |
| `CONTEX_BATCH_SIZE` | `batch_size` | Optional; defaults to 500 |

For table/column filters or `key_columns` overrides, mount your own yaml over
the baked default:

```bash
docker run --rm -v "$PWD/connector.yaml:/etc/contex/connector.yaml" \
  ghcr.io/cahoots-org/contex-connector-postgres:latest
```

## Selection model

- **tables.include / tables.exclude** — glob patterns over `schema.table`.  All
  accessible base tables are discovered; `include` narrows the set; `exclude`
  removes matches.
- **columns.include / columns.exclude** — glob patterns applied to column names.
- **include_binary** — binary columns (`bytea`, `bit`, `varbit`) are skipped by
  default. Set `include_binary: true` to include them.  This keeps `pgcrypto`
  ciphertext out of the index by default.

## Key mapping

Each row becomes a `ChangeEvent` with:

- `data_key`: `schema.table.pk` (composite PKs joined with `:`).
- `payload`: the selected non-binary columns as a JSON dict.

Tables with no primary key and no `key_columns` override are skipped with a
warning.

## Limitations

- Snapshot only — no live sync. Re-run the connector to refresh.
- Deletes are not propagated on re-run (v1 has no diff).
- Tables missing a PK require a `key_columns` entry or they are skipped.
- All readable, non-binary columns are ingested unless excluded. Use the column
  deny list to keep PII and plaintext secrets out.
