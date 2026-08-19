# Semantic Subscriptions — Design Spec

**Date:** 2026-08-18
**Status:** Draft for review
**Parent:** `docs/superpowers/specs/2026-08-16-contex-mcp-reposition-design.md` (this refines and re-sequences it)

---

## 1. The idea (the thing worth building)

**Semantic subscriptions: give your agents context that updates itself.**

An agent declares, in plain language, what it needs to know ("our auth config and its constraints"). The relevant context flows to it — and stays current. When reality changes, the agent's context changes under it, pushed in real time, with no polling and no hand-wiring.

Today every agent gets context the same way: it retrieves a snapshot at startup and then operates on stale data until someone rebuilds it. Its context is a *photograph*. Contex makes it a *live feed* — the agent subscribes to a *meaning*, not a table or a topic, and the system keeps that meaning satisfied over time.

Sharpened by MCP: MCP is a *pull* protocol (agents call tools, fetch resources). Contex ships **an MCP resource that updates itself and notifies you** — a standing subscription over semantic content. Almost nobody has used MCP's resource-subscription primitive for live semantic push. That is the hook.

## 2. Why this, why now

- **The problem is validated.** Research (Aug 2026) confirms both halves of the shared-context-distribution problem: (a) *selection* — irrelevant/excess context measurably degrades LLMs (Chroma "Context Rot" across 18 models: focused ~300-token prompts beat full ~113k-token prompts; a single distractor lowers accuracy; Liu et al. "lost in the middle" >30% drop; Stanford 20-doc retrieval 70–75%→55–60%); and (b) *fleets exist* — Salesforce Agentforce ~20k enterprise customers averaging 13 agents each, Microsoft Copilot Studio 230k orgs building agents. Practitioner guidance explicitly names our failure modes (context drift, no freshness guarantees, silos) and our architecture (context broker / pub-sub mesh / MCP delivery).
- **This is an open-source project.** Success = adoption (installs, stars, contributors), not near-term revenue. Adoption is won by a low-friction, delightful interface — not by a scaling engine no self-hoster will stress. Monetization, if ever, is a later hosted version whose value-add is exactly the operationally-hard scale features we defer here (see §9).
- **Boring guts, magical experience.** The engineering underneath is intentionally boring — that is what makes the "context updates itself" experience feel *trustworthy* rather than like demo-ware. Reliability and two-command self-host are the features that make the wonder credible.

## 3. Goals / Non-goals

**Goals**
- An MCP-native adoption surface that delivers live semantic subscriptions end to end.
- **A query sandbox:** test what a natural-language need matches against the current context *without* creating a subscription — the design-time tool for tuning need phrasing before promoting it to a live subscription.
- A developer can self-host in two commands (Postgres + Contex) and *feel* the magic in a live demo within minutes.
- Clean seams (`Matcher`, `reconcile`) so the deferred scale engine and future LLM matching drop in without external rework.

**Non-goals (deliberately deferred — see §9)**
- The async processing spine (queue + workers).
- The symmetric subscription-vector index and two-stage reverse matching.
- Coalescing / backpressure tuning and the load test.
- LLM-based matching (the `Matcher` seam is built; no LLM implementation).
- A2A / federation.

## 4. Architecture

Four units, each with one responsibility and a well-defined interface.

### 4.1 MCP server layer (the adoption surface)
Exposes Contex as an MCP server, reusing the reposition spec §2 model:
- **Resources:** `contex://{project}/subscriptions/{id}` (the live matched bundle for a need) and `contex://{project}/items/{key}` (individual published items). `resources/subscribe` + `notifications/resources/updated` deliver the push.
- **Tools:** `contex_create_subscription(needs, scope)`, `contex_update_subscription`, `contex_delete_subscription`, `contex_query(needs, scope)`, `contex_publish(key, data, format)`.
- Sits over the existing engine and the frozen REST hatch; clients use standard MCP libraries (no bespoke SDK).

**A subscription is a *saved live query.*** `contex_query` runs the exact same `Matcher` path a subscription's `reconcile` uses — it just returns the matched bundle *statelessly*: no persistence, no push, nothing to tear down. This gives two guarantees that matter for adoption: (a) **what you test is what you get** — the sandbox's matches are identical to what the subscription will deliver, because it's the same code path; and (b) **promote-in-place** — once a need's matches look right in the sandbox, `create_subscription` with those same needs turns it into the persistent, live-updating version. Query is the design-time twin; subscription is the run-time twin.

### 4.2 The `reconcile` seam (the heart)
A single, clean operation: **given a change (a published/updated/deleted item, or a new/edited subscription), recompute the affected subscription bundle(s), buffer until fully computed, atomically swap the materialized bundle, then emit `resources/updated` for each changed subscription.**

- Invoked **synchronously, inline on publish**, for now. MCP consumers already receive notifications asynchronously, so "publish means matched" is an *internal* timing detail, not an external contract. This keeps the self-host footprint to two services (no worker process) and — critically — means the async spine is a later *internal* swap behind this same seam, with **no change to the MCP contract or the `Matcher` interface**. The seam is `reconcile`, not a queue.
- **Buffer-until-complete:** the materialized bundle only ever reflects a finished reconcile pass. Agents never see an intermediate state that later churns; one notification per settled change.

### 4.3 The `Matcher` seam
Interface: given a changed item plus its parsed metadata (format, type, structured flag, length) and the set of candidate subscriptions, return match results. Ships with **one implementation: a naive-but-correct matcher** that re-evaluates the affected subscriptions using Plan 1's already-merged `HybridSearchService` (`PgVectorSearch` + `PgFtsLexical` + RRF). The metadata on the interface is what a future LLM/tiered-model matcher will route on — designed in, not built.

"Naive" = correct, not clever: on a change, determine affected subscriptions (initially: all subscriptions in the changed item's project) and recompute each bundle. This is O(subscriptions-in-project) per change and is right-sized for self-host scale. The symmetric index that makes this sublinear is the deferred scale engine (§9).

### 4.4 Subscription + materialized-bundle persistence
A `Subscription` model (id, project, tenant, natural-language needs, delivery/scope config, timestamps) and a materialized **bundle** per subscription (the current matched items + scores + provenance) stored in Postgres, reusing existing schema/migration patterns. The bundle is what `resources/read` returns and what `reconcile` swaps.

### 4.5 The sandbox: test, then watch it update (a first-class deliverable, not an afterthought)
The existing sandbox UI supports two modes, both in scope from day one:
- **Test mode (query sandbox):** type a natural-language need and instantly see what it matches against the current context — no subscription created. Iterate on phrasing until the bundle looks right. This is the everyday design-time loop and the honest way a developer discovers whether the matching is good enough for their data.
- **Live mode (the demo):** promote the tested need to a subscription, then a split view where publishing a change to matching data makes the subscribed context bundle update **live**, on screen, no refresh. This is the README GIF and the growth engine.

Because both modes run the same `Matcher` path, the sandbox is truthful: what you see in test mode is exactly what live mode delivers.

## 5. Data flow

0. **(Design-time, optional)** Developer calls `contex_query(needs=["auth config and constraints"])` → Contex runs the `Matcher` and returns the matched bundle statelessly. They iterate on phrasing, re-querying, until the matches look right. Nothing is persisted.
1. Agent (or developer promoting a tested need) calls `contex_create_subscription(needs=["auth config and constraints"])` → Contex creates the `Subscription`, runs an initial reconcile, materializes the bundle, returns the resource URI.
2. Agent `resources/subscribe`s to that URI; `resources/read` returns the current bundle.
3. A publisher calls `contex_publish(...)` (or REST) → the engine stores the item → **inline `reconcile`** recomputes affected bundles → for each changed bundle, emit `notifications/resources/updated`.
4. Agent receives the (contentless) notification → re-reads the resource → gets the fresh bundle.

## 6. Error handling
- Matcher failure on one subscription → log, skip that subscription's update, do not fail the publish or other subscriptions.
- Subscription whose matches all vanished → materialize an empty bundle + notify (not an error).
- Notification to a dropped MCP client → clean teardown of that subscription channel; server state unaffected.
- Publish latency guard: because reconcile is inline, a publish that fans to many subscriptions is bounded by project size; document this limit and treat exceeding it as the trigger to build the async spine (§9), not a bug to hack around now.

## 7. Testing
- **MCP round-trip (highest-value):** subscribe → publish a matching change → assert `resources/updated` fires and a re-read returns the new bundle. This *is* the product; integration-tested end to end.
- **Query = subscription consistency (the trust property):** for the same needs and same corpus, `contex_query` returns the same matched set as a freshly-reconciled subscription's bundle. This guards "what you test is what you get" against the two paths ever drifting.
- **`reconcile` unit tests:** change → correct set of affected subscriptions recomputed; buffer-until-complete (no partial bundle observable); empty-bundle case.
- **`Matcher` seam contract tests:** backend-agnostic; the naive matcher satisfies the interface the future matchers will.
- **Governance regression:** RBAC/tenancy/audit stay green; governance opt-in doesn't burden the default path.
- **Demo smoke test:** the sandbox live-update path works headlessly.

## 8. Simplicity / self-host DX
- Default deploy: **Postgres + Contex, two commands.** Governance (RBAC, multi-tenancy) opt-in and invisible by default. No worker, no queue, no OpenSearch.
- The whole point is that a developer gets to the "whoa" (live context) with near-zero setup.

## 9. Deferred: the scale engine (the future hosted value-add)
When adoption produces real load, a later plan adds — behind the *unchanged* `reconcile` and `Matcher` seams — the async processing spine (queue + workers), the symmetric subscription-vector index + two-stage reverse matching, coalescing/backpressure, and the load test. This is the operationally-hard surface that a hosted version would monetize (open-core: monetize managed operations and scale, not withheld features). Building it before adoption would be building the upsell before the funnel. The seams here exist precisely so that build is a drop-in, not a rewrite.

## 10. Build order (for the implementation plan)
1. `Subscription` model + materialized-bundle persistence (migration).
2. The `Matcher` seam + naive matcher (reusing Plan 1's `HybridSearchService`).
3. The `reconcile` operation (inline, buffer-until-complete) + its tests.
4. The MCP server layer wired to `reconcile`: resources + resource-subscription push, and tools — including `contex_query` (the stateless `Matcher` call) verified to return the same matches a subscription would.
5. The sandbox: **test mode** (query a need, see matches, iterate) and **live mode** (promote to subscription, watch it update) + README GIF.
6. Two-command self-host polish + governance-opt-in verification.
