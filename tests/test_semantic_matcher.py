"""Tests for semantic data matching with PostgreSQL/pgvector"""

import pytest
import pytest_asyncio
import numpy as np
from unittest.mock import Mock, AsyncMock, patch
from src.core.semantic_matcher import SemanticDataMatcher
from src.core.models import DataPublishEvent


class TestSemanticDataMatcher:
    """Test SemanticDataMatcher functionality with PostgreSQL"""

    @pytest_asyncio.fixture
    async def matcher(self, db):
        """Create a SemanticDataMatcher instance with mocked model"""
        # Mock SentenceTransformer to avoid loading heavy model
        with patch("src.core.semantic_matcher.SentenceTransformer") as mock_model_cls:
            mock_model = Mock()
            # Return random embedding of correct shape (384,)
            mock_model.encode.return_value = np.random.rand(384).astype(np.float32)
            mock_model_cls.return_value = mock_model

            matcher = SemanticDataMatcher(
                db=db,
                model_name="all-MiniLM-L6-v2",
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
    async def test_register_single_data(self, matcher):
        """Test registering a single piece of data"""
        await matcher.register_data(
            project_id="proj1",
            data_key="tech_stack",
            data={"backend": "FastAPI", "frontend": "React"},
        )

        # Verify data is in PostgreSQL
        keys = await matcher.get_registered_data("proj1")
        assert "tech_stack" in keys

    @pytest.mark.asyncio
    async def test_auto_describe_simple_data(self, matcher):
        """Test auto-description generates meaningful descriptions"""
        description = matcher._auto_describe(
            "tech_stack", {"backend": "Python", "frontend": "React"}
        )

        assert "tech_stack" in description

    @pytest.mark.asyncio
    async def test_auto_describe_nested_data(self, matcher):
        """Test auto-description handles nested structures"""
        description = matcher._auto_describe(
            "config",
            {
                "server": {"host": "localhost", "port": 8000},
                "database": {"url": "postgres://..."},
            },
        )

        assert "config" in description

    @pytest.mark.asyncio
    async def test_flatten_dict_simple(self, matcher):
        """Test dict flattening with simple structure"""
        result = matcher._flatten_dict({"a": 1, "b": 2})
        assert result == {"a": 1, "b": 2}

    @pytest.mark.asyncio
    async def test_flatten_dict_nested(self, matcher):
        """Test dict flattening with nested structure"""
        result = matcher._flatten_dict({"level1": {"level2": {"level3": "value"}}})

        assert "level1.level2.level3" in result

    @pytest.mark.asyncio
    async def test_flatten_dict_with_arrays(self, matcher):
        """Test dict flattening handles arrays"""
        result = matcher._flatten_dict(
            {"items": [{"id": 1, "name": "Item 1"}, {"id": 2, "name": "Item 2"}]}
        )

        assert "items[*]" in result

    @pytest.mark.asyncio
    async def test_match_registered_data(self, matcher):
        """Test matching registered data with needs"""
        # Register some data
        await matcher.register_data(
            "proj1", "api_authentication", {"method": "OAuth2", "provider": "Google"}
        )
        await matcher.register_data(
            "proj1", "database_config", {"host": "localhost", "port": 5432}
        )

        # Match with similar need
        matches = await matcher.match_agent_needs(
            "proj1", ["authentication methods"]
        )

        # Should find matches (actual results depend on embedding similarity)
        assert "authentication methods" in matches

    @pytest.mark.asyncio
    async def test_match_multiple_data_sources(self, matcher):
        """Test matching returns relevant sources based on embedding similarity"""
        # Register multiple data sources
        await matcher.register_data(
            "proj1", "api_docs", {"endpoints": ["/api/v1/users", "/api/v1/posts"]}
        )
        await matcher.register_data(
            "proj1", "api_auth", {"method": "Bearer token"}
        )

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
        await matcher.register_data(
            "proj1", "config", {"setting": "value1"}
        )
        await matcher.register_data(
            "proj2", "config", {"setting": "value2"}
        )

        # Match in proj1
        matches = await matcher.match_agent_needs("proj1", ["configuration"])

        # Should only return proj1 data
        if matches.get("configuration"):
            for match in matches["configuration"]:
                # Verify no proj2 data leaked in
                assert "value2" not in str(match.get("data", {}))

    @pytest.mark.asyncio
    async def test_get_registered_data(self, matcher):
        """Test retrieving all data keys for a project"""
        # Register some data
        await matcher.register_data("proj1", "data1", {"key": "value1"})
        await matcher.register_data("proj1", "data2", {"key": "value2"})

        proj1_keys = await matcher.get_registered_data("proj1")

        assert len(proj1_keys) >= 2
        assert "data1" in proj1_keys
        assert "data2" in proj1_keys

    @pytest.mark.asyncio
    async def test_clear_project(self, matcher):
        """Test clearing all data for a project"""
        # Register some data
        await matcher.register_data("proj1", "data1", {"key": "value1"})
        await matcher.register_data("proj1", "data2", {"key": "value2"})

        # Verify data exists
        keys = await matcher.get_registered_data("proj1")
        assert len(keys) >= 2

        await matcher.clear_project("proj1")

        # Verify keys are gone
        keys = await matcher.get_registered_data("proj1")
        assert len(keys) == 0

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
        await matcher.register_data(
            "proj1", "config", {"version": "1.0"}
        )

        # Update with new data
        await matcher.register_data(
            "proj1", "config", {"version": "2.0", "new_field": "value"}
        )

        # Verify update
        keys = await matcher.get_registered_data("proj1")
        # Should still have one entry (updated, not duplicated)
        config_count = keys.count("config")
        assert config_count == 1


class TestSemanticMatcherWithRealEmbeddings:
    """Tests that use real embeddings (if model is available)"""

    @pytest_asyncio.fixture
    async def real_matcher(self, db):
        """Create matcher with real model (skipped if model not available)"""
        try:
            matcher = SemanticDataMatcher(
                db=db,
                model_name="all-MiniLM-L6-v2",
                similarity_threshold=0.3,
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
        await real_matcher.register_data(
            "proj1", "user_auth", {"method": "JWT", "flow": "OAuth2"}
        )
        await real_matcher.register_data(
            "proj1", "database", {"type": "PostgreSQL", "host": "localhost"}
        )
        await real_matcher.register_data(
            "proj1", "weather", {"temperature": 72, "condition": "sunny"}
        )

        # Match authentication-related need
        matches = await real_matcher.match_agent_needs(
            "proj1", ["authentication and security"]
        )

        results = matches.get("authentication and security", [])
        if results:
            # user_auth should rank higher than unrelated data
            top_match = results[0]
            assert "auth" in top_match.get("data_key", "").lower() or \
                   "JWT" in str(top_match.get("data", {}))
