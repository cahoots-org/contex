# GitHub Connector (v1) — Design

**Status:** proposed
**Author:** design brainstorm, 2026-09-20

## Goal

Bulk-load a GitHub repository's knowledge — its files, issues, and pull requests
— into a Contex project with a read-only token, no glue code. Third connector;
shares the seam and framework from the
[Postgres connector spec](2026-09-20-postgres-connector-design.md).

## Shared foundation (see Postgres spec)

- **`contex_publish_batch` MCP tool** — the batch upsert sink.
- **`connectors/base/`** — the `ChangeEvent` contract, batching/progress/
  service-account runner, and config base.

This spec covers only the GitHub-specific readers and config.

## Scope decision: bulk snapshot, not sync

Same as the others: a **run-once, repeatable bulk importer**, not live sync.
Webhooks are out of scope for v1. Re-running upserts by key.

## What we ingest (configurable resources)

Three resource types, each of which can be toggled; v1 defaults to files +
issues + pulls:

- **Files** — the default branch's tree. Text/code files only; binary files
  (detected via the API's blob metadata / extension) are skipped, mirroring the
  Postgres `bytea` and S3 text-only rules. Path allow/deny globs apply.
- **Issues** — title, body, labels, state, author, and comments, per issue.
- **Pull requests** — title, body, state, author, and comments, per PR.

## Selection model

- `repos` — one or more `owner/repo`.
- `resources` — subset of `[files, issues, pulls]` (default: all three).
- `paths.include` / `paths.exclude` — glob patterns over file paths.
- `state` — for issues/pulls: `open | closed | all` (default `all`).
- Binary files skipped by default (`include_binary: false` to override).

## Mapping

- **File**: `data_key = "{owner}/{repo}:{path}"`; payload = the decoded file
  text, published as `text`.
- **Issue**: `data_key = "{owner}/{repo}#{number}"`; payload = a dict of
  `{title, body, state, labels, author, comments:[...]}`, published as `json`.
- **Pull request**: `data_key = "{owner}/{repo}!{number}"`; payload same shape
  as issues.

Re-runs upsert on these keys.

## Auth & rate limits

- Read-only **token** (PAT or GitHub App installation token) via
  `source.token`. v1 uses a token; the App installation flow is out of scope.
- **Respect rate limits**: read `X-RateLimit-Remaining`/`Reset`, back off and
  resume rather than hammering. Large repos will be slow — that's expected and
  reported in progress output.

## Config (`connector.yaml`)

```yaml
contex:
  url: http://localhost:8001/mcp
  project_id: my-app
  service_account_token: ${CONTEX_TOKEN}
source:
  token: ${GITHUB_TOKEN}
  repos: ["cahoots-org/contex"]
  resources: [files, issues, pulls]
  state: all
batch_size: 200
include_binary: false
paths:
  exclude: ["*.lock", "vendor/*", "*.min.js"]
```

## Runner behavior

1. For each repo and each enabled resource:
   - **files**: fetch the default-branch git tree, apply `paths` allow/deny, skip
     binary blobs, fetch contents.
   - **issues / pulls**: page through the list (filtered by `state`), fetch
     comments.
2. Map each item to a `ChangeEvent(op="upsert")`; batch to
   `contex_publish_batch`; back off on rate-limit headers.
3. Log progress per resource and a final summary. Idempotent.

## Limitations (documented up front)

- **Not live sync.** Snapshot; refresh by re-running. No webhooks in v1.
- **Deletes don't propagate** on re-run (a deleted file/closed issue stays until
  overwritten).
- **Binary files skipped**; no code parsing/chunking (whole file = one item).
- **Rate limits** make large repos slow; the run reports progress and backs off.
- **Bulk means embeddings** — every file/issue/PR is embedded; counts reported.

## Out of scope (later)

Webhooks for live sync, GitHub App installation flow, wiki/discussions/releases,
code-aware chunking, delete propagation.

## Testing

- Unit: path allow/deny + binary skip; file/issue/PR → `ChangeEvent` mapping and
  key derivation; rate-limit backoff logic.
- Integration: run the readers against a mocked GitHub API (recorded/fixture
  responses) — assert files/issues/pulls publish with correct keys and re-run
  upserts. (A live-API integration test would be rate-limited and flaky, so mock.)
