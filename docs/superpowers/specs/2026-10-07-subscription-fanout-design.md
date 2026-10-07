# Subscription fan-out: reconcile only what a publish can affect

**Status:** draft for review
**Date:** 2026-10-07
**Related:** `SubscriptionService.reconcile_project` (`src/core/subscriptions.py`), the unused `changed_data_key` parameter, Postgres LISTEN/NOTIFY (#250).

## Problem

Every publish re-matches every subscription in the project, inline on the request path.

- `publish_data_batch` calls `_record_publish` per item, and each call runs a full `reconcile_project`. A 2,000-file connector snapshot into a project with 200 subscriptions of 3 needs runs 1.2M searches.
- Each search re-encodes its need text.

`changed_data_key` was added to narrow this and is ignored.

## Goal

Cost of a publish scales with the subscriptions it can affect, not with all subscriptions in the project. No new persisted per-need state: subscriptions are meant to be short-lived, and this work must not add stored data about them.

## Decisions (locked with Rob)

- **Eventual consistency is acceptable.** The filter is exact for vector-only matching. Under hybrid search, a document admitted by BM25 with cosine below the floor is missed until the backstop sweep.
- **No `subscription_needs` table.** Need vectors live in process memory and are rebuilt from `subscriptions.needs`. The admission floor is derived from the stored bundle.
- **Subscription lifetime is out of scope.** Subscriptions persist until explicit delete and bundles copy matched data. That is tracked as a separate issue (lease / `expires_at`, keys-only bundles).

## Design

### 1. One reconcile per publish call

`reconcile_project(project_id, changed_keys: set[str] | None = None)` replaces the `changed_data_key` parameter. `publish_data` and `publish_data_batch` record all events, then call it once with the set of published `data_key`s. The per-item call in `_record_publish` is removed. A reconcile failure is logged and does not fail the publish (unchanged).

`changed_keys=None` reconciles every subscription in the project (the sweep path, unchanged behaviour).

### 2. `subscriptions.documents`

New `text[]` column, GIN-indexed: every document key the bundle touches — each match's `document`, its `related` nodes' documents, and its `links` targets' documents. Written in the same UPDATE as `bundle` on create and on every changed reconcile. It holds only keys already present in `bundle`.

Migration `016_subscription_documents` adds the column (default `'{}'`) and index, and backfills from existing bundles in SQL.

### 3. Affected-subscription filter

Given `changed_keys` K, a subscription is affected if any of:

1. **Already includes K.** `documents && K`. Covers edits that change or demote a matched document.
2. **K changes its links.** `documents && D`, where D is the set of documents whose nodes `ref` a name that some node of K `def`s. One query over `symbols`.
3. **Could match a new node.** Some node of a document in K has cosine to some need at or above that need's floor.

Only the union is reconciled with the existing per-subscription logic.

Filter 3 runs in Python:
- Load all node embeddings for documents in K (`embeddings.data_key IN K`), as one matrix.
- Need vectors come from an in-process cache keyed by need text (`functools.lru_cache` around `model.encode`, normalized). Misses encode on demand; a restart re-encodes on first use.
- One matmul of nodes × needs; a subscription is affected if any need column has a value ≥ that need's floor.
- `# ponytail:` ceiling: about 3KB per cached need (768 floats), so 30k distinct needs is about 90MB per process. Upgrade path is an ANN index on persisted need vectors if this binds.

The filter loads each subscription's `needs`, `top_k`, `threshold` and per-need floor, never the bundle itself (see §4).

### 4. Admission floor

Per need: if the bundle for that need holds fewer than `top_k` documents, `floor = effective threshold`. Otherwise `floor = min(similarity of each document's best node)`. Reported `similarity` is cosine in both vector and hybrid modes (`hybrid_search_service.py`), so the comparison is on one scale.

The floor is computed in SQL from the stored bundle, so nothing is cached and nothing goes stale across replicas: `jsonb_each(bundle)` per need, `jsonb_array_elements` per document, `count(*)` and `min((m->>'similarity')::float)` grouped by `(subscription_id, need)`. Postgres reads the bundles; only `(subscription_id, need, count, min)` rows cross the wire.

### 5. Backstop sweep

A background task started in `main.py`'s lifespan, next to the `Notifier`, runs `reconcile_project(project_id)` for every project with subscriptions every `RECONCILE_SWEEP_SECONDS` (default 300, `0` disables). Only one replica runs a given pass: it takes `pg_try_advisory_lock` and skips the pass if the lock is held. Exceptions are logged per project and the loop continues.

## Error handling

- Filter failure (e.g. embedding load error): log and fall back to reconciling every subscription in the project. Correctness over cost.
- A subscription deleted between filter and reconcile: the existing `scalar_one()` raises inside the per-subscription `try`, is logged, and skipped. Change it to `scalar_one_or_none()` and skip silently.

## Testing

Against real Postgres, with a recording matcher:

- A batch publish of N items triggers exactly one reconcile pass.
- An unrelated publish whose nodes fall below every floor reconciles no subscriptions.
- A publish whose node clears a need's floor reconciles that subscription only.
- An edit to a document already in a bundle reconciles that subscription.
- A new `def` for a name a bundled node `ref`s reconciles that subscription.
- A not-full bundle uses the threshold as its floor.
- The sweep reconciles a subscription the filter skipped.
- Two concurrent sweeps: only one runs (advisory lock).
- Migration 016 backfills `documents` from an existing bundle.

## Commits

Each a vertical slice, CI-green:

1. One reconcile per publish call (`changed_keys` set).
2. `documents` column + migration 016 + filter 1.
3. Filter 2 (symbol links).
4. Need-vector cache, floor, filter 3.
5. Backstop sweep.
