# Changelog

## Unreleased

### Added

- **Kafka connector** (`connectors/kafka`, image `contex-connector-kafka`):
  streams topics into Contex. Keyed messages upsert, tombstones delete, and
  unkeyed messages append. Offsets are committed only after Contex accepts the
  batch, so delivery is at-least-once. See `connectors/kafka/README.md`.
- **Streaming connector runner** (`run_stream_connector`): flushes a partial
  batch after `flush_interval` seconds (default 1) and acknowledges positions
  after each publish.
- **Publisher reconnects.** A dropped MCP connection reopens the session and
  retries the call. Streaming connectors retry without limit, with backoff
  capped at 60 seconds. Snapshot connectors keep their retry cap.

### Fixed

- `contex_delete` reports a key containing control characters as missing.
  Before, a NUL in a key failed the whole call with a database error.

### Upgrading from v1.1.0

- **Back up the database first.** The server migrates on boot, and migrations
  020–028 drop the event log, audit, rate-limit, tenant-usage, service-account,
  webhook and snapshot tables and the `data_original` column. They can't be
  undone: rolling back means restoring that backup.
- **Redis is gone.** Notifications use Postgres. Drop the Redis service and the
  `REDIS_*` settings.
- **Tools and fields:**
  - `contex_delete_subscription` is removed. Identical requests share one
    subscription, which expires after `SUBSCRIPTION_TTL_SECONDS` (default 3600)
    without a read. Reading an expired one returns an error; create it again
    with the same arguments.
  - `contex_delete` and `contex_list_keys` are new.
  - `contex_publish` returns `{"published": key}`, without `sequence`. It also
    takes a string, whose format defaults to the key's extension; objects are
    still JSON.
  - `contex_publish_batch` lists bad items under `failed` and stores the rest,
    instead of failing the call. Check `failed`.
  - Matches, links and `contex_query` results no longer carry `description`;
    `data` holds the content.
  - A node's `data_key` joins the document key and node path with `\x1f`.
    `document` is still the plain key. Data keys can't contain control
    characters.
  - Subscription bundles update in the background after a publish returns.
  - Text, Markdown and PDF documents split into blocks of about 1,500
    characters at blank lines, not one node per paragraph or sentence. Node
    paths change (`sentence_N` is gone); republish to re-chunk.
  - Needs longer than 8,192 characters are rejected, and each API key may hold
    32 open MCP sessions.
- **Defaults and settings:**
  - `CORS_ORIGINS` defaults to none instead of `*`.
  - `CONTEX_HOST` defaults to `127.0.0.1`. The image and compose set
    `0.0.0.0`, and compose publishes the app on `127.0.0.1` only. Postgres is
    no longer published to the host.
  - `CONTEX_ALLOWED_HOSTS` now guards the whole app, not just `/mcp`.
  - With auth on, `/sandbox` and `/` return 404. `readonly` keys become
    `consumer`. Issue keys with `python -m src.cli keys create`;
    `SERVICE_ACCOUNT_JWT_SECRET` is gone.
  - `contex_query` and `contex_create_subscription` are limited to 120 calls a
    minute per key (`RATE_LIMIT_QUERY`).
  - Removed: `MAX_CONTEXT_SIZE`, `RATE_LIMIT_DEFAULT`,
    `AUDIT_RETENTION_DAYS`, `RETENTION_*`.
  - Startup stops if `EMBED_MODEL`'s dimension doesn't match the database.
    Stored documents re-embed once, the next time each is published.
- **Connectors:** keys and the credential setting changed
  (`service_account_token` is now `api_key`). See
  [Upgrading from v1.1.0](https://github.com/cahoots-org/contex/blob/main/connectors/README.md#upgrading-from-v110)
  to clean up old keys.
  - Connectors now delete what the source no longer has. Two configs reading
    one source into one project need distinct `origin:` names, or `prune: false`.
  - The v1.1.0 Postgres example's excludes (`*.password`, `*.*_token`) never
    matched, so those columns were ingested. Use `password` and `*_token`, and
    delete the stored rows.
