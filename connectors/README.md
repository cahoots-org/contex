# Contex connectors

Connectors bulk-load an external source into a Contex project. Each is an
**out-of-process** program you run (a CLI for v1) whose only contract with
Contex is publishing over MCP — so connectors scale and fail independently of
the server.

```
source (read-only)  ->  connector  ->  MCP: contex_publish_batch  ->  Contex
                        reads -> ChangeEvents   (with an API key)        embeds + stores
```

v1 connectors are **bulk snapshots, not live sync**: a run reads the source and
upserts every item by a stable key, so re-running (e.g. on a cron) is the
refresh story.

To run a connector image against Contex in another container, put both on one
Docker network (`--network`) and add the host in `CONTEX_URL` to the server's
`CONTEX_ALLOWED_HOSTS`; otherwise the server answers 421.

## The shared framework (`connectors/base/`)

- **`ChangeEvent`** — the seam every reader emits: `{op, key, payload,
  data_format}`. `op` is `"upsert"`, `"delete"`, or `"retain"`
  (keep an item that could not be fetched this run).
- **`run` / `run_connector`** — batch a stream of `ChangeEvent`s and publish each
  batch, reporting progress. A batch holds up to `batch_size` items (default
  500); the server rejects batches over 1000 items by default, so keep it at or below that.
- **`ContexPublisher`** — the MCP transport; publishes batches through the
  `contex_publish_batch` tool and deletes through `contex_delete`, authenticating with an API key when
  one is configured.
- **Documents** — `document_event` sends a PDF, `.docx`, or image as base64,
  and Contex extracts the text server-side. `documents:` in `connector.yaml`
  lists the extensions to send: `[.pdf, .docx]` by default. Add `.png`, `.jpg`,
  or `.tiff` only when the server runs with `OCR_ENABLED=true`, which also OCRs
  scanned PDF pages.
- **Deletes** — each run tags what it publishes with an origin (e.g.
  `s3:bucket/prefix`). After a full run completes, the runner lists that
  origin's keys with `contex_list_keys` and deletes the ones the source no
  longer has. Runs filtered by a since-date, and runs that read nothing (usually
  bad credentials), never delete. Set `prune: false` in
  `connector.yaml` to turn this off. The origin names the source, not your
  filters, so two configs reading one source (say, Jira projects A and B)
  would delete each other's items: give each a distinct top-level `origin:`
  name (e.g. `origin: team-a`). Postgres keys don't name the host, nor S3
  keys the endpoint, so two servers with a same-named database, or two
  endpoints with a same-named bucket, overwrite each other: publish each to
  its own project. Don't overlap runs of one config, or each prunes the
  other's fresh items; on a cron, wrap the run in `flock -n`, e.g.
  `flock -n /tmp/pg.lock python -m connectors.postgres --config connector.yaml`.
  Removing or renaming a source leaves its old origin's keys behind: list
  them with `contex_list_keys` (passing the old `origin`) and remove them
  with `contex_delete`.
- **`load_config` / `ContexConfig` / `resolve_batch_size`** — read a
  `connector.yaml`, expanding `${VAR}` from the environment.
- **`allowed` / `matches_any`** — include/exclude glob selection.
- **Secret guard** — on by default, the runner drops any item that is or
  contains a secret (`.env`, `*.pem`, `id_rsa`, private-key blocks, `AKIA…` /
  `ghp_…` / Slack / Google keys) before it is published, and counts the drops in
  `RunStats.skipped_secrets`. Documents are scanned by their text, so images
  and scanned PDFs, which have none to read, are dropped too while content
  scanning is on. Configure in `connector.yaml`:

  ```yaml
  allow_secrets: false   # the switch: true ingests secrets as-is (no scanning)
  secrets:               # optional, only when protecting
    scan_content: true   # also scan file *contents* (layer 2), not just names
    # Both lists ADD to the built-ins (they never replace them):
    files: []            # secret filename globs, e.g. "*.secret"
    content: []          # secret-content regexes, e.g. "ACME_[A-Z0-9]{32}"
  ```

## Upgrading from v1.1.0

v1.1.0 connectors published without an origin, and the S3, Postgres and
Atlassian keys have since changed. Pruning only lists keys by origin, so it
never removes the old copies; each stays next to its replacement until you
delete it.

| Connector  | v1.1.0 key              | Current key                  |
| ---------- | ----------------------- | ---------------------------- |
| S3         | `{key}`                 | `{bucket}/{key}`             |
| Postgres   | `{schema}.{table}.{pk}` | `{db}.{schema}.{table}.{pk}` |
| Jira       | `jira:{KEY}`            | `{site}:jira:{KEY}`          |
| Confluence | `confluence:{id}`       | `{site}:confluence:{id}`     |

GitHub keys are unchanged. Run each connector once on the new version, then
list the project's keys that still have no origin:

```sql
SELECT DISTINCT data_key FROM embeddings
WHERE project_id = '<project>' AND origin IS NULL ORDER BY data_key;
```

This also lists anything published with `contex_publish`, which never sets an
origin, so keep only keys in the v1.1.0 formats above. Delete them with
`contex_delete` (up to 1000 keys per call). If the project holds only connector
data, re-running the connectors into a new project is simpler.

## Writing a connector

A connector supplies a reader that yields `ChangeEvent`s and hands the stream to
the runner:

```python
from connectors.base import ChangeEvent, ContexConfig, load_config, resolve_batch_size, run_connector

def read_rows(source_config):
    for row in ...:                      # source-specific
        yield ChangeEvent(
            op="upsert",
            key=f"{database}.{schema}.{table}.{pk}",
            payload=row_as_dict,
        )

config = load_config("connector.yaml")
await run_connector(
    ContexConfig.from_dict(config),
    read_rows(config["source"]),
    batch_size=resolve_batch_size(config),
    progress=lambda n: print(f"published {n}"),
)
```

Each connector lives in its own package (e.g. `connectors/postgres/`) with its
own source-client dependency and `connector.yaml` example.
