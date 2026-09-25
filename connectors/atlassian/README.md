# Atlassian Connector

Bulk-loads **Jira issues** and **Confluence pages** from one Atlassian Cloud
site into a Contex project. Jira and Confluence share the same site credentials,
so they live in one connector with a shared client and two readers. v1 is a
snapshot connector: re-run it (e.g. on a cron) to refresh. Live sync via
webhooks is out of scope (but the readers are structured for it — see below).

## Quick start

```bash
pip install -r connectors/atlassian/requirements.txt

cp connectors/atlassian/connector.yaml.example connector.yaml
# edit connector.yaml, then export the referenced env vars:
export ATLASSIAN_URL=https://your-org.atlassian.net
export ATLASSIAN_EMAIL=you@your-org.com
export ATLASSIAN_API_KEY=...        # https://id.atlassian.com/manage-profile/security/api-tokens
export CONTEX_URL=http://localhost:8001/mcp
export CONTEX_TOKEN=svc_...          # only if AUTH_ENABLED

# read a few items without publishing (no Contex needed):
python -m connectors.atlassian --config connector.yaml --dry-run 5

# full run:
python -m connectors.atlassian --config connector.yaml
```

## Configuration

See `connector.yaml.example` for the full schema. Key fields:

| Field | Description |
|---|---|
| `contex.url` / `contex.project_id` | MCP endpoint and target project |
| `contex.service_account_token` | Optional service-account token (expand from env) |
| `source.site_url` | Atlassian site base URL (`https://org.atlassian.net`) |
| `source.email` / `source.token` | Basic-auth credentials (account email + API token) |
| `source.resources` | Subset of `[jira, confluence]`; defaults to both |
| `source.jira.projects` | Project-key allow-list; omit for all visible projects |
| `source.jira.jql` | Extra JQL clause AND-ed into the query |
| `source.jira.since` | Only issues with `updated >=` this date |
| `source.jira.include_comments` | Fetch comment threads (default `true`) |
| `source.confluence.spaces` | Space-key allow-list; omit for all non-personal spaces |
| `source.confluence.include_personal` | Include `~personal` spaces (default `false`) |
| `source.confluence.include_comments` | Fetch footer comments (default `true`) |
| `source.confluence.since` | Only pages whose current version is newer |
| `batch_size` | Items per `contex_publish_batch` call (default 500) |

## Run the container image

The published image `ghcr.io/cahoots-org/contex-connector-atlassian` is fully
env-driven — no config file needed:

```bash
docker run --rm \
  -e CONTEX_URL=http://contex:8001/mcp \
  -e CONTEX_PROJECT_ID=my-app \
  -e CONTEX_TOKEN=svc_... \
  -e ATLASSIAN_URL=https://your-org.atlassian.net \
  -e ATLASSIAN_EMAIL=you@your-org.com \
  -e ATLASSIAN_API_KEY=... \
  ghcr.io/cahoots-org/contex-connector-atlassian:latest
```

| Env var | Maps to | Notes |
|---|---|---|
| `CONTEX_URL` | `contex.url` | MCP endpoint |
| `CONTEX_PROJECT_ID` | `contex.project_id` | Target project |
| `CONTEX_TOKEN` | `contex.service_account_token` | Optional; omit if auth is off |
| `ATLASSIAN_URL` | `source.site_url` | Site base URL (`https://org.atlassian.net`) |
| `ATLASSIAN_EMAIL` | `source.email` | Account the API token belongs to |
| `ATLASSIAN_API_KEY` | `source.token` | Atlassian API token (basic auth) |
| `CONTEX_BATCH_SIZE` | `batch_size` | Optional; defaults to 500 |

The image ingests both `jira` and `confluence` across every project/space you
can see. To scope to specific projects or spaces (or add JQL / `since` filters),
mount your own yaml over the baked default:

```bash
docker run --rm -v "$PWD/connector.yaml:/etc/contex/connector.yaml" \
  ghcr.io/cahoots-org/contex-connector-atlassian:latest
```

## Resource mappings

- **Jira issue**: key `jira:{ISSUE-KEY}` (e.g. `jira:DEV-2946`), format `json`.
  Payload: summary, description (ADF flattened to text), type, status, priority,
  resolution, labels, components, assignee, reporter, created, updated, parent,
  url, comments.
- **Confluence page**: key `confluence:{pageId}`, format `json`. Payload: title,
  space, body (storage HTML flattened to text), version, author, timestamps,
  labels, url, comments.

Re-running upserts on these stable keys — no duplicates.

## APIs used

- Jira enhanced search: `POST /rest/api/3/search/jql` (token pagination), with
  `/rest/api/3/issue/{key}/comment` for large comment threads.
- Confluence v2: `/wiki/api/v2/spaces`, `/wiki/api/v2/pages` (cursor pagination),
  plus `/labels` and `/footer-comments` per page.

## Webhook-ready seam

Each reader splits crawl/pagination from the pure mappers `issue_to_event()` /
`page_to_event()` (and the pure `build_jql()`). A future webhook receiver can
import those mappers and publish a single event per callback with no change to
the mapping logic.

## Limitations

- Not live sync: snapshot only; no webhook support in v1.
- Deleted/archived issues and pages are not removed from Contex on re-run.
- Attachments are not ingested (only their pages/issues).
- Confluence inline comments are skipped; footer comments are included.

## Rate limits

The client retries `429` responses honoring `Retry-After`, then raises. Large
sites are slow; progress is logged per resource.

## Running tests

```bash
pip install pytest pytest-asyncio -r connectors/atlassian/requirements-dev.txt
pytest connectors/atlassian -v
```
