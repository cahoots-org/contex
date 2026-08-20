# Sandbox Watch Mode + Quickstart — Design Spec

**Date:** 2026-08-18 · **Revised:** 2026-08-19 (repositioned from a standalone demo page to a real feature + reproducible quickstart)
**Status:** Draft for review
**Parent:** `docs/superpowers/specs/2026-08-18-semantic-subscriptions-design.md` (§4.5 — the sandbox as first-class: test mode + live mode); final increment of the reposition.

---

## 1. What changed and why

The first cut of this increment built a bespoke `/sandbox/demo` page whose only job was to produce a marketing GIF. Two problems surfaced in review:

- It **duplicated** the real sandbox query editor (an operator/developer tool) in a locked-down, non-functional-for-real-work form.
- Its single-actor "edit your own JSON, watch it flash back" loop **hid the actual thesis** (a producer changes data; decoupled consumers subscribed to a *meaning* get the right updated slice) and made semantic matching look like keyword matching.

**How infra tools demo (Redis, Kafka) — the playbook we adopt:** a copy-pasteable **quickstart transcript** as the headline (engineers trust what they can reproduce); a **two-pane producer/consumer** that makes decoupling legible (Kafka `console-producer`/`console-consumer`; Redis `PUBLISH`/`SUBSCRIBE`); **terminal-first**, with any GUI as the secondary operator view.

So this increment ships **live mode as a real capability of the sandbox query editor** ("Watch"), makes the headline artifact a **reproducible README quickstart** (two-terminal, `curl`-only), and keeps a **screen recording of Watch mode** as a supporting visual. An **MCP terminal capture** is a fast-follow.

## 2. Scope

**In:**
1. **Watch mode in the sandbox query editor** (`sandbox.html`): the existing query box gains a "Watch" action alongside "Run". Run = one-shot test query (unchanged). Watch = promote the current `(project_id, need)` to a live subscription and stream updates in place, flashing changed items.
2. **Remove the standalone demo surface**: delete `demo.html`, `GET /sandbox/demo`, `POST /sandbox/demo/publish`, `src/web/demo_seed.py`, and their tests.
3. **README quickstart** (headline): a copy-pasteable, two-terminal producer/consumer walkthrough using only `curl`.
4. **Capture doc** update: repoint `docs/demo/capture.md` at recording the sandbox Watch mode (the supporting GIF), not the deleted page.

**Kept from the prior cut (the engine — no rework):** `src/web/live.py` (`stream_subscription_updates`) and the rewired `GET /sandbox/subscribe`. Watch mode and the quickstart's consumer both ride on these.

**Deferred (fast-follow, its own tiny increment):** an MCP-native terminal capture (an MCP client subscribed to `contex://subscriptions/{id}` receiving `notifications/resources/updated` on publish). Noted, not built here.

## 3. Watch mode in the sandbox editor

The sandbox (`GET /sandbox`, `src/web/routes.py::sandbox_home`, template `sandbox.html`) already has a project selector and a query box that POSTs to `/sandbox/query` (one-shot "test mode"). This task adds "live mode":

- **UI:** next to the existing Run control, add a **Watch** toggle. When engaged, the browser opens `new EventSource("/sandbox/subscribe?project_id=<selected>&need=<query text>")` and renders each streamed bundle into the results area, adding a `flash` class to any item whose serialized `data` changed since the previous frame. Disengaging Watch (or changing project/need, or leaving the page) closes the `EventSource`, which server-side deletes the ephemeral subscription (via `live.py`'s `finally`).
- **Server:** no new endpoint — Watch consumes the existing `GET /sandbox/subscribe` (already backed by `stream_subscription_updates`, already tested). The frame shape stays `{"type": "bundle", "bundle": <dict>, "updated_at": <iso|null>}`.
- **Frontend reuse:** the `flatten()` / `render()` / per-key `flash` diff logic from the deleted `demo.html` is good and moves into `sandbox.html`, wired to the sandbox's existing project selector + query box rather than to a hardcoded need.
- **Relationship to test mode:** Run and Watch express the same `(project_id, need)`. Run answers "what does this need match right now?"; Watch answers "keep that answer current." This is the §4.5 "what you test is what you get" property made visible in one tool.

The result is a genuine operator feature (inspect and then *watch* a need), and the demo GIF becomes a recording of it — not a Potemkin page.

## 4. README quickstart (the headline artifact)

A copy-pasteable, two-terminal producer/consumer walkthrough — the Kafka/Redis move, with `curl` as the only dependency:

- **Bring it up:** `docker compose up` (Postgres + Redis + app; images build `--platform linux/amd64`).
- **Consumer (terminal A):** `curl -N "http://localhost:8000/sandbox/subscribe?project_id=quickstart&need=<a natural-language need>"` — the SSE stream holds open and prints the initial matched bundle, then a fresh `data: {…}` frame **every time the underlying data changes**. This is the console-consumer analogue, and it exercises the real subscription/reconcile pipeline.
- **Producer (terminal B):** `curl -X POST http://localhost:8000/api/v1/data/publish` with a JSON body publishing a data item into project `quickstart`. Terminal A prints an updated bundle within a second — no polling, no refresh.
- **The point, stated in prose:** the producer never named a consumer; the consumer never named the producer. The consumer subscribed to a *meaning*, and Contex keeps that meaning's context current. Choose a need whose wording does **not** lexically overlap the published item's contents, so the match is unmistakably semantic, not `grep`.

**Grounded in the real API (verified):**
- Producer body is `DataPublishEvent` (`src/api/routes.py:363`): `{"project_id", "data_key", "data", "data_format"?}`. `data_format` is optional/auto-detected.
- **Auth is off by default:** `AUTH_ENABLED` defaults to `false` (`main.py:318`) and `POST /api/v1/data/publish` carries no auth dependency, so a stock `docker compose up` leaves both the producer (`/api/v1/data/publish`) and consumer (`/sandbox/subscribe`) endpoints open. The quickstart needs **no API key and no bootstrap step** — it runs as written with `curl` alone. (The doc notes that production sets `AUTH_ENABLED=true`, out of scope here.)

## 5. Supporting visual: Watch-mode recording

`docs/demo/capture.md` is repointed to record the **sandbox Watch mode**: open `/sandbox`, pick a project with data, type a need, engage Watch, then (in another terminal) `curl` a publish and record the results pane updating + flashing. Export a short GIF to `docs/assets/demo.gif`, embedded in the README **below** the quickstart as a supporting visual — not the headline.

## 6. Removals (and what guards against breakage)

Delete: `src/web/templates/demo.html`; the `sandbox_demo` (`GET /demo`) and `demo_publish` (`POST /demo/publish`) handlers in `src/web/routes.py` and their imports; `src/web/demo_seed.py`; and the demo-specific tests in `tests/test_sandbox_demo_routes.py` (`test_demo_publish_publishes_data`, `test_demo_route_seeds_and_renders`, `test_demo_template_has_live_wiring`, `test_readme_embeds_demo_gif`) plus `tests/test_demo_seed.py`.

Keep: `src/web/live.py`, the rewired `GET /sandbox/subscribe`, and `tests/test_sandbox_live.py` (both live-stream tests). Retain `test_subscribe_returns_event_stream` (rename the file if it now only covers `/subscribe`).

Nothing else imports `demo_seed` or the demo routes (they were added this branch), so removal is self-contained.

## 7. Error handling

- Watch mode: closing the `EventSource` must delete the ephemeral subscription — already guaranteed by `live.py`'s `finally` (including the setup-failure path fixed in the prior cut). The sandbox JS must close the stream on toggle-off / project-or-need change / page unload.
- Quickstart consumer: `curl -N` naturally streams; if the app is still starting, the connection simply waits — the doc notes to start the consumer after `docker compose up` reports healthy.

## 8. Testing

- **Watch endpoint:** already covered by `tests/test_sandbox_live.py` (publish→update→cleanup; cleanup-on-setup-failure). No new server endpoint is added.
- **Sandbox Watch UI wiring:** a smoke test that `GET /sandbox` renders `sandbox.html` containing the Watch wiring strings (`EventSource`, `/sandbox/subscribe`) and still contains the existing `/sandbox/query` test-mode form — mirroring the template-string checks used before, so the two modes coexist.
- **Quickstart honesty:** at minimum assert the endpoints the quickstart documents exist and behave — the producer path `POST /api/v1/data/publish` is already covered by existing API tests; the consumer path is `tests/test_sandbox_live.py`. A doc-lint test asserting the README quickstart references `/api/v1/data/publish` and `/sandbox/subscribe` guards against the doc drifting from the real surface.
- Front-end JS is not unit-tested (proportionate).

## 9. Non-goals (YAGNI)

- No standalone demo page or demo-only publish route (removed).
- No new front-end framework — reuse the sandbox's existing htmx/SSE/vanilla-JS.
- No new subscription-management UI (list/edit/delete subscriptions) — Watch is a single live need at a time.
- No MCP client work in this increment (fast-follow).
- No auth redesign — the quickstart documents the existing posture; it does not add or remove auth.

## 10. Build order (for the plan)

1. Add Watch mode to `sandbox.html` (reuse `flatten`/`render`/`flash`, wire to the project selector + query box) + the coexistence smoke test.
2. Remove the standalone demo surface (demo.html, `/sandbox/demo`, `/sandbox/demo/publish`, `demo_seed.py`, their tests).
3. README quickstart (two-terminal `curl` producer/consumer), grounded in the verified `POST /api/v1/data/publish` body + auth reality, + the doc-lint test.
4. Repoint `docs/demo/capture.md` at Watch-mode capture; embed the GIF below the quickstart.
