"""Tests for data models"""

import pytest
from pydantic import ValidationError
from src.models import (
    AgentRegistration,
    DataPublishEvent,
    RegistrationResponse,
    MatchedDataSource,
)


class TestAgentRegistration:
    """Test AgentRegistration model"""

    def test_create_basic_registration(self):
        """Test creating a basic agent registration"""
        registration = AgentRegistration(
            agent_id="test-agent",
            project_id="test-project",
            data_needs=["tech stack", "API documentation"],
        )

        assert registration.agent_id == "test-agent"
        assert registration.project_id == "test-project"
        assert len(registration.data_needs) == 2
        assert registration.last_seen_sequence == "0"
        assert registration.notification_channel is None

    def test_registration_with_custom_channel(self):
        """Test registration with custom notification channel"""
        registration = AgentRegistration(
            agent_id="test-agent",
            project_id="test-project",
            data_needs=["tech stack"],
            notification_channel="custom:channel:name",
        )

        assert registration.notification_channel == "custom:channel:name"

    def test_registration_with_last_seen(self):
        """Test registration with last seen sequence"""
        registration = AgentRegistration(
            agent_id="test-agent",
            project_id="test-project",
            data_needs=["tech stack"],
            last_seen_sequence="1234567890-0",
        )

        assert registration.last_seen_sequence == "1234567890-0"

    def test_validation_requires_fields(self):
        """Test that validation requires all required fields"""
        with pytest.raises(ValidationError):
            AgentRegistration(
                agent_id="test-agent",
                # Missing project_id and data_needs
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
            data={"endpoints": ["/api/v1/users"]},
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


class TestRegistrationResponse:
    """Test RegistrationResponse model"""

    def test_create_response(self):
        """Test creating a registration response"""
        response = RegistrationResponse(
            status="registered",
            agent_id="test-agent",
            project_id="test-project",
            caught_up_events=5,
            current_sequence="1234567890-0",
            matched_needs={"tech stack": 3, "API docs": 2},
            notification_channel="agent:test-agent:updates",
        )

        assert response.status == "registered"
        assert response.agent_id == "test-agent"
        assert response.caught_up_events == 5
        assert response.matched_needs["tech stack"] == 3
        assert response.notification_channel == "agent:test-agent:updates"

    def test_response_serialization(self):
        """Test that response can be serialized to dict"""
        response = RegistrationResponse(
            status="registered",
            agent_id="test-agent",
            project_id="test-project",
            caught_up_events=0,
            current_sequence="0",
            matched_needs={},
            notification_channel="test:channel",
        )

        data = response.model_dump()
        assert isinstance(data, dict)
        assert data["status"] == "registered"
        assert data["agent_id"] == "test-agent"
