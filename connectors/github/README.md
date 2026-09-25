# GitHub Connector

Bulk-loads files, issues, pull requests, and commits from one or more GitHub
repositories into a Contex project. v1 is a snapshot connector: re-run it (e.g.
on a cron) to refresh. Live sync via webhooks is out of scope.

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
| `source.resources` | Subset of `[files, issues, pulls, commits]`; defaults to `[files, issues, pulls]` |
| `source.state` | Issue/PR state filter: `open`, `closed`, or `all` (default) |
| `batch_size` | Items per `contex_publish_batch` call (default 500) |
| `include_binary` | Include binary files (default `false`) |
| `paths.include` | Glob allowlist for file paths |
| `paths.exclude` | Glob denylist for file paths |
| `commits.since` | Lower bound for commit history (ISO date); defaults to the latest release |
| `commits.branch` | Ref to read commits from; defaults to the repo's default branch |

## Run the container image

The published image `ghcr.io/cahoots-org/contex-connector-github` is fully
env-driven — no config file needed. `GITHUB_REPOS` takes a single `owner/repo`
slug:

```bash
docker run --rm \
  -e CONTEX_URL=http://contex:8001/mcp \
  -e CONTEX_PROJECT_ID=my-app \
  -e CONTEX_TOKEN=svc_... \
  -e GITHUB_TOKEN=ghp_... \
  -e GITHUB_REPOS=cahoots-org/contex \
  ghcr.io/cahoots-org/contex-connector-github:latest
```

| Env var | Maps to | Notes |
|---|---|---|
| `CONTEX_URL` | `contex.url` | MCP endpoint |
| `CONTEX_PROJECT_ID` | `contex.project_id` | Target project |
| `CONTEX_TOKEN` | `contex.service_account_token` | Optional; omit if auth is off |
| `GITHUB_TOKEN` | `source.token` | PAT with `repo` read scope |
| `GITHUB_REPOS` | `source.repos` | A single `owner/repo` slug |
| `CONTEX_BATCH_SIZE` | `batch_size` | Optional; defaults to 500 |

The image ingests `files, issues, pulls`. To ingest several repos, add
`commits`, or change path/state filters, mount your own yaml over the baked
default:

```bash
docker run --rm -v "$PWD/connector.yaml:/etc/contex/connector.yaml" \
  ghcr.io/cahoots-org/contex-connector-github:latest
```

## Resource mappings

- **File**: key `{owner}/{repo}:{path}`, format `text`
- **Issue**: key `{owner}/{repo}#{number}`, format `json` — includes `created_at`,
  `updated_at`, `closed_at`, and per-comment `created_at`.
- **Pull request**: key `{owner}/{repo}!{number}`, format `json` — includes
  `created_at`, `updated_at`, `closed_at`, `merged_at`, and per-comment `created_at`.
- **Commit**: key `{owner}/{repo}@{sha}`, format `json`. Payload includes the
  message, author and committer, parents, additions/deletions stats, and the
  list of changed files (filename, status, additions, deletions).

Re-running upserts on these stable keys — no duplicates.

## Commits

Commits are opt-in: add `commits` to `source.resources`. Because the GitHub
list endpoint omits per-commit files and stats, the connector fetches each
commit's detail individually, so history is bounded to keep the API cost sane:

- By default, commits are read from the latest published release forward.
- A repo with no published release is skipped unless you set `commits.since`.
- `commits.since` (an ISO date) overrides the release bound, and
  `commits.branch` selects the ref.

Diffs and patches are not ingested, only the changed-file list and line stats.

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
pip install pytest -r connectors/github/requirements-dev.txt
pytest connectors/github
```
