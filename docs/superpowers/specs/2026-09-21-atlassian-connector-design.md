# Atlassian Connector (v1) — Design

**Status:** proposed
**Author:** design brainstorm, 2026-09-21

## Goal

Bulk-load an Atlassian Cloud site's knowledge — **Jira issues** and
**Confluence pages** — into a Contex project with an API token, no glue code.
Fourth connector; shares the seam and framework from the
[GitHub connector spec](2026-09-20-github-connector-design.md) and
[Postgres connector spec](2026-09-20-postgres-connector-design.md).

## Shared foundation (see Postgres spec)

- **`contex_publish_batch` MCP tool** — the batch upsert sink.
- **`connectors/base/`** — the `ChangeEvent` contract, batching/progress/
  service-account runner, and config base.

This spec covers only the Atlassian-specific client, readers, and config.

## Why one package, not two

Jira and Confluence are separate products with different APIs and payloads, but
they authenticate against the **same site with the same credentials** (account
email + API token, HTTP basic auth). So they live in one `connectors/atlassian`
package sharing one `AtlassianClient`, with a reader per product — exactly how
the GitHub connector bundles files/issues/pulls under one client. `resources:
[jira, confluence]` selects which run.

## Scope decision: bulk snapshot, not sync

Same as the others: a **run-once, repeatable bulk importer**, not live sync.
Webhooks are out of scope for v1. Re-running upserts by key.

The readers are nonetheless **structured for a future webhook receiver**: each
splits crawl/pagination from a pure entity→`ChangeEvent` mapper
(`issue_to_event`, `page_to_event`) and a pure `build_jql`. A receiver can
import those and publish a single event per callback with no change to mapping.

## What we ingest (configurable resources)

- **Jira issues** — summary, description, type, status, priority, resolution,
  labels, components, assignee, reporter, created/updated, parent, and the full
  comment thread, per issue. Descriptions and comments are **ADF** (Atlassian
  Document Format JSON), flattened to text.
- **Confluence pages** — title, space, body, version, author, timestamps,
  labels, and footer comments, per current page. Bodies are **storage-format
  XHTML**, flattened to text.

## Selection model

- `resources` — subset of `[jira, confluence]` (default: both).
- `jira.projects` — project-key allow-list; omit for all visible projects.
- `jira.jql` — extra clause AND-ed into the query; `jira.since` — `updated >=`
  date filter (cheap incremental).
- `confluence.spaces` — space-key allow-list; omit for all non-personal spaces.
- `confluence.include_personal` — include `~personal` spaces (default `false`).
- `{jira,confluence}.include_comments` — fetch comment threads (default `true`).

## Mapping

- **Jira issue**: `data_key = "jira:{ISSUE-KEY}"` (e.g. `jira:DEV-2946`); payload
  the dict above; published as `json`. `source_meta` carries source/project/key/
  url/updated.
- **Confluence page**: `data_key = "confluence:{pageId}"`; payload the dict
  above; published as `json`. `source_meta` carries source/spaceId/id/url/updated.

Source-prefixed keys keep Jira, Confluence, and GitHub collision-free inside one
project. Re-runs upsert on these keys.

## Auth & rate limits

- **HTTP basic auth**: `source.email` + `source.token` (an Atlassian API token).
- **Respect rate limits**: retry `429` honoring `Retry-After`, then raise. Large
  sites are slow — expected and reported in progress output.

## APIs used

- Jira enhanced search `POST /rest/api/3/search/jql` (opaque `nextPageToken`
  pagination + `isLast`), with `/rest/api/3/issue/{key}/comment` for comment
  threads larger than the inline `comment` field returns. (The legacy
  `/rest/api/3/search` is deprecated.)
- Confluence v2 `/wiki/api/v2/spaces` and `/wiki/api/v2/pages` (`_links.next`
  cursor pagination), plus `/labels` and `/footer-comments` per page.

## Config (`connector.yaml`)

```yaml
contex:
  url: ${CONTEX_URL}
  project_id: my-app
  service_account_token: ${CONTEX_TOKEN}
source:
  site_url: ${ATLASSIAN_URL}
  email: ${ATLASSIAN_EMAIL}
  token: ${ATLASSIAN_API_KEY}
  resources: [jira, confluence]
  jira:
    projects: [DEV, CONT]
    include_comments: true
  confluence:
    spaces: [ENGINEERIN, PKB]
    include_personal: false
    include_comments: true
batch_size: 200
```

## Runner behavior

1. Build one `AtlassianClient` from `site_url`/`email`/`token`.
2. For each enabled resource:
   - **jira**: compose JQL from projects/since/jql, page search results, flatten
     ADF, page large comment threads, map each issue → `ChangeEvent`.
   - **confluence**: enumerate spaces (drop personal unless opted in, apply
     allow-list), page each space's current pages, flatten storage HTML, fetch
     labels + footer comments, map each page → `ChangeEvent`.
3. Batch to `contex_publish_batch`; retry on 429. Log progress per resource and a
   final summary. Idempotent.
4. `--dry-run N` reads and prints the first N events per resource without
   publishing (no Contex instance required) — the smoke test for credentials and
   mapping.

## Limitations (documented up front)

- **Not live sync.** Snapshot; refresh by re-running. No webhooks in v1.
- **Deletes don't propagate** on re-run (a deleted/archived issue or page stays
  until overwritten).
- **Attachments not ingested** (only their issues/pages); **inline** Confluence
  comments skipped (footer comments included).
- **Rate limits** make large sites slow; the run reports progress and backs off.
- **Bulk means embeddings** — every issue/page is embedded; counts reported.

## Out of scope (later)

Webhooks for live sync (receiver seam is in place), attachment/PDF extraction,
inline comments, Jira boards/sprints/worklogs, Confluence blogposts, delete
propagation.

## Testing

- Unit: ADF→text and storage-HTML→text conversion; `build_jql`; issue/page →
  `ChangeEvent` mapping and key derivation; `Retry-After` backoff.
- Integration: run the readers against a mocked Atlassian API (`pytest-httpx`) —
  assert issues/pages publish with correct keys, inline vs paged comments, and
  personal-space skipping. (A live-API test would be flaky, so mock; a manual
  `--dry-run` covers the real API.)
