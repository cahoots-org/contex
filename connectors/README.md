# Contex connectors

Connectors bulk-load an external source into a Contex project. Each is an
**out-of-process** program you run (a CLI for v1) whose only contract with
Contex is publishing over MCP — so connectors scale and fail independently of
the server.

```
source (read-only)  ->  connector  ->  MCP: contex_publish_batch  ->  Contex
                        reads -> ChangeEvents   (as a service account)   embeds + stores
```

v1 connectors are **bulk snapshots, not live sync**: a run reads the source and
upserts every item by a stable key, so re-running (e.g. on a cron) is the
refresh story. See each connector's design spec under
[`docs/superpowers/specs/`](../docs/superpowers/specs/).

## The shared framework (`connectors/base/`)

- **`ChangeEvent`** — the seam every reader emits: `{op, key, payload,
  source_meta, data_format}`. `op` is `"upsert"`, `"delete"`, or `"retain"`
  (keep an item that could not be fetched this run).
- **`run` / `run_connector`** — batch a stream of `ChangeEvent`s and publish each
  batch, reporting progress. Batches are bounded by the server's `MAX_BATCH_SIZE`.
- **`ContexPublisher`** — the MCP transport; publishes batches through the
  `contex_publish_batch` tool and deletes through `contex_delete`, authenticating with a service-account token when
  one is configured.
- **Deletes** — each run tags what it publishes with an origin (e.g.
  `s3:bucket/prefix`). After a full run completes, the runner lists that
  origin's keys with `contex_list_keys` and deletes the ones the source no
  longer has. Runs filtered by a since-date never delete. Set `prune: false` in
  `connector.yaml` to turn this off.
- **`load_config` / `ContexConfig` / `resolve_batch_size`** — read a
  `connector.yaml`, expanding `${VAR}` from the environment.
- **`allowed` / `matches_any`** — include/exclude glob selection.
- **Secret guard** — on by default, the runner drops any item that is or
  contains a secret (`.env`, `*.pem`, `id_rsa`, private-key blocks, `AKIA…` /
  `ghp_…` / Slack / Google keys) before it is published, and counts the drops in
  `RunStats.skipped_secrets`. Configure in `connector.yaml`:

  ```yaml
  allow_secrets: false   # the switch: true ingests secrets as-is (no scanning)
  secrets:               # optional, only when protecting
    scan_content: true   # also scan file *contents* (layer 2), not just names
    # Both lists ADD to the built-ins (they never replace them):
    files: []            # secret filename globs, e.g. "*.secret"
    content: []          # secret-content regexes, e.g. "ACME_[A-Z0-9]{32}"
  ```

## Writing a connector

A connector supplies a reader that yields `ChangeEvent`s and hands the stream to
the runner:

```python
from connectors.base import ChangeEvent, ContexConfig, load_config, resolve_batch_size, run_connector

def read_rows(source_config):
    for row in ...:                      # source-specific
        yield ChangeEvent(
            op="upsert",
            key=f"{schema}.{table}.{pk}",
            payload=row_as_dict,
            source_meta={"source": "postgres", "schema": schema, "table": table},
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
