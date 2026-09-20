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
  source_meta, data_format}`. v1 only emits `op="upsert"`.
- **`run` / `run_connector`** — batch a stream of `ChangeEvent`s and publish each
  batch, reporting progress. Batches are bounded by the server's `MAX_BATCH_SIZE`.
- **`ContexPublisher`** — the MCP transport; publishes batches through the
  `contex_publish_batch` tool, authenticating with a service-account token when
  one is configured.
- **`load_config` / `ContexConfig` / `resolve_batch_size`** — read a
  `connector.yaml`, expanding `${VAR}` from the environment.
- **`allowed` / `matches_any`** — include/exclude glob selection.

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
