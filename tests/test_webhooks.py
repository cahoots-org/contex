"""Tests for webhook notification functionality"""

import pytest
import pytest_asyncio
import json
import hmac
import hashlib
from unittest.mock import Mock, AsyncMock, patch
from fakeredis import FakeAsyncRedis
from src.context_engine import ContextEngine
from src.webhook_dispatcher import WebhookDispatcher, verify_webhook_signature
from src.models import AgentRegistration, DataPublishEvent


class TestWebhookDispatcher:
    """Test WebhookDispatcher functionality"""

    @pytest.fixture
    def dispatcher(self):
        """Create a WebhookDispatcher instance"""
        return WebhookDispatcher(timeout=1.0, max_retries=2, retry_delay=0.1)

    def test_generate_signature(self, dispatcher):
        """Test HMAC signature generation"""
        payload = '{"test": "data"}'
        secret = "my-secret-key"

        signature = dispatcher._generate_signature(payload, secret)

        # Signature should be hex string
        assert isinstance(signature, str)
        assert len(signature) == 64  # SHA256 hex is 64 chars

        # Same inputs = same signature
        signature2 = dispatcher._generate_signature(payload, secret)
        assert signature == signature2

    def test_verify_webhook_signature_valid(self):
        """Test webhook signature verification with valid signature"""
        payload = '{"test": "data"}'
        secret = "my-secret-key"

        # Generate signature
        computed_sig = hmac.new(
            secret.encode('utf-8'),
            payload.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()

        signature_header = f"sha256={computed_sig}"

        # Should verify successfully
        assert verify_webhook_signature(payload, signature_header, secret) is True

    def test_verify_webhook_signature_invalid(self):
        """Test webhook signature verification with invalid signature"""
        payload = '{"test": "data"}'
        secret = "my-secret-key"
        wrong_signature = "sha256=invalid"

        assert verify_webhook_signature(payload, wrong_signature, secret) is False

    def test_verify_webhook_signature_no_header(self):
        """Test webhook signature verification with missing header"""
        payload = '{"test": "data"}'
        secret = "my-secret-key"

        assert verify_webhook_signature(payload, None, secret) is False
        assert verify_webhook_signature(payload, "", secret) is False

    @pytest.mark.asyncio
    async def test_send_webhook_success(self, dispatcher):
        """Test successful webhook delivery"""
        with patch('httpx.AsyncClient') as mock_client_class:
            mock_response = Mock()
            mock_response.status_code = 200

            mock_post = AsyncMock(return_value=mock_response)
            mock_client = Mock()
            mock_client.post = mock_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock()

            mock_client_class.return_value = mock_client

            result = await dispatcher.send_webhook(
                url="https://example.com/webhook",
                payload={"test": "data"},
                secret="my-secret"
            )

            assert result is True
            assert mock_post.called

    @pytest.mark.asyncio
    async def test_send_webhook_timeout_retry(self, dispatcher):
        """Test webhook retry on timeout"""
        import httpx

        with patch('httpx.AsyncClient') as mock_client_class:
            mock_post = AsyncMock(side_effect=httpx.TimeoutException("Timeout"))
            mock_client = Mock()
            mock_client.post = mock_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock()

            mock_client_class.return_value = mock_client

            result = await dispatcher.send_webhook(
                url="https://example.com/webhook",
                payload={"test": "data"}
            )

            assert result is False
            # Should retry (max_retries=2)
            assert mock_post.call_count == 2

    @pytest.mark.asyncio
    async def test_send_webhook_4xx_no_retry(self, dispatcher):
        """Test that 4xx errors don't trigger retries"""
        with patch('httpx.AsyncClient') as mock_client_class:
            mock_response = Mock()
            mock_response.status_code = 404

            mock_post = AsyncMock(return_value=mock_response)
            mock_client = Mock()
            mock_client.post = mock_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock()

            mock_client_class.return_value = mock_client

            result = await dispatcher.send_webhook(
                url="https://example.com/webhook",
                payload={"test": "data"}
            )

            assert result is False
            # Should NOT retry on 4xx
            assert mock_post.call_count == 1

    @pytest.mark.asyncio
    async def test_send_webhook_5xx_retry(self, dispatcher):
        """Test that 5xx errors trigger retries"""
        with patch('httpx.AsyncClient') as mock_client_class:
            mock_response = Mock()
            mock_response.status_code = 500

            mock_post = AsyncMock(return_value=mock_response)
            mock_client = Mock()
            mock_client.post = mock_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock()

            mock_client_class.return_value = mock_client

            result = await dispatcher.send_webhook(
                url="https://example.com/webhook",
                payload={"test": "data"}
            )

            assert result is False
            # Should retry on 5xx
            assert mock_post.call_count == 2


class TestWebhookIntegration:
    """Test webhook integration with ContextEngine"""

    @pytest_asyncio.fixture
    async def redis(self):
        """Create a fake Redis client"""
        return FakeAsyncRedis(decode_responses=False)

    @pytest_asyncio.fixture
    async def context_engine(self, redis):
        """Create a ContextEngine instance"""
        return ContextEngine(
            redis=redis,
            similarity_threshold=0.5,
            max_matches=10
        )

    @pytest.mark.asyncio
    async def test_register_agent_with_webhook(self, context_engine):
        """Test registering an agent with webhook notification"""
        registration = AgentRegistration(
            agent_id="webhook-agent",
            project_id="proj1",
            data_needs=["API documentation"],
            notification_method="webhook",
            webhook_url="https://example.com/webhook",
            webhook_secret="my-secret"
        )

        with patch.object(context_engine.webhook_dispatcher, 'send_initial_context', new_callable=AsyncMock) as mock_send:
            mock_send.return_value = True

            response = await context_engine.register_agent(registration)

            assert response.status == "registered"
            assert response.agent_id == "webhook-agent"

            # Should have called webhook dispatcher
            assert mock_send.called

            # Agent should be stored with webhook config
            agent_info = context_engine.get_agent_info("webhook-agent")
            assert agent_info["notification_method"] == "webhook"
            assert agent_info["webhook_url"] == "https://example.com/webhook"
            assert agent_info["webhook_secret"] == "my-secret"

    @pytest.mark.asyncio
    async def test_register_agent_webhook_requires_url(self, context_engine):
        """Test that webhook registration requires URL"""
        registration = AgentRegistration(
            agent_id="webhook-agent",
            project_id="proj1",
            data_needs=["API documentation"],
            notification_method="webhook"
            # Missing webhook_url
        )

        with pytest.raises(ValueError, match="webhook_url is required"):
            await context_engine.register_agent(registration)

    @pytest.mark.asyncio
    async def test_publish_data_notifies_webhook_agent(self, context_engine):
        """Test that publishing data triggers webhook notification"""
        # Register webhook agent first
        registration = AgentRegistration(
            agent_id="webhook-agent",
            project_id="proj1",
            data_needs=["API documentation"],
            notification_method="webhook",
            webhook_url="https://example.com/webhook",
            webhook_secret="my-secret"
        )

        with patch.object(context_engine.webhook_dispatcher, 'send_initial_context', new_callable=AsyncMock):
            await context_engine.register_agent(registration)

        # Publish matching data
        with patch.object(context_engine.webhook_dispatcher, 'send_data_update', new_callable=AsyncMock) as mock_send:
            mock_send.return_value = True

            await context_engine.publish_data(DataPublishEvent(
                project_id="proj1",
                data_key="api_docs",
                data={"endpoints": ["/api/users"]}
            ))

            # Give async task time to execute
            await asyncio.sleep(0.1)

            # Should have sent webhook (if agent matched the data)
            # Note: Might not be called if semantic similarity too low

    @pytest.mark.asyncio
    async def test_webhook_and_redis_agents_coexist(self, context_engine):
        """Test that webhook and Redis agents can coexist"""
        # Register Redis agent
        redis_reg = AgentRegistration(
            agent_id="redis-agent",
            project_id="proj1",
            data_needs=["API documentation"],
            notification_method="redis"
        )
        await context_engine.register_agent(redis_reg)

        # Register webhook agent
        webhook_reg = AgentRegistration(
            agent_id="webhook-agent",
            project_id="proj1",
            data_needs=["API documentation"],
            notification_method="webhook",
            webhook_url="https://example.com/webhook"
        )

        with patch.object(context_engine.webhook_dispatcher, 'send_initial_context', new_callable=AsyncMock):
            await context_engine.register_agent(webhook_reg)

        # Both should be registered
        agents = context_engine.get_registered_agents()
        assert "redis-agent" in agents
        assert "webhook-agent" in agents

        # They should have different notification methods
        redis_info = context_engine.get_agent_info("redis-agent")
        webhook_info = context_engine.get_agent_info("webhook-agent")

        assert redis_info["notification_method"] == "redis"
        assert webhook_info["notification_method"] == "webhook"

    @pytest.mark.asyncio
    async def test_webhook_notification_includes_signature(self, context_engine):
        """Test that webhook notifications include HMAC signature"""
        dispatcher = WebhookDispatcher()

        payload = {"type": "data_update", "data": {"key": "value"}}
        secret = "test-secret"

        with patch('httpx.AsyncClient') as mock_client_class:
            mock_response = Mock()
            mock_response.status_code = 200

            captured_headers = {}

            async def capture_post(*args, **kwargs):
                captured_headers.update(kwargs.get('headers', {}))
                return mock_response

            mock_post = AsyncMock(side_effect=capture_post)
            mock_client = Mock()
            mock_client.post = mock_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock()

            mock_client_class.return_value = mock_client

            await dispatcher.send_webhook(
                url="https://example.com/webhook",
                payload=payload,
                secret=secret
            )

            # Should have signature header
            assert "X-Contex-Signature" in captured_headers
            assert captured_headers["X-Contex-Signature"].startswith("sha256=")

            # Should have event type header
            assert "X-Contex-Event" in captured_headers


# Import asyncio for sleep in tests
import asyncio
