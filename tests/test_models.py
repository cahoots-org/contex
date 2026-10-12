"""Tests for data models"""

from src.core.models import DataPublishEvent


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

    def test_nested_data_structure(self):
        """Test event with nested data"""
        event = DataPublishEvent(
            project_id="test-project",
            data_key="complex_data",
            data={"level1": {"level2": {"level3": "value"}, "array": [1, 2, 3]}},
        )

        assert event.data["level1"]["level2"]["level3"] == "value"
        assert event.data["level1"]["array"] == [1, 2, 3]

