# Document Dates and Date-Window Filtering — Design

**Status:** proposed
**Author:** design brainstorm, 2026-10-05
**Related:** #135 (time-window filtering), [async ingest spec](2026-10-05-async-ingest-and-gated-reconcile-design.md)

## Goal

Filter search and subscriptions by when a document was written or last
changed, not when Contex ingested it. A 2019 Confluence page backfilled today
must fall in a 2019 window, not "this week."

## Current state

- `since` (#188) filters on `COALESCE(embeddings.updated_at, embeddings.created_at)`,
  which is ingest time. One helper, `src/core/recency.py`, supplies the predicate
  to the vector, lexical, and hybrid paths.
- Connectors already read real dates (GitHub issues, PRs, commits, releases;
  Atlassian pages and issues) but `ChangeEvent.to_item()` sends only `data_key`,
  `data`, and `data_format`. The dates never reach the server.

## Model

Each document carries two dates, copied to every node row of that document:

| Column | Meaning |
|---|---|
| `doc_created_at` | when the document was created, if known |
| `doc_updated_at` | when the document last changed, if known |
| `date_source` | which rule supplied the dates: `explicit`, `front_matter`, `filename`, `body` |

A document's **effective date** is `COALESCE(doc_updated_at, doc_created_at)`.
A document with neither is **undated**.

## Resolution order

Dates are resolved at ingest, server-side, so every publisher benefits. The
first rule that yields a date wins:

1. **Explicit:** `created_at` / `updated_at` on the publish item. Connectors use
   this for their source metadata.
2. **Front matter:** YAML front matter keys `date`, `created`, `updated`,
   `lastmod`, `last_modified` (Jekyll/Hugo conventions). `date` and `created`
   set `doc_created_at`; `updated`, `lastmod`, `last_modified` set
   `doc_updated_at`.
3. **Filename:** a `YYYY-MM-DD` date in the `data_key`'s basename
   (`2025-03-04-notes.md`) sets `doc_created_at`.
4. **Body:** the latest date in the document text that is not after ingest time
   sets `doc_updated_at`. Only unambiguous formats count: ISO 8601 dates and
   timestamps, and month-name forms (`March 4, 2025`, `4 Mar 2025`). Numeric
   slash dates (`3/4/25`) are ignored because their order is ambiguous. Dates
   before 1970 are ignored. Parsing uses the standard library (regex plus
   `datetime.strptime`); no new dependency.

Rules 2–4 run only when rule 1 supplied nothing. Taking the latest non-future
body date errs toward a newer date, so a document may appear in a more recent
window than it belongs in, but is not dropped from the one it belongs in.
`date_source = 'body'` lets callers and debugging tell inferred dates apart.

Dates are written per document even when every node's content hash is
unchanged, so re-running a connector backfills dates without re-embedding.

## Filtering and ordering

- `contex_query` and `contex_create_subscription` take `since` (exists) and
  `until` (new), both ISO 8601. The window is `[since, until)` on the effective
  date.
- **No window:** no date filtering; undated documents are included like
  everything else.
- **Any window:** undated documents are excluded. A document with no date
  cannot be shown to fall inside a window. A caller who wants undated documents
  drops the window. No extra flag.
- `recency.py` is the only filter change; all three ranking paths pick it up.
- `contex_query` gains `order: "relevance" | "date"` (default `relevance`).
  `date` re-orders the relevance-selected `top_k` newest first. Relevance still
  picks which documents return.
- Subscriptions persist `until` in `scope` next to `since`.

**Behavior change:** `since` now means "dated on or after," not "ingested or
updated on or after." Existing undated data drops out of windowed results until
re-ingested with dates. "What changed since I last checked" belongs to the event
stream's per-project sequences, not to a date window.

## Connector wiring

`ChangeEvent` gains optional `created_at` / `updated_at`; `to_item()` forwards
them as the publish item's explicit dates.

| Source | `created_at` | `updated_at` |
|---|---|---|
| GitHub file | — | last commit touching the path |
| GitHub issue / PR | `created_at` | `updated_at` |
| GitHub commit | committer date | committer date |
| GitHub release | `published_at` (else `created_at`) | same |
| Atlassian Jira issue | `created` | `updated` |
| Atlassian Confluence page | page created | latest version date |
| S3 object | — | `LastModified` (from the listing, no extra call) |
| Postgres row | configured column | configured column |

- **GitHub files:** one `GET /repos/{o}/{r}/commits?path=…&per_page=1` per file,
  alongside the blob fetch the reader already makes per file. This doubles REST
  calls for files; the client already waits out rate limits. If call volume
  matters, a GraphQL query can fetch content and last-commit date for a batch of
  files in one call.
- **Postgres:** new per-table config `dates: {created: <column>, updated: <column>}`,
  both optional.

## Schema

Migration adds `doc_created_at`, `doc_updated_at` (timestamptz, nullable) and
`date_source` (text, nullable) to `embeddings`, plus an expression index on
`(project_id, COALESCE(doc_updated_at, doc_created_at))`. Existing rows start
undated; re-running connectors or re-publishing backfills them.

## Interaction with async ingest

- Dates travel in the job's `items` like any other field.
- A date-only change to a document counts as a change to that `data_key` for
  the gated reconciler (witness hit), so windowed subscriptions re-match.
- Absolute windows need nothing more. A relative window ("last 7 days") must age
  documents out without a publish; the reconcile sweep handles that once
  relative windows exist (#135).

## Testing

- Resolution order: each rule wins over the ones after it; rules 2–4 skipped
  when an explicit date is given.
- Body heuristic: picks the latest past date; ignores future dates, slash dates,
  and pre-1970 dates; month-name and ISO forms parse.
- Filtering: `[since, until)` bounds on the effective date across vector,
  lexical, and hybrid; undated excluded under any window, included without one.
- `order="date"` re-orders without changing the selected set.
- Unchanged content with a new date updates the date without re-embedding.
- Each connector maps its source dates per the table.

## Out of scope

- Relative sliding windows (#135).
- Per-node dates (an issue comment's own timestamp). Nodes inherit their
  document's dates.
- Model-based date inference (Jev). Revisit only if the body heuristic's misses
  show up in practice, and then only for documents with several conflicting
  candidate dates.
