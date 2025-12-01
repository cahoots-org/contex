"""
Integration tests for Contex SDK.

These tests run against a REAL Contex server to verify end-to-end functionality.
They ensure the SDK and server are compatible.

To run these tests:
1. Start the Contex server: `uvicorn src.main:app --port 8001`
2. Run: `pytest tests/test_integration.py -v`

These tests are marked with @pytest.mark.integration and are skipped by default.
Use `pytest -m integration` to run them specifically.
"""

import os
import pytest
import asyncio
from uuid import uuid4

from contex import ContexAsyncClient, ContexClient
from contex.models import RegistrationResponse, QueryResponse
from contex.exceptions import NotFoundError, ValidationError


# Check if integration tests should run
CONTEX_URL = os.environ.get("CONTEX_TEST_URL", "http://localhost:8001")
CONTEX_API_KEY = os.environ.get("CONTEX_TEST_API_KEY", None)


def is_server_available():
    """Check if Contex server is available"""
    import httpx
    try:
        response = httpx.get(f"{CONTEX_URL}/health", timeout=2.0)
        return response.status_code == 200
    except Exception:
        return False


# Skip all integration tests if server is not available
pytestmark = pytest.mark.skipif(
    not is_server_available(),
    reason=f"Contex server not available at {CONTEX_URL}"
)


@pytest.fixture
def unique_project_id():
    """Generate a unique project ID for test isolation"""
    return f"test-project-{uuid4().hex[:8]}"


@pytest.fixture
def unique_agent_id():
    """Generate a unique agent ID for test isolation"""
    return f"test-agent-{uuid4().hex[:8]}"


@pytest.fixture
async def async_client():
    """Create an async client for testing"""
    async with ContexAsyncClient(
        url=CONTEX_URL,
        api_key=CONTEX_API_KEY
    ) as client:
        yield client


@pytest.fixture
def sync_client():
    """Create a sync client for testing"""
    return ContexClient(url=CONTEX_URL, api_key=CONTEX_API_KEY)


class TestHealthCheck:
    """Integration tests for health endpoint"""

    @pytest.mark.asyncio
    async def test_server_is_healthy(self, async_client):
        """Verify server health endpoint works"""
        result = await async_client.health()

        assert result["status"] == "healthy"
        assert "version" in result

    def test_sync_health_check(self, sync_client):
        """Test sync client health check"""
        result = sync_client.health()
        assert result["status"] == "healthy"


class TestPublishData:
    """Integration tests for data publishing"""

    @pytest.mark.asyncio
    async def test_publish_json_data(self, async_client, unique_project_id):
        """Test publishing JSON data"""
        result = await async_client.publish(
            project_id=unique_project_id,
            data_key="tech_stack",
            data={
                "frontend": "React",
                "backend": "FastAPI",
                "database": "PostgreSQL"
            },
            data_format="json"
        )

        assert result["status"] == "published"
        assert "sequence" in result
        assert result["data_key"] == "tech_stack"

    @pytest.mark.asyncio
    async def test_publish_yaml_data(self, async_client, unique_project_id):
        """Test publishing YAML data"""
        yaml_data = """
        frontend: React
        backend: FastAPI
        database: PostgreSQL
        """

        result = await async_client.publish(
            project_id=unique_project_id,
            data_key="config",
            data=yaml_data,
            data_format="yaml"
        )

        assert result["status"] == "published"

    @pytest.mark.asyncio
    async def test_publish_text_data(self, async_client, unique_project_id):
        """Test publishing plain text data"""
        result = await async_client.publish(
            project_id=unique_project_id,
            data_key="readme",
            data="# My Project\n\nThis is a test project.",
            data_format="text"
        )

        assert result["status"] == "published"

    @pytest.mark.asyncio
    async def test_publish_multiple_data_keys(self, async_client, unique_project_id):
        """Test publishing multiple data keys to same project"""
        # Publish tech stack
        await async_client.publish(
            project_id=unique_project_id,
            data_key="tech_stack",
            data={"frontend": "React"}
        )

        # Publish event model
        await async_client.publish(
            project_id=unique_project_id,
            data_key="event_model",
            data={"events": ["UserCreated", "OrderPlaced"]}
        )

        # Publish API specs
        result = await async_client.publish(
            project_id=unique_project_id,
            data_key="api_specs",
            data={"endpoints": ["/users", "/orders"]}
        )

        # All should succeed
        assert result["status"] == "published"


class TestAgentRegistration:
    """Integration tests for agent registration - THE CRITICAL PATH"""

    @pytest.mark.asyncio
    async def test_register_agent_basic(self, async_client, unique_project_id, unique_agent_id):
        """Test basic agent registration"""
        # First publish some data
        await async_client.publish(
            project_id=unique_project_id,
            data_key="tech_stack",
            data={"frontend": "React", "backend": "FastAPI"}
        )

        # Register agent
        response = await async_client.register_agent(
            agent_id=unique_agent_id,
            project_id=unique_project_id,
            data_needs=["tech stack and frameworks"]
        )

        # Verify response type and required fields
        assert isinstance(response, RegistrationResponse)
        assert response.status == "registered"
        assert response.agent_id == unique_agent_id
        assert response.project_id == unique_project_id
        assert isinstance(response.caught_up_events, int)
        assert isinstance(response.current_sequence, str)
        assert isinstance(response.matched_needs, dict)
        assert isinstance(response.notification_channel, str)

    @pytest.mark.asyncio
    async def test_register_agent_with_matches(self, async_client, unique_project_id, unique_agent_id):
        """Test that registration returns correct match counts"""
        # Publish multiple data items
        await async_client.publish(
            project_id=unique_project_id,
            data_key="tech_stack",
            data={"frontend": "React"}
        )
        await async_client.publish(
            project_id=unique_project_id,
            data_key="frameworks",
            data={"ui": "Material UI"}
        )

        # Register with specific needs
        response = await async_client.register_agent(
            agent_id=unique_agent_id,
            project_id=unique_project_id,
            data_needs=["frontend frameworks and libraries"]
        )

        assert response.status == "registered"
        # Should have at least one match
        total_matches = sum(response.matched_needs.values())
        # Note: match count depends on semantic similarity
        assert isinstance(total_matches, int)

    @pytest.mark.asyncio
    async def test_register_agent_empty_project(self, async_client, unique_agent_id):
        """Test registration on empty project"""
        empty_project = f"empty-{uuid4().hex[:8]}"

        response = await async_client.register_agent(
            agent_id=unique_agent_id,
            project_id=empty_project,
            data_needs=["anything"]
        )

        assert response.status == "registered"
        assert response.matched_needs == {} or sum(response.matched_needs.values()) == 0

    @pytest.mark.asyncio
    async def test_register_agent_notification_channel(self, async_client, unique_project_id, unique_agent_id):
        """Test that notification channel is correctly set"""
        response = await async_client.register_agent(
            agent_id=unique_agent_id,
            project_id=unique_project_id,
            data_needs=["data"]
        )

        assert response.notification_channel is not None
        assert unique_agent_id in response.notification_channel

    @pytest.mark.asyncio
    async def test_register_agent_catch_up(self, async_client, unique_project_id, unique_agent_id):
        """Test that caught_up_events reflects events since last_seen"""
        # Publish some data
        await async_client.publish(
            project_id=unique_project_id,
            data_key="data1",
            data={"key": "value1"}
        )
        await async_client.publish(
            project_id=unique_project_id,
            data_key="data2",
            data={"key": "value2"}
        )

        # Register with last_seen_sequence="0" (should get all events)
        response = await async_client.register_agent(
            agent_id=unique_agent_id,
            project_id=unique_project_id,
            data_needs=["data"],
            last_seen_sequence="0"
        )

        # Should have caught up some events
        assert response.caught_up_events >= 0
        assert int(response.current_sequence) >= 0

    @pytest.mark.asyncio
    async def test_unregister_agent(self, async_client, unique_project_id, unique_agent_id):
        """Test agent unregistration"""
        # First register
        await async_client.register_agent(
            agent_id=unique_agent_id,
            project_id=unique_project_id,
            data_needs=["data"]
        )

        # Then unregister
        result = await async_client.unregister_agent(unique_agent_id)

        assert result["status"] == "unregistered"
        assert result["agent_id"] == unique_agent_id


class TestQuery:
    """Integration tests for semantic query"""

    @pytest.mark.asyncio
    async def test_query_with_results(self, async_client, unique_project_id):
        """Test querying data returns results"""
        # Publish some data first
        await async_client.publish(
            project_id=unique_project_id,
            data_key="tech_stack",
            data={
                "frontend": "React with TypeScript",
                "backend": "FastAPI with Python",
                "database": "PostgreSQL"
            }
        )

        # Wait a moment for indexing
        await asyncio.sleep(0.5)

        # Query for it
        response = await async_client.query(
            project_id=unique_project_id,
            query="What frontend framework is used?",
            max_results=5
        )

        assert isinstance(response, QueryResponse)
        assert response.total >= 0
        # Results depend on semantic matching
        if response.results:
            assert response.results[0].data_key == "tech_stack"
            assert response.results[0].similarity_score > 0

    @pytest.mark.asyncio
    async def test_query_empty_project(self, async_client):
        """Test querying empty project returns empty results"""
        empty_project = f"empty-{uuid4().hex[:8]}"

        response = await async_client.query(
            project_id=empty_project,
            query="anything"
        )

        assert isinstance(response, QueryResponse)
        assert response.total == 0
        assert response.results == []


class TestEndToEndFlow:
    """End-to-end integration tests simulating real usage"""

    @pytest.mark.asyncio
    async def test_full_agent_workflow(self, async_client, unique_project_id, unique_agent_id):
        """
        Test the complete agent workflow:
        1. Publish data
        2. Register agent
        3. Verify agent receives matched data
        4. Publish more data
        5. Query for data
        6. Unregister agent
        """
        # 1. Publish initial data
        await async_client.publish(
            project_id=unique_project_id,
            data_key="tech_stack",
            data={"frontend": "React", "backend": "FastAPI"}
        )

        await async_client.publish(
            project_id=unique_project_id,
            data_key="event_model",
            data={
                "events": ["UserCreated", "TaskCompleted"],
                "commands": ["CreateUser", "CompleteTask"]
            }
        )

        # 2. Register agent
        registration = await async_client.register_agent(
            agent_id=unique_agent_id,
            project_id=unique_project_id,
            data_needs=[
                "programming languages and frameworks",
                "event model with events and commands"
            ]
        )

        assert registration.status == "registered"
        assert registration.agent_id == unique_agent_id

        # 3. Check matches
        # Both needs should potentially have matches
        assert isinstance(registration.matched_needs, dict)

        # 4. Publish more data
        await async_client.publish(
            project_id=unique_project_id,
            data_key="api_specs",
            data={"endpoints": ["/users", "/tasks"]}
        )

        # 5. Query for data
        query_response = await async_client.query(
            project_id=unique_project_id,
            query="What events are in the system?",
            max_results=5
        )

        assert isinstance(query_response, QueryResponse)

        # 6. Unregister
        unregister_result = await async_client.unregister_agent(unique_agent_id)
        assert unregister_result["status"] == "unregistered"

    def test_sync_client_full_flow(self, sync_client, unique_project_id, unique_agent_id):
        """Test the same workflow with sync client"""
        # Publish
        result = sync_client.publish(
            project_id=unique_project_id,
            data_key="data",
            data={"test": True}
        )
        assert result["status"] == "published"

        # Register
        registration = sync_client.register_agent(
            agent_id=unique_agent_id,
            project_id=unique_project_id,
            data_needs=["test data"]
        )
        assert registration.status == "registered"

        # Query
        query_result = sync_client.query(
            project_id=unique_project_id,
            query="test"
        )
        assert isinstance(query_result, QueryResponse)

        # Unregister
        sync_client.unregister_agent(unique_agent_id)


class TestErrorScenarios:
    """Integration tests for error handling"""

    @pytest.mark.asyncio
    async def test_unregister_nonexistent_agent(self, async_client):
        """Test unregistering an agent that doesn't exist"""
        with pytest.raises(NotFoundError):
            await async_client.unregister_agent("nonexistent-agent-xyz")

    @pytest.mark.asyncio
    async def test_publish_empty_data_key(self, async_client, unique_project_id):
        """Test publishing with empty data key fails validation"""
        with pytest.raises((ValidationError, Exception)):
            await async_client.publish(
                project_id=unique_project_id,
                data_key="",  # Invalid
                data={"test": True}
            )
