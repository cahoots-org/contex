# Streaming Foundation + Kafka Connector — Design

**Status:** proposed
**Author:** design brainstorm, 2026-10-13

## Goal

Keep a Contex project current with a source as it changes, instead of
re-running a snapshot on a cron. This spec adds a streaming mode to
`connectors/base/` and a first streaming connector, Kafka, that proves it.

It is the first of three streaming sub-projects:

1. Streaming foundation + Kafka (this spec).
2. Kinesis, Redis Streams, S3 via SQS: pull sources that reuse the foundation.
3. GitHub and Atlassian webhook receivers.

Postgres CDC is out of scope until we decide how to handle it.

## Why the snapshot runner can't stream

`run()` assumes a finite stream. Run against an endless one, it breaks four ways:

- **Batches only flush on size.** One event in a quiet minute sits in the
  buffer until 499 more arrive.
- **Nothing acknowledges progress.** A stream has to commit its position
  (a Kafka offset) only once Contex has accepted the batch, or a crash either
  loses events or replays from the start.
- **The MCP session can't survive a drop.** `ContexPublisher` opens one session
  and retries only on `rate_limit_exceeded`; a dropped connection kills the
  process.
- **`seen` grows without bound**, and prune has no meaning on a stream.

## Foundation (`connectors/base/`)

### `stream()`

A sibling of `run()` in `runner.py`:

```python
async def stream(events, publisher, ack, batch_size=500, flush_interval=1.0,
                 max_batch_bytes=MAX_BATCH_BYTES, secret_scanner=None,
                 progress=None) -> RunStats
```

- `events` is an async iterable of `(ChangeEvent, position)` pairs. `position`
  is opaque to the runner.
- Batching, the byte cap, the secret guard and flush-on-op-change behave as in
  `run()`. The shared item-building code moves into a helper both use.
- A batch also flushes `flush_interval` seconds after its first event arrived,
  so a lone event reaches subscribers in about a second.
- After a batch is accepted, the runner awaits `ack(positions)` with the
  positions of every event the batch consumed, in order, including events the
  secret guard dropped, so a run of secrets can't stall the stream. The
  connector turns them into a commit; the runner never interprets them.
- No `seen`, no prune. Deletes come from the source as `op="delete"` events.
- Returns when `events` ends (useful in tests); a live consumer never ends.

Delivery is **at-least-once**. A crash between publish and ack replays the
batch on restart; upserts and deletes are idempotent by key, so a replay
writes the same state.

Items the server rejects (logged by `publish_batch` today) are still acked: a
poison message must not block the stream behind it.

### Reconnecting publisher

`ContexPublisher._call` treats transport failures (connection reset, session
closed, HTTP 5xx from the MCP endpoint) as retryable: it closes the session,
reopens it, and retries the same call with the existing backoff. Tool errors
other than `rate_limit_exceeded` stay fatal. The snapshot connectors get this
for free.

A streaming connector must not die on a long outage, so streaming publishers
retry transport failures without a cap (backoff capped at 60s, as now). The
snapshot path keeps `_MAX_RETRIES`.

### `run_stream_connector`

The streaming counterpart of `run_connector`: opens a `ContexPublisher` with
the connector's origin and calls `stream()`. `flush_interval` is read from
`connector.yaml` (`flush_interval`, seconds, default 1).

## Kafka connector (`connectors/kafka/`)

Same shape as the others: `python -m connectors.kafka --config connector.yaml`,
its own `requirements.txt` and `tests/`, a README, a Dockerfile entry.

### Library

`aiokafka` (0.14): pure-Python, asyncio-native, which matches the runner.
`confluent-kafka` is faster but its asyncio support is a thin wrapper over a
thread; throughput here is bounded by embedding, not consumption.

### Consumption

- One `AIOKafkaConsumer` over `source.topics` in consumer group
  `source.group_id`, with `enable_auto_commit=False`.
- `auto_offset_reset` from config, default `earliest`: a new group reads the
  topic's full retention, which is the backfill. There is no separate
  snapshot step.
- A position is `(TopicPartition, offset)`. `ack(positions)` commits
  `offset + 1` for the highest offset per partition, since a batch can span
  partitions.
- On rebalance, aiokafka resumes from committed offsets; anything uncommitted
  is redelivered (at-least-once, as above).

### Mapping a message to a ChangeEvent

The mapping follows log-compaction semantics, where the latest value per key
is the record and a null value deletes it:

| Message | Event |
|---|---|
| key, value | `upsert` on `{cluster}:{topic}/{key}` |
| key, null value (tombstone) | `delete` on `{cluster}:{topic}/{key}` |
| no key | `upsert` on `{cluster}:{topic}/{partition}/{offset}` |

- `cluster` is a required config name for the Kafka cluster. Keys must name
  their source instance (#330); the bootstrap address isn't a stable identity.
- A value that parses as JSON publishes as `data_format: json`; anything else
  decodes as UTF-8 `text`. A value that isn't valid UTF-8 is skipped with a
  warning and still acked.
- `published_at` is the message timestamp.
- Message keys are decoded as UTF-8. A key that isn't valid UTF-8, or that contains
  control characters (integer-serialized keys do), is hex-encoded.
- `unkeyed: publish | skip`, default `publish`. Append-only topics (logs,
  clickstreams) become one document per message; set `skip` to ingest only
  keyed topics.
- Origin is `kafka:{cluster}`. It only tags documents; streams never prune.

### Config

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

The secret guard config (`allow_secrets`, `secrets:`) applies unchanged.

## Not in v1

- **Avro / Protobuf via a schema registry.** Values are JSON or text. Add it
  when someone has registry-encoded topics.
- **Topic patterns.** `topics` is an explicit list.
- **Exactly-once.** Idempotent writes make at-least-once enough.
- **Retention-driven deletes.** A message aging out of Kafka does not delete
  its document; only tombstones do.

## Testing

- **Runner (unit, `tests/connectors/`):** time flush fires for a lone event;
  ack is called once per accepted batch with every position it consumed; ack is not
  called when publish raises; secret-dropped events still advance the
  position; op changes flush in order.
- **Publisher (unit):** a transport error reopens the session and retries the
  same call; a non-rate-limit tool error is still fatal.
- **Kafka mapping (unit):** the table above, plus JSON vs text, non-UTF-8
  value skipped, `unkeyed: skip`.
- **Kafka integration** (`connectors/kafka/tests/`, env-gated on
  `KAFKA_TEST_BOOTSTRAP`, run against Redpanda in Docker): produce keyed,
  tombstone and unkeyed messages; assert publishes and deletes reach a fake
  publisher and offsets are committed; restart the consumer and assert nothing
  is lost and acked messages aren't redelivered.
