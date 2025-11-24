
"""Tests for semantic data matching"""

import pytest
import pytest_asyncio
import numpy as np
from unittest.mock import Mock, AsyncMock, patch
from src.core.semantic_matcher import SemanticDataMatcher
from src.core.models import DataPublishEvent


class TestSemanticDataMatcher:
    """Test SemanticDataMatcher functionality"""

    @pytest_asyncio.fixture
    async def matcher(self, redis):
        """Create a SemanticDataMatcher instance with mocks"""
        # Mock SentenceTransformer to avoid loading heavy model
        with patch("src.core.semantic_matcher.SentenceTransformer") as mock_model_cls:
            mock_model = Mock()
            # Return random embedding of correct shape (384,)
            mock_model.encode.return_value = np.random.rand(384).astype(np.float32)
            mock_model_cls.return_value = mock_model
            
            matcher = SemanticDataMatcher(
                redis=redis,
                model_name="all-MiniLM-L6-v2", 
                similarity_threshold=0.5, 
                max_matches=10
            )
            
            # Mock RediSearch commands since fakeredis doesn't support them
            # We mock the ft() method to return an AsyncMock
            matcher.redis.ft = Mock()
            mock_ft = AsyncMock()
            matcher.redis.ft.return_value = mock_ft
            
            # Mock search results
            mock_search_result = Mock()
            mock_search_result.docs = []
            mock_ft.search.return_value = mock_search_result
            
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

        # Verify data is in Redis (standard keys)
        # The embedding and index operations are mocked, but basic hash storage should work
        keys = await matcher.redis.keys("embedding:proj1:tech_stack")
        assert len(keys) > 0
        
        data = await matcher.redis.hgetall("embedding:proj1:tech_stack")
        assert b"data_key" in data
        assert data[b"data_key"] == b"tech_stack"

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
    async def test_match_exact_semantic_match(self, matcher):
        """Test matching calls search correctly"""
        # Register data
        await matcher.register_data(
            "proj1", "api_authentication", {"method": "OAuth2", "provider": "Google"}
        )

        # Mock search response
        mock_doc = Mock()
        mock_doc.score = 0.1  # Distance 0.1 -> Similarity 0.9
        mock_doc.data_key = "api_authentication"
        mock_doc.description = "auth"
        mock_doc.data = '{"method": "OAuth2"}'
        
        matcher.redis.ft().search.return_value.docs = [mock_doc]

        # Match with similar need
        matches = await matcher.match_agent_needs(
            "proj1", ["authentication and authorization methods"]
        )

        assert len(matches) == 1
        assert "authentication and authorization methods" in matches
        assert len(matches["authentication and authorization methods"]) == 1
        assert matches["authentication and authorization methods"][0]["data_key"] == "api_authentication"

    @pytest.mark.asyncio
    async def test_match_multiple_data_sources(self, matcher):
        """Test matching returns multiple relevant sources"""
        # Register multiple data sources
        await matcher.register_data(
            "proj1", "api_docs", {"endpoints": ["/api/users", "/api/posts"]}
        )
        
        # Mock search response with multiple docs
        doc1 = Mock()
        doc1.score = 0.1
        doc1.data_key = "api_docs"
        doc1.description = "docs"
        doc1.data = '{}'
        
        doc2 = Mock()
        doc2.score = 0.2
        doc2.data_key = "api_auth"
        doc2.description = "auth"
        doc2.data = '{}'
        
        matcher.redis.ft().search.return_value.docs = [doc1, doc2]

        # Match API-related needs
        matches = await matcher.match_agent_needs(
            "proj1", ["API endpoints and authentication"]
        )

        need_key = "API endpoints and authentication"
        assert need_key in matches
        assert len(matches[need_key]) == 2

    @pytest.mark.asyncio
    async def test_match_respects_project_isolation(self, matcher):
        """Test that matches are project-specific"""
        # This test mainly verifies that the query construction includes project_id
        # since we are mocking the search execution
        
        await matcher.match_agent_needs("proj1", ["data information"])
        
        # Verify search was called
        assert matcher.redis.ft().search.called
        
        # Inspect call arguments to ensure project_id filter was used
        call_args = matcher.redis.ft().search.call_args
        query_obj = call_args[0][0]
        # The query string should contain the project id tag filter
        assert "@project_id:{proj1}" in str(query_obj.query_string())

    @pytest.mark.asyncio
    async def test_match_respects_threshold(self, matcher):
        """Test that low similarity matches are filtered"""
        # Mock search response with low similarity doc
        doc = Mock()
        doc.score = 0.9  # Distance 0.9 -> Similarity 0.1 (below default 0.5)
        doc.data_key = "weather"
        doc.description = "weather"
        doc.data = '{}'
        
        matcher.redis.ft().search.return_value.docs = [doc]

        # Match 
        matches = await matcher.match_agent_needs(
            "proj1", ["database schema"]
        )

        # Should filter out the low similarity match
        assert len(matches["database schema"]) == 0

    @pytest.mark.asyncio
    async def test_match_respects_max_matches(self, redis):
        """Test that max_matches limit is enforced"""
        # We need to create a new matcher with custom max_matches
        with patch("src.core.semantic_matcher.SentenceTransformer") as mock_model_cls:
            mock_model = Mock()
            mock_model.encode.return_value = np.random.rand(384).astype(np.float32)
            mock_model_cls.return_value = mock_model
            
            matcher = SemanticDataMatcher(redis=redis, max_matches=1)
            matcher.redis.ft = Mock()
            matcher.redis.ft.return_value = AsyncMock()
            
            # Mock 2 results
            doc1 = Mock(score=0.1, data_key="k1", description="d1", data='{}')
            doc2 = Mock(score=0.2, data_key="k2", description="d2", data='{}')
            matcher.redis.ft().search.return_value.docs = [doc1, doc2]
            
            await matcher.initialize_index()

            # Match
            matches = await matcher.match_agent_needs("proj1", ["API endpoints"])

            # Should return only 1 result due to max_matches=1
            assert len(matches["API endpoints"]) == 1

    @pytest.mark.asyncio
    async def test_get_registered_data(self, matcher):
        """Test retrieving all data keys for a project"""
        # Mock search results for get_registered_data
        doc1 = Mock()
        doc1.data_key = "data1"
        doc2 = Mock()
        doc2.data_key = "data2"
        
        matcher.redis.ft().search.return_value.docs = [doc1, doc2]

        proj1_keys = await matcher.get_registered_data("proj1")

        assert len(proj1_keys) == 2
        assert "data1" in proj1_keys
        assert "data2" in proj1_keys

    @pytest.mark.asyncio
    async def test_clear_project(self, matcher):
        """Test clearing all data for a project"""
        # Register some data (real redis operations for keys)
        await matcher.register_data("proj1", "data1", {})
        await matcher.register_data("proj1", "data2", {})
        
        # Verify keys exist
        keys = await matcher.redis.keys("embedding:proj1:*")
        assert len(keys) > 0

        await matcher.clear_project("proj1")

        # Verify keys are gone
        keys = await matcher.redis.keys("embedding:proj1:*")
        assert len(keys) == 0

    @pytest.mark.asyncio
    async def test_empty_data_needs(self, matcher):
        """Test matching with empty data needs list"""
        matches = await matcher.match_agent_needs("proj1", [])
        assert matches == {}

    @pytest.mark.asyncio
    async def test_match_with_no_registered_data(self, matcher):
        """Test matching when no data is registered"""
        matcher.redis.ft().search.return_value.docs = []
        matches = await matcher.match_agent_needs("proj1", ["something"])

        assert "something" in matches
        assert len(matches["something"]) == 0
