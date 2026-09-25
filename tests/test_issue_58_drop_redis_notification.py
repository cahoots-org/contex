"""Regression tests for issue #58: drop the client-facing Redis notification path.

Clients must not choose a Redis pub/sub channel. External delivery is
webhook-only; MCP clients receive push via the internal mcp_bridge. Redis stays
server-internal. These tests pin:

* ``notification_method`` / ``notification_channel`` are rejected on the request
  model (422 at the API boundary).
* Delivery is inferred from ``webhook_url``: present -> webhook; absent ->
  MCP-only with no direct publish to a client channel.
* The internal ``subscription:*:updated`` pub/sub still fires and the
  mcp_bridge still relays it (the "context comes to the agent" push).
"""

import asyncio

import numpy as np
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, Mock, patch

from pydantic import ValidationError

from src.core import mcp_bridge
from src.core.context_engine import ContextEngine
from src.core.models import AgentRegistration, DataPublishEvent


class TestRequestModelRejectsRedisFields:
    def test_notification_method_is_rejected(self):
        with pytest.raises(ValidationError):
            AgentRegistration(
                agent_id="a1",
                project_id="p1",
                data_needs=["data"],
                notification_method="redis",
            )

    def test_notification_channel_is_rejected(self):
        with pytest.raises(ValidationError):
            AgentRegistration(
                agent_id="a1",
                project_id="p1",
                data_needs=["data"],
                notification_channel="victim:channel",
            )


class TestInferredDelivery:
    @pytest_asyncio.fixture
    async def context_engine(self, db, redis):
        with patch("src.core.semantic_matcher.SentenceTransformer") as mock_model_cls:
            mock_model = Mock()
            mock_model.encode.side_effect = lambda x, *a, **k: (
                np.array([0.1] * 384, dtype=np.float32)
                if isinstance(x, str)
                else np.array([[0.1] * 384] * len(x), dtype=np.float32)
            )
            mock_model_cls.return_value = mock_model

            engine = ContextEngine(
                db=db, redis=redis, similarity_threshold=0.5, max_matches=10
            )
            await engine.semantic_matcher.initialize_index()
            return engine

    @pytest.mark.asyncio
    async def test_webhook_url_present_uses_webhook_delivery(self, context_engine):
        registration = AgentRegistration(
            agent_id="webhook-agent",
            project_id="proj1",
            data_needs=["API documentation"],
            webhook_url="https://example.com/webhook",
            webhook_secret="secret",
        )
        with patch.object(
            context_engine.webhook_dispatcher,
            "send_initial_context",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_send:
            response = await context_engine.register_agent(registration)

        assert response.status == "registered"
        assert mock_send.called
        info = context_engine.get_agent_info("webhook-agent")
        assert info["notification_method"] == "webhook"
        assert info["webhook_url"] == "https://example.com/webhook"

    @pytest.mark.asyncio
    async def test_no_webhook_url_does_not_publish_to_client_channel(
        self, context_engine
    ):
        """MCP-only registration must not publish to a per-agent Redis channel."""
        context_engine.redis.publish = AsyncMock()

        await context_engine.register_agent(
            AgentRegistration(
                agent_id="mcp-agent", project_id="proj1", data_needs=["data"]
            )
        )

        published_channels = [
            call.args[0] for call in context_engine.redis.publish.await_args_list
        ]
        assert not any(
            ch.startswith("agent:") for ch in published_channels
        ), f"unexpected client-channel publish: {published_channels}"
        assert context_engine.get_agent_info("mcp-agent")["notification_method"] == "mcp"

    @pytest.mark.asyncio
    async def test_data_update_to_mcp_agent_skips_client_channel(self, context_engine):
        await context_engine.register_agent(
            AgentRegistration(
                agent_id="mcp-agent", project_id="proj1", data_needs=["config"]
            )
        )
        await context_engine.publish_data(
            DataPublishEvent(project_id="proj1", data_key="config", data={"k": "v"})
        )

        context_engine.redis.publish = AsyncMock()
        await context_engine.publish_data(
            DataPublishEvent(project_id="proj1", data_key="config", data={"k": "v2"})
        )

        published_channels = [
            call.args[0] for call in context_engine.redis.publish.await_args_list
        ]
        assert not any(ch.startswith("agent:") for ch in published_channels)


class TestInternalSubscriptionBridgePreserved:
    @pytest.mark.asyncio
    async def test_subscription_updated_relays_to_mcp_bridge(self, redis):
        """Internal subscription:*:updated must still reach the MCP bridge.

        Publishing an update on the internal channel while the bridge is
        psubscribed must produce a ResourceUpdated on the MCP bus.
        """
        bus = Mock()
        bus.publish = AsyncMock()
        stop_event = asyncio.Event()

        task = asyncio.create_task(mcp_bridge.run_bridge(redis, bus, stop_event))
        await asyncio.sleep(0.1)

        import json

        await redis.publish(
            "subscription:sub_123:updated",
            json.dumps({"subscription_id": "sub_123", "updated_at": "now"}),
        )

        for _ in range(30):
            if bus.publish.await_count:
                break
            await asyncio.sleep(0.1)

        stop_event.set()
        await task

        assert bus.publish.await_count >= 1
        published = bus.publish.await_args.args[0]
        assert published.uri == mcp_bridge.resource_uri_for("sub_123")
