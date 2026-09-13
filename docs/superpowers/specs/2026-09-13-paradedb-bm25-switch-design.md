# Switch to ParadeDB as the Postgres — real BM25 lexical ranking

**Status:** draft for review
**Date:** 2026-09-13
**Related:** #138 (hybrid degraded to vector-only — AND bug, fixed in #139), the follow-up finding that the OR fix is *necessary but insufficient* (ts_rank_cd over a broad OR has no IDF → floods RRF, recall ties dense at ~40% more tokens).

## Goal

Replace Contex's Postgres with **ParadeDB** (Postgres bundling `pg_search` + pgvector) everywhere — local, CI, and prod — and rewrite the lexical retriever to use **pg_search's BM25** instead of `plainto_tsquery`/`ts_rank_cd`. This gives the hybrid retriever a real IDF-weighted lexical ranker feeding the existing RRF fusion, which is the piece the eval showed is missing.

## Decision (locked with Rob)

- **Commit outright to ParadeDB as *the* Postgres** — not gated on the eval result. ParadeDB is the platform direction (one Postgres for relational + vector + BM25); the recall win is expected upside. No stock-Postgres/pgvector-only fallback path is kept.
- **No data migration needed for prod** — it has no users and was reset to near-empty during the 2026-09-13 migration incident; prod cuts over to a fresh ParadeDB DB and migrates-from-empty. The only corpus worth preserving is the local **SciFact eval index (5,183 docs)**, which is re-indexed by re-running the eval harness against ParadeDB (route B below). Route A (`pg_dump`/`pg_restore`) is documented for any future corpus but not used here.

## Architecture

`pg_search` indexes **raw text** with its own BM25 tokenizer (not a tsvector). So the lexical retriever indexes `embeddings.description` + `embeddings.data_original` via a `USING bm25` index keyed on the existing `embeddings.id` (int PK), and the generated `search_text` tsvector column (migration 002) is dropped — it's used only by the current lexical path. pgvector is bundled in the ParadeDB image, so the `vector(384)` column + HNSW index are unaffected; the dense side and RRF fusion are untouched.

## Components

### 1. Migration `008_pg_search_bm25`
- `CREATE EXTENSION IF NOT EXISTS pg_search;`
- Create the BM25 index: `CREATE INDEX embeddings_bm25 ON embeddings USING bm25 (id, description, data_original) WITH (key_field = 'id');` (exact `WITH` options/tokenizer config verified against the pinned ParadeDB version at implementation).
- Drop the `search_text` generated column and its `idx_embeddings_search_text` GIN index.
- `CREATE EXTENSION vector` stays (migration 001) — bundled in ParadeDB.
- Fresh installs run `001 → 008`; there are no populated DBs to reconcile.

### 2. `src/core/db_models.py`
- Remove the `search_text` `mapped_column` and its `Index(..., postgresql_using="gin")`. The BM25 index is created in the migration (SQLAlchemy doesn't model it), not declared on the ORM class.

### 3. `src/core/lexical_search.py`
- Replace the body of `PgFtsLexical.search` (keep the `LexicalSearch` Protocol and the `search(project_id, query, top_k) -> list[tuple[str, float]]` signature so `HybridSearchService`/RRF are untouched). New query shape:
  ```sql
  SELECT node_key, paradedb.score(id) AS score
    FROM embeddings
   WHERE project_id = :project_id
     AND (description @@@ :q OR data_original @@@ :q)
   ORDER BY score DESC
   LIMIT :top_k
  ```
  (Operator/score-function names — `@@@` vs `|||`, `paradedb.score` vs `pdb.score` — are version-dependent; pinned and verified against the chosen ParadeDB tag during implementation.) Single path; no stock-FTS fallback. Class may be renamed `PgSearchLexical` for accuracy.

### 4. Images / infra (local, CI, prod → ParadeDB)
- `docker-compose.yml`: `pgvector/pgvector:pg16` → `paradedb/paradedb:<pinned tag>`.
- `.github/workflows/ci.yml` + `release.yml`: `ankane/pgvector:latest` → the same pinned `paradedb/paradedb` tag.
- **Pin one `paradedb/paradedb` tag across all three envs**, choosing the PG major to match prod (18) if a matching tag exists; otherwise the latest stable tag (prod is fresh-migrate, so a PG-major difference is not a data-restore concern). The tag's bundled pgvector must support `vector(384)` + HNSW (it does).
- **Prod cutover** (Rob does the Railway steps; app-side config is in this work): deploy the ParadeDB template as the DB service, repoint `contex.DATABASE_URL` to it, drop the old `pgvector` service. Fresh DB → boot `migrate_to_head` runs `001→008`.

### 5. Tests
- CI + local now run against ParadeDB, so the suite exercises pg_search for real (conftest migrates-from-empty; `008` creates the extension + index).
- Rewrite `tests/test_pgfts_lexical.py` for BM25: partial multi-term match still returns the doc, a doc matching more/rarer terms ranks higher, project scoping holds.
- Delete `tests/test_embedding_search_text.py` (the `search_text` column is gone).
- `tests/test_hybrid_search_service.py` + `tests/test_rank_fusion.py` unchanged (interface preserved).

### 6. Docs
- README + deploy docs: Contex now requires ParadeDB (pg_search + pgvector); update the Railway deploy notes (ParadeDB template in place of the pgvector template).

## Error handling / edge cases

- Empty / stopword-only query → BM25 match predicate returns no rows → `[]` (same contract as today).
- pg_search extension absent → migration `008` fails fast at boot (intended: ParadeDB is now required, and this is a clear signal rather than a silent degrade).
- `project_id` filter composes with the BM25 predicate in one query (verified against the pinned version; if the planner needs the filter field in the index, it's added there).

## Testing strategy

Unit/integration via the existing pytest suite against a ParadeDB container (local + CI). End-to-end retrieval quality is validated **out of band** by re-running the SciFact eval harness against ParadeDB (target: recall@10 meaningfully above dense's 0.783, approaching the rank-bm25 sim's ~0.824, at comparable-or-lower token cost). The platform switch stands regardless of that number (per the locked decision), but it is the success signal for the hybrid fix.

## Out of scope

- Tuning BM25 parameters (k1/b) or tokenizer beyond defaults — a follow-up if the eval warrants it.
- min-should-match / query-side relevance tuning — pg_search's BM25 is expected to subsume the need; revisit only if the eval underperforms.
- `pg_dump`/`pg_restore` corpus migration (route A) — documented but unused here.

## Rollout / sequencing

1. Code + migration + image swaps on this branch; suite green against ParadeDB (local + CI).
2. Merge.
3. Prod: stand up ParadeDB template, repoint `contex`, drop old pgvector service, verify clean boot + `alembic_version=008`.
4. Re-run the SciFact harness against ParadeDB → record the number.
