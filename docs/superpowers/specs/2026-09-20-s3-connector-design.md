# S3 Connector (v1) — Design

**Status:** proposed
**Author:** design brainstorm, 2026-09-20

## Goal

Bulk-load the text objects in an S3 bucket (or prefix) into a Contex project
with read-only access, no glue code. Second connector; shares the seam and
framework defined in the [Postgres connector spec](2026-09-20-postgres-connector-design.md).

## Shared foundation (see Postgres spec)

- **`contex_publish_batch` MCP tool** — the batch upsert sink.
- **`connectors/base/`** — the `ChangeEvent{op,key,payload,source_meta}` contract,
  the batching/progress/service-account runner, and config base
  (`contex.url/project_id/service_account_token`, `batch_size`, allow/deny globs).

This spec covers only the S3-specific reader and config.

## Scope decision: bulk snapshot, not sync

Same as Postgres: a **run-once, repeatable bulk importer**, not live sync. S3
event notifications (SNS/SQS) are out of scope for v1. Re-running upserts by
object key, so a cron re-run is the refresh story.

## What we ingest

**Text-like objects only.** Just as the Postgres connector skips `bytea`, the
S3 connector skips non-text objects by default (images, video, archives,
binaries) — they aren't useful as searchable context. An object is ingested if
its content type / extension is text-like: `.txt .md .markdown .json .csv .tsv
.yaml .yml .html .log .rst` (configurable). Binary/unknown types are skipped
unless explicitly allowed.

## Selection model

Pull **everything under the prefix** by default, then narrow:

- `keys.include` / `keys.exclude` — glob patterns over the object key.
- `content_types` — the allow-list of text-like extensions/types (has a sane
  default; overridable).
- `max_object_bytes` — skip objects larger than this (default e.g. 5 MB) so a
  stray giant file doesn't dominate the embedding run. Skips are logged.

## Object → context mapping

- **data_key**: the full object key (e.g. `docs/runbooks/db.md`). Re-runs upsert.
- **payload**:
  - `.json` → parsed into a dict, published as `json`.
  - everything else text-like → published as `text` (the file's decoded content).
- **One object = one context item** in v1 (no chunking). Large-but-under-cap
  files are published whole; chunking is a later refinement.

## Config (`connector.yaml`)

```yaml
contex:
  url: http://localhost:8001/mcp
  project_id: my-app
  service_account_token: ${CONTEX_TOKEN}
source:
  bucket: my-knowledge-bucket
  prefix: docs/
  region: us-east-1
  # credentials via the standard AWS chain (env/instance role); explicit keys optional
batch_size: 200
max_object_bytes: 5242880
keys:
  exclude: ["*.tmp", "archive/*"]
content_types: [".md", ".txt", ".json", ".csv", ".yaml", ".html"]
```

## Runner behavior

1. List objects under `bucket/prefix` (paginated), apply `keys` allow/deny and
   the content-type filter, and drop anything over `max_object_bytes`.
2. Fetch each surviving object, decode, map to a `ChangeEvent(op="upsert")`.
3. Batch to `contex_publish_batch`; log progress (`N / total objects`,
   skipped-count with reasons) and a final summary. Idempotent.

## Limitations (documented up front)

- **Not live sync.** Snapshot; refresh by re-running or publishing updates.
- **Deletes don't propagate** on re-run.
- **Text objects only.** Binary/unknown types skipped; images, PDFs, docx are
  not parsed in v1.
- **Whole-object items.** No chunking, and objects over `max_object_bytes` are
  skipped (logged).
- **Bulk means embeddings.** Every ingested object is embedded; the run reports
  counts up front.

## Out of scope (later)

S3 event notifications (SNS/SQS) for live updates, PDF/docx/image-OCR parsing,
per-object chunking, versioned-object handling.

## Testing

- Unit: key allow/deny + content-type filtering; `max_object_bytes` skip; object
  → `ChangeEvent` mapping (json vs text); key derivation.
- Integration: run against a mock S3 (`moto`/localstack) seeded with a few
  objects; assert text objects are published and binary/oversized are skipped;
  re-run and assert upsert (no duplication).
