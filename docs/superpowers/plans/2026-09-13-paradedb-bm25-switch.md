# ParadeDB / pg_search BM25 Switch — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.
> **WORKSPACE:** Do NOT create a git worktree. Work on the `paradedb-switch` branch in the main checkout (`/Users/robmiller/Projects/contex`) so files are visible in the editor. (Rob's standing preference — worktrees only for parallel agents.)

**Goal:** Replace Contex's Postgres with ParadeDB (pg_search + pgvector) across local/CI/prod and rewrite the lexical retriever to use pg_search BM25, giving the hybrid retriever a real IDF-weighted lexical ranker feeding the existing RRF fusion.

**Architecture:** pg_search indexes raw text (`description`, `data_original`) with a `USING bm25` index keyed on `embeddings.id`; the retriever queries it with `@@@` + `paradedb.score()`, keeping the `LexicalSearch` interface so `HybridSearchService`/RRF are untouched. The generated `search_text` tsvector (migration 002) is dropped — it was only used by the old lexical path. pgvector is bundled in the ParadeDB image, so the dense side is unaffected.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy async + asyncpg, ParadeDB (`paradedb/paradedb:0.25.9-pg18` — PostgreSQL 18.6, pg_search 0.25.9, pgvector 0.8.4), Alembic, pytest.

**Spec:** `docs/superpowers/specs/2026-09-13-paradedb-bm25-switch-design.md`

## Global Constraints

- **Pinned image `paradedb/paradedb:0.25.9-pg18`** everywhere (docker-compose, CI). ParadeDB is now a hard requirement — no stock-Postgres/pgvector-only fallback path.
- **Verified pg_search 0.25.9 API (use exactly this):**
  - Index: `CREATE INDEX embeddings_bm25 ON embeddings USING bm25 (id, description, data_original, project_id) WITH (key_field = 'id');`
  - Query: `col @@@ 'terms'` — a multi-word string is OR-matched and BM25-ranked; score via `paradedb.score(id)`. `project_id` is in the index so the `WHERE project_id = :pid` filter composes.
- **Tests run against ParadeDB** at `DATABASE_URL="postgresql+asyncpg://contex:contex_password@localhost:5432/contex_test"`. Before running any task's tests, a `paradedb/paradedb:0.25.9-pg18` container must be listening on `localhost:5432` with db `contex_test` (Task 1 sets docker-compose to this image; for the local suite, run that image on 5432 — it replaces the old pgvector container).
- **Full suite via plain shell** (nohup), not a dispatched agent — a watchdog kills suite-running subagents; agents run targeted tests only.
- **Style (Rob):** top-level imports, no meta/"per-task" comments, concise docstrings, don't leak internals in errors.
- **Commits:** small, single-purpose, each CI-green.

## File Structure

- `docker-compose.yml`, `.github/workflows/ci.yml`, `.github/workflows/release.yml` — DB image → ParadeDB (Task 1)
- `README.md` / deploy docs — require ParadeDB (Task 1)
- `alembic/versions/008_pg_search_bm25.py` — new migration (Task 2)
- `src/core/db_models.py` — drop `search_text` column + GIN index (Task 2)
- `src/core/lexical_search.py` — BM25 query (Task 2)
- `tests/test_pgfts_lexical.py` — rewrite for BM25 (Task 2); `tests/test_embedding_search_text.py` — delete (Task 2)

---

## Task 1: Switch all container images to ParadeDB

**Files:**
- Modify: `docker-compose.yml` (postgres service, ~line 3)
- Modify: `.github/workflows/ci.yml` (postgres service, ~line 7)
- Modify: `.github/workflows/release.yml` (postgres service ~line 68; "Setup pgvector extension" step ~line 113)
- Modify: `README.md` (deployment / requirements section)

**Interfaces — Produces:** a ParadeDB Postgres (pg_search + pgvector) at the same host/port/credentials as before, so all existing code/tests keep working unchanged.

- [ ] **Step 1: Point docker-compose at ParadeDB.** In `docker-compose.yml`, change the postgres service image:
```yaml
  postgres:
    image: paradedb/paradedb:0.25.9-pg18
```
Keep `container_name`, `POSTGRES_DB/USER/PASSWORD`, ports, and healthcheck as-is. (The ParadeDB image honors the standard `POSTGRES_*` env vars and exposes 5432.)

- [ ] **Step 2: Point CI at ParadeDB.** In `.github/workflows/ci.yml`, change the `postgres` service image to `paradedb/paradedb:0.25.9-pg18` (keep the `POSTGRES_*` env and health `options`). Do the same in `.github/workflows/release.yml` (the `postgres` service image). In `release.yml`, update the "Setup pgvector extension" step to create both extensions:
```yaml
    - name: Setup extensions
      run: |
        psql -h localhost -U contex -d contex_test -c "CREATE EXTENSION IF NOT EXISTS vector; CREATE EXTENSION IF NOT EXISTS pg_search;"
```

- [ ] **Step 3: Document the requirement.** In `README.md`, update the Postgres/deployment section to state Contex requires **ParadeDB** (`paradedb/paradedb`, which bundles `pg_search` for BM25 + `pgvector` for embeddings) — not stock Postgres. Note the Railway deployment uses the ParadeDB template.

- [ ] **Step 4: Bring up ParadeDB locally and run the existing suite (regression).** Start the new image on 5432 and create the test DB, then run the full suite — ParadeDB is a superset (stock PG + pgvector + pg_search), so everything must still pass with no code changes yet.
```bash
docker compose up -d postgres    # now the paradedb image
# create contex_test if absent:
docker compose exec -T postgres psql -U contex -d contex -c "CREATE DATABASE contex_test" 2>/dev/null || true
export DATABASE_URL="postgresql+asyncpg://contex:contex_password@localhost:5432/contex_test"
python -m pytest tests/ -q     # via plain shell
```
Expected: same pass count as on pgvector (the current lexical path — `plainto_tsquery`/`ts_rank_cd`/`search_text` — still works on ParadeDB).

- [ ] **Step 5: Commit.**
```bash
git add docker-compose.yml .github/workflows/ci.yml .github/workflows/release.yml README.md
git commit -m "build(db): switch Postgres to ParadeDB (pg_search + pgvector)"
```

---

## Task 2: Switch the lexical retriever to pg_search BM25

Atomic task: the migration that drops `search_text` and the query rewrite that stops using it must land together to stay green.

**Files:**
- Create: `alembic/versions/008_pg_search_bm25.py`
- Modify: `src/core/db_models.py` (remove `search_text` mapped_column + its Index)
- Modify: `src/core/lexical_search.py` (BM25 query)
- Modify: `tests/test_pgfts_lexical.py` (rewrite for BM25)
- Delete: `tests/test_embedding_search_text.py`

**Interfaces:**
- Consumes: ParadeDB (Task 1); `embeddings.id` (int PK) as the BM25 `key_field`; text columns `embeddings.description`, `embeddings.data_original`.
- Produces: `PgFtsLexical.search(project_id: str, query: str, top_k: int) -> list[tuple[str, float]]` — signature unchanged, now BM25-ranked. `HybridSearchService` and RRF are untouched.

- [ ] **Step 1: Write the migration** `alembic/versions/008_pg_search_bm25.py` (`revision='008'`, `down_revision='007'`):
```python
"""pg_search BM25 index; drop search_text tsvector

Revision ID: 008
"""
from alembic import op

revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute('CREATE EXTENSION IF NOT EXISTS pg_search')
    # BM25 index over the raw text columns, keyed on the int PK; project_id is
    # included so the per-project WHERE filter is served by the index.
    op.execute(
        "CREATE INDEX embeddings_bm25 ON embeddings "
        "USING bm25 (id, description, data_original, project_id) "
        "WITH (key_field = 'id')"
    )
    # search_text (generated tsvector, migration 002) was only used by the old
    # FTS lexical path, now replaced by pg_search.
    op.drop_index("idx_embeddings_search_text", table_name="embeddings")
    op.drop_column("embeddings", "search_text")


def downgrade() -> None:
    op.execute(
        "ALTER TABLE embeddings ADD COLUMN search_text tsvector "
        "GENERATED ALWAYS AS (to_tsvector('english', "
        "coalesce(description,'') || ' ' || coalesce(data_original,''))) STORED"
    )
    op.create_index("idx_embeddings_search_text", "embeddings", ["search_text"],
                    postgresql_using="gin")
    op.execute("DROP INDEX IF EXISTS embeddings_bm25")
```

- [ ] **Step 2: Remove `search_text` from the ORM** in `src/core/db_models.py` — delete the `search_text = mapped_column(...)` (~line 285) and the `Index("idx_embeddings_search_text", "search_text", postgresql_using="gin")` entry in `__table_args__` (~line 302). Leave the vector column + its HNSW index and everything else intact.

- [ ] **Step 3: Rewrite the failing lexical tests** `tests/test_pgfts_lexical.py` — same `_seed` fixture (two p1 docs: `timeout`/`retry`), driving BM25:
```python
import pytest
from src.core.db_models import Embedding
from src.core.lexical_search import PgFtsLexical


async def _seed(db):
    async with db.session() as session:
        session.add_all([
            Embedding(project_id="p1", data_key="cfg", node_key="timeout",
                      description="request timeout setting",
                      data={}, data_original="SERVICE_TIMEOUT_MS=30000",
                      data_format="text", embedding=[0.0] * 384),
            Embedding(project_id="p1", data_key="cfg", node_key="retry",
                      description="retry policy",
                      data={}, data_original="SERVICE_RETRY_MS=1000",
                      data_format="text", embedding=[0.0] * 384),
        ])
        await session.commit()


@pytest.mark.asyncio
async def test_exact_token_matches(db):
    await _seed(db)
    results = await PgFtsLexical(db).search("p1", "SERVICE_TIMEOUT_MS", top_k=10)
    assert results[0][0] == "timeout"
    assert all(isinstance(s, float) for _, s in results)


@pytest.mark.asyncio
async def test_partial_multi_term_match(db):
    # BM25 OR-matches: a doc containing a subset of terms still matches.
    await _seed(db)
    results = await PgFtsLexical(db).search("p1", "timeout kubernetes ingress", top_k=10)
    assert [k for k, _ in results] == ["timeout"]


@pytest.mark.asyncio
async def test_matches_data_original(db):
    await _seed(db)
    results = await PgFtsLexical(db).search("p1", "SERVICE_RETRY_MS", top_k=10)
    assert results[0][0] == "retry"


@pytest.mark.asyncio
async def test_scoped_to_project(db):
    await _seed(db)
    results = await PgFtsLexical(db).search("other-project", "timeout", top_k=10)
    assert results == []
```
Delete `tests/test_embedding_search_text.py` (the column is gone).

- [ ] **Step 4: Run — expect failures** (migration/index not yet wired, lexical still on `search_text` which the migration dropped):

Run: `python -m pytest tests/test_pgfts_lexical.py -v`
Expected: FAIL.

- [ ] **Step 5: Rewrite `src/core/lexical_search.py`** — replace the query in `PgFtsLexical.search` with the verified BM25 form (keep the `LexicalSearch` Protocol and signature):
```python
        sql = text(
            """
            SELECT node_key, paradedb.score(id) AS score
            FROM embeddings
            WHERE project_id = :project_id
              AND (description @@@ :q OR data_original @@@ :q)
            ORDER BY score DESC
            LIMIT :top_k
            """
        )
        async with self.db.session() as session:
            result = await session.execute(
                sql, {"q": query, "project_id": project_id, "top_k": top_k}
            )
            return [(row.node_key, float(row.score)) for row in result]
```
Update the module/class docstring from "ts_rank_cd + plainto_tsquery" to pg_search BM25. Remove the now-unused OR-tsquery comment block.

- [ ] **Step 6: Run the lexical tests — expect PASS:**

Run: `python -m pytest tests/test_pgfts_lexical.py -v`
Expected: PASS. Also confirm no residual `search_text`: `git grep -n search_text src/` returns nothing.

- [ ] **Step 7: Full suite (regression).** `python -m pytest tests/ -q` via plain shell (conftest migrates-from-empty → runs 001→008 on ParadeDB, creating pg_search + the BM25 index). Expected: green; `test_hybrid_search_service.py` + `test_rank_fusion.py` pass unchanged.

- [ ] **Step 8: Commit.**
```bash
git add alembic/versions/008_pg_search_bm25.py src/core/db_models.py src/core/lexical_search.py tests/test_pgfts_lexical.py
git rm tests/test_embedding_search_text.py
git commit -m "feat(search): pg_search BM25 lexical ranker; drop search_text tsvector (#138)"
```

---

## Self-Review

- **Spec coverage:** migration 008 (ext + BM25 index + drop search_text) → Task 2; db_models → Task 2; lexical BM25 → Task 2; images local/CI → Task 1; prod cutover → out-of-band (Rob does Railway steps post-merge; the app already migrates-from-empty at boot); docs → Task 1; tests → Task 2; validation (SciFact harness) → out-of-band after cutover.
- **Placeholder scan:** none — migration, model, query, and test code are concrete and use the empirically-verified pg_search 0.25.9 syntax.
- **Type consistency:** `PgFtsLexical.search(project_id, query, top_k) -> list[tuple[str,float]]` unchanged; BM25 index `key_field='id'` matches `embeddings.id`; query filters `project_id` (in the index).
- **Ordering:** Task 1 (image) must precede Task 2 (migration needs pg_search present). Within Task 2, the search_text drop and the query rewrite are one atomic commit — no broken intermediate.
- **Out of scope:** BM25 param/tokenizer tuning; min-should-match; `pg_dump` corpus migration (route A) — all deferred per spec.
