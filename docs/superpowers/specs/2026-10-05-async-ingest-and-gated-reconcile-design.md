# Async Ingest and Gated Reconcile — Design

**Status:** proposed
**Author:** design brainstorm, 2026-10-05

## Goal

Query latency stays flat while Contex ingests. Today a large publish freezes
every other request until it finishes.

## Problem

Contex runs as one uvicorn process with one event loop. `contex_publish_batch`
does all of its work inside the request, on that loop:

1. Parse every item (tree-sitter for code), synchronously.
2. Embed every changed node in one `model.encode` call, synchronously.
3. Write embeddings and symbols.
4. Per item: append an event, then `reconcile_project`.

Step 4 multiplies the cost. A 1,000-item batch runs 1,000 reconciles, and each
one re-matches every subscription in the project, embedding every need again,
on the loop. Queries wait behind all of it.

## Decisions

- **No read-your-writes.** Publish acknowledges once the data is accepted.
  Indexing and reconcile happen afterward. No caller depends on publish being
  synchronous.
- **Postgres is the only infrastructure.** It holds the store, the ingest queue,
  and cross-process notifications (`LISTEN/NOTIFY`). Redis is removed.
- **Two process roles, one image.** The API process serves MCP, the web sandbox,
  and queries, and never parses or embeds published data. The worker process
  runs ingest jobs and reconciles.
- **Reconcile is gated.** A change re-matches only the subscription needs it can
  affect, not every subscription in the project.

## Staging

Three stages, each a separate PR that ships on its own:

1. Redis → `LISTEN/NOTIFY`. Single process, no behavior change.
2. Ingest queue and worker.
3. Coalesced, gated reconcile.

## Stage 1: Redis → LISTEN/NOTIFY

Redis does one job today: `reconcile_project` publishes
`subscription:{id}:updated`, and two in-process listeners re-read the bundle
(the MCP bridge in `mcp_bridge.py` and the SSE stream in `src/web/live.py`).
Rate limiting is in-memory.

- `reconcile_project` calls
  `pg_notify('contex_subscription_updated', '{"subscription_id": …, "updated_at": …}')`
  inside the transaction that swaps the bundle. The notification fires on
  commit, so a listener never sees an update before the bundle is readable.
- A `Notifier` in the API process owns one dedicated asyncpg connection that
  `LISTEN`s, and fans each notification out in-process: to the MCP bridge
  (`resources/updated`) and to a per-subscription queue for each SSE stream.
- On a dropped connection the `Notifier` reconnects with backoff, then tells
  every live consumer to re-read, since notifications may have been missed while
  disconnected.
- Removed: `src/core/pubsub.py`, Sentinel and `REDIS_*` config, the compose
  `redis` service, Redis tracing and Sentry integrations, the `redis` dependency,
  and the `redis` test fixture.

**Pooling constraint.** `LISTEN` needs a session-level connection, so it does not
work through transaction-mode PgBouncer. This adds no new constraint: asyncpg's
default prepared statements already fail under transaction pooling, and Contex
requires pg_search, which rules out most pooled managed Postgres. `NOTIFY`
itself runs inside an ordinary transaction and works through any pooler. If
transaction pooling is ever supported, an optional `DATABASE_LISTEN_URL` can
point the one listener connection directly at Postgres. Not built now.

## Stage 2: Ingest queue and worker

### Publish path

Publish authorizes, rate-limits, and validates sizes as today, then inserts one
`ingest_jobs` row per call holding the whole batch (so embedding still batches
across items), runs `NOTIFY contex_ingest`, and returns.

### `ingest_jobs` table

| Column | Type | Notes |
|---|---|---|
| `id` | bigserial PK | returned to the caller as `job_id` |
| `project_id` | text | |
| `tenant_id` | text | |
| `source`, `actor` | text, JSONB | carried through to the event store |
| `items` | JSONB | the batch as submitted: `[{data_key, data, data_format}]` |
| `status` | text | `queued`, `running`, `done`, `failed` |
| `attempts` | int | |
| `last_error` | text | |
| `run_after` | timestamptz | retry backoff |
| `lease_expires_at` | timestamptz | crash recovery |
| `created_at`, `started_at`, `finished_at` | timestamptz | |

A partial index on `(status, run_after)` over `queued`/`running` rows serves the
claim query. Payloads are always JSON: MCP is the only publish caller.

### Worker

`python -m src.worker` wakes on `NOTIFY contex_ingest`, with a polling
fallback, and claims jobs with `SELECT … FOR UPDATE SKIP LOCKED`. It runs the
existing pipeline (`publish_data_batch`: parse, embed, write, append events)
without the per-item reconcile. Until Stage 3 lands, the worker runs one
full `reconcile_project` per job instead. Stage 3 replaces that with change sets
and the gated reconciler.

**Per-project ordering.** Two jobs that write the same `data_key` must apply in
order. A worker claims only a project's oldest queued job, and only when that
project has no `running` job. Different projects run in parallel.

### Failure handling

- **Errors:** the job returns to `queued` with backoff via `run_after`. After 3
  attempts it becomes `failed` with `last_error`. A parse error in one item fails
  the whole job, as it does today.
- **Crashed worker:** a running job holds a lease, renewed while it works. An
  expired lease makes the job claimable again.
- **Re-runs are safe:** ingest skips unchanged nodes by content hash. Appending
  the job's events and marking it `done` commit in one transaction, so a crash
  cannot double-append events.
- **Retention:** `done` jobs are deleted after 24h (payloads can reach the 50MB
  upload limit). `failed` jobs are kept 7 days.

### Topology

- `CONTEX_WORKER=embedded` (default): the API process starts the worker as a
  child process, restarts it if it exits, and stops it on shutdown. One-container
  deploys keep working, and ingest still runs in a separate OS process and GIL.
- `CONTEX_WORKER=external`: no child. Run `python -m src.worker` as its own
  service, with any number of replicas.

### Interface changes

- `contex_publish` returns `{"accepted": data_key, "job_id": …}`. `sequence` is
  dropped: sequences are now assigned by the worker, and nothing in contex or
  contex-web reads it.
- `contex_publish_batch` returns `{"published": n, "job_id": …}`. The
  `published` key stays because `connectors/base/publisher.py` reads it; it now
  counts accepted items.
- New tool `contex_ingest_status(job_id)` returns `status`, `attempts`, and
  `last_error`, scoped by the caller's project permissions. Without it a failed
  ingest is invisible to the publisher.

## Stage 3: Coalesced, gated reconcile

### Why gating is sound

For each need, a bundle is a function of the vector ranker's top `pool`
candidates, the lexical ranker's top `pool` candidates, RRF fusion (rank-based),
the similarity threshold, document collapsing and roots, and the symbols join
that adds `links`. If neither candidate list changes and the linking inputs do
not change, the bundle cannot change. A change can affect need *n* only by:

1. **Witness hit:** an updated or removed node was in *n*'s candidate lists, or
   a touched `data_key` is a document in *n*'s bundle (collapsing and roots read
   the whole document).
2. **Vector entry:** a new or updated node's cosine to *n* reaches *n*'s vector
   floor.
3. **Lexical entry:** a new or updated node's BM25 score for *n* reaches *n*'s
   lexical floor.
4. **Link change:** a changed file adds or removes a definition of a symbol that
   a node in *n*'s bundle references.

### `subscription_needs` table

One row per (subscription, need), written whenever the need is matched (create,
reconcile, sweep):

- the need text and its **cached embedding**, so reconcile never re-encodes needs
- `vector_floor`: cosine of the last of the `pool` vector hits, or −1 when the
  project has fewer than `pool` nodes
- `lexical_floor`: BM25 score of the last of the `pool` lexical hits, or 0 when
  the list is not full
- `witness_node_keys` and `witness_data_keys` (GIN-indexed arrays)
- `ref_names`: symbols referenced by the bundle's nodes (GIN-indexed)

Existing subscriptions have no rows. A need without a row counts as flagged, so
its first reconcile or the first sweep backfills it.

### Change sets

After each job, the worker records what it changed in `project_changes`:
node_keys added, updated, or removed; `data_key`s touched; and def names added or
removed. Changes accumulate until the reconciler drains them, so a burst of jobs
becomes one change set.

### Reconciler

Runs in the worker. It claims projects with pending changes (`SKIP LOCKED`),
drains their change sets, and gates, cheapest check first, each check running
only on needs not yet flagged:

1. **Witness and link:** one query using array overlap on the GIN-indexed
   columns.
2. **Vector:** one query joining the changed embeddings to the project's need
   embeddings, keeping pairs with cosine ≥ `vector_floor`.
3. **Lexical:** per remaining need, BM25 restricted to the changed rows
   (`id = ANY(:changed) AND (description @@@ :q OR data_original @@@ :q)`),
   flagging scores ≥ `lexical_floor`.

**Large-change fallback:** when the change set exceeds 20% of the project's
nodes, skip the gate and flag every need. Past that point gating costs about as
much as re-matching.

Flagged needs are re-matched and spliced into their bundles, `links` are re-run
for each affected subscription, and the existing equality check decides whether
to `NOTIFY`. Changes that arrive mid-reconcile stay in `project_changes`, and
the project is reconciled again afterward.

### Inexactness and the sweep

Witness, vector, and link checks are exact: existing embeddings never move. The
one inexact input is BM25 corpus statistics. New documents lower the IDF of
their terms, which can reorder existing lexical candidates or move one across the
floor without any change set touching it. The effect is a slightly stale lexical
ordering in some bundles.

A **sweep** bounds it. When the queue is idle and more than
`CONTEX_RECONCILE_SWEEP_INTERVAL` (default 1h) has passed since the last sweep,
the worker re-matches every need project by project (searches only, since need
embeddings are cached), refreshes `subscription_needs`, and notifies only for
bundles that changed.

## Testing

- **Existing tests** that publish then query get an `await engine.drain()`
  helper that runs pending jobs and the reconciler inline.
- **Stage 1:** notification fires only on commit; reconnect triggers re-read;
  MCP bridge and SSE receive updates without Redis.
- **Stage 2:** claim ordering (no same-project overlap, cross-project
  parallelism); lease expiry and reclaim; retry then `failed`; events and `done`
  commit together; `contex_ingest_status` respects project scope.
- **Stage 3:** a differential test applies random publishes, deletes, and symbol
  changes and asserts gated reconcile produces the same bundles as a full
  re-match. The corpus is fixed so lexical drift cannot occur. Also: a burst
  coalesces into one reconcile; the large-change fallback flags everything;
  needs without rows get backfilled.
- **End to end:** query latency during a large batch ingest stays within a set
  bound of idle latency.

## To verify before implementing

- `paradedb.score` returns the same index-wide BM25 score when the query adds an
  `id = ANY(…)` filter. The lexical gate compares that score to a floor recorded
  from an unfiltered search, so they must be comparable.
- Whether the per-need lexical gate can be batched in one statement (for
  example `LATERAL` over `subscription_needs` with a non-constant `@@@`
  right-hand side). The fallback is one small query per remaining need.

## Out of scope

- A cap on queued jobs per project. The ingest rate limiter bounds intake.
- Relative sliding time windows for subscriptions (#135).
- `DATABASE_LISTEN_URL` for transaction pooling.
