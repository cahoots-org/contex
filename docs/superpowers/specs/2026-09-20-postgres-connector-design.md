# Postgres Connector (v1) — Design

**Status:** proposed
**Author:** design brainstorm, 2026-09-20

## Goal

Let a user get the data out of a Postgres database into a Contex project with
no glue code — point a connector at a DB with read-only access and it bulk-loads
the rows as published context. This is the first connector; it establishes the
reusable seam the others (GitHub, Atlassian) will build on.

## Scope decision: bulk snapshot, not sync

v1 is a **run-once, repeatable bulk importer**, not a change-data-capture
pipeline. CDC (logical replication, polling, triggers) asks too much of a
customer's database to require on day one. Instead:

- The connector reads the tables it's pointed at and publishes every row.
- Each row is keyed deterministically, so **re-running the importer upserts** —
  that is the "update" story (e.g. run it on a cron). No per-row glue.

We are explicit about the limits (see Limitations). CDC can be added later
behind the same `ChangeEvent` seam without changing the sink.

## Architecture

Connectors run **out-of-process** (a separate program the user runs; a CLI for
v1, containerizable later) so they scale and fail independently of the server.
A connector's only contract with Contex is publishing over MCP.

```
Postgres (read-only)  ->  connector (out-of-process)  ->  MCP: contex_publish_batch  ->  Contex
                          reads rows -> ChangeEvents         (as a service account)      embeds + stores
```

### The seam: `ChangeEvent`

Every connector emits a stream of `ChangeEvent`s; the runner batches and
publishes them. v1 only ever emits `op: "upsert"`, but the shape is future-proof
for CDC.

```python
@dataclass
class ChangeEvent:
    op: str            # "upsert" | "delete"  (v1: always "upsert")
    key: str           # stable data_key, e.g. "public.users.42"
    payload: dict      # the row as a dict (published as data)
    source_meta: dict  # {"source": "postgres", "schema": ..., "table": ...}
```

### Pieces, in build order

1. **`contex_publish_batch` MCP tool** — the one missing prerequisite. Accepts
   `project_id` and a list of `{data_key, data, data_format?}` items, bounded by
   `MAX_BATCH_SIZE` (reuse `check_batch_size`), authorized by `PUBLISH_DATA`.
   Internally upserts each item through the existing `publish_data` path.
2. **Connector framework** (`connectors/base/`) — the `ChangeEvent` contract, a
   runner that batches events and pushes them via the SDK's MCP client as a
   service account, config loading, and progress logging. Depends on
   `sdk/python` for transport. (May graduate into the published SDK later, which
   is the "build your own connector" roadmap item.)
3. **Postgres importer** (`connectors/postgres/`) — reads the DB and emits
   `ChangeEvent`s.

## Selection model

Pull **everything** by default, then narrow with allow/deny lists:

- `tables.include` / `tables.exclude` — glob patterns over `schema.table`.
  Start from all base tables in the accessible schemas; if `include` is set,
  keep only matches; then drop anything matching `exclude`.
- `columns.include` / `columns.exclude` — glob patterns, applied per table
  (with optional per-table overrides).
- **Binary columns (`bytea` and other binary types) are skipped by default.**
  This is both a cleanliness win (binary isn't useful as searchable text) and a
  safety one: `pgcrypto` ciphertext lands in `bytea`, so encrypted-column data is
  excluded without pretending to "detect encryption." Opt in with
  `include_binary: true`.

We do **not** decrypt anything — the connector holds no keys, so any
application- or `pgcrypto`-encrypted values are only ever seen as ciphertext.

## Row → context mapping

- **data_key**: `"{schema}.{table}.{pk}"`. Composite PKs join with `:`. This
  makes re-runs idempotent (upsert).
- **payload**: the selected, non-binary columns of the row as a JSON dict.
  Contex's normalizer turns that into searchable text (consistent with
  schema-free publish), so we don't make users hand-pick text columns.
- **No primary key**: a row with no PK can't be stably keyed, so re-runs would
  duplicate. v1 **requires a PK or a configured `key_column`** per table;
  tables with neither are skipped with a warning.

## Config (`connector.yaml`)

```yaml
contex:
  url: http://localhost:8001/mcp
  project_id: my-app
  service_account_token: ${CONTEX_TOKEN}   # omitted when AUTH_ENABLED=false
source:
  dsn: postgresql://readonly@db.internal:5432/app
batch_size: 500
include_binary: false
tables:
  exclude: ["audit.*", "public.sessions"]
columns:
  exclude: ["*.password", "*.*_token"]
key_columns:                # for tables lacking a PK
  public.events: event_id
```

## Runner behavior

1. Connect (read-only), discover base tables in accessible schemas, apply the
   table allow/deny lists.
2. For each table: resolve included, non-binary columns and the key; page
   through rows with a server-side cursor.
3. Map each row to a `ChangeEvent(op="upsert")`; accumulate into batches of
   `batch_size`; publish via `contex_publish_batch`.
4. Log progress (`table: N / total rows`) and a final summary. Idempotent.

## Limitations (documented up front)

- **Not live sync.** A snapshot; refresh by re-running (e.g. cron) or publishing
  updates yourself.
- **Deletes don't propagate** on re-run (no diff in v1).
- **No-PK tables require a configured `key_column`**, else skipped.
- **Bulk means embeddings.** Importing a whole DB embeds every row; we batch and
  log progress. No silent cap — but the run clearly reports how many rows it will
  embed so nobody melts their box unaware.
- **Plaintext data comes along.** Anything readable and non-binary is ingested
  unless excluded; the user owns excluding plaintext secrets/PII via the deny
  lists.

## Out of scope (later)

CDC / incremental sync, delete propagation via diffing, per-table `WHERE`
filters, GitHub and Atlassian connectors (same seam, read-only), embedding
throughput hardening.

## Testing

- Unit: config parsing + glob allow/deny resolution; binary-column skip; row →
  `ChangeEvent` mapping; composite-key and `key_column` handling; no-PK skip.
- Integration: seed a table in the CI ParadeDB, run the importer against a Contex
  instance, assert the rows are published and queryable; re-run and assert upsert
  (no duplication).
