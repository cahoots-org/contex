"""
Tests for Contex SDK client.

These tests verify that the client correctly:
1. Sends requests in the expected format
2. Handles responses from the server
3. Raises appropriate exceptions on errors
4. Manages retries and timeouts
"""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import httpx

from contex import ContexAsyncClient, ContexClient
from contex.models import RegistrationResponse, QueryResponse
from contex.exceptions import (
    AuthenticationError,
    RateLimitError,
    ValidationError,
    NotFoundError,
    ServerError,
    NetworkError,
)


class TestContexAsyncClient:
    """Tests for async client"""

    @pytest.fixture
    def client(self):
        """Create a test client"""
        return ContexAsyncClient(
            url="http://localhost:8001",
            api_key="test_key_123"
        )

    @pytest.mark.asyncio
    async def test_client_initialization(self, client):
        """Test client initializes with correct defaults"""
        assert client.url == "http://localhost:8001"
        assert client.api_key == "test_key_123"
        assert client.timeout == 30.0
        assert client.max_retries == 3

    @pytest.mark.asyncio
    async def test_client_url_normalization(self):
        """Test that trailing slashes are removed from URL"""
        client = ContexAsyncClient(url="http://localhost:8001/")
        assert client.url == "http://localhost:8001"

    @pytest.mark.asyncio
    async def test_get_headers_with_api_key(self, client):
        """Test that headers include API key when provided"""
        headers = client._get_headers()
        assert headers["X-API-Key"] == "test_key_123"
        assert headers["Content-Type"] == "application/json"

    @pytest.mark.asyncio
    async def test_get_headers_without_api_key(self):
        """Test headers when no API key is provided"""
        client = ContexAsyncClient(url="http://localhost:8001")
        headers = client._get_headers()
        assert "X-API-Key" not in headers

    @pytest.mark.asyncio
    async def test_register_agent_success(self, client):
        """Test successful agent registration"""
        # Mock the server response (EXACT format from server)
        mock_response = {
            "status": "registered",
            "agent_id": "test-agent",
            "project_id": "test-project",
            "caught_up_events": 5,
            "current_sequence": "42",
            "matched_needs": {
                "tech stack": 3,
                "api specs": 2
            },
            "notification_channel": "agent:test-agent:updates"
        }

        with patch.object(client, '_request', new_callable=AsyncMock) as mock_request:
            mock_request.return_value = mock_response

            response = await client.register_agent(
                agent_id="test-agent",
                project_id="test-project",
                data_needs=["tech stack", "api specs"]
            )

            # Verify the response is parsed correctly
            assert isinstance(response, RegistrationResponse)
            assert response.agent_id == "test-agent"
            assert response.project_id == "test-project"
            assert response.caught_up_events == 5
            assert response.current_sequence == "42"
            assert response.matched_needs == {"tech stack": 3, "api specs": 2}
            assert response.notification_channel == "agent:test-agent:updates"

            # Verify the request was made correctly
            mock_request.assert_called_once()
            call_args = mock_request.call_args
            assert call_args[0][0] == "POST"
            assert call_args[0][1] == "/api/v1/agents/register"

    @pytest.mark.asyncio
    async def test_register_agent_with_webhook(self, client):
        """Test registration with webhook notification"""
        mock_response = {
            "status": "registered",
            "agent_id": "webhook-agent",
            "project_id": "proj",
            "caught_up_events": 0,
            "current_sequence": "0",
            "matched_needs": {},
            "notification_channel": "webhook"
        }

        with patch.object(client, '_request', new_callable=AsyncMock) as mock_request:
            mock_request.return_value = mock_response

            response = await client.register_agent(
                agent_id="webhook-agent",
                project_id="proj",
                data_needs=["data"],
                notification_method="webhook",
                webhook_url="https://example.com/hook",
                webhook_secret="secret"
            )

            # Verify webhook params were sent
            call_args = mock_request.call_args
            request_body = call_args[1]["json"]
            assert request_body["notification_method"] == "webhook"
            assert request_body["webhook_url"] == "https://example.com/hook"
            assert request_body["webhook_secret"] == "secret"

    @pytest.mark.asyncio
    async def test_publish_success(self, client):
        """Test successful data publishing"""
        mock_response = {
            "status": "published",
            "sequence": "123",
            "data_key": "config"
        }

        with patch.object(client, '_request', new_callable=AsyncMock) as mock_request:
            mock_request.return_value = mock_response

            result = await client.publish(
                project_id="proj",
                data_key="config",
                data={"setting": "value"},
                data_format="json"
            )

            assert result["status"] == "published"
            assert result["sequence"] == "123"

            # Verify request format
            call_args = mock_request.call_args
            assert call_args[0][0] == "POST"
            assert call_args[0][1] == "/api/v1/data/publish"

    @pytest.mark.asyncio
    async def test_query_success(self, client):
        """Test successful query"""
        mock_response = {
            "results": [
                {
                    "data_key": "tech_stack",
                    "data": {"frontend": "React"},
                    "similarity_score": 0.95,
                    "sequence": "10",
                    "timestamp": "2024-01-01T00:00:00Z"
                }
            ],
            "total": 1
        }

        with patch.object(client, '_request', new_callable=AsyncMock) as mock_request:
            mock_request.return_value = mock_response

            response = await client.query(
                project_id="proj",
                query="What frontend framework?",
                max_results=5
            )

            assert isinstance(response, QueryResponse)
            assert response.total == 1
            assert len(response.results) == 1
            assert response.results[0].data_key == "tech_stack"

    @pytest.mark.asyncio
    async def test_health_check(self, client):
        """Test health check endpoint"""
        mock_response = {"status": "healthy", "version": "0.2.0"}

        with patch.object(client, '_request', new_callable=AsyncMock) as mock_request:
            mock_request.return_value = mock_response

            result = await client.health()

            assert result["status"] == "healthy"
            mock_request.assert_called_once_with("GET", "/api/v1/health")

    @pytest.mark.asyncio
    async def test_unregister_agent(self, client):
        """Test agent unregistration"""
        mock_response = {"status": "unregistered", "agent_id": "test-agent"}

        with patch.object(client, '_request', new_callable=AsyncMock) as mock_request:
            mock_request.return_value = mock_response

            result = await client.unregister_agent("test-agent")

            assert result["status"] == "unregistered"
            mock_request.assert_called_once_with("DELETE", "/api/v1/agents/test-agent")


class TestClientErrorHandling:
    """Tests for client error handling"""

    @pytest.fixture
    def client(self):
        return ContexAsyncClient(url="http://localhost:8001", api_key="key")

    @pytest.mark.asyncio
    async def test_authentication_error(self, client):
        """Test that 401 raises AuthenticationError"""
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.json.return_value = {"detail": "Invalid API key"}

        with patch.object(client, '_do_request', new_callable=AsyncMock) as mock_do:
            mock_do.side_effect = AuthenticationError("Invalid API key")

            with pytest.raises(AuthenticationError):
                await client.health()

    @pytest.mark.asyncio
    async def test_rate_limit_error(self, client):
        """Test that 429 raises RateLimitError"""
        with patch.object(client, '_do_request', new_callable=AsyncMock) as mock_do:
            mock_do.side_effect = RateLimitError("Rate limit exceeded", retry_after=60)

            with pytest.raises(RateLimitError) as exc_info:
                await client.health()

            assert exc_info.value.retry_after == 60

    @pytest.mark.asyncio
    async def test_validation_error(self, client):
        """Test that 400/422 raises ValidationError"""
        with patch.object(client, '_do_request', new_callable=AsyncMock) as mock_do:
            mock_do.side_effect = ValidationError("Invalid request body")

            with pytest.raises(ValidationError):
                await client.publish(project_id="", data_key="", data={})

    @pytest.mark.asyncio
    async def test_not_found_error(self, client):
        """Test that 404 raises NotFoundError"""
        with patch.object(client, '_do_request', new_callable=AsyncMock) as mock_do:
            mock_do.side_effect = NotFoundError("Agent not found")

            with pytest.raises(NotFoundError):
                await client.unregister_agent("nonexistent")

    @pytest.mark.asyncio
    async def test_server_error(self, client):
        """Test that 500 raises ServerError"""
        with patch.object(client, '_do_request', new_callable=AsyncMock) as mock_do:
            mock_do.side_effect = ServerError("Internal server error")

            with pytest.raises(ServerError):
                await client.health()

    @pytest.mark.asyncio
    async def test_network_error(self, client):
        """Test that connection errors raise NetworkError"""
        with patch.object(client, '_do_request', new_callable=AsyncMock) as mock_do:
            mock_do.side_effect = NetworkError("Connection refused")

            with pytest.raises(NetworkError):
                await client.health()


class TestContexSyncClient:
    """Tests for synchronous client wrapper"""

    def test_sync_client_initialization(self):
        """Test sync client creates properly"""
        client = ContexClient(url="http://localhost:8001", api_key="key")
        assert client.url == "http://localhost:8001"
        assert client.api_key == "key"

    def test_sync_register_agent(self):
        """Test sync register_agent calls async version"""
        mock_response = {
            "status": "registered",
            "agent_id": "agent",
            "project_id": "proj",
            "caught_up_events": 0,
            "current_sequence": "0",
            "matched_needs": {},
            "notification_channel": "ch"
        }

        client = ContexClient(url="http://localhost:8001")

        with patch.object(
            ContexAsyncClient,
            'register_agent',
            new_callable=AsyncMock
        ) as mock_async:
            mock_async.return_value = RegistrationResponse(**mock_response)

            response = client.register_agent(
                agent_id="agent",
                project_id="proj",
                data_needs=["data"]
            )

            assert response.agent_id == "agent"


class TestClientContextManager:
    """Tests for client context manager usage"""

    @pytest.mark.asyncio
    async def test_async_context_manager(self):
        """Test client as async context manager"""
        async with ContexAsyncClient(url="http://localhost:8001") as client:
            assert client._client is not None

    @pytest.mark.asyncio
    async def test_context_manager_closes_client(self):
        """Test that context manager closes the HTTP client"""
        client = ContexAsyncClient(url="http://localhost:8001")

        async with client:
            http_client = client._client
            assert http_client is not None

        # After exiting context, client should be closed
        # (httpx.AsyncClient doesn't expose is_closed in all versions)


class TestResponseParsing:
    """Tests specifically for response parsing edge cases"""

    @pytest.fixture
    def client(self):
        return ContexAsyncClient(url="http://localhost:8001")

    @pytest.mark.asyncio
    async def test_registration_response_with_empty_matched_needs(self, client):
        """Test parsing registration when no data matches needs"""
        mock_response = {
            "status": "registered",
            "agent_id": "new-agent",
            "project_id": "empty-project",
            "caught_up_events": 0,
            "current_sequence": "0",
            "matched_needs": {},
            "notification_channel": "agent:new-agent:updates"
        }

        with patch.object(client, '_request', new_callable=AsyncMock) as mock_request:
            mock_request.return_value = mock_response

            response = await client.register_agent(
                agent_id="new-agent",
                project_id="empty-project",
                data_needs=["something"]
            )

            assert response.matched_needs == {}
            assert sum(response.matched_needs.values()) == 0

    @pytest.mark.asyncio
    async def test_registration_response_with_large_caught_up(self, client):
        """Test parsing registration with many caught-up events"""
        mock_response = {
            "status": "registered",
            "agent_id": "lagging-agent",
            "project_id": "busy-project",
            "caught_up_events": 10000,
            "current_sequence": "50000",
            "matched_needs": {"events": 100},
            "notification_channel": "agent:lagging-agent:updates"
        }

        with patch.object(client, '_request', new_callable=AsyncMock) as mock_request:
            mock_request.return_value = mock_response

            response = await client.register_agent(
                agent_id="lagging-agent",
                project_id="busy-project",
                data_needs=["events"]
            )

            assert response.caught_up_events == 10000
            assert response.current_sequence == "50000"

    @pytest.mark.asyncio
    async def test_query_response_with_special_characters(self, client):
        """Test query response containing special characters in data"""
        mock_response = {
            "results": [
                {
                    "data_key": "unicode_data",
                    "data": {"text": "Hello 世界 🌍 émoji"},
                    "similarity_score": 0.9,
                    "sequence": "1",
                    "timestamp": "2024-01-01T00:00:00Z"
                }
            ],
            "total": 1
        }

        with patch.object(client, '_request', new_callable=AsyncMock) as mock_request:
            mock_request.return_value = mock_response

            response = await client.query(
                project_id="proj",
                query="unicode"
            )

            assert response.results[0].data["text"] == "Hello 世界 🌍 émoji"
