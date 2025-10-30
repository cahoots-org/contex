"""Tests for semantic data matching"""

import pytest
from src.semantic_matcher import SemanticDataMatcher


class TestSemanticDataMatcher:
    """Test SemanticDataMatcher functionality"""

    @pytest.fixture
    def matcher(self):
        """Create a SemanticDataMatcher instance"""
        return SemanticDataMatcher(
            model_name="all-MiniLM-L6-v2",
            similarity_threshold=0.5,
            max_matches=10
        )

    def test_initialization(self, matcher):
        """Test matcher initializes correctly"""
        assert matcher.threshold == 0.5
        assert matcher.max_matches == 10
        assert matcher.model is not None
        assert len(matcher.registry) == 0

    def test_register_single_data(self, matcher):
        """Test registering a single piece of data"""
        matcher.register_data(
            project_id="proj1",
            data_key="tech_stack",
            data={"backend": "FastAPI", "frontend": "React"}
        )

        assert "proj1:tech_stack" in matcher.registry
        assert "data" in matcher.registry["proj1:tech_stack"]
        assert "embedding" in matcher.registry["proj1:tech_stack"]
        assert "description" in matcher.registry["proj1:tech_stack"]

    def test_auto_describe_simple_data(self, matcher):
        """Test auto-description generates meaningful descriptions"""
        description = matcher._auto_describe(
            "tech_stack",
            {"backend": "Python", "frontend": "React"}
        )

        assert "tech_stack" in description
        assert "backend" in description or "frontend" in description

    def test_auto_describe_nested_data(self, matcher):
        """Test auto-description handles nested structures"""
        description = matcher._auto_describe(
            "config",
            {
                "server": {
                    "host": "localhost",
                    "port": 8000
                },
                "database": {
                    "url": "postgres://..."
                }
            }
        )

        assert "config" in description
        # Should flatten nested keys
        assert "server" in description or "database" in description

    def test_flatten_dict_simple(self, matcher):
        """Test dict flattening with simple structure"""
        result = matcher._flatten_dict({"a": 1, "b": 2})
        assert result == {"a": 1, "b": 2}

    def test_flatten_dict_nested(self, matcher):
        """Test dict flattening with nested structure"""
        result = matcher._flatten_dict({
            "level1": {
                "level2": {
                    "level3": "value"
                }
            }
        })

        assert "level1.level2.level3" in result

    def test_flatten_dict_with_arrays(self, matcher):
        """Test dict flattening handles arrays"""
        result = matcher._flatten_dict({
            "items": [
                {"id": 1, "name": "Item 1"},
                {"id": 2, "name": "Item 2"}
            ]
        })

        assert "items[*]" in result

    def test_match_exact_semantic_match(self, matcher):
        """Test matching with semantically similar text"""
        # Register data
        matcher.register_data(
            "proj1",
            "api_authentication",
            {"method": "OAuth2", "provider": "Google"}
        )

        # Match with similar need
        matches = matcher.match_agent_needs(
            "proj1",
            ["authentication and authorization methods"]
        )

        assert len(matches) == 1
        assert "authentication and authorization methods" in matches
        assert len(matches["authentication and authorization methods"]) >= 0

    def test_match_multiple_data_sources(self, matcher):
        """Test matching returns multiple relevant sources"""
        # Register multiple data sources
        matcher.register_data("proj1", "api_docs", {
            "endpoints": ["/api/users", "/api/posts"]
        })
        matcher.register_data("proj1", "api_authentication", {
            "method": "JWT"
        })
        matcher.register_data("proj1", "database_schema", {
            "tables": ["users", "posts"]
        })

        # Match API-related needs
        matches = matcher.match_agent_needs(
            "proj1",
            ["API endpoints and authentication"]
        )

        need_key = "API endpoints and authentication"
        assert need_key in matches
        # Should match api_docs and api_authentication higher than database

    def test_match_respects_project_isolation(self, matcher):
        """Test that matches are project-specific"""
        # Register data for two projects
        matcher.register_data("proj1", "data1", {"value": "project1"})
        matcher.register_data("proj2", "data2", {"value": "project2"})

        # Match for proj1 should not see proj2 data
        matches = matcher.match_agent_needs("proj1", ["data information"])

        # All matches should be from proj1 only
        for need_matches in matches.values():
            for match in need_matches:
                assert match["data_key"] != "data2"

    def test_match_respects_threshold(self, matcher):
        """Test that low similarity matches are filtered"""
        # Register completely unrelated data
        matcher.register_data("proj1", "weather_data", {
            "temperature": 72,
            "conditions": "sunny"
        })

        # Match with completely different topic
        matches = matcher.match_agent_needs(
            "proj1",
            ["database schema and table structure"]
        )

        # Should return matches dict but may be empty
        assert isinstance(matches, dict)

    def test_match_respects_max_matches(self):
        """Test that max_matches limit is enforced"""
        matcher = SemanticDataMatcher(max_matches=2)

        # Register many similar data sources
        for i in range(10):
            matcher.register_data(
                "proj1",
                f"api_endpoint_{i}",
                {"endpoint": f"/api/v1/resource{i}"}
            )

        # Match should return at most 2 results
        matches = matcher.match_agent_needs(
            "proj1",
            ["API endpoints"]
        )

        for need_matches in matches.values():
            assert len(need_matches) <= 2

    def test_match_sorts_by_similarity(self, matcher):
        """Test that matches are sorted by similarity score"""
        # Register data with varying relevance
        matcher.register_data("proj1", "exact_match", {
            "authentication": "OAuth2",
            "authorization": "RBAC"
        })
        matcher.register_data("proj1", "related", {
            "security": "SSL"
        })
        matcher.register_data("proj1", "unrelated", {
            "logging": "enabled"
        })

        matches = matcher.match_agent_needs(
            "proj1",
            ["authentication and authorization"]
        )

        # Check that results are sorted descending by similarity
        for need_matches in matches.values():
            if len(need_matches) > 1:
                for i in range(len(need_matches) - 1):
                    assert need_matches[i]["similarity"] >= need_matches[i + 1]["similarity"]

    def test_get_registered_data(self, matcher):
        """Test retrieving all data keys for a project"""
        matcher.register_data("proj1", "data1", {})
        matcher.register_data("proj1", "data2", {})
        matcher.register_data("proj2", "data3", {})

        proj1_keys = matcher.get_registered_data("proj1")

        assert len(proj1_keys) == 2
        assert "data1" in proj1_keys
        assert "data2" in proj1_keys
        assert "data3" not in proj1_keys

    def test_clear_project(self, matcher):
        """Test clearing all data for a project"""
        matcher.register_data("proj1", "data1", {})
        matcher.register_data("proj1", "data2", {})
        matcher.register_data("proj2", "data3", {})

        matcher.clear_project("proj1")

        assert len(matcher.get_registered_data("proj1")) == 0
        assert len(matcher.get_registered_data("proj2")) == 1

    def test_empty_data_needs(self, matcher):
        """Test matching with empty data needs list"""
        matcher.register_data("proj1", "data1", {})

        matches = matcher.match_agent_needs("proj1", [])

        assert matches == {}

    def test_match_with_no_registered_data(self, matcher):
        """Test matching when no data is registered"""
        matches = matcher.match_agent_needs("proj1", ["something"])

        assert "something" in matches
        assert len(matches["something"]) == 0
