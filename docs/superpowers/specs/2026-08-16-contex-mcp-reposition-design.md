# Contex Reposition — Design Spec

**Date:** 2026-08-16
**Status:** Draft for review
**Topic:** Repositioning Contex from a bespoke-API semantic context service into an MCP-native, permissioned, real-time semantic context bus for production multi-agent fleets.

---

## 1. Context and Motivation

### 1.1 Why reposition

Contex today is a self-hosted service where publishers push schema-free data and agents register natural-language "data needs," and the service semantically routes matching context (sentence-transformers + pgvector, optional BM25/OpenSearch hybrid with RRF) with real-time push via Redis pub/sub or webhooks. It also has event sourcing, RBAC/API keys, multi-tenancy, and Prometheus/OTel observability.

A landscape assessment (Aug 2026) found the codebase healthy (~19k LOC, 371 tests, production Helm/k8s) but **strategically stranded**:

- **Agent → context/tool access has standardized on MCP** (~110M monthly SDK downloads, 1000+ integrations, native in every major client and in AWS Bedrock AgentCore Gateway). Contex's bespoke REST interface is off the mainstream integration path, and the codebase has **zero MCP awareness**.
- **Cross-agent communication is standardizing on A2A** (v1.0, April 2026, Linux Foundation, 150+ orgs).
- **The agent memory layer is commoditized** by well-funded incumbents (Mem0 — 41k stars, 14M downloads, AWS's exclusive memory provider; plus Zep, Letta, LangMem, Cognee, Supermemory, Redis Agent Memory Server).

However, one gap remains genuinely open: **role/permission-aware shared context with real-time push to long-running agents.** "Agent-aware shared memory" with role/trust-conditioned access is described in the literature as a *future direction*, not current practice. MCP and A2A are fundamentally pull / request-response; memory layers are CRUD/pull. Nobody mainstream does **push-on-change semantic subscription** to a fleet of running agents with governance. That is Contex's defensible sliver — and Contex already has the governance layer (RBAC, multi-tenancy, event sourcing, OTel) that this audience requires and that the memory incumbents lack.

### 1.2 Goal and target adopter

- **Goal:** real external adoption. Success = teams actually install and connect. This makes MCP-native and low-friction non-negotiable.
- **Primary adopter (the wedge):** agent platform / infra teams running fleets of long-lived agents in production, who need permissioned, real-time shared context with audit trails. Fewest adopters, deepest need, closest to Contex's existing feature set.
- **Positioning:** *the permissioned, real-time, auditable semantic context bus for production multi-agent fleets.*

### 1.3 Non-goals (deliberate deferrals)

- **A2A federation / service sharding.** Appealing long-term (a mesh of nodes each owning a context slice, or edge-distributed nodes), and A2A is a good fit as the *node-to-node* fabric. But it is a scaling answer to a problem we are far from having, and A2A only solves node discovery/delegation — not the hard parts (partitioning, cross-shard RRF fusion, rebalancing). **Deferred.** Optionality is preserved cheaply by giving each node a stable identity + capability descriptor at startup (see §3.5); zero federation mechanics are built now.
- **LLM-based matching as default.** The literature flags embedding-only matching as weak at semantic subscription (vocabulary/modality gaps). We address this with a high-recall candidate stage + a *pluggable* precise stage where an LLM reranker can drop in later (§2.3, §5.4). We do **not** add an external LLM dependency to the default path — it cuts against self-hosted simplicity.
- **A bespoke MCP client SDK.** MCP clients use standard client libraries; there is nothing Contex-specific to install (§3.3).

---

## 2. The MCP Interface (subscription-as-resource)

MCP's three primitives map cleanly onto Contex, and two design commitments — *first-class subscription* and *native push* — collapse into a single construct.

### 2.1 Resources

- `contex://{tenant}/{project}/subscriptions/{id}` — a resource whose content is the **current matched context bundle** for a registered need. This is the primary object. Reading it returns the pull; subscribing to it (via MCP `resources/subscribe`) yields the push.
- `contex://{tenant}/{project}/items/{key}` — individual published context items, for direct reads.

### 2.2 Tools (model-invoked actions)

- `contex_create_subscription(needs: [natural language], scope)` — runs the semantic match, creates a persistent server-side subscription object, returns its resource URI.
- `contex_update_subscription(id, ...)` / `contex_delete_subscription(id)` — mutate the first-class object. Future filters / TTL / delivery-mode land here as additive params.
- `contex_query(needs, scope)` — ad-hoc one-shot semantic pull, no subscription created.
- `contex_publish(key, data, format)` — write/update context (also available on the frozen REST hatch for service accounts).

### 2.3 Prompts

None at launch (YAGNI).

### 2.4 Governance

Existing RBAC roles and tenant isolation map onto **which resources and tools a given MCP connection can see or call**. MCP's auth handshake gates the connection; Contex's RBAC gates the surface. This is largely already built and is the differentiator vs. Mem0 / agent-bus.

### 2.5 Subscription-as-resource lifecycle

1. **Create.** Agent calls `contex_create_subscription(...)`; Contex returns a URI. The URI *is* the subscription.
2. **Read = pull.** `resources/read` returns the current matched bundle (matching items with data, keys, scores, provenance).
3. **Subscribe = watch.** `resources/subscribe` on the URI registers interest.
4. **Change → notify = push.** When a publish changes the bundle, Contex emits `notifications/resources/updated` for the URI. MCP notifications are deliberately contentless ("come re-read"), which fits Contex exactly.
5. **Re-read.** Agent re-reads to get the fresh bundle.

**Why this shape:**
- Identity, pull, and push are one object — no drift between "registered for" and "notified about."
- Churn-resistant: future features are params/fields on the object, not new subsystems.
- Survives reconnects: durable server-side object with a stable name; a restarted agent re-subscribes to the same URI and resumes.
- Rides standard rails: any compliant MCP client already speaks read + subscribe; nothing Contex-specific to learn or install.

Redis pub/sub moves from a client-facing interface to **internal plumbing** behind the notification layer; clients never touch it.

---

## 3. Engine, Migration, and Frozen Interfaces

### 3.1 What is kept

RBAC, multi-tenancy, event sourcing/audit, OTel/Prometheus observability — the governance layer that makes the reposition defensible. Already built and tested.

### 3.2 Search: pgvector + Postgres-FTS hybrid by default

The data on an agent-fleet context bus is disproportionately **exact-token-heavy** (config keys, code symbols, error codes, IDs, internal codenames) — precisely where pure vector search is weakest and lexical (BM25-style) search excels. Therefore **hybrid is the default, not opt-in.**

- **Default backend:** pgvector (vectors) + Postgres native FTS `tsvector`/`tsquery` (lexical) + backend-agnostic RRF fusion — all in **one database, two services total** (Postgres + Redis).
- **OpenSearch:** **removed** from the codebase now (it caused the deployment thrash and a required third stateful service). Re-addable later as a lexical/scale backend **only** — never again as a vector store.
- **Contingency:** the PG-FTS-good-enough assumption is validated by the golden eval (§5.1) before this is final; OpenSearch-default is the documented fallback if PG-FTS fails the eval.

### 3.3 The `LexicalSearch` seam (makes OpenSearch re-addable cheaply)

Rip OpenSearch out **through** an abstraction, not around it:

- Introduce a `LexicalSearch` interface with one implementation, `PgFtsLexical`.
- Keep RRF fusion backend-agnostic (it operates on ranked ID lists).
- Build the "on publish, update the lexical index" hook once (PG-FTS needs it to maintain `tsvector` anyway).

Re-adding OpenSearch later then = write `OpenSearchLexical` implementing the interface + wire its indexing into the existing hook + a config flag. Days, not weeks, with zero risk to the matcher's core flow. The old `RankFusionSearch` (currently `src/core/hybrid_search.py`) stays in git history as a reference for connection setup / BM25 query construction.

**Coupling note:** current OpenSearch usage is two things — the self-contained `hybrid_search.py` module (deletes cleanly) and the deeply-woven `VECTOR_STORE == "opensearch"` branches in `semantic_matcher.py` (the Railway "OpenSearch-only vector store" hack, ~35 guard clauses). The latter is deleted along with its branches, keeping the pgvector `else`-path. Banning OpenSearch-as-vector-store is what guarantees future re-adds stay clean.

### 3.4 Matcher seam

Refactor the matcher into a pluggable stage so an optional LLM-rerank can drop into the precise stage later (§5.4) with no interface change. Default remains the embedding pipeline (fast, cheap, self-hosted).

### 3.5 Node identity (A2A optionality, cheap)

Each Contex node gets a stable identity + capability descriptor at startup. Costs almost nothing, doubles as external discoverability, and is the single seam that keeps the A2A-federation door open without building any of it.

### 3.6 Frozen REST + SDK

- **Frozen v1 REST endpoints** (current contract, no new features): `publish`, `query`, auth/key management, `health`/`metrics`, `export`/`import`, and (for back-compat) `agents/register`. This is the escape hatch for service-account publishers and programmatic callers. New capability only ever lands on MCP.
- **One intentional breaking change:** drop the pre-v1 legacy `/api/*` endpoints (already deprecated). Everything works on `/api/v1`.
- **Python SDK (`contex-python`):** maintenance-mode, stays published as the REST wrapper, docs point new users to MCP. No new MCP SDK is built.

### 3.7 Data migration

- **Default-path change:** a single Alembic migration adds a generated `tsvector` column + GIN index and backfills existing rows. Event-sourcing data untouched.
- **OpenSearch-only-mode deployments** (the Railway hack): no in-place migration (vectors live in OpenSearch). **Replay the event log** to re-publish into pgvector + FTS — a one-time re-index, no data loss. Realistically only affects our own deployment.
- **Deployment surface shrinks:** `docker-compose.yml`, Helm, and k8s drop the OpenSearch service; quickstart goes from three stateful services to two.

---

## 4. Reverse-Matching Subsystem (the one net-new build)

Everything else is refactor-and-reface; this is the genuinely new engineering, and where the "real-time push at fleet scale" promise is won or lost. **Primary build-and-derisk target — prototype and load-test earliest.**

### 4.1 Problem

Matching runs in two directions:
- **Forward** (need → matching items): standard vector search, used on `resources/read` and `contex_query`.
- **Reverse** (changed item → affected standing subscriptions): the subscription/pub-sub-routing problem. Naive approach (re-run every subscription's query per publish) is O(subscriptions) per write — a non-starter.

### 4.2 Core trick: symmetric reverse vector search

Matching is symmetric (need-embedding vs. item-embedding, cosine above threshold). So maintain a **second vector index over active subscriptions' need-vectors.** On publish, run **one** reverse ANN query with the changed item's embedding against the subscription index → candidate affected subscriptions. Converts O(N) searches into a single query per changed item.

### 4.3 Two-stage matching

1. **Candidate generation (cheap, high-recall, low-precision):**
   - Semantic: reverse ANN search over need-vectors.
   - Lexical: term-overlap between the changed doc's significant tokens and an inverted index of subscription terms (approximates a percolator; see §4.5).
   - Union of both → candidate set. Cast a wide net; false candidates are fine, misses are not.
2. **Precise recompute + diff (bounded):** for each candidate only, recompute its actual bundle (forward query) and diff against its stored last-known bundle. **Notify only on material change.** Expensive forward work is bounded to the candidate set.

Each subscription stores its last-known bundle (small state blob alongside the durable subscription object) to enable the diff. This is also where a future "only notify on material change" filter lives.

### 4.4 Deletes and updates

A bundle also changes when an item *leaves* it. On update, reverse-search both old and new embeddings; on delete, the old embedding. Catches items dropping out, not just joining.

### 4.5 The lexical reverse-match corner

Reverse *lexical* matching ("which subscription queries match this document?") is the classic **percolator** problem. OpenSearch has a native percolate query for exactly this (a real point in the "add OpenSearch back later" column). Postgres FTS has no native percolator, so the default approximates it with the subscription-term inverted index (§4.3). It's coarser, but acceptable because stage 2 recomputes precisely — lexical candidate gen only needs high recall.

### 4.6 Scaling levers

- **Scope partitioning** (biggest lever): reverse-search is confined to the changed item's project/tenant; RBAC scoping partitions further. Candidate index is a slice, not the global set.
- **Coalescing / debounce:** batch reverse-matches over a short window for rapidly-changing hot data; recompute each affected bundle once per window. Prevents notification storms (§5.5 backpressure).
- **Material-change gating:** the stage-2 diff means changes that don't alter a bundle's top-K produce no notification.

### 4.7 Relationship to the LLM-rerank seam

Stage 1 is deliberately high-recall (embeddings ∪ lexical catches vocab-gap cases lexically). Stage 2 enforces precision. The optional **LLM reranker slots into stage 2** — adjudicating "does this item genuinely satisfy this need?" for candidates where precision matters — without touching candidate generation or the MCP interface.

---

## 5. Testing and Evaluation

### 5.1 Golden eval set (load-bearing; gates §3.2)

Before PG-FTS is the committed default, build a representative query set — one cluster per data shape: config keys, code symbols, error codes/IDs, internal codenames (out-of-vocab), semantic prose (runbooks/policies) — with known-correct expected matches. Measure precision/recall three ways: **PG-FTS hybrid** (proposed default), **vector-only** (naive baseline), **old OpenSearch hybrid** (resurrected from git history as reference ceiling). Validates the PG-FTS-good-enough bet and quantifies the future OpenSearch-upgrade delta.

### 5.2 MCP subscription/push end-to-end test (highest-risk behavior)

A client subscribes to a subscription-resource, a publisher changes matching data, the client receives `resources/updated` and re-reads the new bundle. This round trip *is* the product; must be integration-tested, not just unit-tested.

### 5.3 Seam contract tests

`LexicalSearch` interface unit tests (a spec for the future `OpenSearchLexical` adapter). Matcher pluggable-stage tests that are backend-agnostic.

### 5.4 Governance regression guardrails

RBAC, multi-tenancy, event-sourcing/audit tests must stay green throughout the refactor — they are the differentiator. OpenSearch-branch tests are deleted with the code; the rest of the 371-test suite is a regression guard.

### 5.5 Error handling to cover explicitly

- Matcher failure → degrade, don't crash the subscription.
- Subscription whose underlying data was deleted → empty bundle + notification, not an error.
- Notification delivery to a dropped MCP client → clean teardown.
- Backpressure when many subscribers watch hot data → internal fan-out must not amplify (coalescing, §4.6).

---

## 6. Derisking Order

1. **Golden eval (§5.1)** — validates the PG-FTS default before committing to the rip-out. Cheap; do first.
2. **Reverse-matching prototype + load test (§4)** — the net-new subsystem; prove push-at-scale early.
3. **`LexicalSearch` seam + OpenSearch removal (§3.2–3.3)** — through the abstraction.
4. **MCP interface + subscription-as-resource (§2)** — the reface.
5. **Migration, freeze, docs/positioning (§3.6–3.7)** — finalize the adoption surface.

---

## 7. Open Questions / To Confirm During Planning

- Exact tenant/project scoping in resource URIs vs. MCP auth claims.
- Storage choice for per-subscription last-known-bundle state (Redis vs. Postgres).
- Coalescing window default and whether it is per-subscription configurable.
- Whether `contex_publish` should be MCP-exposed at all, or REST-hatch-only for service accounts.
