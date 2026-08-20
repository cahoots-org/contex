# Sandbox Watch Mode + Quickstart Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the live-subscription capability into a real feature of the sandbox query editor ("Watch"), delete the bespoke demo page, and make the headline artifact a copy-pasteable two-terminal `curl` quickstart.

**Architecture:** Reuse the already-built `src/web/live.py` (`stream_subscription_updates`) and the rewired `GET /sandbox/subscribe`. The sandbox editor gains a client-side Watch toggle that opens an `EventSource` to that endpoint and renders the live bundle with per-item flash. The standalone `demo.html` / `/sandbox/demo` / `/sandbox/demo/publish` / `demo_seed.py` are removed. The README leads with a `curl`-only producer/consumer walkthrough (`POST /api/v1/data/publish` produces; `curl -N /sandbox/subscribe` consumes).

**Tech Stack:** FastAPI/Starlette 1.6, Alpine.js 3.13.3 + htmx 1.9.10 (already loaded via `base.html`), SSE (`EventSource`), Redis pub/sub, pytest + pytest-asyncio.

## Global Constraints

- Reuse existing machinery only: `src/web/live.py::stream_subscription_updates(engine, project_id, need, *, top_k=10, threshold=0.3)` and `GET /sandbox/subscribe?project_id=&need=`. Add NO new server endpoint for Watch. SSE frame shape is `{"type": "bundle", "bundle": <dict>, "updated_at": <iso|null>}` where `bundle` is `{need: [{data_key, similarity, data, description}, ...]}`.
- The sandbox router is mounted at prefix `/sandbox` (`main.py:396`), so absolute paths `/sandbox/subscribe` and `/sandbox/query` are correct from the browser.
- Ephemeral-subscription cleanup: closing the `EventSource` deletes the subscription server-side (guaranteed by `live.py`'s `finally`). On page navigation/close the browser tears down the connection automatically, so no `beforeunload` handler is required; Watch must still explicitly close the stream on Stop, on one-shot query submit, and on project change so cleanup is immediate.
- Render bundle data into the DOM with `.textContent` only (never `innerHTML` of user data) — the data is user-published; avoid injection.
- Do NOT import the `mcp` SDK anywhere.
- Auth is off by default (`AUTH_ENABLED` defaults to `false`, `main.py:318`); the quickstart uses `curl` with no key.
- Any docker image build uses `--platform linux/amd64`.
- Starlette 1.6 `TemplateResponse` signature is `TemplateResponse(request, name, context)` — the `request` is positional, not a context key.

---

## File Structure

- **Modify `src/web/templates/sandbox.html`** — replace the dead Alpine props left over from the old live block with Watch state; add a Watch toggle button beside the existing "Find Matches"; add `startWatch`/`stopWatch`/`renderWatch` Alpine methods; stop Watch on query submit and project change.
- **Modify `src/web/routes.py`** — migrate `sandbox_home`'s `TemplateResponse` to the Starlette 1.6 signature (so it renders/tests cleanly); remove the `sandbox_demo` and `demo_publish` handlers and their now-unused imports.
- **Modify `src/web/static/css/style.css`** — add `.watch-item` / `.watch-key` styles and a `flash` keyframe.
- **Delete `src/web/templates/demo.html`, `src/web/demo_seed.py`, `tests/test_demo_seed.py`, `tests/test_sandbox_demo_routes.py`.**
- **Create `tests/test_sandbox_watch.py`** — route-render smoke, template-wiring check, and the `/subscribe` route media-type test (migrated from the deleted demo-routes test).
- **Modify `README.md`** — quickstart as headline; the GIF becomes a supporting visual below it.
- **Modify `docs/demo/capture.md`** — repoint capture at Watch mode in `/sandbox`.
- **Create `tests/test_readme_quickstart.py`** — doc-lint: the README references the real producer/consumer endpoints and the capture doc exists.

---

### Task 1: Watch mode in the sandbox query editor

**Files:**
- Modify: `src/web/templates/sandbox.html` (x-data props ~lines 14-18; the query `<form>` ~lines 109-116; the submit button ~lines 208-212; add methods inside the x-data object)
- Modify: `src/web/routes.py` (`sandbox_home` `TemplateResponse` call, ~lines 41-45)
- Modify: `src/web/static/css/style.css` (append styles)
- Create: `tests/test_sandbox_watch.py`

**Interfaces:**
- Consumes: `GET /sandbox/subscribe?project_id=&need=` (SSE, from `src/web/live.py`, already built); its frame shape `{"type":"bundle","bundle":{need:[{data_key,data,...}]},"updated_at":...}`.
- Produces: sandbox editor Watch mode (client-only); `sandbox_home` returning a `TemplateResponse` whose `.template.name == "sandbox.html"`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_sandbox_watch.py
import types
from pathlib import Path

import pytest

from src.core.context_engine import ContextEngine
from src.web.routes import sandbox_home, subscribe_to_updates


def _request_with(engine):
    return types.SimpleNamespace(
        app=types.SimpleNamespace(state=types.SimpleNamespace(context_engine=engine))
    )


@pytest.mark.asyncio
async def test_sandbox_home_renders(db, redis):
    engine = ContextEngine(db=db, redis=redis, similarity_threshold=0.1, max_matches=10)
    await engine.initialize()
    resp = await sandbox_home(_request_with(engine))
    assert resp.template.name == "sandbox.html"


@pytest.mark.asyncio
async def test_subscribe_returns_event_stream(db, redis):
    engine = ContextEngine(db=db, redis=redis, similarity_threshold=0.1, max_matches=10)
    await engine.initialize()
    resp = await subscribe_to_updates(_request_with(engine), project_id="p", need="database connection settings")
    assert resp.media_type == "text/event-stream"
    assert resp.headers["Cache-Control"] == "no-cache"


def test_sandbox_template_has_watch_and_query_wiring():
    html = Path("src/web/templates/sandbox.html").read_text()
    # Live mode wiring:
    assert "EventSource" in html
    assert "/sandbox/subscribe" in html
    assert "startWatch" in html
    # Test mode (one-shot query) still present:
    assert "/sandbox/query" in html
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_sandbox_watch.py -v`
Expected: `test_sandbox_home_renders` fails (old `TemplateResponse("sandbox.html", {...})` signature raises `TypeError: unhashable type: 'dict'` under Starlette 1.6) and `test_sandbox_template_has_watch_and_query_wiring` fails (no `EventSource`/`startWatch` yet). `test_subscribe_returns_event_stream` should pass already.

- [ ] **Step 3: Migrate `sandbox_home` to the Starlette 1.6 TemplateResponse signature**

In `src/web/routes.py`, change the return of `sandbox_home` (currently):

```python
    return templates.TemplateResponse(
        "sandbox.html",
        {
            "request": request,
            "projects": sorted(list(projects)),
        }
    )
```

to:

```python
    return templates.TemplateResponse(
        request,
        "sandbox.html",
        {
            "projects": sorted(list(projects)),
        },
    )
```

- [ ] **Step 4: Replace the dead Alpine props with Watch state**

In `src/web/templates/sandbox.html`, inside the root `x-data="{ ... }"` object, replace this leftover block:

```javascript
    subscribed: false,
    subscribedProject: '',
    dataNeeds: '',
    updates: [],
    eventSource: null,
```

with:

```javascript
    watching: false,
    watchLastByKey: {},
    eventSource: null,
```

- [ ] **Step 5: Add the Watch methods**

Still inside the `x-data` object, add these three methods (e.g. immediately after the existing `toggleFormat() { ... },` method):

```javascript
    startWatch() {
        if (!this.selectedProject || !this.queryText.trim()) {
            alert('Pick a project and enter a need first.');
            return;
        }
        this.stopWatch();
        this.watchLastByKey = {};
        const url = '/sandbox/subscribe?project_id=' + encodeURIComponent(this.selectedProject)
                  + '&need=' + encodeURIComponent(this.queryText);
        this.eventSource = new EventSource(url);
        this.watching = true;
        this.eventSource.onmessage = (e) => {
            const payload = JSON.parse(e.data);
            if (payload.type === 'bundle') this.renderWatch(payload.bundle);
        };
    },
    stopWatch() {
        if (this.eventSource) { this.eventSource.close(); this.eventSource = null; }
        this.watching = false;
    },
    renderWatch(bundle) {
        const results = document.getElementById('results');
        if (!results) return;
        const items = [];
        for (const matches of Object.values(bundle || {})) {
            for (const m of matches) items.push(m);
        }
        const nextByKey = {};
        let html = '<div class="watch-results">';
        if (!items.length) {
            html += '<div class="placeholder"><p>Watching — no matching context yet. '
                 +  'Publish data to this project to see it appear.</p></div>';
        }
        for (const m of items) {
            const serialized = JSON.stringify(m.data);
            nextByKey[m.data_key] = serialized;
            const changed = this.watchLastByKey[m.data_key] !== undefined
                         && this.watchLastByKey[m.data_key] !== serialized;
            html += '<div class="watch-item' + (changed ? ' flash' : '') + '">'
                 +  '<div class="watch-key"></div><pre></pre></div>';
        }
        html += '</div>';
        results.innerHTML = html;
        // Fill user data via textContent (no innerHTML of published data — avoids injection).
        const nodes = results.querySelectorAll('.watch-item');
        items.forEach((m, i) => {
            nodes[i].querySelector('.watch-key').textContent = m.data_key;
            nodes[i].querySelector('pre').textContent = JSON.stringify(m.data, null, 2);
        });
        this.watchLastByKey = nextByKey;
    },
```

- [ ] **Step 6: Add the Watch button and stop-on-submit / stop-on-project-change**

In `sandbox.html`:

(a) Add a Watch toggle button right after the existing submit button (`<button type="submit" class="btn-primary"> ... Find Matches ... </button>`):

```html
            <button type="button" class="btn-secondary" @click="watching ? stopWatch() : startWatch()">
                <span x-show="!watching">Watch</span>
                <span x-show="watching">Stop watching</span>
            </button>
```

(b) On the query `<form>` (the one with `hx-post="/sandbox/query"`), extend the existing `@submit` handler so running a one-shot query stops Watch. Change:

```html
                    @submit="$event.target.querySelector('button').disabled = true"
```

to:

```html
                    @submit="stopWatch(); $event.target.querySelector('button').disabled = true"
```

(c) Find the project selector `<select>` bound to `selectedProject` (it has `x-model="selectedProject"`), and add a change handler so switching projects stops a stale Watch:

```html
                    @change="stopWatch()"
```

(If the project selector is a set of buttons rather than a `<select>`, add `stopWatch()` to whatever handler assigns `selectedProject`.)

- [ ] **Step 7: Add Watch styles**

Append to `src/web/static/css/style.css`:

```css
/* Sandbox Watch (live subscription) results */
.watch-item {
    border: 1px solid #2b2f3a;
    border-radius: 8px;
    padding: 10px 12px;
    margin: 10px 0;
}
.watch-key { font-weight: 600; }
.watch-item pre { margin: 6px 0 0; white-space: pre-wrap; }
.flash { animation: flash 1.2s ease; }
@keyframes flash {
    0% { background: #14351f; }
    100% { background: transparent; }
}
```

- [ ] **Step 8: Run the tests to verify they pass**

Run: `pytest tests/test_sandbox_watch.py -v`
Expected: all 3 tests PASS. (Postgres+Redis via `docker compose up -d postgres redis` if the fixtures need them.)

- [ ] **Step 9: Commit**

```bash
git add src/web/templates/sandbox.html src/web/routes.py src/web/static/css/style.css tests/test_sandbox_watch.py
git commit -m "feat: watch mode in the sandbox query editor"
```

---

### Task 2: Remove the standalone demo surface

**Files:**
- Delete: `src/web/templates/demo.html`, `src/web/demo_seed.py`, `tests/test_demo_seed.py`, `tests/test_sandbox_demo_routes.py`
- Modify: `src/web/routes.py` (remove `sandbox_demo`, `demo_publish`, and their now-unused imports)

**Interfaces:**
- Consumes: nothing new.
- Produces: a routes module with no `/demo` or `/demo/publish` handlers and no `demo_seed` import. `sandbox_home`, `execute_query`, `project_stats`, `get_project_data`, and `subscribe_to_updates` remain.

- [ ] **Step 1: Delete the demo files**

```bash
git rm src/web/templates/demo.html src/web/demo_seed.py tests/test_demo_seed.py tests/test_sandbox_demo_routes.py
```

- [ ] **Step 2: Remove the demo handlers and imports from `routes.py`**

In `src/web/routes.py`:
- Delete the entire `@router.get("/demo", ...)` `sandbox_demo` handler.
- Delete the entire `@router.post("/demo/publish")` `demo_publish` handler.
- Delete the import line `from src.web.demo_seed import DEMO_ITEMS, DEMO_NEED, DEMO_PROJECT_ID, ensure_demo_seed`.
- Delete the import line `from src.core.models import DataPublishEvent` (it was used only by `demo_publish`; verify no other reference remains in the file before removing).

- [ ] **Step 3: Verify nothing else references the removed symbols**

Run:
```bash
grep -rn "demo_seed\|sandbox_demo\|demo_publish\|DEMO_PROJECT_ID\|DEMO_ITEMS\|DEMO_NEED\|/sandbox/demo" src/ tests/ ; echo "exit: $?"
```
Expected: no matches in `src/` or `tests/` (grep prints nothing). If any appear, resolve them before continuing.

- [ ] **Step 4: Run the suite to confirm no breakage**

Run: `pytest -q`
Expected: no failures other than the 6 pre-existing `sdk/python` failures. The Watch tests from Task 1 and `tests/test_sandbox_live.py` still pass; the deleted demo tests are gone.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "refactor: remove standalone sandbox demo page in favor of watch mode"
```

---

### Task 3: README quickstart + capture doc + doc-lint

**Files:**
- Modify: `README.md`
- Modify: `docs/demo/capture.md`
- Create: `tests/test_readme_quickstart.py`

**Interfaces:**
- Consumes: the real endpoints — producer `POST /api/v1/data/publish` (body `DataPublishEvent`: `project_id`, `data_key`, `data`, optional `data_format`), consumer `GET /sandbox/subscribe?project_id=&need=`.
- Produces: a headline quickstart; a doc-lint test guarding the doc↔API link.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_readme_quickstart.py
from pathlib import Path


def test_readme_quickstart_references_real_endpoints():
    readme = Path("README.md").read_text()
    # Producer + consumer endpoints the quickstart is built on:
    assert "/api/v1/data/publish" in readme
    assert "/sandbox/subscribe" in readme
    # Supporting visual still referenced:
    assert "docs/assets/demo.gif" in readme


def test_capture_doc_exists():
    assert Path("docs/demo/capture.md").exists()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_readme_quickstart.py -v`
Expected: `test_readme_quickstart_references_real_endpoints` fails — the README currently references `docs/assets/demo.gif` but not `/api/v1/data/publish` or `/sandbox/subscribe`.

- [ ] **Step 3: Replace the README demo section with the quickstart (headline) + GIF (supporting)**

In `README.md`, replace the existing "## Context that updates itself" section (added in the prior branch, containing the `docs/assets/demo.gif` embed) with the following. Place it near the top, below the title/tagline:

````markdown
## Quickstart — context that updates itself

Two terminals, `curl` only. Auth is off by default, so there's nothing to configure.

```bash
docker compose up          # Postgres + Redis + app on http://localhost:8000
```

**Terminal A — a consumer subscribes to a *need* and streams live context:**

```bash
curl -N "http://localhost:8000/sandbox/subscribe?project_id=quickstart&need=how%20the%20service%20reaches%20its%20datastore"
```

It prints the current matched context, then holds the stream open.

**Terminal B — a producer publishes data (note: no shared words with the need):**

```bash
curl -X POST http://localhost:8000/api/v1/data/publish \
  -H "Content-Type: application/json" \
  -d '{"project_id":"quickstart","data_key":"pg_dsn",
       "data":{"engine":"postgres","host":"db.internal","port":5432,"pool":20}}'
```

Terminal A prints an updated bundle within a second — the consumer never named the
producer, and matched on *meaning* ("datastore") not keywords. Change it again and
watch it stay current:

```bash
curl -X POST http://localhost:8000/api/v1/data/publish \
  -H "Content-Type: application/json" \
  -d '{"project_id":"quickstart","data_key":"pg_dsn",
       "data":{"engine":"postgres","host":"db-2.internal","port":6543,"pool":50}}'
```

Prefer a UI? Open `http://localhost:8000/sandbox`, pick a project, type a need, and hit
**Watch** — the same live subscription, in the query editor.

![Contex Watch mode](docs/assets/demo.gif)

> Production sets `AUTH_ENABLED=true`; the quickstart runs in the default open dev mode.
````

- [ ] **Step 4: Repoint the capture doc at Watch mode**

Replace the contents of `docs/demo/capture.md` with:

```markdown
# Capturing the Watch-mode GIF

The supporting GIF shows the sandbox query editor in **Watch** mode: publish a change, watch the subscribed context update itself.

## Steps
1. Start the stack: `docker compose up` (images build `--platform linux/amd64`).
2. Publish a couple of items so there's something to match, e.g.:
   ```bash
   curl -X POST http://localhost:8000/api/v1/data/publish -H "Content-Type: application/json" \
     -d '{"project_id":"quickstart","data_key":"pg_dsn","data":{"engine":"postgres","host":"db.internal","port":5432}}'
   ```
3. Open `http://localhost:8000/sandbox`, select the `quickstart` project, type a need such as
   *"how the service reaches its datastore"*, and click **Watch**. The results pane populates.
4. Start a screen recorder scoped to the browser window.
5. In another terminal, publish a change to `pg_dsn` (new `host`/`port`). Record the matching
   card in the results pane flashing and showing the new value — no refresh.
6. Stop recording; convert to GIF, e.g.
   `ffmpeg -i demo.mov -vf "fps=12,scale=960:-1" docs/assets/demo.gif`.
7. Keep it short (5-8s) and under ~3 MB so it renders inline on GitHub.

Save the result as `docs/assets/demo.gif`.
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `pytest tests/test_readme_quickstart.py -v`
Expected: both tests PASS.

- [ ] **Step 6: Commit**

```bash
git add README.md docs/demo/capture.md tests/test_readme_quickstart.py
git commit -m "docs: lead with a two-terminal curl quickstart; GIF as supporting visual"
```

---

## Self-Review

**1. Spec coverage:**
- §2.1 Watch mode in the sandbox editor → Task 1. ✓
- §2.2 remove standalone demo surface → Task 2. ✓
- §2.3 README quickstart (headline) → Task 3 Step 3. ✓
- §2.4 capture doc repointed → Task 3 Step 4. ✓
- §3 Watch UI reuses `/sandbox/subscribe`, flatten/render/flash, stop-on-submit/project-change → Task 1 Steps 5-6. ✓
- §4 curl producer/consumer grounded in `POST /api/v1/data/publish` + `/sandbox/subscribe`, auth-off → Task 3 Step 3. ✓
- §5 GIF as supporting visual below quickstart → Task 3 Step 3 (embed after the walkthrough). ✓
- §6 removals + "nothing else imports them" guard → Task 2 Steps 1-3. ✓
- §7 error handling: EventSource close deletes sub (live.py, unchanged); JS closes on stop/submit/project-change → Task 1. Connection teardown on unload noted, no handler needed. ✓
- §8 testing: Watch endpoint already covered by `tests/test_sandbox_live.py`; Watch UI wiring + coexistence smoke (Task 1); doc-lint (Task 3). ✓
- §9 non-goals respected (no new endpoint, no new framework, no subscription-management UI, no MCP work, no auth change). ✓

**2. Placeholder scan:** No TBD/TODO. All code/edits are concrete. The binary `docs/assets/demo.gif` remains a human deliverable (unchanged from before); the doc-lint test checks only the reference, which is present after Step 3.

**3. Type/name consistency:** `startWatch`/`stopWatch`/`renderWatch` and `watching`/`watchLastByKey`/`eventSource` are defined in Task 1 Step 4-5 and referenced by the button/handlers in Step 6 and asserted by the template test in Step 1. `sandbox_home`/`subscribe_to_updates` names match `routes.py`. The `/subscribe` param is `need` (matches the rewired route). The producer body fields (`project_id`, `data_key`, `data`) match `DataPublishEvent`. The doc-lint strings (`/api/v1/data/publish`, `/sandbox/subscribe`, `docs/assets/demo.gif`) all appear in Task 3 Step 3.
