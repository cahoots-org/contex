"""Tests for data models"""

import pytest
from pydantic import ValidationError
from src.core.models import (
    DataPublishEvent,
    MatchedDataSource,
)


class TestDataPublishEvent:
    """Test DataPublishEvent model"""

    def test_create_basic_event(self):
        """Test creating a basic data publish event"""
        event = DataPublishEvent(
            project_id="test-project",
            data_key="tech_stack",
            data={"backend": "FastAPI", "frontend": "React"},
        )

        assert event.project_id == "test-project"
        assert event.data_key == "tech_stack"
        assert event.data["backend"] == "FastAPI"
        assert event.event_type is None

    def test_event_with_custom_type(self):
        """Test event with custom event type"""
        event = DataPublishEvent(
            project_id="test-project",
            data_key="config",
            data={"setting": "value"},
            event_type="config_updated",
        )

        assert event.event_type == "config_updated"

    def test_nested_data_structure(self):
        """Test event with nested data"""
        event = DataPublishEvent(
            project_id="test-project",
            data_key="complex_data",
            data={"level1": {"level2": {"level3": "value"}, "array": [1, 2, 3]}},
        )

        assert event.data["level1"]["level2"]["level3"] == "value"
        assert event.data["level1"]["array"] == [1, 2, 3]


class TestMatchedDataSource:
    """Test MatchedDataSource model"""

    def test_create_matched_source(self):
        """Test creating a matched data source"""
        match = MatchedDataSource(
            data_key="api_docs",
            similarity=0.85,
            data={"endpoints": ["/api/v1/v1/users"]},
            description="API documentation with endpoints",
        )

        assert match.data_key == "api_docs"
        assert match.similarity == 0.85
        assert match.description == "API documentation with endpoints"

    def test_matched_source_without_description(self):
        """Test matched source without description"""
        match = MatchedDataSource(
            data_key="config", similarity=0.75, data={"key": "value"}
        )

        assert match.description is None

    def test_similarity_bounds(self):
        """Test that similarity accepts valid range"""
        # Valid similarities
        MatchedDataSource(data_key="test", similarity=0.0, data={})
        MatchedDataSource(data_key="test", similarity=0.5, data={})
        MatchedDataSource(data_key="test", similarity=1.0, data={})
        MatchedDataSource(
            data_key="test", similarity=1.5, data={}
        )  # Should not validate bounds
