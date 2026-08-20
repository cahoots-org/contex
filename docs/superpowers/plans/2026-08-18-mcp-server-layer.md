# MCP Server Layer (Plan B) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose Contex as an MCP server — subscription-as-resource with live `resources/updated` push driven by Plan A's reconcile events — so an agent gets context that updates itself, over standard MCP.

**Architecture:** A new `src/core/mcp_adapter.py` builds an `mcp.server.MCPServer` (mcp 2.0) with an `InMemorySubscriptionBus`, registers tools (query/publish/create-/update-/delete-subscription) that delegate to the existing `ContextEngine`/`SubscriptionService`, and registers a templated resource `contex://subscriptions/{id}` returning a subscription's materialized bundle. A `src/core/mcp_bridge.py` background task subscribes to Redis `subscription:*:updated` (emitted by Plan A's `reconcile_project`) and republishes each as `bus.publish(ResourceUpdated(uri=...))`. `main.py` mounts the MCP ASGI app at `/mcp`, enters the transport's session manager in its lifespan, and starts the bridge. All `mcp` SDK usage is confined to these two modules (anti-corruption layer), so a future SDK migration is a one-module swap.

**Tech Stack:** Python 3.12, mcp==2.0.0, FastAPI 0.141/Starlette 1.6, Redis (pub/sub), pytest + pytest-asyncio, fakeredis.

## Global Constraints

- Design spec: `docs/superpowers/specs/2026-08-18-semantic-subscriptions-design.md` (§2 MCP interface, §4.1 subscription-as-resource, "saved live query" trust property). This plan implements §4.1's MCP layer.
- **Pin: `mcp==2.0.0`** (already in requirements.txt from the framework upgrade). mcp 1.x is a DIFFERENT API — do not use 1.x idioms (`FastMCP`, `@server.subscribe_resource`). The verified 2.0 API is documented per-task below.
- **All `mcp` imports live only in `src/core/mcp_adapter.py` and `src/core/mcp_bridge.py`** (anti-corruption layer for a future 2.x→3.x migration). Handlers delegate to `ContextEngine`/`SubscriptionService`; do not put business logic in the adapter.
- Reuse Plan A: `SubscriptionService(db, matcher, redis)` with `create/get_bundle/reconcile_project/delete`; `ContextEngine.query_project_data(project_id, query, top_k, threshold)`; `ContextEngine.publish_data(DataPublishEvent)`; reconcile emits Redis channel `subscription:{id}:updated` with JSON `{"subscription_id","updated_at"}`.
- Resource URI scheme: `contex://subscriptions/{id}` (subscription_id is a globally-unique `sub_<uuid>` — no project needed in the URI).
- DB tests use the `db` fixture (real Postgres `contex_test`); Redis tests use the `redis` fixture (fakeredis). Controller commits (implementer subagents cannot); do NOT push.

## Verified mcp 2.0 API reference (use these exact calls)
- `from mcp.server import MCPServer` → `MCPServer(name="contex", version="0.3.0", subscriptions=bus)`.
- `from mcp.server.subscriptions import InMemorySubscriptionBus, ResourceUpdated` → `bus = InMemorySubscriptionBus()`; push: `await bus.publish(ResourceUpdated(uri="contex://subscriptions/<id>"))` (`ResourceUpdated` is a dataclass with a single `uri: str` field; `publish` is async).
- Tools: `@server.tool(name="contex_query", description="...")` on an `async def` — its typed params become the input schema.
- Resources: `@server.resource("contex://subscriptions/{id}", name="subscription", mime_type="application/json")` on an `async def(id: str)` returning a JSON string.
- Mount: `app.mount("/mcp", server.streamable_http_app(streamable_http_path="/mcp"))` (returns a Starlette app).
- Lifespan: `server.session_manager` is a `StreamableHTTPSessionManager` with `run() -> AsyncIterator[None]`; enter it in main.py's lifespan: `async with server.session_manager.run(): ... yield`.
- In-process testing of tools/resources without HTTP: call `await server.call_tool(name, arguments)` and `await server.read_resource(uri)`.

---

### Task 1: Make query/subscription share explicit params (deferred Plan-A hardening)

**Files:**
- Modify: `src/core/subscriptions.py` (`SubscriptionService.create`), `src/core/matcher.py` (`HybridMatcher.match`)
- Test: `tests/test_subscription_params.py`

**Interfaces:**
- Consumes: existing `SemanticDataMatcher.match_agent_needs`.
- Produces: `HybridMatcher.match(project_id, needs, metadata=None, top_k=None, threshold=None)` and `SubscriptionService.create(..., top_k=None, threshold=None)` — when provided, these are applied to the underlying matcher so a subscription created with `(top_k, threshold)` yields the same matches as `query_project_data(project_id, need, top_k, threshold)`. This makes the §4.1 "what you test is what you get" property structural rather than incidental.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_subscription_params.py
import pytest
from src.core.context_engine import ContextEngine
from src.core.models import DataPublishEvent


@pytest.mark.asyncio
async def test_create_with_params_matches_query_with_same_params(db, redis):
    engine = ContextEngine(db=db, redis=redis, similarity_threshold=0.9, max_matches=1)
    await engine.initialize()
    await engine.publish_data(DataPublishEvent(
        project_id="p", data_key="db", data={"purpose": "database connection settings"}, data_format="json",
    ))
    need = "database connection settings"
    # query with explicit permissive params
    q = await engine.query_project_data("p", need, top_k=10, threshold=0.1)
    # a subscription created with the SAME params must yield the same matches,
    # even though the engine's defaults (threshold 0.9 / max 1) would differ
    sub_id = await engine.subscriptions.create("p", [need], top_k=10, threshold=0.1)
    bundle = await engine.subscriptions.get_bundle(sub_id)
    assert [m["data_key"] for m in q] == [m["data_key"] for m in bundle[need]]
    assert len(bundle[need]) >= 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_subscription_params.py -v`
Expected: FAIL — `create()` got an unexpected keyword `top_k` (and/or the assertion, since defaults differ).

- [ ] **Step 3: Thread the params through**

In `src/core/matcher.py`, change `HybridMatcher.match` to accept and apply optional overrides by temporarily setting the underlying matcher's `max_matches`/`threshold` (mirroring how `ContextEngine.query_project_data` already overrides them), restoring in a `finally`:

```python
    async def match(self, project_id, needs, metadata=None, top_k=None, threshold=None):
        sm = self.semantic_matcher
        old_max, old_thr = sm.max_matches, sm.threshold
        try:
            if top_k is not None:
                sm.max_matches = top_k
            if threshold is not None:
                sm.threshold = threshold
            return await sm.match_agent_needs(project_id, needs)
        finally:
            sm.max_matches, sm.threshold = old_max, old_thr
```

(Update the `Matcher` Protocol signature in the same file to include `top_k: int | None = None, threshold: float | None = None`.)

In `src/core/subscriptions.py`, thread the params from `create` into the matcher call:

```python
    async def create(self, project_id, needs, tenant_id=None, scope=None, subscription_id=None, top_k=None, threshold=None) -> str:
        sub_id = subscription_id or f"sub_{uuid4().hex}"
        bundle = await self.matcher.match(project_id, needs, top_k=top_k, threshold=threshold)
        ...
```

Verify the field spec of `semantic_matcher` (attribute names `max_matches`, `threshold`) against `src/core/semantic_matcher.py:38-54` — adjust the attribute names if they differ.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_subscription_params.py tests/test_matcher.py tests/test_subscription_service.py -v`
Expected: PASS (existing matcher/service tests still green — the new params are optional and default to prior behavior).

- [ ] **Step 5: Commit** (controller)

```bash
git add src/core/matcher.py src/core/subscriptions.py tests/test_subscription_params.py
git commit -m "feat: thread explicit top_k/threshold through matcher+create for query/subscription parity"
```

---

### Task 2: MCP adapter — server + `contex_query` tool (in-process testable)

**Files:**
- Create: `src/core/mcp_adapter.py`
- Test: `tests/test_mcp_adapter.py`

**Interfaces:**
- Consumes: `ContextEngine` (has `.query_project_data`, `.subscriptions`, `.publish_data`), mcp 2.0 API.
- Produces: `build_mcp_server(engine) -> MCPServer` and module-level access to its `bus` (an `InMemorySubscriptionBus`). Registers the `contex_query` tool. Callable in-process via `await server.call_tool("contex_query", {...})`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_mcp_adapter.py
import json
import pytest
from src.core.context_engine import ContextEngine
from src.core.models import DataPublishEvent
from src.core.mcp_adapter import build_mcp_server


@pytest.mark.asyncio
async def test_contex_query_tool_returns_matches(db, redis):
    engine = ContextEngine(db=db, redis=redis, similarity_threshold=0.1, max_matches=10)
    await engine.initialize()
    await engine.publish_data(DataPublishEvent(
        project_id="p", data_key="db", data={"purpose": "database connection settings"}, data_format="json",
    ))
    server, bus = build_mcp_server(engine)
    result = await server.call_tool("contex_query", {
        "project_id": "p", "query": "database connection settings", "top_k": 10, "threshold": 0.1,
    })
    # call_tool returns a CallToolResult; its content carries the JSON payload text
    text = result.content[0].text
    payload = json.loads(text)
    assert any(m["data_key"] == "db" for m in payload["matches"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_mcp_adapter.py -v`
Expected: FAIL — `No module named 'src.core.mcp_adapter'`.

- [ ] **Step 3: Write the adapter**

```python
# src/core/mcp_adapter.py
"""MCP (Model Context Protocol) server for Contex — the ONLY module (with mcp_bridge)
that imports the mcp SDK. Handlers delegate to ContextEngine/SubscriptionService."""
from __future__ import annotations

import json

from mcp.server import MCPServer
from mcp.server.subscriptions import InMemorySubscriptionBus


def build_mcp_server(engine):
    """Build the Contex MCP server bound to a ContextEngine. Returns (server, bus)."""
    bus = InMemorySubscriptionBus()
    server = MCPServer(name="contex", version="0.3.0", subscriptions=bus)

    @server.tool(name="contex_query", description="Semantic query over a project's context (stateless).")
    async def contex_query(project_id: str, query: str, top_k: int = 5, threshold: float | None = None) -> str:
        matches = await engine.query_project_data(project_id, query, top_k=top_k, threshold=threshold)
        return json.dumps({"query": query, "matches": matches})

    return server, bus
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_mcp_adapter.py -v`
Expected: PASS. If `result.content[0].text` is not the shape, print `result` and adjust the accessor to the actual `CallToolResult` content type (mcp 2.0 wraps a string return in a text content block) — do not change the tool's JSON contract, only the test's accessor.

- [ ] **Step 5: Commit** (controller)

```bash
git add src/core/mcp_adapter.py tests/test_mcp_adapter.py
git commit -m "feat: MCP adapter with contex_query tool (mcp 2.0)"
```

---

### Task 3: Subscription tools — create / delete

**Files:**
- Modify: `src/core/mcp_adapter.py`
- Test: `tests/test_mcp_subscription_tools.py`

**Interfaces:**
- Consumes: `engine.subscriptions` (`create(project_id, needs, top_k, threshold) -> str`, `delete(id)`).
- Produces: tools `contex_create_subscription(project_id, needs, top_k, threshold)` (returns JSON `{"subscription_id", "resource_uri"}` where `resource_uri = f"contex://subscriptions/{id}"`) and `contex_delete_subscription(subscription_id)` (returns JSON `{"deleted": id}`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_mcp_subscription_tools.py
import json
import pytest
from src.core.context_engine import ContextEngine
from src.core.mcp_adapter import build_mcp_server


@pytest.mark.asyncio
async def test_create_and_delete_subscription_tools(db, redis):
    engine = ContextEngine(db=db, redis=redis, similarity_threshold=0.1, max_matches=10)
    await engine.initialize()
    server, _ = build_mcp_server(engine)

    created = json.loads((await server.call_tool("contex_create_subscription", {
        "project_id": "p", "needs": ["auth config"], "top_k": 10, "threshold": 0.1,
    })).content[0].text)
    sub_id = created["subscription_id"]
    assert created["resource_uri"] == f"contex://subscriptions/{sub_id}"
    # bundle now exists
    assert await engine.subscriptions.get_bundle(sub_id) is not None

    deleted = json.loads((await server.call_tool("contex_delete_subscription", {
        "subscription_id": sub_id,
    })).content[0].text)
    assert deleted["deleted"] == sub_id
    with pytest.raises(KeyError):
        await engine.subscriptions.get_bundle(sub_id)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_mcp_subscription_tools.py -v`
Expected: FAIL — unknown tool `contex_create_subscription`.

- [ ] **Step 3: Add the tools** (inside `build_mcp_server`, after `contex_query`)

```python
    @server.tool(name="contex_create_subscription",
                 description="Create a live subscription; returns its resource URI to subscribe to.")
    async def contex_create_subscription(project_id: str, needs: list[str],
                                         top_k: int = 5, threshold: float | None = None) -> str:
        sub_id = await engine.subscriptions.create(project_id, needs, top_k=top_k, threshold=threshold)
        return json.dumps({"subscription_id": sub_id, "resource_uri": f"contex://subscriptions/{sub_id}"})

    @server.tool(name="contex_delete_subscription", description="Delete a subscription.")
    async def contex_delete_subscription(subscription_id: str) -> str:
        await engine.subscriptions.delete(subscription_id)
        return json.dumps({"deleted": subscription_id})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_mcp_subscription_tools.py -v`
Expected: PASS.

- [ ] **Step 5: Commit** (controller)

```bash
git add src/core/mcp_adapter.py tests/test_mcp_subscription_tools.py
git commit -m "feat: MCP create/delete subscription tools"
```

---

### Task 4: Subscription-as-resource (the pull)

**Files:**
- Modify: `src/core/mcp_adapter.py`
- Test: `tests/test_mcp_resource.py`

**Interfaces:**
- Consumes: `engine.subscriptions.get_bundle(id) -> dict`.
- Produces: a templated resource `contex://subscriptions/{id}` whose read returns the materialized bundle as a JSON string; reading an unknown id raises so MCP returns a not-found error.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_mcp_resource.py
import json
import pytest
from src.core.context_engine import ContextEngine
from src.core.mcp_adapter import build_mcp_server


@pytest.mark.asyncio
async def test_read_subscription_resource_returns_bundle(db, redis):
    engine = ContextEngine(db=db, redis=redis, similarity_threshold=0.1, max_matches=10)
    await engine.initialize()
    server, _ = build_mcp_server(engine)
    sub_id = await engine.subscriptions.create("p", ["auth config"], top_k=10, threshold=0.1)

    contents = await server.read_resource(f"contex://subscriptions/{sub_id}")
    # read_resource returns an iterable of ReadResourceContents; take the first's text
    first = list(contents)[0]
    bundle = json.loads(first.content)
    assert "auth config" in bundle
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_mcp_resource.py -v`
Expected: FAIL — no resource registered for that URI.

- [ ] **Step 3: Register the resource** (inside `build_mcp_server`)

```python
    @server.resource("contex://subscriptions/{id}", name="subscription",
                     description="A subscription's current matched context bundle.",
                     mime_type="application/json")
    async def read_subscription(id: str) -> str:
        return json.dumps(await engine.subscriptions.get_bundle(id))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_mcp_resource.py -v`
Expected: PASS. If `contents` element exposes `.text` rather than `.content`, adjust the test accessor to the real `ReadResourceContents` field (print it once); keep the resource returning the JSON string.

- [ ] **Step 5: Commit** (controller)

```bash
git add src/core/mcp_adapter.py tests/test_mcp_resource.py
git commit -m "feat: subscription-as-resource (contex://subscriptions/{id})"
```

---

### Task 5: The Redis→MCP bridge (live push)

**Files:**
- Create: `src/core/mcp_bridge.py`
- Test: `tests/test_mcp_bridge.py`

**Interfaces:**
- Consumes: a Redis client (`.pubsub()`), the `InMemorySubscriptionBus` from the adapter, `mcp.server.subscriptions.ResourceUpdated`.
- Produces:
  - `resource_uri_for(subscription_id: str) -> str` = `f"contex://subscriptions/{subscription_id}"`.
  - `async def handle_message(bus, raw: bytes | str) -> str | None` — parse a `subscription:{id}:updated` payload, `await bus.publish(ResourceUpdated(uri=...))`, return the uri (or None if unparseable).
  - `async def run_bridge(redis, bus, stop_event)` — psubscribe `subscription:*:updated`, loop calling `handle_message`. (Tested via `handle_message`; `run_bridge` is exercised in the Task 7 boot test.)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_mcp_bridge.py
import json
import pytest
from mcp.server.subscriptions import InMemorySubscriptionBus, ResourceUpdated
from src.core.mcp_bridge import handle_message, resource_uri_for


@pytest.mark.asyncio
async def test_handle_message_publishes_resource_updated():
    bus = InMemorySubscriptionBus()
    seen = []
    bus.subscribe(lambda ev: seen.append(ev))
    payload = json.dumps({"subscription_id": "sub_abc", "updated_at": "2026-08-18T00:00:00Z"})

    uri = await handle_message(bus, payload)

    assert uri == "contex://subscriptions/sub_abc"
    assert resource_uri_for("sub_abc") == uri
    assert any(isinstance(ev, ResourceUpdated) and ev.uri == uri for ev in seen)


@pytest.mark.asyncio
async def test_handle_message_ignores_garbage():
    bus = InMemorySubscriptionBus()
    assert await handle_message(bus, b"not json") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_mcp_bridge.py -v`
Expected: FAIL — `No module named 'src.core.mcp_bridge'`.

- [ ] **Step 3: Write the bridge**

```python
# src/core/mcp_bridge.py
"""Bridges Plan A's Redis `subscription:{id}:updated` events into MCP resources/updated
notifications. The second of two modules allowed to import the mcp SDK."""
from __future__ import annotations

import asyncio
import json
import logging

from mcp.server.subscriptions import ResourceUpdated

logger = logging.getLogger(__name__)

_CHANNEL_PATTERN = "subscription:*:updated"


def resource_uri_for(subscription_id: str) -> str:
    return f"contex://subscriptions/{subscription_id}"


async def handle_message(bus, raw) -> str | None:
    try:
        data = json.loads(raw.decode() if isinstance(raw, (bytes, bytearray)) else raw)
        sub_id = data["subscription_id"]
    except (ValueError, KeyError, AttributeError):
        return None
    uri = resource_uri_for(sub_id)
    await bus.publish(ResourceUpdated(uri=uri))
    return uri


async def run_bridge(redis, bus, stop_event: asyncio.Event) -> None:
    pubsub = redis.pubsub()
    await pubsub.psubscribe(_CHANNEL_PATTERN)
    logger.info("MCP bridge listening on %s", _CHANNEL_PATTERN)
    try:
        while not stop_event.is_set():
            msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if msg and msg.get("type") in ("pmessage", "message"):
                await handle_message(bus, msg["data"])
    finally:
        await pubsub.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_mcp_bridge.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit** (controller)

```bash
git add src/core/mcp_bridge.py tests/test_mcp_bridge.py
git commit -m "feat: Redis->MCP bridge (subscription updates -> resources/updated)"
```

---

### Task 6: `contex_publish` tool

**Files:**
- Modify: `src/core/mcp_adapter.py`
- Test: `tests/test_mcp_publish_tool.py`

**Interfaces:**
- Consumes: `engine.publish_data(DataPublishEvent(project_id, data_key, data, data_format))`.
- Produces: tool `contex_publish(project_id, data_key, data, data_format="json")` returning JSON `{"published": data_key, "sequence": <str>}`. Publishing triggers Plan A reconcile inline (already wired).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_mcp_publish_tool.py
import json
import pytest
from src.core.context_engine import ContextEngine
from src.core.mcp_adapter import build_mcp_server


@pytest.mark.asyncio
async def test_publish_tool_updates_subscription_bundle(db, redis):
    engine = ContextEngine(db=db, redis=redis, similarity_threshold=0.1, max_matches=10)
    await engine.initialize()
    server, _ = build_mcp_server(engine)
    sub_id = await engine.subscriptions.create("p", ["database connection settings"], top_k=10, threshold=0.1)

    res = json.loads((await server.call_tool("contex_publish", {
        "project_id": "p", "data_key": "db",
        "data": {"purpose": "database connection settings"}, "data_format": "json",
    })).content[0].text)
    assert res["published"] == "db"

    bundle = await engine.subscriptions.get_bundle(sub_id)
    assert any(m["data_key"] == "db" for matches in bundle.values() for m in matches)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_mcp_publish_tool.py -v`
Expected: FAIL — unknown tool `contex_publish`.

- [ ] **Step 3: Add the tool** (inside `build_mcp_server`; import `DataPublishEvent` at module top: `from src.core.models import DataPublishEvent`)

```python
    @server.tool(name="contex_publish", description="Publish/update context data for a project.")
    async def contex_publish(project_id: str, data_key: str, data: dict, data_format: str = "json") -> str:
        seq = await engine.publish_data(DataPublishEvent(
            project_id=project_id, data_key=data_key, data=data, data_format=data_format,
        ))
        return json.dumps({"published": data_key, "sequence": str(seq)})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_mcp_publish_tool.py -v`
Expected: PASS.

- [ ] **Step 5: Commit** (controller)

```bash
git add src/core/mcp_adapter.py tests/test_mcp_publish_tool.py
git commit -m "feat: MCP contex_publish tool"
```

---

### Task 7: Mount MCP on FastAPI + wire lifespan + start bridge

**Files:**
- Modify: `main.py` (app construction ~line 242, lifespan ~lines 32-238, mounts ~lines 314-368)
- Test: `tests/test_mcp_mount.py`

**Interfaces:**
- Consumes: `build_mcp_server(engine)` (Task 2), `run_bridge(redis, bus, stop_event)` (Task 5), `app.state.context_engine`/`app.state.redis`.
- Produces: the MCP server mounted at `/mcp`; its `session_manager.run()` entered in the lifespan; the bridge task started at startup and stopped at shutdown. `app.state.mcp_server` and `app.state.mcp_bus` set.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_mcp_mount.py
import main


def test_mcp_mounted_at_slash_mcp():
    paths = [getattr(r, "path", "") for r in main.app.routes]
    assert any(p == "/mcp" or p.startswith("/mcp") for p in paths), f"/mcp not mounted; routes={paths}"


def test_build_mcp_server_is_wired_in_main():
    # the factory + bridge are imported and used by main
    import inspect
    src = inspect.getsource(main)
    assert "build_mcp_server" in src and "run_bridge" in src and "session_manager" in src
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_mcp_mount.py -v`
Expected: FAIL — `/mcp` not in routes; `build_mcp_server`/`run_bridge` not referenced.

- [ ] **Step 3: Wire it into `main.py`**

Add imports near the other `src.core` imports:

```python
from src.core.mcp_adapter import build_mcp_server
from src.core.mcp_bridge import run_bridge
```

In the `lifespan` startup (after `context_engine` is created and stored on `app.state`, and after `redis` is available), build the server, start the bridge, and enter the session manager. The mounted sub-app's lifespan does NOT run automatically, so the session manager MUST be entered here:

```python
    mcp_server, mcp_bus = build_mcp_server(context_engine)
    app.state.mcp_server = mcp_server
    app.state.mcp_bus = mcp_bus
    mcp_stop = asyncio.Event()
    async with mcp_server.session_manager.run():
        bridge_task = asyncio.create_task(run_bridge(redis, mcp_bus, mcp_stop))
        try:
            yield        # <-- the existing `yield` in the lifespan moves inside this block
        finally:
            mcp_stop.set()
            bridge_task.cancel()
```

(Ensure `import asyncio` is present. Fit this around the existing startup/shutdown — the single `yield` that already exists must end up inside the `async with mcp_server.session_manager.run():` block. Keep all existing startup/shutdown steps.)

After the app is created and other routers are mounted (after line ~368), mount the MCP app:

```python
app.mount("/mcp", app.state.mcp_server.streamable_http_app(streamable_http_path="/mcp"))
```

Note: `app.state.mcp_server` is only set during lifespan startup, which runs before requests but AFTER module import. If `app.mount` at module level can't see `app.state`, instead build a module-level server for mounting and reuse it in the lifespan — i.e. construct `mcp_server`/`mcp_bus` once at module level via `build_mcp_server(...)`. But `build_mcp_server` needs the engine, which is created in lifespan. Resolve by: construct the MCP server at module level bound to a lazy accessor, OR mount inside the lifespan using `app.router.routes.append(Mount("/mcp", app=...))`. The implementer should choose the pattern that keeps `/mcp` routable; the test asserts `/mcp` is in `main.app.routes`. Verify the chosen approach against how `context_engine` is currently created (module-level vs lifespan) in `main.py:145-231` and pick the one consistent with it.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_mcp_mount.py -v`
Expected: PASS. Also confirm the app still boots: `python -c "import main; print('ok')"`.

- [ ] **Step 5: Full suite regression check**

Run: `python -m pytest -q`
Expected: no NEW failures vs. the pre-existing 6 `sdk/python` failures.

- [ ] **Step 6: Commit** (controller)

```bash
git add main.py tests/test_mcp_mount.py
git commit -m "feat: mount MCP server at /mcp, wire session manager + bridge into lifespan"
```

---

### Task 8: End-to-end live-update test (the payoff)

**Files:**
- Test: `tests/test_mcp_live_update_e2e.py`

**Interfaces:**
- Consumes: everything above. Verifies the full loop *without* HTTP by driving the bus directly: create subscription → subscribe a listener to the bus (standing in for an MCP client's `resources/subscribe`) → publish matching data (which reconciles + emits the Redis event) → run the bridge once → assert a `ResourceUpdated` for the subscription's URI fired → re-read the resource shows the new bundle.

- [ ] **Step 1: Write the test**

```python
# tests/test_mcp_live_update_e2e.py
import json
import pytest
from mcp.server.subscriptions import ResourceUpdated
from src.core.context_engine import ContextEngine
from src.core.models import DataPublishEvent
from src.core.mcp_adapter import build_mcp_server
from src.core.mcp_bridge import handle_message, resource_uri_for


@pytest.mark.asyncio
async def test_publish_pushes_resource_updated_and_bundle_refreshes(db, redis):
    engine = ContextEngine(db=db, redis=redis, similarity_threshold=0.1, max_matches=10)
    await engine.initialize()
    server, bus = build_mcp_server(engine)

    sub_id = await engine.subscriptions.create("p", ["database connection settings"], top_k=10, threshold=0.1)
    uri = resource_uri_for(sub_id)

    updated = []
    bus.subscribe(lambda ev: updated.append(ev))

    # subscribe to the raw Redis channel to capture the reconcile emit
    pubsub = redis.pubsub()
    await pubsub.subscribe(f"subscription:{sub_id}:updated")

    # publish matching data -> reconcile updates the bundle + emits the redis event
    await engine.publish_data(DataPublishEvent(
        project_id="p", data_key="db",
        data={"purpose": "database connection settings"}, data_format="json",
    ))

    # drain the redis event and drive the bridge (stands in for the running bridge task)
    msg = None
    for _ in range(5):
        msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1)
        if msg:
            break
    assert msg is not None, "reconcile did not emit a subscription-updated event"
    pushed_uri = await handle_message(bus, msg["data"])

    # the MCP resources/updated push fired for our subscription's URI
    assert pushed_uri == uri
    assert any(isinstance(ev, ResourceUpdated) and ev.uri == uri for ev in updated)

    # re-reading the resource shows the freshly-materialized bundle containing the new item
    contents = list(await server.read_resource(uri))
    bundle = json.loads(contents[0].content)
    assert any(m["data_key"] == "db" for matches in bundle.values() for m in matches)
```

- [ ] **Step 2: Run test to verify it fails, then passes**

Run: `python -m pytest tests/test_mcp_live_update_e2e.py -v`
Expected: PASS (all upstream tasks merged). If the `read_resource`/`content` accessor differs, align it with what Task 4 established (single source of truth for the accessor).

- [ ] **Step 3: Commit** (controller)

```bash
git add tests/test_mcp_live_update_e2e.py
git commit -m "test: end-to-end publish -> resources/updated -> refreshed bundle"
```

---

## Self-Review Notes

- **Spec coverage:** §2.1 resources (subscription-as-resource) → Task 4; §2.2 tools (query/create/delete/publish) → Tasks 2,3,6; §2.5 lifecycle push (resources/subscribe→updated) → Tasks 5,7,8; §4.1 "saved live query" parity → Task 1. Deferred correctly (Plan C): §4.5 sandbox. `update_subscription` tool is intentionally omitted (YAGNI for this increment — create+delete cover the loop; add when a real need appears).
- **Placeholder scan:** every step has runnable code. Three accessor points (`CallToolResult` content, `ReadResourceContents` field, and the mount pattern in main.py) carry an explicit "verify against the real shape and adjust the *test accessor*, not the contract" instruction — these are grounded checks against mcp 2.0's actual return types, not placeholders. Task 7's mount-vs-lifespan approach names the concrete decision and the test that constrains it.
- **Type consistency:** the bundle shape (`dict[need -> list[{data_key,...}]]`) flows unchanged from Plan A through tools/resource; `resource_uri_for(id)` = `f"contex://subscriptions/{id}"` is used identically in the adapter, bridge, and tests; `build_mcp_server(engine) -> (server, bus)` is consumed consistently.
- **Anti-corruption:** all `mcp` imports are confined to `mcp_adapter.py` + `mcp_bridge.py` (Global Constraint), so the 2.x→future migration the user worried about is a two-module swap.
