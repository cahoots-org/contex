# GitHub Connector

Bulk-loads files, issues, and pull requests from one or more GitHub repositories
into a Contex project. v1 is a snapshot connector: re-run it (e.g. on a cron)
to refresh. Live sync via webhooks is out of scope.

## Quick start

```bash
pip install -r connectors/github/requirements.txt
# also install the base connector deps
pip install PyYAML mcp

cp connectors/github/connector.yaml.example connector.yaml
# edit connector.yaml — set GITHUB_TOKEN and CONTEX_TOKEN in the environment

export GITHUB_TOKEN=ghp_...
export CONTEX_TOKEN=svc_...

python -m connectors.github --config connector.yaml
```

## Configuration

See `connector.yaml.example` for the full schema. Key fields:

| Field | Description |
|---|---|
| `contex.url` | MCP endpoint of your Contex instance |
| `contex.project_id` | Target project |
| `contex.service_account_token` | Optional service-account token (expand from env) |
| `source.token` | GitHub PAT with `repo` read scope (or App installation token) |
| `source.repos` | List of `owner/repo` slugs to ingest |
| `source.resources` | Subset of `[files, issues, pulls]`; defaults to all three |
| `source.state` | Issue/PR state filter: `open`, `closed`, or `all` (default) |
| `batch_size` | Items per `contex_publish_batch` call (default 500) |
| `include_binary` | Include binary files (default `false`) |
| `paths.include` | Glob allowlist for file paths |
| `paths.exclude` | Glob denylist for file paths |

## Resource mappings

- **File**: key `{owner}/{repo}:{path}`, format `text`
- **Issue**: key `{owner}/{repo}#{number}`, format `json`
- **Pull request**: key `{owner}/{repo}!{number}`, format `json`

Re-running upserts on these stable keys — no duplicates.

## Rate limits

The connector reads `X-RateLimit-Remaining` / `X-RateLimit-Reset` from every
GitHub response and sleeps until the reset window before retrying. Large
repositories will be slow; progress is logged per resource.

## Limitations

- Not live sync: snapshot only; no webhook support in v1.
- Deleted files / closed issues are not removed from Contex on re-run.
- Binary files are skipped by default.
- Whole-file ingestion; no code-aware chunking.

## Running tests

```bash
pip install pytest pytest-anyio pytest-httpx
pytest connectors/github
```
