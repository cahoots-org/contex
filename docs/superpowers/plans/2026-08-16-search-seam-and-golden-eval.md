# Search-Backend Seam + Golden Eval Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Introduce a backend-agnostic hybrid-search seam with a Postgres-FTS lexical implementation, and build a golden-eval harness that measures retrieval quality of PG-FTS-hybrid vs. vector-only vs. the existing OpenSearch-hybrid — the gate that decides whether PG-FTS can be the default backend.

**Architecture:** A `LexicalSearch` interface abstracts lexical retrieval; `PgFtsLexical` implements it using a Postgres `tsvector` generated column + `ts_rank_cd`. A pure `rrf_fuse` function performs *true* Reciprocal Rank Fusion (the current `hybrid_search.py` mislabels a score-max union as RRF). A `HybridSearchService` composes a vector ranker + a `LexicalSearch` via `rrf_fuse`. A standalone eval harness (fixture dataset tagged by data-shape, pure metric functions, a runner) produces a comparison report. This plan is plan 1 of 5 (see design spec §6); it must produce a working PG-FTS hybrid search path and a runnable eval, but does NOT remove OpenSearch (that is plan 3, gated on this eval).

**Tech Stack:** Python 3.11, SQLAlchemy 2.0 (async), pgvector, PostgreSQL full-text search (`tsvector`/`ts_rank_cd`), Alembic, pytest + pytest-asyncio, sentence-transformers (`all-MiniLM-L6-v2`, 384-dim).

## Global Constraints

- Design spec: `docs/superpowers/specs/2026-08-16-contex-mcp-reposition-design.md`. This plan implements §3.2, §3.3, and §5.1.
- Vector storage is pgvector only. Do NOT reintroduce or depend on `VECTOR_STORE == "opensearch"`.
- Embedding dimension is 384 (`all-MiniLM-L6-v2`). Vector column: `Vector(384)`.
- The unique document identifier within a project is `Embedding.node_key` (unique per `(project_id, node_key)`). All rankings key on `node_key`.
- DB-dependent tests use the existing `db` fixture in `tests/conftest.py` (real Postgres `contex_test`, pgvector enabled, `Base.metadata.create_all`). They skip automatically if `DATABASE_URL` is unset.
- Do NOT run `git add`/`commit`/`push` on the user's behalf — the user runs commits themselves (global CLAUDE.md). The "Commit" steps below are written for the user/executor to run; an agentic executor should pause at each and let the user commit, OR the user may authorize commits explicitly.
- Follow existing patterns: async SQLAlchemy sessions via `self.db.session()`, `structlog` logging, type hints throughout.

---

### Task 1: True RRF fusion utility

**Files:**
- Create: `src/core/rank_fusion.py`
- Test: `tests/test_rank_fusion.py`

**Interfaces:**
- Consumes: nothing (pure function).
- Produces: `rrf_fuse(rankings: list[list[str]], k: int = 60) -> list[tuple[str, float]]` — takes N ranked lists of doc ids (each ordered best-first), returns doc ids with fused scores, ordered best-first. Score for a doc = `sum(1.0 / (k + rank))` over each list containing it, where `rank` is 1-based position in that list.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_rank_fusion.py
from src.core.rank_fusion import rrf_fuse


def test_doc_in_both_lists_outranks_doc_in_one():
    vector_ranking = ["a", "b", "c"]
    lexical_ranking = ["b", "d", "a"]
    fused = rrf_fuse([vector_ranking, lexical_ranking], k=60)
    ids = [doc_id for doc_id, _ in fused]
    # "a" (ranks 1 and 3) and "b" (ranks 2 and 1) appear in both -> top two
    assert set(ids[:2]) == {"a", "b"}
    # every input doc appears exactly once
    assert sorted(ids) == ["a", "b", "c", "d"]


def test_scores_use_reciprocal_rank_formula():
    fused = dict(rrf_fuse([["a", "b"]], k=60))
    assert fused["a"] == 1.0 / (60 + 1)
    assert fused["b"] == 1.0 / (60 + 2)


def test_empty_input_returns_empty():
    assert rrf_fuse([]) == []
    assert rrf_fuse([[], []]) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_rank_fusion.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.core.rank_fusion'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/core/rank_fusion.py
"""Reciprocal Rank Fusion (RRF) for combining ranked result lists.

Cormack, Clarke & Buettcher (2009): fuse rankings by summing 1/(k+rank),
which is robust to differing score scales across retrievers.
"""
from __future__ import annotations


def rrf_fuse(rankings: list[list[str]], k: int = 60) -> list[tuple[str, float]]:
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda item: item[1], reverse=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_rank_fusion.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/core/rank_fusion.py tests/test_rank_fusion.py
git commit -m "feat: add true reciprocal rank fusion utility"
```

---

### Task 2: `tsvector` column + GIN index on Embedding (model + migration)

**Files:**
- Modify: `src/core/db_models.py` (the `Embedding` class, around lines 230-255)
- Create: `alembic/versions/<rev>_add_embedding_search_text.py` (generate with autogenerate; edit to the content below)
- Test: `tests/test_embedding_search_text.py`

**Interfaces:**
- Consumes: `Embedding` (existing model), `db` fixture.
- Produces: `Embedding.search_text` — a persisted `TSVECTOR` generated column computed as `to_tsvector('english', coalesce(description,'') || ' ' || coalesce(data_original,''))`, with GIN index `idx_embeddings_search_text`. Later tasks query it via `plainto_tsquery`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_embedding_search_text.py
import pytest
from sqlalchemy import select, text
from src.core.db_models import Embedding


@pytest.mark.asyncio
async def test_search_text_is_populated_and_matches(db):
    async with db.session() as session:
        session.add(Embedding(
            project_id="p1", data_key="cfg", node_key="cfg::timeout",
            description="Service request timeout configuration",
            data={"timeout_ms": 30000}, data_original="SERVICE_TIMEOUT_MS=30000",
            data_format="text", embedding=[0.0] * 384,
        ))
        await session.commit()

    async with db.session() as session:
        row = await session.execute(
            select(Embedding.node_key)
            .where(Embedding.project_id == "p1")
            .where(text("search_text @@ plainto_tsquery('english', 'timeout configuration')"))
        )
        assert row.scalar_one() == "cfg::timeout"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_embedding_search_text.py -v`
Expected: FAIL — `search_text` column does not exist (`UndefinedColumn` / `AttributeError`).

- [ ] **Step 3: Add the column to the model**

In `src/core/db_models.py`, add to the imports:

```python
from sqlalchemy import Computed
from sqlalchemy.dialects.postgresql import TSVECTOR
```

In the `Embedding` class, add the column after `embedding` (line ~245) and add the index to `__table_args__`:

```python
    search_text = mapped_column(
        TSVECTOR,
        Computed(
            "to_tsvector('english', coalesce(description,'') || ' ' || coalesce(data_original,''))",
            persisted=True,
        ),
        nullable=True,
    )
```

```python
    __table_args__ = (
        Index("idx_embeddings_project", "project_id"),
        Index("idx_embeddings_project_node_key", "project_id", "node_key", unique=True),
        Index("idx_embeddings_project_data_key", "project_id", "data_key"),
        Index("idx_embeddings_search_text", "search_text", postgresql_using="gin"),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_embedding_search_text.py -v`
Expected: PASS (the `db` fixture's `create_all` builds the generated column + GIN index).

- [ ] **Step 5: Create the Alembic migration for existing deployments**

Generate a revision, then replace its `upgrade`/`downgrade` with:

```python
def upgrade() -> None:
    op.add_column(
        "embeddings",
        sa.Column(
            "search_text",
            postgresql.TSVECTOR(),
            sa.Computed(
                "to_tsvector('english', coalesce(description,'') || ' ' || coalesce(data_original,''))",
                persisted=True,
            ),
            nullable=True,
        ),
    )
    op.create_index(
        "idx_embeddings_search_text", "embeddings", ["search_text"],
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index("idx_embeddings_search_text", table_name="embeddings")
    op.drop_column("embeddings", "search_text")
```

Ensure the migration file imports: `import sqlalchemy as sa` and `from sqlalchemy.dialects import postgresql`.

- [ ] **Step 6: Verify the migration applies cleanly**

Run: `alembic upgrade head && alembic downgrade -1 && alembic upgrade head`
Expected: no errors; column and index created, dropped, recreated.

- [ ] **Step 7: Commit**

```bash
git add src/core/db_models.py alembic/versions/
git commit -m "feat: add tsvector search_text generated column to embeddings"
```

---

### Task 3: `LexicalSearch` interface + `PgFtsLexical` implementation

**Files:**
- Create: `src/core/lexical_search.py`
- Test: `tests/test_pgfts_lexical.py`

**Interfaces:**
- Consumes: `Embedding.search_text` (Task 2), the `db` manager (`DatabaseManager` with `.session()`).
- Produces:
  - `LexicalSearch` (Protocol/ABC) with `async def search(self, project_id: str, query: str, top_k: int) -> list[tuple[str, float]]` returning `(node_key, score)` best-first.
  - `PgFtsLexical(db)` implementing it via `ts_rank_cd` + `plainto_tsquery`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_pgfts_lexical.py
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
async def test_exact_token_ranks_first(db):
    await _seed(db)
    results = await PgFtsLexical(db).search("p1", "SERVICE_TIMEOUT_MS", top_k=10)
    assert results[0][0] == "timeout"
    assert all(isinstance(score, float) for _, score in results)


@pytest.mark.asyncio
async def test_non_matching_query_returns_empty(db):
    await _seed(db)
    results = await PgFtsLexical(db).search("p1", "kubernetes ingress", top_k=10)
    assert results == []


@pytest.mark.asyncio
async def test_scoped_to_project(db):
    await _seed(db)
    results = await PgFtsLexical(db).search("other-project", "timeout", top_k=10)
    assert results == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_pgfts_lexical.py -v`
Expected: FAIL — `No module named 'src.core.lexical_search'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/core/lexical_search.py
"""Lexical (keyword/BM25-style) search behind a backend-agnostic interface.

PgFtsLexical uses Postgres full-text search (ts_rank_cd + plainto_tsquery).
A future OpenSearchLexical can implement the same Protocol without touching
callers (design spec §3.3).
"""
from __future__ import annotations

from typing import Protocol

from sqlalchemy import text


class LexicalSearch(Protocol):
    async def search(
        self, project_id: str, query: str, top_k: int
    ) -> list[tuple[str, float]]:
        """Return (node_key, score) tuples, best match first."""
        ...


class PgFtsLexical:
    """Postgres full-text lexical search over Embedding.search_text."""

    def __init__(self, db) -> None:
        self.db = db

    async def search(
        self, project_id: str, query: str, top_k: int
    ) -> list[tuple[str, float]]:
        sql = text(
            """
            SELECT node_key,
                   ts_rank_cd(search_text, plainto_tsquery('english', :q)) AS score
            FROM embeddings
            WHERE project_id = :project_id
              AND search_text @@ plainto_tsquery('english', :q)
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

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_pgfts_lexical.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/core/lexical_search.py tests/test_pgfts_lexical.py
git commit -m "feat: add LexicalSearch interface and PgFtsLexical implementation"
```

---

### Task 4: Vector ranker adapter

**Files:**
- Create: `src/core/vector_search.py`
- Test: `tests/test_vector_search.py`

**Interfaces:**
- Consumes: `Embedding.embedding` (pgvector), the sentence-transformers model, `db`.
- Produces: `PgVectorSearch(db, model)` with `async def search(self, project_id: str, query: str, top_k: int) -> list[tuple[str, float]]` returning `(node_key, cosine_similarity)` best-first. Mirrors the existing pgvector query in `semantic_matcher.match_agent_needs` (lines 372-399) but returns ranked ids for fusion.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_vector_search.py
import pytest
from sentence_transformers import SentenceTransformer
from src.core.db_models import Embedding
from src.core.vector_search import PgVectorSearch


@pytest.mark.asyncio
async def test_semantically_closest_ranks_first(db):
    model = SentenceTransformer("all-MiniLM-L6-v2")
    async with db.session() as session:
        for key, descr in [("auth", "user authentication and login"),
                           ("billing", "invoice and payment processing")]:
            vec = model.encode(descr).tolist()
            session.add(Embedding(project_id="p1", data_key=key, node_key=key,
                                  description=descr, data={}, data_original=descr,
                                  data_format="text", embedding=vec))
        await session.commit()

    results = await PgVectorSearch(db, model).search("p1", "how do users sign in", top_k=10)
    assert results[0][0] == "auth"
    assert 0.0 <= results[0][1] <= 1.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_vector_search.py -v`
Expected: FAIL — `No module named 'src.core.vector_search'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/core/vector_search.py
"""Vector (semantic) ranker over pgvector, returning ranked node_keys for fusion."""
from __future__ import annotations

from sqlalchemy import select
from src.core.db_models import Embedding


class PgVectorSearch:
    def __init__(self, db, model) -> None:
        self.db = db
        self.model = model

    async def search(
        self, project_id: str, query: str, top_k: int
    ) -> list[tuple[str, float]]:
        query_vec = self.model.encode(query).tolist()
        async with self.db.session() as session:
            result = await session.execute(
                select(
                    Embedding.node_key,
                    (1 - Embedding.embedding.cosine_distance(query_vec)).label("similarity"),
                )
                .where(Embedding.project_id == project_id)
                .order_by(Embedding.embedding.cosine_distance(query_vec))
                .limit(top_k)
            )
            return [(row.node_key, float(row.similarity)) for row in result]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_vector_search.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/core/vector_search.py tests/test_vector_search.py
git commit -m "feat: add pgvector ranker adapter returning ranked node_keys"
```

---

### Task 5: `HybridSearchService` (vector + lexical via RRF)

**Files:**
- Create: `src/core/hybrid_search_service.py`
- Test: `tests/test_hybrid_search_service.py`

**Interfaces:**
- Consumes: `PgVectorSearch` (Task 4), `LexicalSearch`/`PgFtsLexical` (Task 3), `rrf_fuse` (Task 1).
- Produces: `HybridSearchService(vector_search, lexical_search, k=60)` with `async def search(self, project_id: str, query: str, top_k: int) -> list[tuple[str, float]]` returning `(node_key, fused_score)` best-first. Each backend is queried for `top_k` ids; the two rankings are RRF-fused; the top_k fused ids are returned.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_hybrid_search_service.py
import pytest
from src.core.hybrid_search_service import HybridSearchService


class _StubRanker:
    def __init__(self, ranking):
        self._ranking = ranking

    async def search(self, project_id, query, top_k):
        return self._ranking[:top_k]


@pytest.mark.asyncio
async def test_fuses_vector_and_lexical_rankings():
    vector = _StubRanker([("a", 0.9), ("b", 0.8), ("c", 0.7)])
    lexical = _StubRanker([("b", 5.0), ("d", 4.0), ("a", 3.0)])
    service = HybridSearchService(vector, lexical, k=60)
    results = await service.search("p1", "q", top_k=3)
    ids = [doc_id for doc_id, _ in results]
    # a and b appear in both rankings -> top two after fusion
    assert set(ids[:2]) == {"a", "b"}
    assert len(results) == 3


@pytest.mark.asyncio
async def test_handles_one_empty_backend():
    vector = _StubRanker([("a", 0.9), ("b", 0.8)])
    lexical = _StubRanker([])
    service = HybridSearchService(vector, lexical, k=60)
    results = await service.search("p1", "q", top_k=10)
    assert [doc_id for doc_id, _ in results] == ["a", "b"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_hybrid_search_service.py -v`
Expected: FAIL — `No module named 'src.core.hybrid_search_service'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/core/hybrid_search_service.py
"""Backend-agnostic hybrid search: fuse a vector ranker and a lexical ranker via RRF."""
from __future__ import annotations

from src.core.rank_fusion import rrf_fuse


class HybridSearchService:
    def __init__(self, vector_search, lexical_search, k: int = 60) -> None:
        self.vector_search = vector_search
        self.lexical_search = lexical_search
        self.k = k

    async def search(
        self, project_id: str, query: str, top_k: int
    ) -> list[tuple[str, float]]:
        vector_hits = await self.vector_search.search(project_id, query, top_k)
        lexical_hits = await self.lexical_search.search(project_id, query, top_k)
        rankings = [
            [doc_id for doc_id, _ in vector_hits],
            [doc_id for doc_id, _ in lexical_hits],
        ]
        return rrf_fuse(rankings, k=self.k)[:top_k]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_hybrid_search_service.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add src/core/hybrid_search_service.py tests/test_hybrid_search_service.py
git commit -m "feat: add backend-agnostic HybridSearchService using RRF"
```

---

### Task 6: Eval metric functions

**Files:**
- Create: `benchmark/eval/__init__.py` (empty)
- Create: `benchmark/eval/metrics.py`
- Test: `tests/eval/test_metrics.py` (create `tests/eval/__init__.py`, empty)

**Interfaces:**
- Consumes: nothing (pure functions over ranked id lists + relevant sets).
- Produces:
  - `precision_at_k(ranked: list[str], relevant: set[str], k: int) -> float`
  - `recall_at_k(ranked: list[str], relevant: set[str], k: int) -> float`
  - `reciprocal_rank(ranked: list[str], relevant: set[str]) -> float`
  - `ndcg_at_k(ranked: list[str], relevant: set[str], k: int) -> float`

- [ ] **Step 1: Write the failing test**

```python
# tests/eval/test_metrics.py
import math
from benchmark.eval.metrics import (
    precision_at_k, recall_at_k, reciprocal_rank, ndcg_at_k,
)

RANKED = ["a", "x", "b", "y"]
RELEVANT = {"a", "b"}


def test_precision_at_k():
    assert precision_at_k(RANKED, RELEVANT, k=2) == 0.5   # a hit, x miss
    assert precision_at_k(RANKED, RELEVANT, k=4) == 0.5   # 2 of 4


def test_recall_at_k():
    assert recall_at_k(RANKED, RELEVANT, k=2) == 0.5      # found a of {a,b}
    assert recall_at_k(RANKED, RELEVANT, k=4) == 1.0      # found both


def test_reciprocal_rank():
    assert reciprocal_rank(RANKED, RELEVANT) == 1.0       # first item relevant
    assert reciprocal_rank(["x", "a"], {"a"}) == 0.5


def test_ndcg_at_k_perfect_is_one():
    assert math.isclose(ndcg_at_k(["a", "b"], {"a", "b"}, k=2), 1.0)


def test_ndcg_at_k_empty_relevant_is_zero():
    assert ndcg_at_k(RANKED, set(), k=4) == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/eval/test_metrics.py -v`
Expected: FAIL — `No module named 'benchmark.eval.metrics'`.

- [ ] **Step 3: Write minimal implementation**

```python
# benchmark/eval/metrics.py
"""Retrieval quality metrics over ranked id lists and a relevant-id set."""
from __future__ import annotations

import math


def precision_at_k(ranked: list[str], relevant: set[str], k: int) -> float:
    if k == 0:
        return 0.0
    top = ranked[:k]
    return sum(1 for doc_id in top if doc_id in relevant) / k


def recall_at_k(ranked: list[str], relevant: set[str], k: int) -> float:
    if not relevant:
        return 0.0
    top = set(ranked[:k])
    return len(top & relevant) / len(relevant)


def reciprocal_rank(ranked: list[str], relevant: set[str]) -> float:
    for index, doc_id in enumerate(ranked, start=1):
        if doc_id in relevant:
            return 1.0 / index
    return 0.0


def ndcg_at_k(ranked: list[str], relevant: set[str], k: int) -> float:
    if not relevant:
        return 0.0
    dcg = sum(
        1.0 / math.log2(index + 1)
        for index, doc_id in enumerate(ranked[:k], start=1)
        if doc_id in relevant
    )
    ideal_hits = min(len(relevant), k)
    idcg = sum(1.0 / math.log2(index + 1) for index in range(1, ideal_hits + 1))
    return dcg / idcg if idcg else 0.0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/eval/test_metrics.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add benchmark/eval/__init__.py benchmark/eval/metrics.py tests/eval/
git commit -m "feat: add retrieval eval metric functions"
```

---

### Task 7: Golden dataset + eval runner + report

**Files:**
- Create: `benchmark/eval/dataset.py`
- Create: `benchmark/eval/run_eval.py`
- Test: `tests/eval/test_run_eval.py`

**Interfaces:**
- Consumes: `dataset.DOCUMENTS`, `dataset.QUERIES`, `HybridSearchService` (Task 5), `PgVectorSearch` (Task 4), `PgFtsLexical` (Task 3), metrics (Task 6), the `db` fixture, the sentence-transformers model.
- Produces:
  - `dataset.DOCUMENTS: list[dict]` — each `{node_key, description, data_original, shape}`.
  - `dataset.QUERIES: list[dict]` — each `{query, shape, relevant: set[str]}`.
  - `run_eval.load_dataset(db, model, project_id) -> None` — inserts DOCUMENTS as Embedding rows.
  - `run_eval.evaluate(strategy_search, project_id, k=5) -> dict` — returns `{"overall": {...}, "by_shape": {shape: {...}}}` with mean `precision`, `recall`, `mrr`, `ndcg`.
  - `run_eval.build_strategies(db, model) -> dict[str, object]` — returns named searchers each exposing `async search(project_id, query, top_k)`: `"vector"`, `"pgfts_hybrid"`. (OpenSearch strategy is added in plan 3 for comparison; omitted here — log that omission.)
  - `run_eval.render_report(results_by_strategy: dict) -> str` — markdown table.

- [ ] **Step 1: Write the dataset fixture (one cluster per data shape from spec §5.1)**

```python
# benchmark/eval/dataset.py
"""Golden eval dataset: documents + queries tagged by data-shape (spec §5.1).

Shapes: config_keys, code_symbols, error_ids, codenames, prose.
Each query's `relevant` set names the node_keys that genuinely satisfy it.
"""

DOCUMENTS = [
    # config_keys
    {"node_key": "cfg_timeout", "shape": "config_keys",
     "description": "service request timeout in milliseconds",
     "data_original": "SERVICE_TIMEOUT_MS=30000"},
    {"node_key": "cfg_retry", "shape": "config_keys",
     "description": "service retry backoff in milliseconds",
     "data_original": "SERVICE_RETRY_MS=1000"},
    # code_symbols
    {"node_key": "sym_get_user_by_id", "shape": "code_symbols",
     "description": "fetch a user record by primary key",
     "data_original": "def getUserById(user_id): ..."},
    {"node_key": "sym_get_user_by_email", "shape": "code_symbols",
     "description": "fetch a user record by email address",
     "data_original": "def getUserByEmail(email): ..."},
    # error_ids
    {"node_key": "err_conn_refused", "shape": "error_ids",
     "description": "database connection was refused",
     "data_original": "ERR_CONN_REFUSED: could not connect to postgres"},
    {"node_key": "err_503", "shape": "error_ids",
     "description": "service temporarily unavailable",
     "data_original": "HTTP 503 Service Unavailable from upstream"},
    # codenames (out-of-vocab)
    {"node_key": "svc_falcon", "shape": "codenames",
     "description": "internal billing service",
     "data_original": "Project Falcon owns invoicing and payment capture"},
    {"node_key": "svc_kestrel", "shape": "codenames",
     "description": "internal notification service",
     "data_original": "Project Kestrel owns email and push delivery"},
    # prose
    {"node_key": "doc_testing", "shape": "prose",
     "description": "testing requirements and coverage policy",
     "data_original": "All pull requests must maintain at least 80% line coverage."},
    {"node_key": "doc_style", "shape": "prose",
     "description": "code style and formatting standards",
     "data_original": "Use black for formatting and ruff for linting on every commit."},
]

QUERIES = [
    {"query": "SERVICE_TIMEOUT_MS", "shape": "config_keys", "relevant": {"cfg_timeout"}},
    {"query": "how long before a request times out", "shape": "config_keys", "relevant": {"cfg_timeout"}},
    {"query": "getUserByEmail", "shape": "code_symbols", "relevant": {"sym_get_user_by_email"}},
    {"query": "ERR_CONN_REFUSED", "shape": "error_ids", "relevant": {"err_conn_refused"}},
    {"query": "service unavailable upstream", "shape": "error_ids", "relevant": {"err_503"}},
    {"query": "Project Falcon", "shape": "codenames", "relevant": {"svc_falcon"}},
    {"query": "which service handles invoicing", "shape": "codenames", "relevant": {"svc_falcon"}},
    {"query": "what is our test coverage requirement", "shape": "prose", "relevant": {"doc_testing"}},
]
```

- [ ] **Step 2: Write the failing runner test**

```python
# tests/eval/test_run_eval.py
import pytest
from sentence_transformers import SentenceTransformer
from benchmark.eval import run_eval


@pytest.mark.asyncio
async def test_pgfts_hybrid_beats_vector_on_exact_tokens(db):
    model = SentenceTransformer("all-MiniLM-L6-v2")
    await run_eval.load_dataset(db, model, project_id="eval")
    strategies = run_eval.build_strategies(db, model)

    hybrid = await run_eval.evaluate(strategies["pgfts_hybrid"], "eval", k=5)
    vector = await run_eval.evaluate(strategies["vector"], "eval", k=5)

    # Exact-token shapes are where lexical must help; hybrid should be >= vector.
    for shape in ("config_keys", "error_ids", "codenames"):
        assert hybrid["by_shape"][shape]["recall"] >= vector["by_shape"][shape]["recall"]

    report = run_eval.render_report({"pgfts_hybrid": hybrid, "vector": vector})
    assert "pgfts_hybrid" in report and "config_keys" in report
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/eval/test_run_eval.py -v`
Expected: FAIL — `run_eval` has no `load_dataset`/`build_strategies`/`evaluate`/`render_report`.

- [ ] **Step 4: Write the runner implementation**

```python
# benchmark/eval/run_eval.py
"""Golden-eval runner: load dataset, run strategies, aggregate metrics, render report."""
from __future__ import annotations

import asyncio
from statistics import mean

from src.core.db_models import Embedding
from src.core.hybrid_search_service import HybridSearchService
from src.core.lexical_search import PgFtsLexical
from src.core.vector_search import PgVectorSearch
from benchmark.eval import metrics
from benchmark.eval.dataset import DOCUMENTS, QUERIES

_SHAPES = ["config_keys", "code_symbols", "error_ids", "codenames", "prose"]


async def load_dataset(db, model, project_id: str) -> None:
    async with db.session() as session:
        for doc in DOCUMENTS:
            text_for_vec = f"{doc['description']} {doc['data_original']}"
            session.add(Embedding(
                project_id=project_id, data_key=doc["node_key"], node_key=doc["node_key"],
                description=doc["description"], data={}, data_original=doc["data_original"],
                data_format="text", embedding=model.encode(text_for_vec).tolist(),
            ))
        await session.commit()


def build_strategies(db, model) -> dict:
    vector = PgVectorSearch(db, model)
    lexical = PgFtsLexical(db)
    return {
        "vector": vector,
        "pgfts_hybrid": HybridSearchService(vector, lexical, k=60),
    }


async def evaluate(strategy_search, project_id: str, k: int = 5) -> dict:
    per_shape: dict[str, list[dict]] = {shape: [] for shape in _SHAPES}
    for q in QUERIES:
        hits = await strategy_search.search(project_id, q["query"], top_k=k)
        ranked = [doc_id for doc_id, _ in hits]
        rel = q["relevant"]
        per_shape[q["shape"]].append({
            "precision": metrics.precision_at_k(ranked, rel, k),
            "recall": metrics.recall_at_k(ranked, rel, k),
            "mrr": metrics.reciprocal_rank(ranked, rel),
            "ndcg": metrics.ndcg_at_k(ranked, rel, k),
        })

    def _agg(rows: list[dict]) -> dict:
        if not rows:
            return {m: 0.0 for m in ("precision", "recall", "mrr", "ndcg")}
        return {m: mean(r[m] for r in rows) for m in ("precision", "recall", "mrr", "ndcg")}

    all_rows = [r for rows in per_shape.values() for r in rows]
    return {
        "overall": _agg(all_rows),
        "by_shape": {shape: _agg(rows) for shape, rows in per_shape.items() if rows},
    }


def render_report(results_by_strategy: dict) -> str:
    lines = ["# Golden Eval Report", "",
             "> OpenSearch-hybrid strategy omitted in plan 1; added in plan 3 for comparison.",
             "", "| strategy | scope | precision | recall | mrr | ndcg |",
             "|---|---|---|---|---|---|"]
    for name, res in results_by_strategy.items():
        o = res["overall"]
        lines.append(f"| {name} | overall | {o['precision']:.2f} | {o['recall']:.2f} | {o['mrr']:.2f} | {o['ndcg']:.2f} |")
        for shape, s in res["by_shape"].items():
            lines.append(f"| {name} | {shape} | {s['precision']:.2f} | {s['recall']:.2f} | {s['mrr']:.2f} | {s['ndcg']:.2f} |")
    return "\n".join(lines)


async def _main() -> None:
    from src.core.database import DatabaseManager
    from sentence_transformers import SentenceTransformer
    db = DatabaseManager()
    await db.initialize()
    model = SentenceTransformer("all-MiniLM-L6-v2")
    await load_dataset(db, model, project_id="eval")
    strategies = build_strategies(db, model)
    results = {name: await evaluate(s, "eval", k=5) for name, s in strategies.items()}
    report = render_report(results)
    with open("benchmark/eval/report.md", "w") as fh:
        fh.write(report)
    print(report)


if __name__ == "__main__":
    asyncio.run(_main())
```

Note: verify the `DatabaseManager` import path and initialization call in `_main` against `src/core/database.py`; adjust `db.initialize()` to match the actual init method name if different. `_main` is a convenience entrypoint and is not covered by the test (which uses the `db` fixture).

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/eval/test_run_eval.py -v`
Expected: PASS. If a specific exact-token shape fails the `>=` assertion, that is a real signal about PG-FTS quality — record it; do not weaken the assertion to force a pass.

- [ ] **Step 6: Produce the report artifact and read it**

Run: `python -m benchmark.eval.run_eval`
Expected: writes `benchmark/eval/report.md` and prints the table. Read the numbers — this is the §5.1 gate.

- [ ] **Step 7: Commit**

```bash
git add benchmark/eval/dataset.py benchmark/eval/run_eval.py tests/eval/test_run_eval.py benchmark/eval/report.md
git commit -m "feat: add golden-eval dataset, runner, and report"
```

---

## Eval Gate (decision point after Task 7)

Read `benchmark/eval/report.md`. **Decision:** PG-FTS-hybrid should match or beat vector-only on the exact-token shapes (`config_keys`, `error_ids`, `codenames`) and not regress meaningfully on `prose`. 
- **If yes:** the PG-FTS default (spec §3.2) is validated → proceed to plan 3 (OpenSearch removal) and plan 2 (reverse-matching).
- **If no:** do NOT remove OpenSearch. Revisit spec §3.2 — either tune the FTS config (weights, `ts_rank_cd` normalization, `websearch_to_tsquery`) and re-run, or keep OpenSearch as the default lexical backend. The `LexicalSearch` seam makes either outcome cheap.

---

## Self-Review Notes

- **Spec coverage:** §3.2 (pgvector + PG-FTS hybrid) → Tasks 2-5; §3.3 (`LexicalSearch` seam, RRF backend-agnostic) → Tasks 1, 3, 5; §5.1 (golden eval across data shapes, three strategies) → Tasks 6-7 (OpenSearch strategy deferred to plan 3, explicitly logged). §3.7 migration (tsvector) → Task 2. Not in this plan (correctly, other plans): reverse-matching (§4, plan 2), OpenSearch removal (§3.2, plan 3), MCP interface (§2, plan 4), freeze/docs (§3.6, plan 5).
- **Placeholder scan:** no TBD/TODO; all steps carry runnable code. The one flagged verification (DatabaseManager init name in `_main`) is an explicit check against a real file, not a placeholder in tested code.
- **Type consistency:** every ranker (`PgVectorSearch`, `PgFtsLexical`, `HybridSearchService`) exposes the same `async search(project_id, query, top_k) -> list[tuple[str, float]]`; `rrf_fuse` consumes `list[list[str]]` and callers pass `[doc_id for doc_id, _ in hits]`; metrics consume `list[str]` + `set[str]`. `node_key` is the id everywhere.
```
