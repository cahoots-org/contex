"""Tests for semantic data matching with PostgreSQL/pgvector"""

import asyncio
import contextlib
import threading
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
import numpy as np
from sqlalchemy import select, text
from unittest.mock import Mock, AsyncMock, patch
from src.core.db_models import Embedding, Symbol
from src.core.hybrid_search_service import HybridSearchService
from src.core.lexical_search import PgFtsLexical
from src.core.semantic_matcher import SemanticDataMatcher


async def _data_keys(matcher, project_id):
    async with matcher.db.session() as session:
        return list((await session.execute(select(Embedding.data_key).where(Embedding.project_id == project_id))).scalars())


class TestSemanticDataMatcher:
    """Test SemanticDataMatcher functionality with PostgreSQL"""

    @pytest_asyncio.fixture
    async def matcher(self, db):
        """Create a SemanticDataMatcher instance with mocked model"""
        # Mock the embedder to avoid loading heavy model
        with patch("src.core.semantic_matcher.OnnxEmbedder") as mock_model_cls:
            mock_model = Mock()
            mock_model.get_sentence_embedding_dimension.return_value = 768
            # Simulate batched encode: (n, 768) for a list of texts, (768,) for a single string.
            mock_model.encode.side_effect = lambda x, *a, **k: (
                np.random.rand(768).astype(np.float32)
                if isinstance(x, str)
                else np.random.rand(len(x), 768).astype(np.float32)
            )
            mock_model_cls.return_value = mock_model

            matcher = SemanticDataMatcher(
                db=db,
                model_name="thenlper/gte-base",
                similarity_threshold=0.5,
                max_matches=10
            )

            await matcher.initialize_index()
            return matcher

    @pytest.mark.asyncio
    async def test_initialization(self, matcher):
        """Test matcher initializes correctly"""
        assert matcher.threshold == 0.5
        assert matcher.max_matches == 10
        assert matcher.model is not None

    @pytest.mark.asyncio
    async def test_store_failure_fails_only_its_item(self, matcher):
        items = [("a", {"v": 1}, None), ("b", {"v": float("nan")}, None), ("c", {"v": 2}, None)]
        errors = await matcher.register_data_batch("proj1", items)
        assert errors[0] is None and errors[1] and errors[2] is None
        assert sorted(await _data_keys(matcher, "proj1")) == ["a", "c"]

    @pytest.mark.asyncio
    async def test_unreadable_item_fails_only_itself(self, matcher):
        errors = await matcher.register_data_batch("proj1", [("a", {"v": 1}, None), ("b", '{"x\\u0000": {"y": 1}}', "json")])
        assert errors[0] is None and errors[1]

    @pytest.mark.asyncio
    async def test_register_single_data(self, matcher):
        """Test registering a single piece of data"""
        await matcher.register_data_batch(
            "proj1", [("tech_stack", {"backend": "FastAPI", "frontend": "React"}, None)],
        )

        # Verify data is in PostgreSQL
        assert await _data_keys(matcher, "proj1") == ["tech_stack"]

    @pytest.mark.asyncio
    async def test_match_registered_data(self, matcher):
        """Test matching registered data with needs"""
        # Register some data
        await matcher.register_data_batch(
            "proj1", [("api_authentication", {"method": "OAuth2", "provider": "Google"}, None)],
        )
        await matcher.register_data_batch(
            "proj1", [("database_config", {"host": "localhost", "port": 5432}, None)],
        )

        # Match with similar need
        matches = await matcher.match_agent_needs(
            "proj1", ["authentication methods"]
        )

        # Should find matches (actual results depend on embedding similarity)
        assert "authentication methods" in matches

    @pytest.mark.asyncio
    async def test_matches_collapse_to_one_result_per_document(self, matcher):
        """A document with many matching nodes takes one slot, not all of them."""
        await matcher.register_data_batch(
            "proj1", [("ticket", [{"comment": f"note {i}"} for i in range(6)], "json")],
        )

        results = (await matcher.match_agent_needs("proj1", ["notes"], threshold=0.0))["notes"]

        assert [r["document"] for r in results] == ["ticket"]
        assert len(results[0]["related"]) == 2

    @pytest.mark.asyncio
    async def test_matched_child_carries_its_document_root(self, matcher):
        """A comment match comes back with its ticket's own fields."""
        await matcher.register_data_batch(
            "proj1", [("jira:DEV-1", {"summary": "Login times out", "comments": [{"body": "bumped pool size"}]}, None)],
        )
        await matcher.register_data_batch("proj1", [("notes", {"body": "unrelated"}, None)])
        matcher._rank_nodes = AsyncMock(return_value=[
            ("jira:DEV-1\x1fcomments[0]", 0.9), ("notes", 0.8),
        ])

        results = (await matcher.match_agent_needs("proj1", ["pool"], threshold=0.0))["pool"]

        assert results[0]["data"] == {"body": "bumped pool size"}
        assert results[0]["document_data"] == {"summary": "Login times out"}
        assert "document_data" not in results[1]

    @pytest.mark.asyncio
    async def test_root_already_in_result_is_not_repeated(self, matcher):
        await matcher.register_data_batch(
            "proj1", [("jira:DEV-1", {"summary": "Login times out", "comments": [{"body": "bumped pool size"}]}, None)],
        )
        matcher._rank_nodes = AsyncMock(return_value=[
            ("jira:DEV-1\x1fcomments[0]", 0.9), ("jira:DEV-1", 0.8),
        ])

        results = (await matcher.match_agent_needs("proj1", ["pool"], threshold=0.0))["pool"]

        assert [r["data_key"] for r in results[0]["related"]] == ["jira:DEV-1"]
        assert "document_data" not in results[0]

    @pytest.mark.asyncio
    async def test_hybrid_fuses_at_top_k_and_over_fetches_the_pool(self, matcher):
        matcher.hybrid_search = Mock(search=AsyncMock(return_value=[]))
        matcher.candidate_pool_factor = 10

        await matcher.match_agent_needs("proj1", ["anything"], top_k=4)

        kwargs = matcher.hybrid_search.search.call_args.kwargs
        assert (kwargs["top_k"], kwargs["pool"]) == (4, 40)

    @pytest.mark.asyncio
    async def test_reranker_applies_only_when_requested(self, matcher):
        """The opt-in reranker runs for rerank=True (ad-hoc query), not reconcile."""
        for key in ("alpha", "beta", "gamma"):
            await matcher.register_data_batch("proj1", [(key, {"name": key}, None)])

        calls = []

        class Reverse:
            async def rerank(self, task, candidates):
                calls.append([c["document"] for c in candidates])
                return list(reversed(candidates))

        matcher.reranker = Reverse()
        await matcher.match_agent_needs("proj1", ["names"], threshold=0.0)
        assert calls == []

        reranked = await matcher.match_agent_needs("proj1", ["names"], threshold=0.0, rerank=True)
        assert [r["document"] for r in reranked["names"]] == list(reversed(calls[0]))

    @pytest.mark.asyncio
    async def test_match_multiple_data_sources(self, matcher):
        """Test matching returns relevant sources based on embedding similarity"""
        # Register multiple data sources
        await matcher.register_data_batch(
            "proj1", [("api_docs", {"endpoints": ["/api/v1/users", "/api/v1/posts"]}, None)],
        )
        await matcher.register_data_batch("proj1", [("api_auth", {"method": "Bearer token"}, None)])

        # Match API-related needs
        matches = await matcher.match_agent_needs(
            "proj1", ["API endpoints and authentication"]
        )

        need_key = "API endpoints and authentication"
        assert need_key in matches
        # Both should potentially match (depending on embedding similarity)

    @pytest.mark.asyncio
    async def test_match_respects_project_isolation(self, matcher):
        """Test that matches are project-specific"""
        # Register data for different projects
        await matcher.register_data_batch("proj1", [("config", {"setting": "value1"}, None)])
        await matcher.register_data_batch("proj2", [("config", {"setting": "value2"}, None)])

        # Match in proj1
        matches = await matcher.match_agent_needs("proj1", ["configuration"])

        # Should only return proj1 data
        if matches.get("configuration"):
            for match in matches["configuration"]:
                # Verify no proj2 data leaked in
                assert "value2" not in str(match.get("data", {}))

    @pytest.mark.asyncio
    async def test_empty_data_needs(self, matcher):
        """Test matching with empty data needs list"""
        matches = await matcher.match_agent_needs("proj1", [])
        assert matches == {}

    @pytest.mark.asyncio
    async def test_match_with_no_registered_data(self, matcher):
        """Test matching when no data is registered"""
        matches = await matcher.match_agent_needs("proj1", ["something"])

        assert "something" in matches
        assert len(matches["something"]) == 0

    @pytest.mark.asyncio
    async def test_update_existing_data(self, matcher):
        """Test updating existing data"""
        # Register initial data
        await matcher.register_data_batch("proj1", [("config", {"version": "1.0"}, None)])

        # Update with new data
        await matcher.register_data_batch(
            "proj1", [("config", {"version": "2.0", "new_field": "value"}, None)],
        )

        # Verify update
        assert await _data_keys(matcher, "proj1") == ["config"]


class TestSemanticMatcherContentHashDedup:
    """Unchanged content must not be re-embedded on re-ingest (#223)."""

    @pytest_asyncio.fixture
    async def matcher(self, db):
        # Deterministic encode so content is the only thing that varies; the mock
        # lets us count how many nodes were actually embedded.
        mock_model = Mock()
        mock_model.get_sentence_embedding_dimension.return_value = 768
        mock_model.encode.side_effect = lambda x, *a, **k: (
            np.ones(768, dtype=np.float32)
            if isinstance(x, str)
            else np.ones((len(x), 768), dtype=np.float32)
        )
        with patch(
            "src.core.semantic_matcher._load_model", return_value=mock_model
        ):
            matcher = SemanticDataMatcher(
                db=db, model_name="thenlper/gte-base", max_matches=10
            )
            await matcher.initialize_index()
            return matcher

    @pytest.mark.asyncio
    async def test_unchanged_reingest_skips_encode(self, matcher):
        await matcher.register_data_batch("projd", [("config", {"version": "1.0"}, None)])

        matcher.model.encode.reset_mock()
        await matcher.register_data_batch("projd", [("config", {"version": "1.0"}, None)])

        assert matcher.model.encode.call_count == 0

    @pytest.mark.asyncio
    async def test_batch_embeds_all_items_in_one_encode(self, matcher):
        await matcher.register_data_batch("projd", [("kept", {"v": 1}, None)])
        matcher.model.encode.reset_mock()

        await matcher.register_data_batch("projd", [
            ("kept", {"v": 1}, None), ("a", {"v": 2}, None), ("b", {"v": 3}, None),
        ])

        assert matcher.model.encode.call_count == 1
        assert len(matcher.model.encode.call_args.args[0]) == 2
        async with matcher.db.session() as session:
            keys = set((await session.execute(
                select(Embedding.data_key).where(Embedding.project_id == "projd")
            )).scalars())
        assert keys == {"kept", "a", "b"}

    @pytest.mark.asyncio
    async def test_changed_reingest_re_embeds(self, matcher):
        await matcher.register_data_batch("projd", [("config", {"version": "1.0"}, None)])

        matcher.model.encode.reset_mock()
        await matcher.register_data_batch("projd", [("config", {"version": "2.0"}, None)])

        assert matcher.model.encode.call_count >= 1

    @pytest.mark.asyncio
    async def test_model_change_re_embeds(self, matcher):
        await matcher.register_data_batch("projd", [("config", {"version": "1.0"}, None)])

        matcher.model.encode.reset_mock()
        matcher.model_name = "other/model-768"
        await matcher.register_data_batch("projd", [("config", {"version": "1.0"}, None)])

        assert matcher.model.encode.call_count == 1

    @pytest.mark.asyncio
    async def test_removed_text_stops_matching_lexically(self, matcher):
        await matcher.register_data_batch("projd", [("doc.txt", "Alpha stays.\n\nBeta stays.\n\nZanzibarquux in v1.", "text")])
        await matcher.register_data_batch("projd", [("doc.txt", "Alpha stays.\n\nBeta stays.\n\nGamma in v2.", "text")])

        assert await PgFtsLexical(matcher.db).search("projd", "Zanzibarquux", top_k=10) == []

    @pytest.mark.asyncio
    async def test_concurrent_first_publishes_of_one_key_all_succeed(self, matcher):
        results = await asyncio.gather(*[
            matcher.register_data_batch("projd", [(f"race{n}.txt", f"Alpha one.\n\nBeta {i}.", "text")])
            for n in range(10) for i in range(4)
        ])
        assert results == [[None]] * 40

    @pytest.mark.asyncio
    async def test_republish_drops_nodes_and_symbols_it_no_longer_has(self, matcher):
        await matcher.register_data_batch("projd", [("lib/util.py", "def helper():\n    return compute()\n", "code")])
        await matcher.register_data_batch("projd", [("lib/util.py", "First note.\n\nSecond note.", "text")])
        await matcher.register_data_batch("projd", [("lib/util.py", "First note.", "text")])

        async with matcher.db.session() as session:
            nodes = (await session.execute(select(Embedding.node_key))).scalars().all()
            symbols = (await session.execute(select(Symbol.name))).scalars().all()
        assert nodes == ["lib/util.py\x1fsentence_0"]
        assert symbols == []

    @pytest.mark.asyncio
    async def test_dotted_keys_do_not_share_nodes(self, matcher):
        await matcher.register_data_batch("projd", [("config", {"name": "app", "prod": {"host": "prod-db"}}, None)])
        await matcher.register_data_batch("projd", [("config.prod", {"owner": "ops"}, None)])

        async with matcher.db.session() as session:
            keys = (await session.execute(select(Embedding.data_key))).scalars().all()
        assert sorted(keys) == ["config", "config", "config.prod"]

    @pytest.mark.asyncio
    async def test_parsing_runs_off_the_event_loop(self, matcher, monkeypatch):
        parse, threads = matcher.node_converter.parse, []

        def spy(*args, **kwargs):
            threads.append(threading.current_thread())
            return parse(*args, **kwargs)

        monkeypatch.setattr(matcher.node_converter, "parse", spy)
        await matcher.register_data_batch("projd", [("notes", "hello", None)])
        assert threads and threads[0] is not threading.main_thread()


class TestSemanticMatcherConcurrency:
    """Concurrent requests must not clobber each other's per-request parameters.

    Regression test for #105: `match_agent_needs` previously read `top_k`/
    `threshold` from mutable instance attributes on a shared singleton, which
    callers save/mutated/restored around ``await`` points. Under concurrency the
    interleaved queries read each other's values, silently returning results with
    the wrong match count or threshold. The fix threads these as per-call
    arguments so nothing shared is mutated.
    """

    @pytest_asyncio.fixture
    async def matcher(self, db):
        """Matcher with a mocked model so every text embeds identically.

        With identical embeddings and threshold 0, every registered item is a
        match, so a call with ``top_k=k`` must return exactly ``min(k, N)``
        results regardless of what any concurrent call requests.
        """
        # Patch _load_model (not OnnxEmbedder) so the mock never lands in
        # the process-wide _MODEL_CACHE under the real model name and leak into
        # the real-embedding tests.
        mock_model = Mock()
        mock_model.get_sentence_embedding_dimension.return_value = 768
        mock_model.encode.side_effect = lambda x, *a, **k: (
            np.ones(768, dtype=np.float32)
            if isinstance(x, str)
            else np.ones((len(x), 768), dtype=np.float32)
        )
        with patch(
            "src.core.semantic_matcher._load_model", return_value=mock_model
        ):
            matcher = SemanticDataMatcher(
                db=db,
                model_name="thenlper/gte-base",
                similarity_threshold=0.5,
                max_matches=10,
            )
            await matcher.initialize_index()
            return matcher

    @pytest.mark.asyncio
    async def test_concurrent_top_k_do_not_cross_contaminate(self, matcher):
        """Interleaved calls with different top_k each get their own count."""
        n_items = 8
        for i in range(n_items):
            await matcher.register_data_batch("projc", [(f"item_{i}", {"idx": i}, None)])

        need = "anything"

        async def query(k: int) -> int:
            result = await matcher.match_agent_needs(
                "projc", [need], top_k=k, threshold=0.0
            )
            return len(result[need])

        # Many interleaved calls, each requesting a different number of matches.
        # If per-request state leaked through a shared attribute, some calls would
        # return another call's count.
        ks = [(i % n_items) + 1 for i in range(60)]
        counts = await asyncio.gather(*(query(k) for k in ks))

        for requested, got in zip(ks, counts):
            assert got == requested, (
                f"requested top_k={requested} but got {got} matches; "
                "concurrent requests cross-contaminated shared state"
            )

    @pytest.mark.asyncio
    async def test_write_planned_against_a_replaced_version_fails(self, matcher):
        """A writer that planned before another committed must not store a mix of both."""
        doc = "# Doc\n\n## X\n\n{x}\n\n## {extra}\n\nmore\n"
        await matcher.register_data_batch("race", [("doc.md", "# Doc\n\n## X\n\nold\n", "markdown")])

        planned, release = asyncio.Event(), asyncio.Event()
        write = matcher._write_registration

        async def held_write(*args, **kwargs):
            planned.set()
            await release.wait()
            return await write(*args, **kwargs)

        with patch.object(matcher, "_write_registration", held_write):
            late = asyncio.create_task(matcher.register_data_batch(
                "race", [("doc.md", doc.format(x="old", extra="Z"), "markdown")]))
            await planned.wait()
        assert await matcher.register_data_batch(
            "race", [("doc.md", doc.format(x="new", extra="Y"), "markdown")]) == [None]
        release.set()

        assert (await late)[0] is not None
        async with matcher.db.session() as session:
            stored = str((await session.execute(
                select(Embedding.data).where(Embedding.project_id == "race"))).scalars().all())
        assert "new" in stored and "old" not in stored

    @pytest.mark.asyncio
    async def test_delete_during_publish_removes_the_whole_document(self, matcher):
        await matcher.register_data_batch("race", [("doc.md", "# Doc\n\n## A\n\nalpha\n", "markdown")])
        plan = await matcher._plan_registration(
            "race", "doc.md", "# Doc\n\n## A\n\nbeta\n\n## B\n\ngamma\n", "markdown")
        db, written, release = matcher.db, asyncio.Event(), asyncio.Event()

        class HeldCommit:
            @contextlib.asynccontextmanager
            async def session(self):
                async with db.session() as session:
                    yield session
                    await session.flush()
                    written.set()
                    await release.wait()

        matcher.db = HeldCommit()
        write = asyncio.create_task(matcher._write_registration(
            "race", plan, np.random.rand(len(plan["changed"]), 768).astype(np.float32)))
        await written.wait()
        matcher.db = db
        delete = asyncio.create_task(matcher.delete_data_keys("race", ["doc.md"]))
        await asyncio.sleep(0.5)
        release.set()
        await write

        assert await delete == ["doc.md"]
        assert await _data_keys(matcher, "race") == []


class TestSemanticMatcherTimeWindow:
    """`since` restricts matches to rows created/updated on or after a cutoff.

    Uses an identical-embedding model (threshold 0 ⇒ every row matches on
    similarity) so tests isolate the time predicate. Row timestamps are aged
    directly in the DB to simulate stale vs. fresh content.
    """

    @pytest_asyncio.fixture
    async def matcher(self, db):
        # Patch _load_model (not OnnxEmbedder) so the mock is never stored
        # in the process-wide _MODEL_CACHE under the real model name, which would
        # otherwise leak into the real-embedding tests.
        mock_model = Mock()
        mock_model.get_sentence_embedding_dimension.return_value = 768
        mock_model.encode.side_effect = lambda x, *a, **k: (
            np.ones(768, dtype=np.float32)
            if isinstance(x, str)
            else np.ones((len(x), 768), dtype=np.float32)
        )
        with patch(
            "src.core.semantic_matcher._load_model", return_value=mock_model
        ):
            matcher = SemanticDataMatcher(
                db=db,
                model_name="thenlper/gte-base",
                similarity_threshold=0.5,
                max_matches=10,
            )
            await matcher.initialize_index()
            return matcher

    async def _set_timestamps(self, db, data_key, *, created_at, updated_at=None):
        async with db.session() as session:
            await session.execute(
                text(
                    "UPDATE embeddings SET created_at = :c, updated_at = :u "
                    "WHERE data_key = :k"
                ),
                {"c": created_at, "u": updated_at, "k": data_key},
            )
            await session.commit()

    @staticmethod
    def _data_keys(need_matches):
        # A match's data_key is the node_key (`<data_key>\x1f<path>`); reduce to the
        # published data_key for assertions.
        return {m["data_key"].split("\x1f", 1)[0] for m in need_matches}

    @pytest.mark.asyncio
    async def test_since_excludes_older_rows(self, matcher, db):
        now = datetime.now(timezone.utc)
        await matcher.register_data_batch("projt", [("fresh", {"v": 1}, None)])
        await matcher.register_data_batch("projt", [("stale", {"v": 2}, None)])
        await self._set_timestamps(
            db, "stale", created_at=now - timedelta(days=800)
        )

        cutoff = now - timedelta(days=365)
        matches = await matcher.match_agent_needs(
            "projt", ["anything"], threshold=0.0, since=cutoff
        )

        keys = self._data_keys(matches["anything"])
        assert "fresh" in keys
        assert "stale" not in keys

    @pytest.mark.asyncio
    async def test_since_none_returns_everything(self, matcher, db):
        now = datetime.now(timezone.utc)
        await matcher.register_data_batch("projt", [("fresh", {"v": 1}, None)])
        await matcher.register_data_batch("projt", [("stale", {"v": 2}, None)])
        await self._set_timestamps(
            db, "stale", created_at=now - timedelta(days=800)
        )

        matches = await matcher.match_agent_needs(
            "projt", ["anything"], threshold=0.0
        )

        keys = self._data_keys(matches["anything"])
        assert keys == {"fresh", "stale"}

    @pytest.mark.asyncio
    async def test_since_uses_updated_at_when_present(self, matcher, db):
        """A row created long ago but recently updated is within the window."""
        now = datetime.now(timezone.utc)
        await matcher.register_data_batch("projt", [("revived", {"v": 1}, None)])
        # Created 800 days ago, but re-published 10 days ago.
        await self._set_timestamps(
            db,
            "revived",
            created_at=now - timedelta(days=800),
            updated_at=now - timedelta(days=10),
        )

        cutoff = now - timedelta(days=365)
        matches = await matcher.match_agent_needs(
            "projt", ["anything"], threshold=0.0, since=cutoff
        )

        keys = self._data_keys(matches["anything"])
        assert "revived" in keys

    @pytest.mark.asyncio
    async def test_concurrent_threshold_do_not_cross_contaminate(self, matcher):
        """A strict-threshold call must not be relaxed by a concurrent lenient one.

        All items embed identically to the query, so similarity is 1.0 for every
        item. A threshold of 1.5 excludes everything; a threshold of 0.0 includes
        everything. Interleaving the two must keep the strict call at zero.
        """
        for i in range(5):
            await matcher.register_data_batch("projt", [(f"item_{i}", {"idx": i}, None)])

        need = "anything"

        async def strict() -> int:
            result = await matcher.match_agent_needs(
                "projt", [need], top_k=10, threshold=1.5
            )
            return len(result[need])

        async def lenient() -> int:
            result = await matcher.match_agent_needs(
                "projt", [need], top_k=10, threshold=0.0
            )
            return len(result[need])

        tasks = []
        for _ in range(20):
            tasks.append(strict())
            tasks.append(lenient())
        results = await asyncio.gather(*tasks)

        strict_counts = results[0::2]
        lenient_counts = results[1::2]
        assert all(c == 0 for c in strict_counts), (
            f"strict threshold leaked matches: {strict_counts}"
        )
        assert all(c == 5 for c in lenient_counts), (
            f"lenient threshold lost matches: {lenient_counts}"
        )

    @pytest.mark.asyncio
    async def test_match_agent_needs_does_not_mutate_instance_state(self, matcher):
        """Passing per-call top_k/threshold must leave the instance defaults intact."""
        await matcher.register_data_batch("projm", [("item", {"idx": 0}, None)])

        await matcher.match_agent_needs("projm", ["x"], top_k=1, threshold=0.99)

        assert matcher.max_matches == 10
        assert matcher.threshold == 0.5


class TestSemanticMatcherWithRealEmbeddings:
    """Tests that use real embeddings (if model is available)"""

    @pytest_asyncio.fixture
    async def real_matcher(self, db):
        """Create matcher with real model (skipped if model not available)"""
        try:
            matcher = SemanticDataMatcher(
                db=db,
                model_name="thenlper/gte-base",
                similarity_threshold=0.0,  # return all so relative ranking is testable
                max_matches=10
            )
            await matcher.initialize_index()
            return matcher
        except Exception:
            pytest.skip("Sentence transformer model not available")

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_semantic_similarity_ranking(self, real_matcher):
        """Test that semantically similar data ranks higher"""
        # Register varied data
        await real_matcher.register_data_batch(
            "proj1", [("user_auth", {"method": "JWT", "flow": "OAuth2"}, None)],
        )
        await real_matcher.register_data_batch(
            "proj1", [("database", {"type": "PostgreSQL", "host": "localhost"}, None)],
        )
        await real_matcher.register_data_batch(
            "proj1", [("weather", {"temperature": 72, "condition": "sunny"}, None)],
        )

        # Match authentication-related need
        matches = await real_matcher.match_agent_needs(
            "proj1", ["authentication and security"]
        )

        results = matches.get("authentication and security", [])
        assert results, "auth query returned no matches"
        keys = [r.get("data_key", "").lower() for r in results]

        def _rank(substr: str) -> int:
            return next((i for i, k in enumerate(keys) if substr in k), len(keys))

        # The auth data must rank above the clearly-unrelated weather data.
        # We don't require it to strictly beat the database entry: "PostgreSQL"
        # is security-adjacent enough that the two can rank close, and that
        # near-tie flips across CPU architectures (passes locally, fails in CI).
        assert _rank("auth") < _rank("weather"), keys


class TestSemanticMatcherHybridSearch:
    """Hybrid search must run on pgvector + Postgres FTS (no OpenSearch)."""

    @pytest_asyncio.fixture
    async def hybrid_matcher(self, db, monkeypatch):
        monkeypatch.setenv("HYBRID_SEARCH_ENABLED", "true")
        try:
            matcher = SemanticDataMatcher(
                db=db,
                model_name="thenlper/gte-base",
                similarity_threshold=0.3,
                max_matches=10,
            )
            await matcher.initialize_index()
            return matcher
        except Exception:
            pytest.skip("Sentence transformer model not available")

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_hybrid_uses_pgvector_fts_service(self, hybrid_matcher):
        """HYBRID_SEARCH_ENABLED=true wires HybridSearchService, not OpenSearch."""
        assert isinstance(hybrid_matcher.hybrid_search, HybridSearchService)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_hybrid_matches_end_to_end(self, hybrid_matcher):
        """End-to-end hybrid search returns data via pgvector + FTS fusion."""
        await hybrid_matcher.register_data_batch(
            "projhy", [("user_auth", {"method": "JWT", "flow": "OAuth2"}, None)],
        )
        await hybrid_matcher.register_data_batch(
            "projhy", [("database", {"type": "PostgreSQL", "host": "localhost"}, None)],
        )

        matches = await hybrid_matcher.match_agent_needs(
            "projhy", ["authentication and security"]
        )
        results = matches.get("authentication and security", [])
        assert results, "hybrid search returned no results"
        keys = [r["data_key"] for r in results]
        assert any("user_auth" in k for k in keys)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_hybrid_since_filters_old_data(self, hybrid_matcher, db):
        """`since` must apply in hybrid mode (both vector and lexical rankers)."""
        await hybrid_matcher.register_data_batch(
            "projhyt", [("fresh_auth", {"method": "JWT", "flow": "OAuth2"}, None)],
        )
        await hybrid_matcher.register_data_batch(
            "projhyt", [("stale_auth", {"method": "JWT", "flow": "OAuth2"}, None)],
        )
        now = datetime.now(timezone.utc)
        async with db.session() as session:
            await session.execute(
                text(
                    "UPDATE embeddings SET created_at = :c, updated_at = NULL "
                    "WHERE data_key = :k"
                ),
                {"c": now - timedelta(days=800), "k": "stale_auth"},
            )
            await session.commit()

        matches = await hybrid_matcher.match_agent_needs(
            "projhyt", ["authentication and security"],
            since=now - timedelta(days=365),
        )
        keys = {
            r["data_key"].split(".", 1)[0]
            for r in matches.get("authentication and security", [])
        }
        assert "fresh_auth" in keys
        assert "stale_auth" not in keys
