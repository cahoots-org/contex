# Contex Kafka connector

Keeps a Contex project current with Kafka topics as they change. It is a
**streaming** connector: it runs continuously, and each message is published
within about `flush_interval` seconds of arriving. A new consumer group first
reads the topic's whole retention, which is the backfill.

## What gets ingested

Messages follow log-compaction semantics: the latest value per key is the
record, and a null value deletes it.

| Message | Result |
|---|---|
| key, value | upsert of `{cluster}:{topic}/{key}` |
| key, null value (tombstone) | delete of `{cluster}:{topic}/{key}` |
| no key | upsert of `{cluster}:{topic}/{partition}/{offset}` |
| no key, null value | dropped (committed, nothing published) |

A value that parses as a JSON object is published as `data_format=json`.
Everything else, including JSON arrays, numbers, strings and `null`, is
published as UTF-8 text. Keys are decoded as UTF-8. A key that isn't valid
UTF-8, or that contains control characters (an integer-serialized key, for
example), is hex-encoded. `published_at` is the message timestamp.

`source.cluster` is required. Keys must name their source instance, and a
bootstrap address is not a stable identity. Two clusters with a same-named
topic stay apart as long as their `cluster` names differ. Documents are tagged
with origin `kafka:{cluster}`. Streams never prune.

## Prerequisites

```
pip install -r connectors/kafka/requirements.txt
```

## Configuration

Copy `connector.yaml.example` to `connector.yaml` and edit:

```yaml
contex:
  url: http://localhost:8001/mcp
  project_id: my-project
  api_key: ${CONTEX_API_KEY}
source:
  cluster: main                  # required; names this cluster in data keys
  bootstrap_servers: localhost:9092
  topics: [orders, customers]
  group_id: contex-main
  auto_offset_reset: earliest    # earliest | latest
  unkeyed: publish               # publish | skip
  security_protocol: PLAINTEXT   # PLAINTEXT | SSL | SASL_PLAINTEXT | SASL_SSL
  sasl_mechanism: PLAIN          # PLAIN | SCRAM-SHA-256 | SCRAM-SHA-512
  sasl_username: ${KAFKA_USER}
  sasl_password: ${KAFKA_PASSWORD}
flush_interval: 1
batch_size: 500
```

`${VAR}` references are expanded from the environment at load time.

| Key | Default | Notes |
|---|---|---|
| `source.cluster` | required | Names the cluster in data keys and the origin |
| `source.bootstrap_servers` | required | Comma-separated `host:port` list |
| `source.topics` | required | Explicit list, or a comma-separated string |
| `source.group_id` | `contex-{cluster}` | Consumer group; offsets are committed under it |
| `source.auto_offset_reset` | `earliest` | Where a group with no committed offset starts |
| `source.unkeyed` | `publish` | `skip` ingests only keyed messages |
| `source.security_protocol` | `PLAINTEXT` | `SSL` variants use the system CA store |
| `source.sasl_mechanism` | `PLAIN` | Used with the `SASL_*` protocols |
| `source.sasl_username` / `sasl_password` | none | Used with the `SASL_*` protocols |
| `flush_interval` | `1` | Seconds a partial batch waits before it is published |
| `batch_size` | `500` | Items per batch; keep at or below the server's limit of 1000 |

The secret guard applies as for the other connectors: `allow_secrets` and
`secrets:` in `connector.yaml`. See [`connectors/README.md`](../README.md).

## Running

```bash
python -m connectors.kafka --config connector.yaml
```

It logs `published N items` as batches are accepted. Stop it with Ctrl-C.

## Running the container image

The published image `ghcr.io/cahoots-org/contex-connector-kafka` is fully
env-driven, so no config file is needed:

```bash
docker run --rm \
  -e CONTEX_URL=http://contex:8001/mcp \
  -e CONTEX_PROJECT_ID=my-project \
  -e CONTEX_TOKEN=ck_... \
  -e KAFKA_CLUSTER=main \
  -e KAFKA_BOOTSTRAP_SERVERS=broker:9092 \
  -e KAFKA_TOPICS=orders,customers \
  -e KAFKA_GROUP_ID=contex-main \
  ghcr.io/cahoots-org/contex-connector-kafka:latest
```

| Env var | Maps to | Notes |
|---|---|---|
| `CONTEX_URL` | `contex.url` | MCP endpoint |
| `CONTEX_PROJECT_ID` | `contex.project_id` | Target project |
| `CONTEX_TOKEN` | `contex.api_key` | Optional; omit if auth is off |
| `KAFKA_CLUSTER` | `source.cluster` | Required |
| `KAFKA_BOOTSTRAP_SERVERS` | `source.bootstrap_servers` | Required |
| `KAFKA_TOPICS` | `source.topics` | Required; comma-separated |
| `KAFKA_GROUP_ID` | `source.group_id` | Optional; defaults to `contex-{cluster}` |
| `KAFKA_AUTO_OFFSET_RESET` | `source.auto_offset_reset` | Optional |
| `KAFKA_UNKEYED` | `source.unkeyed` | Optional |
| `KAFKA_SECURITY_PROTOCOL` | `source.security_protocol` | Optional |
| `KAFKA_SASL_MECHANISM` | `source.sasl_mechanism` | Optional |
| `KAFKA_SASL_USERNAME` / `KAFKA_SASL_PASSWORD` | `source.sasl_username` / `sasl_password` | Optional |
| `CONTEX_FLUSH_INTERVAL` | `flush_interval` | Optional; defaults to 1 |
| `CONTEX_BATCH_SIZE` | `batch_size` | Optional; defaults to 500 |

To change the secret guard, mount your own yaml over the baked default:

```bash
docker run --rm -v "$PWD/connector.yaml:/etc/contex/connector.yaml" \
  ghcr.io/cahoots-org/contex-connector-kafka:latest
```

## Delivery

Delivery is **at-least-once**. The connector commits a Kafka offset only after
Contex has accepted the batch that contains it. A crash between publish and
commit replays that batch on restart. Upserts and deletes are idempotent by
key, so a replay writes the same state.

- Items the server rejects, and values that aren't valid UTF-8, are logged,
  skipped and committed. A bad message never blocks the ones behind it.
- Messages dropped by the secret guard are committed too.
- Every publish attempt opens a fresh MCP session. If Contex is unreachable,
  the connector retries without limit, with backoff capped at 60 seconds, and
  resumes where it stopped. It does not exit.
- If Contex rejects the API key (HTTP 401 or 403), the connector exits with an
  error that says so, instead of retrying.

## Scaling

Run one process per `group_id`. Within that process, a key's upserts and its
tombstone are applied in the order Kafka holds them.

## Limitations

- **JSON and text only.** No Avro or Protobuf, and no schema registry.
- **Explicit topic list.** Topic patterns are not supported.
- **Retention doesn't delete.** A message aging out of Kafka does not remove
  its document. Only a tombstone does.
- **No prune.** Removing a topic from `source.topics` leaves its documents
  behind. List them with `contex_list_keys` (origin `kafka:{cluster}`) and
  remove them with `contex_delete`.
- **One process per consumer group.** With several processes in one group, a
  rebalance can move a partition while a publish to it is still retrying (a
  slow embedding, or Contex down for longer than Kafka's five-minute poll
  limit). The old owner may then write an older value after the new owner
  has written a newer one, and the document stays stale until the key
  changes again.
- **Not exactly-once.** A replay after a crash may re-embed items that were
  already published.
