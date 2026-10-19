# Streaming Foundation + Kafka Connector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a streaming mode to `connectors/base/` (time flush, ack after publish, reconnecting publisher) and a Kafka connector that keeps a Contex project current with Kafka topics.

**Architecture:** A new `stream()` runner beside `run()` consumes `(ChangeEvent | None, position)` pairs through an internal queue so it can flush on a timer, and calls the connector's `ack(positions)` only after Contex accepts a batch. `ContexPublisher` learns to reopen its MCP session on transport failure (forever, for streams). `connectors/kafka/` maps Kafka messages to events with compacted-topic semantics and commits offsets in `ack`.

**Tech Stack:** Python 3.12, asyncio, `mcp` 2.x client, `aiokafka` 0.14, pytest + pytest-asyncio, Redpanda (CI integration).

**Spec:** `docs/superpowers/specs/2026-10-13-streaming-kafka-connector-design.md`

## Global Constraints

- Delivery is at-least-once: never ack a position before the batch holding its event is accepted.
- Kafka data keys: `{cluster}:{topic}/{key}` (keyed), `{cluster}:{topic}/{partition}/{offset}` (unkeyed). `source.cluster` is required.
- Origin: `kafka:{cluster}`. Streams never prune.
- `unkeyed: publish | skip`, default `publish`. `auto_offset_reset: earliest | latest`, default `earliest`.
- `flush_interval` default 1 second.
- Snapshot connectors keep `_MAX_RETRIES = 5`; streaming publishers retry transport failures forever, backoff capped at 60s.
- `aiokafka>=0.14,<0.15`.
- Follow the repo's style: no inline imports, no meta comments, concise docstrings. Commit messages are sentence-case imperative ("Add …"), no `feat:` prefixes, each ending with the session's Co-Authored-By / Claude-Session trailer lines.
- Every commit passes CI on its own.

## Review Focus

1. **A quiet topic after a burst of skipped messages** (e.g. all unkeyed with `unkeyed: skip`): positions must still be committed by the timer even when nothing is published — tested in Task 2 (`test_skipped_events_ack_on_timer`).
2. **The Contex server restarts mid-stream:** the publisher must reopen the session and resend the same batch, not crash or drop it — tested in Task 1 (`test_transport_error_reopens_and_retries`, `test_reopen_failure_counts_as_an_attempt`).
3. **A consumer-group rebalance between publish and commit:** the commit fails for a partition no longer owned; the stream must keep running (the new owner redelivers) — tested in Task 5 (`test_ack_survives_commit_failure`).
4. **A message whose value isn't UTF-8 or whose key isn't UTF-8:** value is skipped and acked, key is hex-encoded — tested in Task 4.
5. **A batch byte-cap flush mid-event:** the event that triggered the flush must not have its position acked with the previous batch — tested in Task 2 (`test_byte_cap_flush_does_not_ack_the_next_event`).

---

## File Structure

- Modify `connectors/base/publisher.py` — reconnect on transport failure; `retry_forever` flag; per-call read timeout.
- Modify `connectors/base/runner.py` — extract `_item` helper; add `stream()`.
- Modify `connectors/base/config.py` — `resolve_flush_interval`.
- Modify `connectors/base/__init__.py` — export `stream`, `resolve_flush_interval`, add `run_stream_connector`.
- Create `connectors/base/tests/test_stream.py`, extend `connectors/base/tests/test_publisher.py`, extend `tests/connectors/test_config.py`.
- Create `connectors/kafka/{__init__.py, mapping.py, source.py, __main__.py, requirements.txt, README.md, connector.template.yaml, connector.yaml.example}`.
- Create `connectors/kafka/tests/{__init__.py, test_mapping.py, test_source.py, test_integration.py}`.
- Modify `.github/workflows/ci.yml` (install + Redpanda + test path), `.github/workflows/release.yml` (image matrix), `connectors/README.md`, `CHANGELOG.md`.

---

### Task 1: Reconnecting publisher

**Files:**
- Modify: `connectors/base/publisher.py`
- Test: `connectors/base/tests/test_publisher.py`

**Interfaces:**
- Produces: `ContexPublisher(config, origin=None, retry_forever: bool = False)`. `_call` retries transport failures (any exception from `call_tool` except an `MCPError` with code `INVALID_PARAMS` or `METHOD_NOT_FOUND`) by awaiting `self._reopen()` before the next attempt. `_reopen()` closes the old session (ignoring errors) and opens a new one. Every `call_tool` passes `read_timeout_seconds=_CALL_TIMEOUT` (300.0).

- [ ] **Step 1: Update the one existing exact-args assertion and write the failing tests**

In `test_delete_batch_calls_contex_delete_and_counts_deleted`, change the assertion to:

```python
    pub._session.call_tool.assert_awaited_once_with(
        "contex_delete", {"project_id": "proj", "data_keys": ["a", "b", "c"]},
        read_timeout_seconds=300.0,
    )
```

Add imports at the top of the file:

```python
from mcp.shared.exceptions import MCPError
from mcp.types import CONNECTION_CLOSED, INVALID_PARAMS
```

Append:

```python
def _no_sleep(monkeypatch) -> list[float]:
    slept: list[float] = []

    async def fake_sleep(s):
        slept.append(s)

    monkeypatch.setattr("connectors.base.publisher.asyncio.sleep", fake_sleep)
    return slept


def test_transport_error_reopens_and_retries(monkeypatch):
    _no_sleep(monkeypatch)
    pub = ContexPublisher(SimpleNamespace(project_id="proj"))
    pub._session = SimpleNamespace(call_tool=AsyncMock(side_effect=[
        MCPError(CONNECTION_CLOSED, "Connection closed"),
        _ok(2),
    ]))
    pub._reopen = AsyncMock()
    assert asyncio.run(pub.publish_batch([{}, {}])) == 2
    pub._reopen.assert_awaited_once()
    first, second = pub._session.call_tool.call_args_list
    assert first == second


def test_non_mcp_exception_is_treated_as_transport(monkeypatch):
    _no_sleep(monkeypatch)
    pub = ContexPublisher(SimpleNamespace(project_id="proj"))
    pub._session = SimpleNamespace(call_tool=AsyncMock(side_effect=[OSError("reset"), _ok(1)]))
    pub._reopen = AsyncMock()
    assert asyncio.run(pub.publish_batch([{}])) == 1
    pub._reopen.assert_awaited_once()


def test_reopen_failure_counts_as_an_attempt(monkeypatch):
    _no_sleep(monkeypatch)
    pub = ContexPublisher(SimpleNamespace(project_id="proj"))
    pub._session = SimpleNamespace(call_tool=AsyncMock(side_effect=[OSError("down"), _ok(1)]))
    pub._reopen = AsyncMock(side_effect=[OSError("still down"), None])
    assert asyncio.run(pub.publish_batch([{}])) == 1
    assert pub._reopen.await_count == 2


def test_protocol_errors_are_fatal_without_reopen():
    pub = ContexPublisher(SimpleNamespace(project_id="proj"))
    pub._session = SimpleNamespace(call_tool=AsyncMock(side_effect=MCPError(INVALID_PARAMS, "bad items")))
    pub._reopen = AsyncMock()
    with pytest.raises(RuntimeError, match="contex_publish_batch failed: bad items"):
        asyncio.run(pub.publish_batch([{}]))
    pub._reopen.assert_not_awaited()


def test_snapshot_publisher_gives_up_after_max_retries(monkeypatch):
    _no_sleep(monkeypatch)
    pub = ContexPublisher(SimpleNamespace(project_id="proj"))
    pub._session = SimpleNamespace(call_tool=AsyncMock(side_effect=OSError("down")))
    pub._reopen = AsyncMock()
    with pytest.raises(RuntimeError, match="contex_publish_batch failed"):
        asyncio.run(pub.publish_batch([{}]))
    assert pub._session.call_tool.await_count == 5


def test_streaming_publisher_retries_past_max_retries(monkeypatch):
    slept = _no_sleep(monkeypatch)
    pub = ContexPublisher(SimpleNamespace(project_id="proj"), retry_forever=True)
    pub._session = SimpleNamespace(call_tool=AsyncMock(side_effect=[OSError("down")] * 8 + [_ok(1)]))
    pub._reopen = AsyncMock()
    assert asyncio.run(pub.publish_batch([{}])) == 1
    assert max(slept) == 60.0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest connectors/base/tests/test_publisher.py -v`
Expected: the new tests FAIL (`retry_forever` unknown, `_reopen` never awaited, exceptions propagate); the updated delete test FAILS on the missing kwarg.

- [ ] **Step 3: Implement**

In `connectors/base/publisher.py`, add imports:

```python
from mcp.shared.exceptions import MCPError
from mcp.types import INVALID_PARAMS, METHOD_NOT_FOUND
```

Add constants beside `_MAX_RETRIES`:

```python
# A publish that hasn't answered in this long is treated as a dropped connection.
_CALL_TIMEOUT = 300.0
# Protocol errors a retry can't fix; every other raised error is a transport failure.
_FATAL_CODES = (INVALID_PARAMS, METHOD_NOT_FOUND)
```

Replace `__init__`, `__aenter__`, `__aexit__` and `_call` with:

```python
    def __init__(self, config: ContexConfig, origin: str | None = None, retry_forever: bool = False):
        self._config = config
        self._origin = origin
        self._retry_forever = retry_forever
        self._stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None

    async def __aenter__(self) -> "ContexPublisher":
        await self._open()
        return self

    async def __aexit__(self, *exc_info) -> None:
        await self._close()

    async def _open(self) -> None:
        self._stack = AsyncExitStack()
        http_client = None
        if self._config.api_key:
            http_client = create_mcp_http_client(
                headers={"Authorization": f"Bearer {self._config.api_key}"}
            )
        streams = await self._stack.enter_async_context(
            streamable_http_client(self._config.url, http_client=http_client)
        )
        self._session = await self._stack.enter_async_context(
            ClientSession(streams[0], streams[1])
        )
        await self._session.initialize()

    async def _close(self) -> None:
        if self._stack is not None:
            await self._stack.aclose()
        self._stack = None
        self._session = None

    async def _reopen(self) -> None:
        """Replace a broken session with a fresh one."""
        try:
            await self._close()
        except Exception:
            log.debug("closing a broken MCP session failed", exc_info=True)
            self._stack = None
            self._session = None
        await self._open()
```

```python
    async def _call(self, tool: str, arguments: dict) -> dict:
        """Call a project tool and return its JSON result.

        Retries with exponential backoff when the server's limiter rejects the
        call (``rate_limit_exceeded``), honoring its ``retry_after`` hint, and
        when the connection fails, reopening the session first. A streaming
        publisher (``retry_forever``) never gives up on those; any other tool
        error is fatal and raised immediately.
        """
        if self._session is None:
            raise RuntimeError("ContexPublisher must be used as an async context manager")
        attempt = 0
        broken = False
        while True:
            try:
                if broken:
                    await self._reopen()
                    broken = False
                result = await self._session.call_tool(
                    tool,
                    {"project_id": self._config.project_id, **arguments},
                    read_timeout_seconds=_CALL_TIMEOUT,
                )
            except MCPError as exc:
                if exc.code in _FATAL_CODES:
                    raise RuntimeError(f"{tool} failed: {exc.message}") from exc
                error, broken = str(exc), True
            except Exception as exc:
                error, broken = repr(exc), True
            else:
                text = result.content[0].text if result.content else ""
                if not result.is_error:  # mcp>=2 attribute; "isError" is only the JSON alias
                    return json.loads(text)
                if "rate_limit_exceeded" not in text:
                    raise RuntimeError(f"{tool} failed: {text or 'unknown error'}")
                error = text
            if not self._retry_forever and attempt == _MAX_RETRIES - 1:
                raise RuntimeError(f"{tool} failed: {error}")
            log.warning("%s failed (attempt %d), retrying: %s", tool, attempt + 1, error)
            await asyncio.sleep(_backoff(error, attempt))
            attempt += 1
```

Note `_backoff` already caps at `_BACKOFF_CAP` (60s); with `attempt` growing unbounded `2.0**attempt` stays finite for any realistic count but clamp the exponent so it can't overflow: change `_backoff`'s fallback to `2.0 ** min(attempt, 10)`.

Update the module docstring's first paragraph to say the session is reopened when the connection drops.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest connectors/base/tests/test_publisher.py connectors/base/tests/test_prune.py tests/connectors -v`
Expected: all PASS.

- [ ] **Step 5: Verify a real drop surfaces as a retried failure**

Start a local Contex (see the local-test-DB setup: ParadeDB on 55432, server on 8021 with `CONTEX_HOST=127.0.0.1`). In a scratch script, open a `ContexPublisher(ContexConfig(url="http://127.0.0.1:8021/mcp", project_id="scratch"), retry_forever=True)`, publish one item, stop the server, call `publish_batch` again in a task, restart the server, and confirm the call returns `1` after reconnecting. Record the exception type logged in the warning line. If it is a `BaseException` that `except Exception` misses (it should not be), stop and report it.

- [ ] **Step 6: Commit**

```bash
git add connectors/base/publisher.py connectors/base/tests/test_publisher.py
git commit -m "Reopen the MCP session when a connector's connection drops"
```

---

### Task 2: `stream()` runner

**Files:**
- Modify: `connectors/base/runner.py`
- Create: `connectors/base/tests/test_stream.py`

**Interfaces:**
- Consumes: nothing new.
- Produces:
  ```python
  async def stream(events, publisher, ack, batch_size: int = 500, flush_interval: float = 1.0,
                   max_batch_bytes: int = MAX_BATCH_BYTES, secret_scanner=None,
                   progress: Callable[[int], None] | None = None) -> RunStats
  ```
  `events`: sync or async iterable of `(ChangeEvent | None, position)`. `None` means "consumed, nothing to publish". `ack`: `async (positions: list) -> None`, called after each accepted batch (and on timer when only skipped positions are pending) with positions in stream order. `op="retain"` raises `ValueError`.

- [ ] **Step 1: Write the failing tests**

`connectors/base/tests/test_stream.py`:

```python
"""Unit tests for the streaming runner."""
from __future__ import annotations

import asyncio

import pytest

from connectors.base import ChangeEvent
from connectors.base.runner import stream


class FakePublisher:
    def __init__(self):
        self.batches = []

    async def publish_batch(self, items):
        self.batches.append(items)
        return len(items)

    async def delete_batch(self, data_keys):
        self.batches.append(("delete", data_keys))
        return len(data_keys)


class Acks:
    def __init__(self):
        self.calls = []

    async def __call__(self, positions):
        self.calls.append(positions)


def _up(key):
    return ChangeEvent(op="upsert", key=key, payload={"k": key})


async def _then_idle(pairs):
    for pair in pairs:
        yield pair
    await asyncio.Event().wait()


async def _run_briefly(events, publisher, ack, seconds=0.2, **kwargs):
    task = asyncio.create_task(stream(events, publisher, ack, **kwargs))
    await asyncio.sleep(seconds)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_lone_event_flushes_on_timer():
    publisher, ack = FakePublisher(), Acks()
    await _run_briefly(_then_idle([(_up("a"), 0)]), publisher, ack, flush_interval=0.05)
    assert publisher.batches == [[{"data_key": "a", "data": {"k": "a"}, "data_format": "json"}]]
    assert ack.calls == [[0]]


@pytest.mark.asyncio
async def test_size_flush_acks_each_batch_with_its_positions():
    publisher, ack = FakePublisher(), Acks()
    pairs = [(_up(f"k{i}"), i) for i in range(5)]
    stats = await stream(pairs, publisher, ack, batch_size=2, flush_interval=60)
    assert [len(b) for b in publisher.batches] == [2, 2, 1]
    assert ack.calls == [[0, 1], [2, 3], [4]]
    assert stats.published == 5


@pytest.mark.asyncio
async def test_failed_publish_is_not_acked():
    class Failing(FakePublisher):
        async def publish_batch(self, items):
            raise RuntimeError("contex down")

    ack = Acks()
    with pytest.raises(RuntimeError, match="contex down"):
        await stream([(_up("a"), 0)], Failing(), ack, flush_interval=60)
    assert ack.calls == []


@pytest.mark.asyncio
async def test_secret_dropped_events_still_advance():
    class Scanner:
        def reason(self, key, payload, data_format):
            return "looks like a key" if key == "s" else None

    publisher, ack = FakePublisher(), Acks()
    stats = await stream([(_up("s"), 0), (_up("a"), 1)], publisher, ack, secret_scanner=Scanner())
    assert publisher.batches == [[{"data_key": "a", "data": {"k": "a"}, "data_format": "json"}]]
    assert ack.calls == [[0, 1]]
    assert stats.skipped_secrets == 1


@pytest.mark.asyncio
async def test_skipped_events_ack_on_timer():
    publisher, ack = FakePublisher(), Acks()
    await _run_briefly(_then_idle([(None, 0), (None, 1)]), publisher, ack, flush_interval=0.05)
    assert publisher.batches == []
    assert ack.calls == [[0, 1]]


@pytest.mark.asyncio
async def test_op_changes_flush_in_order():
    publisher, ack = FakePublisher(), Acks()
    pairs = [(_up("a"), 0), (ChangeEvent(op="delete", key="b", payload=None), 1), (_up("c"), 2)]
    stats = await stream(pairs, publisher, ack, flush_interval=60)
    assert publisher.batches[0][0]["data_key"] == "a"
    assert publisher.batches[1] == ("delete", ["b"])
    assert publisher.batches[2][0]["data_key"] == "c"
    assert ack.calls == [[0], [1], [2]]
    assert stats.deleted == 1


@pytest.mark.asyncio
async def test_byte_cap_flush_does_not_ack_the_next_event():
    publisher, ack = FakePublisher(), Acks()
    pairs = [(_up("a"), 0), (_up("b"), 1)]
    await stream(pairs, publisher, ack, max_batch_bytes=80, flush_interval=60)
    assert ack.calls == [[0], [1]]


@pytest.mark.asyncio
async def test_retain_is_rejected():
    with pytest.raises(ValueError, match="retain"):
        await stream([(ChangeEvent(op="retain", key="a", payload=None), 0)], FakePublisher(), Acks())


@pytest.mark.asyncio
async def test_source_errors_propagate():
    async def broken():
        yield (_up("a"), 0)
        raise RuntimeError("broker gone")

    with pytest.raises(RuntimeError, match="broker gone"):
        await stream(broken(), FakePublisher(), Acks(), flush_interval=60)
```

(The single serialized item `{"data_key": "a", "data": {"k": "a"}, "data_format": "json"}` is 60 bytes, so a cap of 80 fits one and not two.)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest connectors/base/tests/test_stream.py -v`
Expected: FAIL with `ImportError: cannot import name 'stream'`.

- [ ] **Step 3: Implement**

In `connectors/base/runner.py`, add `import asyncio` and `import contextlib`. Update the module docstring's first line to "Batch a stream of ChangeEvents and publish it, once (`run`) or continuously (`stream`)."

Extract the per-event item logic from `run()` into a helper and use it there:

```python
def _item(event: ChangeEvent, secret_scanner, stats: RunStats):
    """The batch item for an upsert or delete, or None when the secret guard drops it."""
    if event.op == "delete":
        return event.key
    if secret_scanner is not None:
        why = secret_scanner.reason(event.key, event.payload, event.data_format)
        if why is not None:
            stats.skipped_secrets += 1
            log.warning("skip secret %s (%s)", event.key, why)  # nosemgrep: python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure
            return None
    return event.to_item()
```

In `run()`, replace the `if event.op == "delete": ... else: ...` block with:

```python
        item = _item(event, secret_scanner, stats)
        if item is None:
            continue
        if event.op == "upsert":
            seen.add(event.key)
```

Run `pytest connectors/base tests/connectors -v` now: the existing `run()` tests must still pass before you add `stream()`.

Then add:

```python
_DONE = object()


async def stream(
    events,
    publisher,
    ack,
    batch_size: int = 500,
    flush_interval: float = 1.0,
    max_batch_bytes: int = MAX_BATCH_BYTES,
    secret_scanner=None,
    progress: Callable[[int], None] | None = None,
) -> RunStats:
    """Publish an endless stream of ``(ChangeEvent | None, position)`` pairs.

    Batches as :func:`run` does, and also flushes ``flush_interval`` seconds
    after a batch's first event. Once a batch is accepted, ``await
    ack(positions)`` receives every position it consumed, in order, including
    events the secret guard dropped and ``None`` events (consumed, nothing to
    publish). A position is never acked before its event is published, so a
    crash replays at most the unacked tail. Returns when ``events`` ends.
    """
    stats = RunStats()
    buffer: list = []
    positions: list = []
    buffer_bytes = 0
    buffer_op = "upsert"
    deadline: float | None = None
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue(maxsize=batch_size)

    # Reading through a queue lets the timer fire without cancelling the
    # source's iterator, which would close an async generator for good.
    async def pump() -> None:
        try:
            async for pair in _aiter(events):
                await queue.put(pair)
        except Exception as exc:
            await queue.put(exc)
        else:
            await queue.put(_DONE)

    async def flush() -> None:
        nonlocal buffer_bytes, deadline
        if buffer:
            if buffer_op == "delete":
                stats.deleted += int(await publisher.delete_batch(list(buffer)))
            else:
                stats.published += int(await publisher.publish_batch(list(buffer)))
            stats.batches += 1
            buffer.clear()
            buffer_bytes = 0
            if progress is not None:
                progress(stats.published)
        if positions:
            await ack(list(positions))
            positions.clear()
        deadline = None

    reader = asyncio.create_task(pump())
    try:
        while True:
            timeout = None if deadline is None else max(0.0, deadline - loop.time())
            try:
                pair = await asyncio.wait_for(queue.get(), timeout)
            except TimeoutError:
                await flush()
                continue
            if pair is _DONE:
                break
            if isinstance(pair, Exception):
                raise pair
            event, position = pair
            item = None
            if event is not None:
                if not isinstance(event, ChangeEvent):
                    raise TypeError(f"expected ChangeEvent, got {type(event).__name__}")
                if event.op not in ("upsert", "delete"):
                    raise ValueError(f"a stream can't {event.op!r} {event.key}")
                if event.op != buffer_op:
                    await flush()
                    buffer_op = event.op
                item = _item(event, secret_scanner, stats)
            if item is not None:
                item_bytes = len(json.dumps(item, default=str))
                if buffer and buffer_bytes + item_bytes > max_batch_bytes:
                    await flush()
                buffer.append(item)
                buffer_bytes += item_bytes
            positions.append(position)
            if deadline is None:
                deadline = loop.time() + flush_interval
            if len(buffer) >= batch_size:
                await flush()
        await flush()
    finally:
        reader.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await reader
    return stats
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest connectors/base tests/connectors -v`
Expected: all PASS.

- [ ] **Step 5: Commit** (two commits: the refactor is its own vertical slice)

```bash
git add connectors/base/runner.py
git commit -m "Share the batch-item builder between run and a future stream"
```

Do this commit right after the `_item` refactor passes the existing tests (before adding `stream()`), then:

```bash
git add connectors/base/runner.py connectors/base/tests/test_stream.py
git commit -m "Add a streaming runner that flushes on a timer and acks after publish"
```

---

### Task 3: `run_stream_connector` and `flush_interval` config

**Files:**
- Modify: `connectors/base/config.py`, `connectors/base/__init__.py`
- Test: `tests/connectors/test_config.py`, `connectors/base/tests/test_stream.py`

**Interfaces:**
- Consumes: `stream` (Task 2), `ContexPublisher(..., retry_forever=True)` (Task 1).
- Produces:
  ```python
  def resolve_flush_interval(config: dict) -> float  # default 1.0; ValueError if <= 0
  async def run_stream_connector(config: ContexConfig, events, ack, origin: str,
                                 batch_size: int = 500, flush_interval: float = 1.0,
                                 secret_scanner=None, progress=None) -> RunStats
  ```
  Both exported from `connectors.base` along with `stream`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/connectors/test_config.py` (add `resolve_flush_interval` to its `from connectors.base import ...` line):

```python
def test_flush_interval_defaults_when_unset():
    assert resolve_flush_interval({}) == 1.0
    assert resolve_flush_interval({"flush_interval": ""}) == 1.0


def test_flush_interval_reads_seconds():
    assert resolve_flush_interval({"flush_interval": "0.25"}) == 0.25


def test_flush_interval_must_be_positive():
    with pytest.raises(ValueError, match="flush_interval"):
        resolve_flush_interval({"flush_interval": 0})
```

Append to `connectors/base/tests/test_stream.py` (add `import connectors.base` and `from connectors.base import ContexConfig, run_stream_connector` to the imports):

```python
@pytest.mark.asyncio
async def test_run_stream_connector_opens_a_retrying_publisher(monkeypatch):
    opened = []

    class Recording(FakePublisher):
        def __init__(self, config, origin, retry_forever=False):
            super().__init__()
            opened.append((origin, retry_forever))

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return None

    monkeypatch.setattr(connectors.base, "ContexPublisher", Recording)
    config = ContexConfig.from_dict({"contex": {"url": "u", "project_id": "p"}, "origin": "team-a"})
    ack = Acks()
    stats = await run_stream_connector(config, [(_up("a"), 0)], ack, origin="kafka:main")
    assert opened == [("kafka:main#team-a", True)]
    assert stats.published == 1
    assert ack.calls == [[0]]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/connectors/test_config.py connectors/base/tests/test_stream.py -v`
Expected: FAIL with `ImportError` for `resolve_flush_interval` / `run_stream_connector`.

- [ ] **Step 3: Implement**

`connectors/base/config.py`, beside `DEFAULT_BATCH_SIZE`:

```python
DEFAULT_FLUSH_INTERVAL = 1.0  # seconds a streamed event may wait for batch-mates
```

and:

```python
def resolve_flush_interval(config: dict) -> float:
    """Seconds a streaming batch waits before flushing, defaulting when unset."""
    raw = config.get("flush_interval")
    value = DEFAULT_FLUSH_INTERVAL if raw is None or raw == "" else float(raw)
    if value <= 0:
        raise ValueError(f"flush_interval must be positive, got {value}")
    return value
```

`connectors/base/__init__.py`: import `resolve_flush_interval` from `.config` and `stream` from `.runner`; add `"resolve_flush_interval"`, `"run_stream_connector"`, `"stream"` to `__all__` (keep it sorted); update the module docstring to mention `run_stream_connector`/`stream` for continuous sources. Add:

```python
async def run_stream_connector(
    config: ContexConfig,
    events,
    ack,
    origin: str,
    batch_size: int = 500,
    flush_interval: float = 1.0,
    secret_scanner=None,
    progress: Callable[[int], None] | None = None,
) -> RunStats:
    """Open a publisher that rides out outages and stream ``events`` through it.

    ``ack`` receives the positions of each accepted batch (see :func:`stream`).
    ``origin`` is suffixed with the config's ``origin`` name when set.
    """
    if config.origin:
        origin = f"{origin}#{config.origin}"
    async with ContexPublisher(config, origin, retry_forever=True) as publisher:
        return await stream(
            events,
            publisher,
            ack,
            batch_size=batch_size,
            flush_interval=flush_interval,
            secret_scanner=secret_scanner,
            progress=progress,
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest connectors/base tests/connectors -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add connectors/base/config.py connectors/base/__init__.py tests/connectors/test_config.py connectors/base/tests/test_stream.py
git commit -m "Add run_stream_connector and a flush_interval setting"
```

---

### Task 4: Kafka message mapping

**Files:**
- Create: `connectors/kafka/__init__.py`, `connectors/kafka/mapping.py`, `connectors/kafka/requirements.txt`, `connectors/kafka/tests/__init__.py`, `connectors/kafka/tests/test_mapping.py`
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: `ChangeEvent`.
- Produces: `message_to_event(cluster: str, msg, unkeyed: str) -> ChangeEvent | None`, where `msg` has `topic, partition, offset, timestamp (ms), key (bytes | None), value (bytes | None)` (an aiokafka `ConsumerRecord`). Returns `None` for anything not to publish.

- [ ] **Step 1: Scaffold the package and wire it into CI**

`connectors/kafka/__init__.py`:

```python
"""Kafka connector: stream topics into Contex, keyed messages as upserts and tombstones as deletes."""
```

`connectors/kafka/tests/__init__.py`: empty.

`connectors/kafka/requirements.txt`:

```
-r ../base/requirements.txt
aiokafka>=0.14,<0.15
```

In `.github/workflows/ci.yml`'s `connectors` job: add `pip install -r connectors/kafka/requirements.txt` after the atlassian install line, and append `connectors/kafka` to the `pytest connectors/base ...` command.

- [ ] **Step 2: Write the failing tests**

`connectors/kafka/tests/test_mapping.py`:

```python
"""Unit tests for mapping Kafka messages to ChangeEvents."""
from __future__ import annotations

from types import SimpleNamespace

from connectors.kafka.mapping import message_to_event


def _msg(key=b"order-1", value=b'{"total": 3}', topic="orders", partition=0, offset=7,
         timestamp=1760000000000):
    return SimpleNamespace(topic=topic, partition=partition, offset=offset,
                           timestamp=timestamp, key=key, value=value)


def test_keyed_json_message_is_an_upsert():
    event = message_to_event("main", _msg(), "publish")
    assert event.op == "upsert"
    assert event.key == "main:orders/order-1"
    assert event.payload == {"total": 3}
    assert event.data_format == "json"
    assert event.published_at == "2025-10-09T08:53:20+00:00"


def test_tombstone_is_a_delete():
    event = message_to_event("main", _msg(value=None), "publish")
    assert event.op == "delete"
    assert event.key == "main:orders/order-1"


def test_non_json_value_is_text():
    event = message_to_event("main", _msg(value=b"plain words"), "publish")
    assert event.payload == "plain words"
    assert event.data_format == "text"


def test_json_scalar_is_text():
    event = message_to_event("main", _msg(value=b"42"), "publish")
    assert event.payload == "42"
    assert event.data_format == "text"


def test_unkeyed_message_is_keyed_by_position():
    event = message_to_event("main", _msg(key=None, partition=2, offset=99), "publish")
    assert event.op == "upsert"
    assert event.key == "main:orders/2/99"


def test_unkeyed_skip_drops_unkeyed_messages():
    assert message_to_event("main", _msg(key=None), "skip") is None


def test_unkeyed_tombstone_is_dropped():
    assert message_to_event("main", _msg(key=None, value=None), "publish") is None


def test_non_utf8_value_is_dropped():
    assert message_to_event("main", _msg(value=b"\xff\xfe"), "publish") is None


def test_non_utf8_key_is_hex_encoded():
    event = message_to_event("main", _msg(key=b"\xff\x01"), "publish")
    assert event.key == "main:orders/ff01"


def test_missing_timestamp_leaves_published_at_unset():
    assert message_to_event("main", _msg(timestamp=-1), "publish").published_at is None
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `pip install -r connectors/kafka/requirements.txt && pytest connectors/kafka -v`
Expected: FAIL with `ModuleNotFoundError: connectors.kafka.mapping`.

- [ ] **Step 4: Implement**

`connectors/kafka/mapping.py`:

```python
"""Map a Kafka message to a ChangeEvent, following log-compaction semantics.

The latest value per key is the record and a null value (tombstone) deletes
it. Unkeyed messages are an append-only log, keyed by their position.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from connectors.base import ChangeEvent

log = logging.getLogger(__name__)


def _key_text(raw: bytes) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.hex()


def _published_at(timestamp_ms) -> str | None:
    if timestamp_ms is None or timestamp_ms < 0:
        return None
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc).isoformat()


def message_to_event(cluster: str, msg, unkeyed: str) -> ChangeEvent | None:
    """The event for one message, or None when there is nothing to publish."""
    if msg.key is None:
        if unkeyed == "skip" or msg.value is None:
            return None
        key = f"{cluster}:{msg.topic}/{msg.partition}/{msg.offset}"
    else:
        key = f"{cluster}:{msg.topic}/{_key_text(msg.key)}"
    if msg.value is None:
        return ChangeEvent(op="delete", key=key, payload=None)
    try:
        text = msg.value.decode("utf-8")
    except UnicodeDecodeError:
        log.warning("skip %s: value is not UTF-8", key)
        return None
    try:
        payload = json.loads(text)
    except ValueError:
        payload = None
    published_at = _published_at(msg.timestamp)
    if isinstance(payload, dict):
        return ChangeEvent(op="upsert", key=key, payload=payload, published_at=published_at)
    return ChangeEvent(op="upsert", key=key, payload=text, data_format="text",
                       published_at=published_at)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest connectors/kafka -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add connectors/kafka .github/workflows/ci.yml
git commit -m "Map Kafka messages to upserts and tombstones to deletes"
```

---

### Task 5: Kafka source (consume + commit) with integration tests

**Files:**
- Create: `connectors/kafka/source.py`, `connectors/kafka/tests/test_source.py`, `connectors/kafka/tests/test_integration.py`
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: `message_to_event` (Task 4), `stream` (Task 2).
- Produces:
  ```python
  class KafkaSource:
      def __init__(self, config: dict)           # validates source block; raises ValueError
      cluster: str
      async def __aenter__(self) -> "KafkaSource"  # builds and starts the consumer
      async def __aexit__(self, *exc_info) -> None # stops it
      async def events(self)                      # async iterator of (ChangeEvent | None, (TopicPartition, offset))
      async def ack(self, positions: list) -> None  # commits offset+1 per partition; logs and survives KafkaError
  ```

- [ ] **Step 1: Write the failing unit tests**

`connectors/kafka/tests/test_source.py`:

```python
"""Unit tests for KafkaSource config and offset commits."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from aiokafka import TopicPartition
from aiokafka.errors import CommitFailedError

from connectors.kafka.source import KafkaSource


def _config(**source):
    base = {"cluster": "main", "bootstrap_servers": "localhost:9092", "topics": ["orders"]}
    return {"source": {**base, **source}}


def test_cluster_is_required():
    with pytest.raises(ValueError, match="source.cluster"):
        KafkaSource(_config(cluster=""))


def test_topics_are_required():
    with pytest.raises(ValueError, match="source.topics"):
        KafkaSource(_config(topics=[]))


def test_topics_accept_a_comma_separated_string():
    assert KafkaSource(_config(topics="orders, customers"))._topics == ["orders", "customers"]


def test_group_id_defaults_from_cluster():
    assert KafkaSource(_config())._group_id == "contex-main"


def test_unkeyed_must_be_publish_or_skip():
    with pytest.raises(ValueError, match="unkeyed"):
        KafkaSource(_config(unkeyed="maybe"))


def test_auto_offset_reset_must_be_earliest_or_latest():
    with pytest.raises(ValueError, match="auto_offset_reset"):
        KafkaSource(_config(auto_offset_reset="middle"))


def test_ack_commits_next_offset_per_partition():
    source = KafkaSource(_config())
    source._consumer = AsyncMock()
    p0, p1 = TopicPartition("orders", 0), TopicPartition("orders", 1)
    asyncio.run(source.ack([(p0, 4), (p1, 9), (p0, 5)]))
    source._consumer.commit.assert_awaited_once_with({p0: 6, p1: 10})


def test_ack_survives_commit_failure():
    source = KafkaSource(_config())
    source._consumer = AsyncMock()
    source._consumer.commit.side_effect = CommitFailedError("rebalanced")
    asyncio.run(source.ack([(TopicPartition("orders", 0), 1)]))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest connectors/kafka/tests/test_source.py -v`
Expected: FAIL with `ModuleNotFoundError: connectors.kafka.source`.

- [ ] **Step 3: Implement**

`connectors/kafka/source.py`:

```python
"""Consume Kafka topics as a stream of ChangeEvents and commit what Contex accepted."""
from __future__ import annotations

import logging

from aiokafka import AIOKafkaConsumer, TopicPartition
from aiokafka.errors import KafkaError
from aiokafka.helpers import create_ssl_context

from .mapping import message_to_event

log = logging.getLogger(__name__)


class KafkaSource:
    """A consumer-group member whose offsets advance only through :meth:`ack`."""

    def __init__(self, config: dict):
        source = config.get("source") or {}
        self.cluster = source.get("cluster") or ""
        if not self.cluster:
            raise ValueError("source.cluster is required: it names this cluster in data keys")
        self._bootstrap = source.get("bootstrap_servers") or ""
        if not self._bootstrap:
            raise ValueError("source.bootstrap_servers is required")
        topics = source.get("topics") or []
        if isinstance(topics, str):
            topics = topics.split(",")
        self._topics = [t.strip() for t in topics if t.strip()]
        if not self._topics:
            raise ValueError("source.topics is required")
        self._group_id = source.get("group_id") or f"contex-{self.cluster}"
        self._unkeyed = source.get("unkeyed") or "publish"
        if self._unkeyed not in ("publish", "skip"):
            raise ValueError(f"source.unkeyed must be publish or skip, got {self._unkeyed!r}")
        self._offset_reset = source.get("auto_offset_reset") or "earliest"
        if self._offset_reset not in ("earliest", "latest"):
            raise ValueError(
                f"source.auto_offset_reset must be earliest or latest, got {self._offset_reset!r}"
            )
        self._security_protocol = source.get("security_protocol") or "PLAINTEXT"
        self._sasl_mechanism = source.get("sasl_mechanism") or "PLAIN"
        self._sasl_username = source.get("sasl_username") or None
        self._sasl_password = source.get("sasl_password") or None
        self._consumer: AIOKafkaConsumer | None = None

    async def __aenter__(self) -> "KafkaSource":
        ssl_context = create_ssl_context() if self._security_protocol.endswith("SSL") else None
        self._consumer = AIOKafkaConsumer(
            *self._topics,
            bootstrap_servers=self._bootstrap,
            group_id=self._group_id,
            enable_auto_commit=False,
            auto_offset_reset=self._offset_reset,
            security_protocol=self._security_protocol,
            sasl_mechanism=self._sasl_mechanism,
            sasl_plain_username=self._sasl_username,
            sasl_plain_password=self._sasl_password,
            ssl_context=ssl_context,
        )
        await self._consumer.start()
        return self

    async def __aexit__(self, *exc_info) -> None:
        if self._consumer is not None:
            await self._consumer.stop()
        self._consumer = None

    async def events(self):
        """Yield ``(event or None, (TopicPartition, offset))`` for every message."""
        async for msg in self._consumer:
            position = (TopicPartition(msg.topic, msg.partition), msg.offset)
            yield message_to_event(self.cluster, msg, self._unkeyed), position

    async def ack(self, positions: list) -> None:
        """Commit past the highest acked offset on each partition.

        A failed commit (usually a rebalance) is logged, not raised: the
        partition's next owner redelivers from the last commit, and
        redelivered upserts and deletes are harmless.
        """
        offsets: dict[TopicPartition, int] = {}
        for partition, offset in positions:
            offsets[partition] = max(offsets.get(partition, 0), offset + 1)
        try:
            await self._consumer.commit(offsets)
        except KafkaError as exc:
            log.warning("offset commit failed, these messages may be redelivered: %s", exc)
```

- [ ] **Step 4: Run the unit tests to verify they pass**

Run: `pytest connectors/kafka -v`
Expected: all PASS.

- [ ] **Step 5: Write the integration test**

`connectors/kafka/tests/test_integration.py`:

```python
"""Integration tests against a live Kafka-compatible broker (Redpanda in CI).

Skipped unless ``KAFKA_TEST_BOOTSTRAP`` names a reachable broker. Locally:

    docker run -d --name redpanda -p 9092:9092 redpandadata/redpanda:v24.3.6 \
      redpanda start --mode dev-container --kafka-addr 0.0.0.0:9092 \
      --advertise-kafka-addr localhost:9092
    KAFKA_TEST_BOOTSTRAP=localhost:9092 pytest connectors/kafka -v
"""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from aiokafka import AIOKafkaProducer, TopicPartition
from aiokafka.admin import AIOKafkaAdminClient, NewTopic

from connectors.base import stream
from connectors.kafka.source import KafkaSource

BOOTSTRAP = os.environ.get("KAFKA_TEST_BOOTSTRAP")
pytestmark = pytest.mark.skipif(not BOOTSTRAP, reason="KAFKA_TEST_BOOTSTRAP not set")


class Recorder:
    def __init__(self):
        self.published: list[str] = []
        self.deleted: list[str] = []

    async def publish_batch(self, items):
        self.published += [item["data_key"] for item in items]
        return len(items)

    async def delete_batch(self, keys):
        self.deleted += keys
        return len(keys)


async def _topic() -> str:
    name = f"contex-test-{uuid.uuid4().hex[:8]}"
    admin = AIOKafkaAdminClient(bootstrap_servers=BOOTSTRAP)
    await admin.start()
    try:
        await admin.create_topics([NewTopic(name, num_partitions=1, replication_factor=1)])
    finally:
        await admin.close()
    return name


async def _produce(topic, messages):
    producer = AIOKafkaProducer(bootstrap_servers=BOOTSTRAP)
    await producer.start()
    try:
        for key, value in messages:
            await producer.send_and_wait(topic, key=key, value=value)
    finally:
        await producer.stop()


async def _stream_until(config, recorder, done, timeout=30.0):
    async with KafkaSource(config) as source:
        task = asyncio.create_task(
            stream(source.events(), recorder, source.ack, flush_interval=0.1)
        )
        try:
            async with asyncio.timeout(timeout):
                while not done():
                    await asyncio.sleep(0.1)
            await asyncio.sleep(0.3)  # let the timer flush ack what was published
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        committed = await source._consumer.committed(TopicPartition(config["source"]["topics"][0], 0))
    return committed


@pytest.mark.asyncio
async def test_streams_upserts_deletes_and_commits():
    topic = await _topic()
    await _produce(topic, [(b"a", b'{"v": 1}'), (b"b", b"text"), (b"a", None), (None, b"log line")])
    config = {"source": {"cluster": "t", "bootstrap_servers": BOOTSTRAP, "topics": [topic],
                         "group_id": f"g-{topic}"}}
    recorder = Recorder()
    committed = await _stream_until(config, recorder, lambda: len(recorder.published) == 3)
    assert recorder.published == [f"t:{topic}/a", f"t:{topic}/b", f"t:{topic}/0/3"]
    assert recorder.deleted == [f"t:{topic}/a"]
    assert committed == 4


@pytest.mark.asyncio
async def test_restart_resumes_after_the_last_commit():
    topic = await _topic()
    await _produce(topic, [(b"a", b"one")])
    config = {"source": {"cluster": "t", "bootstrap_servers": BOOTSTRAP, "topics": [topic],
                         "group_id": f"g-{topic}"}}
    first = Recorder()
    await _stream_until(config, first, lambda: first.published)
    await _produce(topic, [(b"b", b"two")])
    second = Recorder()
    await _stream_until(config, second, lambda: second.published)
    assert second.published == [f"t:{topic}/b"]
```

Wire Redpanda into CI. In `.github/workflows/ci.yml`'s `connectors` job, add a step before "Run connector tests" (GitHub service containers can't pass the `redpanda start` arguments, so run it directly):

```yaml
    - name: Start Redpanda
      run: |
        docker run -d --name redpanda -p 9092:9092 redpandadata/redpanda:v24.3.6 \
          redpanda start --mode dev-container --kafka-addr 0.0.0.0:9092 \
          --advertise-kafka-addr localhost:9092
        for i in $(seq 1 30); do
          docker exec redpanda rpk cluster health | grep -q 'Healthy:.*true' && exit 0
          sleep 2
        done
        docker logs redpanda
        exit 1
```

and add `KAFKA_TEST_BOOTSTRAP: localhost:9092` to the "Run connector tests" step's `env`.

- [ ] **Step 6: Run the integration tests locally**

Start Redpanda with the `docker run` in the test docstring, then:

Run: `KAFKA_TEST_BOOTSTRAP=localhost:9092 pytest connectors/kafka -v`
Expected: all PASS, none skipped. Then `docker rm -f redpanda`.

- [ ] **Step 7: Commit**

```bash
git add connectors/kafka/source.py connectors/kafka/tests .github/workflows/ci.yml
git commit -m "Consume Kafka topics and commit offsets only after Contex accepts them"
```

---

### Task 6: Kafka CLI, image, and docs

**Files:**
- Create: `connectors/kafka/__main__.py`, `connectors/kafka/connector.template.yaml`, `connectors/kafka/connector.yaml.example`, `connectors/kafka/README.md`
- Modify: `.github/workflows/release.yml`, `connectors/README.md`, `CHANGELOG.md`

**Interfaces:**
- Consumes: `KafkaSource` (Task 5), `run_stream_connector`, `resolve_flush_interval` (Task 3), `load_config`, `ContexConfig`, `resolve_batch_size`, `resolve_secret_scanner`.
- Produces: `python -m connectors.kafka --config connector.yaml`.

- [ ] **Step 1: Write the CLI**

`connectors/kafka/__main__.py`:

```python
"""CLI entrypoint: python -m connectors.kafka --config connector.yaml"""
from __future__ import annotations

import argparse
import asyncio
import logging

from connectors.base import (
    ContexConfig,
    load_config,
    resolve_batch_size,
    resolve_flush_interval,
    resolve_secret_scanner,
    run_stream_connector,
)

from .source import KafkaSource

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Stream Kafka topics into Contex.")
    parser.add_argument("--config", default="connector.yaml", help="Path to connector.yaml")
    parser.add_argument("--log-level", default="INFO", help="Logging level (default INFO)")
    return parser.parse_args()


async def _main(config_path: str) -> None:
    config = load_config(config_path)
    contex_config = ContexConfig.from_dict(config)
    source = KafkaSource(config)

    def progress(n: int) -> None:
        logger.info("published %d items", n)

    logger.info("streaming kafka cluster=%s topics=%s", source.cluster, ",".join(source._topics))
    async with source:
        await run_stream_connector(
            contex_config,
            source.events(),
            source.ack,
            origin=f"kafka:{source.cluster}",
            batch_size=resolve_batch_size(config),
            flush_interval=resolve_flush_interval(config),
            secret_scanner=resolve_secret_scanner(config),
            progress=progress,
        )


def main() -> None:
    args = _parse_args()
    logging.getLogger().setLevel(args.log_level.upper())
    asyncio.run(_main(args.config))


if __name__ == "__main__":
    main()
```

Check the S3/Postgres `__main__.py` for how they apply `--log-level` and match it exactly if it differs from the above.

- [ ] **Step 2: Config files**

`connectors/kafka/connector.template.yaml`:

```yaml
# Fully env-driven config for the Kafka connector image. Every value is a ${VAR}
# placeholder expanded from the environment at load time, so `docker run -e ...`
# needs no file mount.
contex:
  url: ${CONTEX_URL}
  project_id: ${CONTEX_PROJECT_ID}
  api_key: ${CONTEX_TOKEN}

source:
  cluster: ${KAFKA_CLUSTER}
  bootstrap_servers: ${KAFKA_BOOTSTRAP_SERVERS}
  topics: ${KAFKA_TOPICS}            # comma-separated
  group_id: ${KAFKA_GROUP_ID}
  auto_offset_reset: ${KAFKA_AUTO_OFFSET_RESET}
  unkeyed: ${KAFKA_UNKEYED}
  security_protocol: ${KAFKA_SECURITY_PROTOCOL}
  sasl_mechanism: ${KAFKA_SASL_MECHANISM}
  sasl_username: ${KAFKA_SASL_USERNAME}
  sasl_password: ${KAFKA_SASL_PASSWORD}

flush_interval: ${CONTEX_FLUSH_INTERVAL}
batch_size: ${CONTEX_BATCH_SIZE}
```

`connectors/kafka/connector.yaml.example`: the config block from the spec's "Config" section, verbatim.

- [ ] **Step 3: Smoke-test the CLI and image**

With Redpanda and a local Contex running (Task 1 Step 5 setup), produce a keyed JSON message, then run `python -m connectors.kafka --config <a scratch yaml pointing at both>`; confirm the log shows `published 1 items` and that `contex_query` (or the web UI) returns the document under key `main:<topic>/<key>`. Produce a tombstone for the same key and confirm the document disappears. Stop with Ctrl-C.

Build the image: `docker build --build-arg CONNECTOR=kafka -f connectors/Dockerfile -t contex-connector-kafka .` — expected: builds.

- [ ] **Step 4: Docs and release**

- `.github/workflows/release.yml`: add `kafka` to `connector: [s3, postgres, github, atlassian]`.
- `connectors/kafka/README.md`, same sections as `connectors/s3/README.md`: what it does (live sync of topics; keyed upsert / tombstone delete / unkeyed append), quick start (local + Docker env vars from the template), config reference (every `source.*` key, `flush_interval`, `batch_size`, secret guard), key format and the `cluster` requirement, delivery guarantees (at-least-once; replay on crash is harmless; rejected and undecodable messages are skipped and committed), scaling (run more processes with the same `group_id`), and limitations (JSON/text only, no schema registry; explicit topic list; retention expiry doesn't delete; no prune).
- `connectors/README.md`: change the "v1 connectors are bulk snapshots" paragraph to say snapshot connectors (Postgres, S3, GitHub, Atlassian) refresh by re-running, and streaming connectors (Kafka) run continuously, publishing each change within about `flush_interval` seconds and committing their position only after Contex accepts it. Add `stream` / `run_stream_connector` to the shared-framework list.
- `CHANGELOG.md`: under the unreleased section, add the Kafka connector and the publisher's reconnect behavior.

- [ ] **Step 5: Run everything CI runs for connectors**

Run: `KAFKA_TEST_BOOTSTRAP=localhost:9092 pytest connectors/base connectors/postgres connectors/s3 connectors/github connectors/atlassian connectors/kafka tests/connectors -v && ruff check connectors`
Expected: all PASS, ruff clean.

- [ ] **Step 6: Commit**

```bash
git add connectors/kafka connectors/README.md CHANGELOG.md .github/workflows/release.yml
git commit -m "Add the Kafka connector CLI, image and docs"
```

Then push the branch and open a PR against `main` (do not merge).
