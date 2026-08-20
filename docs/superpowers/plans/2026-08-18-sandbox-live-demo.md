# Sandbox Live Demo Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a pre-seeded, split-screen sandbox demo where editing + publishing a value makes a natural-language "need" subscription update its context panel live — captured as the README GIF.

**Architecture:** Rewire the existing sandbox SSE endpoint from the legacy agent-registration model to a real ephemeral `Subscription` (Plan A/B machinery): create → stream initial bundle → re-stream on each `subscription:{id}:updated` Redis event (the same events the MCP bridge consumes) → delete on disconnect. Add an idempotent demo seed, a dedicated sandbox publish route, and a split-screen `demo.html` front-end. The browser becomes a parallel SSE consumer of the same reconcile pipeline.

**Tech Stack:** FastAPI/Starlette, SQLAlchemy async (`DatabaseManager`), Redis pub/sub, Jinja2 templates, vanilla-JS `EventSource` (SSE), pytest + `pytest-asyncio`.

## Global Constraints

- The sandbox `APIRouter` is mounted at prefix `/sandbox` in `main.py` (line ~396), so route paths declared as `/demo` resolve to `/sandbox/demo`, `/subscribe` to `/sandbox/subscribe`, etc.
- Reuse the existing subscription/reconcile machinery only — no new matching/ranking logic. `SubscriptionService.create(project_id, needs, ..., top_k=None, threshold=None) -> str`, `get_bundle(id) -> dict` (raises `KeyError`), `delete(id) -> None` (idempotent). `needs` is a list.
- Demo subscriptions are **ephemeral**: the SSE stream MUST delete its subscription when the stream closes (client disconnect or error), in a `finally`.
- Do NOT import the `mcp` SDK anywhere — those imports stay confined to `src/core/mcp_adapter.py` and `src/core/mcp_bridge.py`. The demo uses SSE, not MCP.
- The REST API is frozen: the demo's publish action uses a dedicated `POST /sandbox/demo/publish` route, never the public `/api/*` publish endpoint.
- Tests instantiate `ContextEngine(db=db, redis=redis, similarity_threshold=0.1, max_matches=10)` directly and `await engine.initialize()`, using the `db` and `redis` fixtures from `tests/conftest.py` (mirror `tests/test_mcp_live_update_e2e.py`).
- `AgentRegistration` / `engine.register_agent` remain in use by the REST agent feature — do NOT remove them from the codebase; only remove the sandbox SSE route's use of them.
- Python 3.12, async throughout. Any docker image builds use `--platform linux/amd64` (user global rule).
- Reconcile publishes to Redis channel `subscription:{subscription_id}:updated` with a JSON body `{"subscription_id": ..., "updated_at": <iso>}`.

---

## File Structure

- **Create `src/web/live.py`** — the load-bearing streaming unit: `stream_subscription_updates(engine, project_id, need, *, top_k, threshold)`, an async generator yielding SSE frames. One responsibility: turn a need into a live SSE bundle stream backed by an ephemeral subscription.
- **Create `src/web/demo_seed.py`** — idempotent demo data: `ensure_demo_seed(engine)` + the demo constants (`DEMO_PROJECT_ID`, `DEMO_NEED`, `DEMO_ITEMS`). One responsibility: guarantee the demo project exists.
- **Modify `src/web/routes.py`** — rewire `GET /subscribe` to delegate to `stream_subscription_updates`; add `GET /demo` (page) and `POST /demo/publish` (publish action). Remove the sandbox's `AgentRegistration` import + `event_stream` body.
- **Create `src/web/templates/demo.html`** — split-screen page: need editor + live context panel (EventSource) on one side, publish panel on the other; JS diffs bundles and flashes changed items.
- **Modify `src/web/templates/sandbox.html`** — remove the now-orphaned live-update `EventSource` block that referenced the old `/subscribe` signature (the query/test mode stays untouched).
- **Modify `README.md`** + **create `docs/demo/capture.md`** — embed the demo GIF and document how to capture it.
- **Create tests:** `tests/test_sandbox_live.py`, `tests/test_demo_seed.py`, `tests/test_sandbox_demo_routes.py`.

---

### Task 1: Live subscription SSE stream

**Files:**
- Create: `src/web/live.py`
- Test: `tests/test_sandbox_live.py`

**Interfaces:**
- Consumes: `engine.subscriptions.create(project_id, needs, top_k=, threshold=) -> str`, `engine.subscriptions.get_bundle(id) -> dict`, `engine.subscriptions.delete(id) -> None`, `engine.redis.pubsub()`, `engine.publish_data(DataPublishEvent) -> str`.
- Produces: `async def stream_subscription_updates(engine, project_id: str, need: str, *, top_k: int = DEMO_TOP_K, threshold: float = DEMO_THRESHOLD) -> AsyncIterator[str]` yielding SSE-framed strings `"data: {json}\n\n"` where json is `{"type": "bundle", "bundle": <dict>, "updated_at": <iso|null>}`. Module constants `DEMO_TOP_K = 10`, `DEMO_THRESHOLD = 0.3`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_sandbox_live.py
import asyncio
import json

import pytest
from sqlalchemy import func, select

from src.core.context_engine import ContextEngine
from src.core.db_models import Subscription
from src.core.models import DataPublishEvent
from src.web.live import stream_subscription_updates


def _payload(frame: str) -> dict:
    assert frame.startswith("data: ") and frame.endswith("\n\n")
    return json.loads(frame[len("data: "):].strip())


@pytest.mark.asyncio
async def test_stream_yields_initial_then_updates_then_cleans_up(db, redis):
    engine = ContextEngine(db=db, redis=redis, similarity_threshold=0.1, max_matches=10)
    await engine.initialize()

    agen = stream_subscription_updates(
        engine, "p", "database connection settings", top_k=10, threshold=0.1
    )

    # First frame is the initial bundle.
    first = _payload(await agen.__anext__())
    assert first["type"] == "bundle"
    assert first["updated_at"] is None

    # Publishing matching data reconciles -> emits subscription:{id}:updated -> next frame.
    await engine.publish_data(DataPublishEvent(
        project_id="p", data_key="db",
        data={"purpose": "database connection settings"}, data_format="json",
    ))

    second = _payload(await asyncio.wait_for(agen.__anext__(), timeout=5))
    assert second["type"] == "bundle"
    assert second["updated_at"] is not None
    # The freshly-published item appears in the streamed bundle.
    assert any(
        m["data_key"].startswith("db")
        for matches in second["bundle"].values()
        for m in matches
    )

    # Closing the stream deletes the ephemeral subscription.
    await agen.aclose()
    async with db.session() as session:
        count = (await session.execute(
            select(func.count()).select_from(Subscription).where(Subscription.project_id == "p")
        )).scalar_one()
    assert count == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_sandbox_live.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.web.live'`

- [ ] **Step 3: Write the implementation**

```python
# src/web/live.py
"""SSE streaming for the sandbox live demo: a natural-language need backed by an
ephemeral Subscription whose materialized bundle is pushed on every reconcile."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import AsyncIterator

logger = logging.getLogger(__name__)

DEMO_TOP_K = 10
DEMO_THRESHOLD = 0.3


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


async def stream_subscription_updates(
    engine, project_id: str, need: str, *, top_k: int = DEMO_TOP_K, threshold: float = DEMO_THRESHOLD,
) -> AsyncIterator[str]:
    """Create an ephemeral subscription for `need`, stream its bundle, and re-stream
    the bundle each time reconcile fires `subscription:{id}:updated`. The subscription
    is always deleted when the stream closes."""
    sub_id = await engine.subscriptions.create(project_id, [need], top_k=top_k, threshold=threshold)
    channel = f"subscription:{sub_id}:updated"
    pubsub = engine.redis.pubsub()
    await pubsub.subscribe(channel)
    try:
        # Re-read AFTER subscribing so a change racing the create() is not missed.
        bundle = await engine.subscriptions.get_bundle(sub_id)
        yield _sse({"type": "bundle", "bundle": bundle, "updated_at": None})

        async for message in pubsub.listen():
            if message.get("type") != "message":
                continue  # skip subscribe-confirmation / pattern frames
            bundle = await engine.subscriptions.get_bundle(sub_id)
            yield _sse({
                "type": "bundle",
                "bundle": bundle,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            })
    finally:
        try:
            await pubsub.unsubscribe(channel)
            await pubsub.aclose()
        finally:
            await engine.subscriptions.delete(sub_id)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_sandbox_live.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/web/live.py tests/test_sandbox_live.py
git commit -m "feat: live subscription SSE stream for sandbox demo"
```

---

### Task 2: Rewire the sandbox `/subscribe` route

**Files:**
- Modify: `src/web/routes.py` (replace `subscribe_to_updates`, lines ~278-375; remove `from src.core.models import AgentRegistration` at line 10)
- Modify: `src/web/templates/sandbox.html` (remove the orphaned `EventSource('/sandbox/subscribe?...')` live block)
- Test: `tests/test_sandbox_demo_routes.py` (new file; add the route test here)

**Interfaces:**
- Consumes: `stream_subscription_updates` from Task 1.
- Produces: `GET /subscribe?project_id=<str>&need=<str>` returning a `StreamingResponse` with `media_type="text/event-stream"`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_sandbox_demo_routes.py
import types

import pytest

from src.core.context_engine import ContextEngine
from src.web.routes import subscribe_to_updates


def _request_with(engine):
    return types.SimpleNamespace(app=types.SimpleNamespace(state=types.SimpleNamespace(context_engine=engine)))


@pytest.mark.asyncio
async def test_subscribe_returns_event_stream(db, redis):
    engine = ContextEngine(db=db, redis=redis, similarity_threshold=0.1, max_matches=10)
    await engine.initialize()

    resp = await subscribe_to_updates(_request_with(engine), project_id="p", need="database connection settings")

    assert resp.media_type == "text/event-stream"
    assert resp.headers["Cache-Control"] == "no-cache"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_sandbox_demo_routes.py::test_subscribe_returns_event_stream -v`
Expected: FAIL — the current `subscribe_to_updates` requires `data_needs` and `session_id` params (TypeError on the call), or asserts fail.

- [ ] **Step 3: Rewrite the route**

In `src/web/routes.py`, delete the `from src.core.models import AgentRegistration` import (line 10) and replace the entire `subscribe_to_updates` function (the `@router.get("/subscribe")` block) with:

```python
@router.get("/subscribe")
async def subscribe_to_updates(
    request: Request,
    project_id: str = Query(...),
    need: str = Query(...),
):
    """Stream a natural-language need as a live-updating context bundle over SSE.

    Backed by an ephemeral Subscription; the browser is a parallel consumer of the
    same reconcile pipeline the MCP bridge uses.
    """
    engine = request.app.state.context_engine
    return StreamingResponse(
        stream_subscription_updates(engine, project_id, need),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # disable nginx buffering
        },
    )
```

Add the import near the top of the file (with the other `src.web`/`src.core` imports):

```python
from src.web.live import stream_subscription_updates
```

- [ ] **Step 4: Remove the orphaned live block in `sandbox.html`**

Open `src/web/templates/sandbox.html`, find the JavaScript block that constructs `new EventSource('/sandbox/subscribe?...')` (it passes `data_needs`/`session_id`) and its associated live-update DOM/markup, and delete that block. Leave the query/test-mode form (`hx-post="/sandbox/query"`) and everything else untouched. If a "live updates" UI section header exists only to host that block, remove it too.

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_sandbox_demo_routes.py::test_subscribe_returns_event_stream tests/test_sandbox_live.py -v`
Expected: PASS. Then `pytest -q` to confirm no other test referenced the old signature.
Expected: no new failures (6 pre-existing `sdk/python` failures are unrelated and may be ignored).

- [ ] **Step 6: Commit**

```bash
git add src/web/routes.py src/web/templates/sandbox.html tests/test_sandbox_demo_routes.py
git commit -m "feat: rewire sandbox /subscribe to ephemeral subscriptions"
```

---

### Task 3: Idempotent demo seed

**Files:**
- Create: `src/web/demo_seed.py`
- Test: `tests/test_demo_seed.py`

**Interfaces:**
- Consumes: `engine.semantic_matcher.get_registered_data(project_id) -> list[str]`, `engine.publish_data(DataPublishEvent) -> str`.
- Produces: `DEMO_PROJECT_ID = "sandbox-demo"`, `DEMO_NEED = "database connection settings"`, `DEMO_ITEMS: list[tuple[str, dict]]`, and `async def ensure_demo_seed(engine) -> None` (idempotent).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_demo_seed.py
import pytest

from src.core.context_engine import ContextEngine
from src.web.demo_seed import DEMO_PROJECT_ID, ensure_demo_seed


@pytest.mark.asyncio
async def test_seed_is_idempotent(db, redis):
    engine = ContextEngine(db=db, redis=redis, similarity_threshold=0.1, max_matches=10)
    await engine.initialize()

    await ensure_demo_seed(engine)
    keys1 = await engine.semantic_matcher.get_registered_data(DEMO_PROJECT_ID)
    assert set(keys1) >= {"db_config", "api_config", "cache_config"}

    # Second call must not raise and must not duplicate keys.
    await ensure_demo_seed(engine)
    keys2 = await engine.semantic_matcher.get_registered_data(DEMO_PROJECT_ID)
    assert sorted(keys2) == sorted(keys1)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_demo_seed.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.web.demo_seed'`

- [ ] **Step 3: Write the implementation**

```python
# src/web/demo_seed.py
"""Idempotent seed data for the sandbox live demo."""
from __future__ import annotations

from src.core.models import DataPublishEvent

DEMO_PROJECT_ID = "sandbox-demo"
DEMO_NEED = "database connection settings"

# (data_key, data) — each item's "purpose" is what the semantic need matches against.
DEMO_ITEMS: list[tuple[str, dict]] = [
    ("db_config", {
        "host": "localhost", "port": 5432, "database": "app",
        "purpose": "primary database connection settings",
    }),
    ("api_config", {
        "base_url": "https://api.example.com", "timeout_seconds": 30,
        "purpose": "external API client configuration",
    }),
    ("cache_config", {
        "backend": "redis", "ttl_seconds": 300,
        "purpose": "cache layer settings",
    }),
]


async def ensure_demo_seed(engine) -> None:
    """Publish the demo project's data if it isn't already present. Safe to call repeatedly."""
    existing = await engine.semantic_matcher.get_registered_data(DEMO_PROJECT_ID)
    if existing:
        return
    for data_key, data in DEMO_ITEMS:
        await engine.publish_data(DataPublishEvent(
            project_id=DEMO_PROJECT_ID, data_key=data_key, data=data, data_format="json",
        ))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_demo_seed.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/web/demo_seed.py tests/test_demo_seed.py
git commit -m "feat: idempotent sandbox demo seed"
```

---

### Task 4: Demo publish route

**Files:**
- Modify: `src/web/routes.py` (add `demo_publish` handler)
- Test: `tests/test_sandbox_demo_routes.py` (add to the file from Task 2)

**Interfaces:**
- Consumes: `engine.publish_data(DataPublishEvent) -> str`, `engine.semantic_matcher.get_registered_data(project_id) -> list[str]`.
- Produces: `POST /demo/publish` with form fields `project_id`, `data_key`, `data` (string), `data_format` (default `"json"`); returns JSON `{"status": "ok", "project_id": ..., "data_key": ...}`. Handler name `demo_publish`.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_sandbox_demo_routes.py
import json as _json

from src.web.routes import demo_publish


@pytest.mark.asyncio
async def test_demo_publish_publishes_data(db, redis):
    engine = ContextEngine(db=db, redis=redis, similarity_threshold=0.1, max_matches=10)
    await engine.initialize()

    result = await demo_publish(
        _request_with(engine),
        project_id="sandbox-demo",
        data_key="db_config",
        data=_json.dumps({"host": "db.internal", "purpose": "primary database connection settings"}),
        data_format="json",
    )

    assert result["status"] == "ok"
    assert result["data_key"] == "db_config"
    keys = await engine.semantic_matcher.get_registered_data("sandbox-demo")
    assert "db_config" in keys
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_sandbox_demo_routes.py::test_demo_publish_publishes_data -v`
Expected: FAIL with `ImportError: cannot import name 'demo_publish'`

- [ ] **Step 3: Add the handler**

In `src/web/routes.py`, add (near the other route handlers):

```python
@router.post("/demo/publish")
async def demo_publish(
    request: Request,
    project_id: str = Form(...),
    data_key: str = Form(...),
    data: str = Form(...),
    data_format: str = Form("json"),
):
    """Publish a changed value for the demo project. Reconcile fires inline, which the
    open SSE stream turns into a live context-panel update."""
    engine = request.app.state.context_engine
    if data_format == "json":
        try:
            parsed = json.loads(data)
        except json.JSONDecodeError:
            parsed = data  # fall back to raw text if the field isn't valid JSON
    else:
        parsed = data
    await engine.publish_data(DataPublishEvent(
        project_id=project_id, data_key=data_key, data=parsed, data_format=data_format,
    ))
    return {"status": "ok", "project_id": project_id, "data_key": data_key}
```

Add `from src.core.models import DataPublishEvent` to the imports if not already present (it is not — the old `AgentRegistration` import was removed in Task 2; add this one).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_sandbox_demo_routes.py::test_demo_publish_publishes_data -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/web/routes.py tests/test_sandbox_demo_routes.py
git commit -m "feat: sandbox demo publish route"
```

---

### Task 5: Demo page route + split-screen template

**Files:**
- Modify: `src/web/routes.py` (add `sandbox_demo` handler)
- Create: `src/web/templates/demo.html`
- Test: `tests/test_sandbox_demo_routes.py` (add to the file)

**Interfaces:**
- Consumes: `ensure_demo_seed`, `DEMO_PROJECT_ID`, `DEMO_NEED`, `DEMO_ITEMS` from Task 3; `stream_subscription_updates` endpoint (`/sandbox/subscribe`) from Task 2; `/sandbox/demo/publish` from Task 4.
- Produces: `GET /demo` (handler `sandbox_demo`) returning a `TemplateResponse` for `demo.html` with context `{"request", "project_id", "need", "items"}` where `items` is the list of demo data keys.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_sandbox_demo_routes.py
from pathlib import Path

from src.web.demo_seed import DEMO_NEED, DEMO_PROJECT_ID
from src.web.routes import sandbox_demo


@pytest.mark.asyncio
async def test_demo_route_seeds_and_renders(db, redis):
    engine = ContextEngine(db=db, redis=redis, similarity_threshold=0.1, max_matches=10)
    await engine.initialize()

    resp = await sandbox_demo(_request_with(engine))

    assert resp.template.name == "demo.html"
    assert resp.context["need"] == DEMO_NEED
    assert resp.context["project_id"] == DEMO_PROJECT_ID
    assert "db_config" in resp.context["items"]
    # Seed ran as a side effect of rendering the page.
    keys = await engine.semantic_matcher.get_registered_data(DEMO_PROJECT_ID)
    assert "db_config" in keys


def test_demo_template_has_live_wiring():
    html = (Path("src/web/templates/demo.html")).read_text()
    assert "EventSource" in html
    assert "/sandbox/subscribe" in html
    assert "/sandbox/demo/publish" in html
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_sandbox_demo_routes.py::test_demo_route_seeds_and_renders tests/test_sandbox_demo_routes.py::test_demo_template_has_live_wiring -v`
Expected: FAIL — `ImportError: cannot import name 'sandbox_demo'` and `FileNotFoundError` for the template.

- [ ] **Step 3: Add the page route**

In `src/web/routes.py`, add the import and handler:

```python
from src.web.demo_seed import DEMO_ITEMS, DEMO_NEED, DEMO_PROJECT_ID, ensure_demo_seed
```

```python
@router.get("/demo", response_class=HTMLResponse)
async def sandbox_demo(request: Request):
    """Pre-seeded split-screen live demo: a need on the left updates itself when data is
    published on the right."""
    engine = request.app.state.context_engine
    await ensure_demo_seed(engine)
    return templates.TemplateResponse(
        "demo.html",
        {
            "request": request,
            "project_id": DEMO_PROJECT_ID,
            "need": DEMO_NEED,
            "items": [key for key, _ in DEMO_ITEMS],
        },
    )
```

- [ ] **Step 4: Create the template**

Create `src/web/templates/demo.html`:

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>Contex — context that updates itself</title>
  <style>
    body { font-family: system-ui, sans-serif; margin: 0; background: #0f1117; color: #e6e6e6; }
    header { padding: 16px 24px; border-bottom: 1px solid #23262f; }
    header h1 { font-size: 18px; margin: 0; }
    header p { margin: 4px 0 0; color: #8b93a7; font-size: 13px; }
    .split { display: flex; gap: 1px; background: #23262f; min-height: calc(100vh - 66px); }
    .pane { flex: 1; background: #0f1117; padding: 20px 24px; }
    .pane h2 { font-size: 13px; text-transform: uppercase; letter-spacing: .06em; color: #8b93a7; margin: 0 0 12px; }
    input, textarea, select, button { font: inherit; }
    #need { width: 100%; padding: 10px 12px; background: #171a22; border: 1px solid #2b2f3a;
            border-radius: 8px; color: #e6e6e6; }
    .item { border: 1px solid #2b2f3a; border-radius: 8px; padding: 10px 12px; margin: 10px 0;
            background: #171a22; transition: background .1s ease, border-color .1s ease; }
    .item .key { font-weight: 600; color: #7cc5ff; }
    .item pre { margin: 6px 0 0; white-space: pre-wrap; font-size: 12px; color: #c7ccd8; }
    .flash { animation: flash 1.2s ease; }
    @keyframes flash { 0% { background: #14351f; border-color: #2f9e57; } 100% { background: #171a22; } }
    label { display: block; margin: 10px 0 4px; color: #8b93a7; font-size: 12px; }
    #pubKey, #pubData { width: 100%; padding: 8px 10px; background: #171a22;
                        border: 1px solid #2b2f3a; border-radius: 8px; color: #e6e6e6; }
    #pubData { min-height: 120px; font-family: ui-monospace, monospace; }
    #publish { margin-top: 12px; padding: 9px 16px; background: #2f9e57; border: none;
               border-radius: 8px; color: #fff; cursor: pointer; }
  </style>
</head>
<body>
  <header>
    <h1>Contex — context that updates itself</h1>
    <p>Subscribe to a meaning in plain English. Publish a change and watch it stay current — no refresh.</p>
  </header>

  <div class="split">
    <section class="pane">
      <h2>The need (live)</h2>
      <input id="need" value="{{ need }}" readonly />
      <div id="context"><p style="color:#8b93a7">Connecting…</p></div>
    </section>

    <section class="pane">
      <h2>Publish a change</h2>
      <label for="pubKey">Item</label>
      <select id="pubKey">
        {% for key in items %}<option value="{{ key }}">{{ key }}</option>{% endfor %}
      </select>
      <label for="pubData">New value (JSON)</label>
      <textarea id="pubData">{"host": "db.internal", "port": 6543, "purpose": "primary database connection settings"}</textarea>
      <button id="publish">Publish</button>
    </section>
  </div>

  <script>
    const projectId = {{ project_id | tojson }};
    const need = {{ need | tojson }};
    const contextEl = document.getElementById("context");
    let lastByKey = {};

    function flatten(bundle) {
      // bundle is { need: [ {data_key, similarity, data, ...}, ... ], ... }
      const out = [];
      for (const matches of Object.values(bundle)) {
        for (const m of matches) out.push(m);
      }
      return out;
    }

    function render(bundle) {
      const items = flatten(bundle);
      const nextByKey = {};
      contextEl.innerHTML = "";
      if (!items.length) {
        contextEl.innerHTML = '<p style="color:#8b93a7">No matching context yet.</p>';
      }
      for (const m of items) {
        const serialized = JSON.stringify(m.data);
        nextByKey[m.data_key] = serialized;
        const changed = lastByKey[m.data_key] !== undefined && lastByKey[m.data_key] !== serialized;
        const div = document.createElement("div");
        div.className = "item" + (changed ? " flash" : "");
        div.innerHTML =
          '<span class="key"></span><pre></pre>';
        div.querySelector(".key").textContent = m.data_key;
        div.querySelector("pre").textContent = JSON.stringify(m.data, null, 2);
        contextEl.appendChild(div);
      }
      lastByKey = nextByKey;
    }

    const url = "/sandbox/subscribe?project_id=" + encodeURIComponent(projectId) +
                "&need=" + encodeURIComponent(need);
    const es = new EventSource(url);
    es.onmessage = (e) => {
      const payload = JSON.parse(e.data);
      if (payload.type === "bundle") render(payload.bundle);
    };

    document.getElementById("publish").addEventListener("click", async () => {
      const body = new URLSearchParams();
      body.set("project_id", projectId);
      body.set("data_key", document.getElementById("pubKey").value);
      body.set("data", document.getElementById("pubData").value);
      body.set("data_format", "json");
      await fetch("/sandbox/demo/publish", { method: "POST", body });
    });
  </script>
</body>
</html>
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_sandbox_demo_routes.py -v`
Expected: PASS (all four tests in the file).

- [ ] **Step 6: Commit**

```bash
git add src/web/routes.py src/web/templates/demo.html tests/test_sandbox_demo_routes.py
git commit -m "feat: split-screen sandbox demo page"
```

---

### Task 6: README GIF + capture instructions

**Files:**
- Create: `docs/demo/capture.md`
- Modify: `README.md`
- Asset (produced by the human partner): `docs/assets/demo.gif`

**Interfaces:**
- Consumes: the running app's `/sandbox/demo` page (Tasks 1-5).
- Produces: a README section embedding `docs/assets/demo.gif`, and `docs/demo/capture.md` with reproducible capture steps.

> **Note for the executor:** capturing the actual GIF requires a screen and a running stack, so the binary `docs/assets/demo.gif` is recorded by the human partner following `docs/demo/capture.md`. This task wires up the README and the instructions; it does not fabricate the binary. Its automated check is that the README references the asset path and the capture doc exists.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_sandbox_demo_routes.py
from pathlib import Path


def test_readme_embeds_demo_gif():
    readme = Path("README.md").read_text()
    assert "docs/assets/demo.gif" in readme
    assert Path("docs/demo/capture.md").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_sandbox_demo_routes.py::test_readme_embeds_demo_gif -v`
Expected: FAIL — the README does not yet reference the asset and `docs/demo/capture.md` does not exist.

- [ ] **Step 3: Write the capture instructions**

Create `docs/demo/capture.md`:

```markdown
# Capturing the sandbox demo GIF

The README GIF shows the core loop: publish a change, watch the subscribed context update itself live.

## Steps
1. Start the stack (Postgres + Redis + app):
   `docker compose up --build` (images build with `--platform linux/amd64`).
2. Open `http://localhost:8000/sandbox/demo`. The context panel populates from the pre-seeded
   `sandbox-demo` project against the need "database connection settings".
3. Start a screen recorder scoped to the browser window (e.g. macOS `Cmd-Shift-5`, or `peek` on Linux).
4. In the right pane, edit the `db_config` JSON (change `host`/`port`) and click **Publish**.
5. Record the `db_config` card in the left pane flashing and showing the new value — no page refresh.
6. Stop recording; export/convert to GIF (e.g. `ffmpeg -i demo.mov -vf "fps=12,scale=960:-1" docs/assets/demo.gif`).
7. Keep it short (5-8s) and under ~3 MB so it renders inline on GitHub.

Save the result as `docs/assets/demo.gif`.
```

- [ ] **Step 4: Wire the README**

Add near the top of `README.md` (below the title/tagline), matching the surrounding markdown style:

```markdown
## Context that updates itself

![Contex live sandbox demo](docs/assets/demo.gif)

Subscribe to a need in plain English; when the underlying data changes, the matched context
re-materializes and is pushed live. Try it: `docker compose up` then open
`/sandbox/demo`. See [docs/demo/capture.md](docs/demo/capture.md) to regenerate this GIF.
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_sandbox_demo_routes.py::test_readme_embeds_demo_gif -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add README.md docs/demo/capture.md tests/test_sandbox_demo_routes.py
git commit -m "docs: embed sandbox demo GIF and capture instructions"
```

- [ ] **Step 7: Hand off GIF capture to the human partner**

Flag that `docs/assets/demo.gif` must be recorded per `docs/demo/capture.md` and added to the repo (the test only checks the README reference and the capture doc, not the binary).

---

## Self-Review

**1. Spec coverage:**
- §2 MCP interface / anti-corruption — respected via Global Constraints (no new `mcp` imports; demo uses SSE). ✓
- §3.1 demo page + idempotent seed → Task 3 (seed) + Task 5 (`GET /demo`). ✓
- §3.2 SSE rewired to a real Subscription (create → initial bundle → Redis-driven updates → delete on disconnect) → Task 1 (stream) + Task 2 (route). ✓
- §3.3 publish panel / `POST /sandbox/demo/publish` → Task 4. ✓
- §3.4 front-end split-screen with flash → Task 5 (`demo.html`). ✓
- §4 data flow — covered end-to-end by Tasks 1-5. ✓
- §5 error handling: ephemeral sub always deleted (Task 1 `finally`); seed idempotent (Task 3); publish JSON fallback (Task 4); reconcile failure not fatal (pre-existing `publish_data` guard, unchanged). ✓
- §6 testing: server-side load-bearing SSE test (Task 1); seed idempotency (Task 3); route/render smoke tests (Tasks 2, 4, 5). Front-end JS not unit-tested — matches spec. ✓
- §7 README GIF → Task 6. ✓
- §8 non-goals respected (no new framework, no browser-as-MCP-client, ephemeral subs, no fuller tool). ✓
- §9 build order matches Task order (stream/route → seed → publish → page → GIF; publish route ordered before the page that uses it). ✓

**2. Placeholder scan:** No TBD/TODO. Every code step has concrete code. Task 6's binary GIF is explicitly a human deliverable with an automated check on the wiring — not a placeholder. ✓

**3. Type consistency:** `stream_subscription_updates(engine, project_id, need, *, top_k, threshold)` defined in Task 1, consumed identically in Task 2. `ensure_demo_seed`/`DEMO_PROJECT_ID`/`DEMO_NEED`/`DEMO_ITEMS` defined in Task 3, consumed in Task 5. `demo_publish` form fields (`project_id`, `data_key`, `data`, `data_format`) match the `demo.html` fetch body in Task 5. SSE frame shape `{"type": "bundle", "bundle", "updated_at"}` produced in Task 1, consumed by `es.onmessage` in Task 5. Bundle iteration (`for matches in bundle.values() for m in matches`) matches Task 1's test and the engine's bundle shape. ✓
