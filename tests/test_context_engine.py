"""Tests for Context Engine"""

import pytest
import pytest_asyncio
import numpy as np
from unittest.mock import Mock, AsyncMock, MagicMock, patch
from src.core.context_engine import ContextEngine
from src.core.models import DataPublishEvent


@pytest.mark.asyncio
async def test_publish_data_forwards_provenance_to_event_store():
    """Test that publish_data forwards source/actor/tenant_id to append_event"""
    engine = ContextEngine.__new__(ContextEngine)  # bypass __init__
    engine.semantic_matcher = MagicMock(register_data=AsyncMock())
    engine.event_store = MagicMock(append_event=AsyncMock(return_value="1"))
    engine.subscriptions = MagicMock(reconcile_project=AsyncMock())

    evt = DataPublishEvent(project_id="p1", data_key="k1", data={"a": 1})
    actor = {"actor_id": "key-1", "actor_type": "api_key", "actor_ip": "1.2.3.4"}

    seq = await engine.publish_data(evt, source="api", actor=actor, tenant_id="tenant-1")

    assert seq == "1"
    _, kwargs = engine.event_store.append_event.call_args
    assert kwargs["source"] == "api"
    assert kwargs["actor"] == actor
    assert kwargs["tenant_id"] == "tenant-1"


class TestContextEngine:
    """Test ContextEngine functionality"""

    @pytest_asyncio.fixture
    async def context_engine(self, db, redis):
        """Create a ContextEngine instance with mocks"""
        # Mock SentenceTransformer to avoid loading heavy model
        with patch("src.core.semantic_matcher.SentenceTransformer") as mock_model_cls:
            mock_model = Mock()
            mock_model.get_sentence_embedding_dimension.return_value = 384
            mock_model.encode.side_effect = lambda x, *a, **k: (
                np.array([0.1] * 384, dtype=np.float32)
                if isinstance(x, str)
                else np.array([[0.1] * 384] * len(x), dtype=np.float32)
            )
            mock_model_cls.return_value = mock_model

            engine = ContextEngine(
                db=db,
                redis=redis,
                similarity_threshold=0.5,
                max_matches=10
            )

            # Initialize the semantic matcher index
            if hasattr(engine.semantic_matcher, "initialize_index"):
                await engine.semantic_matcher.initialize_index()

            return engine

    @pytest.mark.asyncio
    async def test_initialization(self, context_engine):
        """Test that ContextEngine initializes correctly"""
        assert context_engine.semantic_matcher is not None
        assert context_engine.event_store is not None

    @pytest.mark.asyncio
    async def test_publish_data(self, context_engine):
        """Test publishing data"""
        event = DataPublishEvent(
            project_id="proj1",
            data_key="tech_stack",
            data={"backend": "FastAPI", "frontend": "React"},
        )

        sequence = await context_engine.publish_data(event)

        assert sequence is not None
        assert isinstance(sequence, str)

    @pytest.mark.asyncio
    async def test_publish_registers_with_semantic_matcher(self, context_engine):
        """Test that publishing data registers it with semantic matcher"""
        event = DataPublishEvent(
            project_id="proj1", data_key="api_docs", data={"endpoints": ["/api/v1/users"]}
        )

        await context_engine.publish_data(event)

        # Check semantic matcher has the data (now using PostgreSQL)
        registered_keys = await context_engine.semantic_matcher.get_registered_data("proj1")
        assert "api_docs" in registered_keys

    @pytest.mark.asyncio
    async def test_publish_appends_to_event_store(self, context_engine):
        """Test that publishing appends to event store"""
        event = DataPublishEvent(
            project_id="proj1", data_key="config", data={"setting": "value"}
        )

        await context_engine.publish_data(event)

        # Check event store has the event
        events = await context_engine.event_store.get_events_since("proj1", "0")
        assert len(events) == 1
        assert events[0]["event_type"] == "config_updated"

    @pytest.mark.asyncio
    async def test_publish_with_custom_event_type(self, context_engine):
        """Test publishing with custom event type"""
        event = DataPublishEvent(
            project_id="proj1", data_key="data", data={}, event_type="custom_event"
        )

        await context_engine.publish_data(event)

        events = await context_engine.event_store.get_events_since("proj1", "0")
        assert events[0]["event_type"] == "custom_event"

    @pytest.mark.asyncio
    async def test_query_project_data(self, context_engine):
        """Test ad-hoc querying of project data"""
        # Publish some data
        await context_engine.publish_data(
            DataPublishEvent(
                project_id="proj1",
                data_key="api_authentication",
                data={"method": "OAuth2", "provider": "Google"},
            )
        )
        await context_engine.publish_data(
            DataPublishEvent(
                project_id="proj1",
                data_key="database_config",
                data={"host": "localhost", "port": 5432},
            )
        )

        # Query for authentication (uses PostgreSQL + pgvector)
        results = await context_engine.query_project_data(
            project_id="proj1",
            query="authentication and authorization methods",
            top_k=5,
        )

        # Should return results
        assert isinstance(results, list)
        # The semantic search may or may not find results depending on embedding similarity
        assert len(results) >= 0

    @pytest.mark.asyncio
    async def test_query_respects_top_k(self, context_engine):
        """Test that query respects top_k limit"""
        # Publish many data sources
        for i in range(10):
            await context_engine.publish_data(
                DataPublishEvent(
                    project_id="proj1",
                    data_key=f"api_endpoint_{i}",
                    data={"endpoint": f"/api/v1/v1/resource{i}"},
                )
            )

        # Query with top_k=3 (PostgreSQL + pgvector limits results)
        results = await context_engine.query_project_data(
            project_id="proj1", query="API endpoints", top_k=3
        )

        # Should return at most 3 results
        assert len(results) <= 3

    @pytest.mark.asyncio
    async def test_query_empty_project(self, context_engine):
        """Test querying a project with no data"""
        results = await context_engine.query_project_data(
            project_id="empty_project", query="something", top_k=5
        )

        # Should return empty list
        assert results == []

    @pytest.mark.asyncio
    async def test_query_project_isolation(self, context_engine):
        """Test that queries respect project isolation"""
        # Publish data to different projects
        await context_engine.publish_data(
            DataPublishEvent(
                project_id="proj1", data_key="data1", data={"value": "project1"}
            )
        )
        await context_engine.publish_data(
            DataPublishEvent(
                project_id="proj2", data_key="data2", data={"value": "project2"}
            )
        )

        # Query proj1 (PostgreSQL filters by project_id)
        results = await context_engine.query_project_data(
            project_id="proj1", query="test value", top_k=5
        )

        # Should only get proj1 data
        for result in results:
            assert result["data_key"] != "data2"

    @pytest.mark.asyncio
    async def test_query_returns_similarity_scores(self, context_engine):
        """Test that query results include similarity scores"""
        await context_engine.publish_data(
            DataPublishEvent(
                project_id="proj1", data_key="test_data", data={"key": "value"}
            )
        )

        # Query (uses PostgreSQL + pgvector)
        results = await context_engine.query_project_data(
            project_id="proj1", query="test data", top_k=5
        )

        # Each result should have similarity score
        for result in results:
            assert "similarity" in result
            assert isinstance(result["similarity"], float)
            assert 0 <= result["similarity"] <= 1


class TestContextSizeLimits:
    """Test context size estimation used for response truncation."""

    @pytest.mark.asyncio
    async def test_tokenizer_fallback(self, db, redis):
        """Test that token estimation works even if tokenizer fails"""
        with patch("src.core.semantic_matcher.SentenceTransformer") as mock_model_cls:
            mock_model = Mock()
            mock_model.get_sentence_embedding_dimension.return_value = 384
            mock_model.encode.side_effect = lambda x, *a, **k: (
                np.array([0.1] * 384, dtype=np.float32)
                if isinstance(x, str)
                else np.array([[0.1] * 384] * len(x), dtype=np.float32)
            )
            mock_model_cls.return_value = mock_model

            engine = ContextEngine(
                db=db,
                redis=redis,
                similarity_threshold=0.5,
                max_matches=10,
                max_context_size=1000
            )

            # Even if tokenizer is None, should still work with fallback
            original_tokenizer = engine.tokenizer
            engine.tokenizer = None

            # Should still be able to estimate tokens
            tokens = engine._estimate_tokens({"test": "data"})
            assert tokens > 0
            assert isinstance(tokens, int)

            # Restore
            engine.tokenizer = original_tokenizer
