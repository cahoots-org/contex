"""
Tests for Contex SDK models.

These tests ensure that SDK models can correctly parse responses
from the Contex server. This is critical for preventing model
drift between SDK and server.
"""

import pytest
from contex.models import (
    DataEvent,
    AgentRegistration,
    MatchedData,
    RegistrationResponse,
    QueryRequest,
    QueryResponse,
    APIKeyResponse,
    RateLimitInfo,
)


class TestDataEvent:
    """Tests for DataEvent model"""

    def test_minimal_data_event(self):
        """Test creating a DataEvent with required fields only"""
        event = DataEvent(
            project_id="proj_123",
            data_key="tech_stack",
            data={"frontend": "React", "backend": "FastAPI"}
        )
        assert event.project_id == "proj_123"
        assert event.data_key == "tech_stack"
        assert event.data == {"frontend": "React", "backend": "FastAPI"}
        assert event.data_format == "json"  # default
        assert event.metadata is None

    def test_full_data_event(self):
        """Test creating a DataEvent with all fields"""
        event = DataEvent(
            project_id="proj_123",
            data_key="config",
            data="key: value\nother: data",
            data_format="yaml",
            metadata={"source": "config_file", "version": "1.0"}
        )
        assert event.data_format == "yaml"
        assert event.metadata["source"] == "config_file"

    def test_data_event_with_various_data_types(self):
        """Test that data field accepts various types"""
        # String data
        event1 = DataEvent(project_id="p", data_key="k", data="plain text")
        assert event1.data == "plain text"

        # List data
        event2 = DataEvent(project_id="p", data_key="k", data=[1, 2, 3])
        assert event2.data == [1, 2, 3]

        # Nested dict
        event3 = DataEvent(project_id="p", data_key="k", data={"nested": {"deep": True}})
        assert event3.data["nested"]["deep"] is True


class TestAgentRegistration:
    """Tests for AgentRegistration model"""

    def test_minimal_registration(self):
        """Test creating registration with required fields"""
        reg = AgentRegistration(
            agent_id="task-decomposer",
            project_id="proj_123",
            data_needs=["tech stack", "coding standards"]
        )
        assert reg.agent_id == "task-decomposer"
        assert reg.notification_method == "redis"  # default
        assert reg.last_seen_sequence == "0"  # default

    def test_webhook_registration(self):
        """Test creating registration with webhook config"""
        reg = AgentRegistration(
            agent_id="webhook-agent",
            project_id="proj_123",
            data_needs=["events"],
            notification_method="webhook",
            webhook_url="https://example.com/webhook",
            webhook_secret="secret123"
        )
        assert reg.notification_method == "webhook"
        assert reg.webhook_url == "https://example.com/webhook"
        assert reg.webhook_secret == "secret123"

    def test_registration_with_sequence(self):
        """Test registration with last_seen_sequence for catch-up"""
        reg = AgentRegistration(
            agent_id="agent",
            project_id="proj",
            data_needs=["data"],
            last_seen_sequence="42"
        )
        assert reg.last_seen_sequence == "42"


class TestRegistrationResponse:
    """Tests for RegistrationResponse model - CRITICAL for SDK/Server compatibility"""

    def test_parse_server_response_format(self):
        """
        Test parsing the ACTUAL format returned by the Contex server.

        This is the most critical test - it uses the exact format from
        src/core/context_engine.py:register_agent()
        """
        # This is the exact format the server returns
        server_response = {
            "status": "registered",
            "agent_id": "task-decomposer",
            "project_id": "proj_123",
            "caught_up_events": 5,
            "current_sequence": "42",
            "matched_needs": {
                "tech stack": 3,
                "coding standards": 2
            },
            "notification_channel": "agent:task-decomposer:updates"
        }

        response = RegistrationResponse(**server_response)

        assert response.status == "registered"
        assert response.agent_id == "task-decomposer"
        assert response.project_id == "proj_123"
        assert response.caught_up_events == 5
        assert response.current_sequence == "42"
        assert response.matched_needs == {"tech stack": 3, "coding standards": 2}
        assert response.notification_channel == "agent:task-decomposer:updates"

    def test_parse_server_response_no_matches(self):
        """Test parsing server response when no data matches"""
        server_response = {
            "status": "registered",
            "agent_id": "new-agent",
            "project_id": "empty_proj",
            "caught_up_events": 0,
            "current_sequence": "0",
            "matched_needs": {},
            "notification_channel": "agent:new-agent:updates"
        }

        response = RegistrationResponse(**server_response)

        assert response.matched_needs == {}
        assert response.caught_up_events == 0

    def test_parse_server_response_with_many_matches(self):
        """Test parsing server response with multiple matched needs"""
        server_response = {
            "status": "registered",
            "agent_id": "analyzer",
            "project_id": "big_proj",
            "caught_up_events": 100,
            "current_sequence": "500",
            "matched_needs": {
                "programming languages": 5,
                "frameworks": 3,
                "database schemas": 2,
                "API endpoints": 10,
                "event models": 1
            },
            "notification_channel": "agent:analyzer:updates"
        }

        response = RegistrationResponse(**server_response)

        assert len(response.matched_needs) == 5
        assert response.matched_needs["API endpoints"] == 10
        assert sum(response.matched_needs.values()) == 21

    def test_all_required_fields_present(self):
        """Test that all required fields are present in response"""
        # Minimal valid response (all fields are required in current API)
        minimal_response = {
            "status": "registered",
            "agent_id": "agent",
            "project_id": "proj",
            "caught_up_events": 0,
            "current_sequence": "0",
            "matched_needs": {},
            "notification_channel": "ch"
        }

        response = RegistrationResponse(**minimal_response)

        # All fields should be accessible
        assert response.status == "registered"
        assert response.agent_id == "agent"
        assert response.project_id == "proj"
        assert response.caught_up_events == 0
        assert response.current_sequence == "0"
        assert response.matched_needs == {}
        assert response.notification_channel == "ch"

    def test_total_matches_calculation(self):
        """Test calculating total matches from matched_needs dict"""
        response = RegistrationResponse(
            status="registered",
            agent_id="agent",
            project_id="proj",
            caught_up_events=0,
            current_sequence="0",
            matched_needs={"need1": 5, "need2": 3, "need3": 2},
            notification_channel="ch"
        )

        total = sum(response.matched_needs.values())
        assert total == 10


class TestMatchedData:
    """Tests for MatchedData model"""

    def test_create_matched_data(self):
        """Test creating MatchedData"""
        data = MatchedData(
            data_key="tech_stack",
            data={"frontend": "React"},
            similarity_score=0.92,
            sequence="42",
            timestamp="2024-01-01T00:00:00Z"
        )
        assert data.data_key == "tech_stack"
        assert data.similarity_score == 0.92


class TestQueryResponse:
    """Tests for QueryResponse model"""

    def test_parse_query_response(self):
        """Test parsing a query response from server"""
        server_response = {
            "results": [
                {
                    "data_key": "tech_stack",
                    "data": {"frontend": "React"},
                    "similarity_score": 0.95,
                    "sequence": "10",
                    "timestamp": "2024-01-01T00:00:00Z"
                },
                {
                    "data_key": "frameworks",
                    "data": {"backend": "FastAPI"},
                    "similarity_score": 0.87,
                    "sequence": "15",
                    "timestamp": "2024-01-02T00:00:00Z"
                }
            ],
            "total": 2
        }

        response = QueryResponse(**server_response)

        assert response.total == 2
        assert len(response.results) == 2
        assert response.results[0].data_key == "tech_stack"
        assert response.results[0].similarity_score == 0.95

    def test_empty_query_response(self):
        """Test parsing empty query response"""
        response = QueryResponse(results=[], total=0)
        assert response.total == 0
        assert response.results == []


class TestAPIKeyResponse:
    """Tests for APIKeyResponse model"""

    def test_parse_api_key_response(self):
        """Test parsing API key creation response"""
        server_response = {
            "key_id": "key_abc123",
            "key": "ctx_live_abcdefghijklmnop",
            "name": "Production Key",
            "created_at": "2024-01-01T00:00:00Z"
        }

        response = APIKeyResponse(**server_response)

        assert response.key_id == "key_abc123"
        assert response.key.startswith("ctx_")
        assert response.name == "Production Key"


class TestRateLimitInfo:
    """Tests for RateLimitInfo model"""

    def test_parse_rate_limit_info(self):
        """Test parsing rate limit headers"""
        info = RateLimitInfo(
            limit=100,
            remaining=95,
            reset_at="2024-01-01T00:01:00Z"
        )
        assert info.limit == 100
        assert info.remaining == 95


class TestModelCompatibility:
    """
    Integration-style tests that verify SDK models match server expectations.

    These tests simulate the full round-trip of:
    1. Client creates request model
    2. Server processes and returns response
    3. Client parses response model
    """

    def test_registration_round_trip(self):
        """Test full registration flow model compatibility"""
        # 1. Client creates registration request
        request = AgentRegistration(
            agent_id="test-agent",
            project_id="test-proj",
            data_needs=["technical requirements", "API specifications"],
            notification_method="redis",
            last_seen_sequence="0"
        )

        # Verify request can be serialized (sent to server)
        request_dict = request.model_dump()
        assert "agent_id" in request_dict
        assert "data_needs" in request_dict

        # 2. Simulate server response (exact format from server)
        server_response = {
            "status": "registered",
            "agent_id": request.agent_id,
            "project_id": request.project_id,
            "caught_up_events": 0,
            "current_sequence": "0",
            "matched_needs": {
                "technical requirements": 2,
                "API specifications": 1
            },
            "notification_channel": f"agent:{request.agent_id}:updates"
        }

        # 3. Client parses response
        response = RegistrationResponse(**server_response)

        # Verify response matches request
        assert response.agent_id == request.agent_id
        assert response.project_id == request.project_id
        assert len(response.matched_needs) == len(request.data_needs)

    def test_publish_round_trip(self):
        """Test full publish flow model compatibility"""
        # 1. Client creates publish event
        event = DataEvent(
            project_id="proj",
            data_key="config",
            data={"setting": "value"},
            data_format="json"
        )

        # Verify event can be serialized
        event_dict = event.model_dump()
        assert event_dict["project_id"] == "proj"
        assert event_dict["data_key"] == "config"

        # Server would process this and return success
        # (No specific response model for publish - just HTTP 200)

    def test_query_round_trip(self):
        """Test full query flow model compatibility"""
        # 1. Client creates query request
        request = QueryRequest(
            project_id="proj",
            query="What frameworks are used?",
            max_results=5
        )

        # Verify request can be serialized
        request_dict = request.model_dump()
        assert "query" in request_dict

        # 2. Simulate server response
        server_response = {
            "results": [
                {
                    "data_key": "tech_stack",
                    "data": {"frontend": "React", "backend": "FastAPI"},
                    "similarity_score": 0.93,
                    "sequence": "5",
                    "timestamp": "2024-01-01T00:00:00Z"
                }
            ],
            "total": 1
        }

        # 3. Client parses response
        response = QueryResponse(**server_response)
        assert response.total == 1
        assert response.results[0].similarity_score > 0.9
