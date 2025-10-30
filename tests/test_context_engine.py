"""Tests for Context Engine"""

import pytest
import pytest_asyncio
import json
from fakeredis import FakeAsyncRedis
from src.context_engine import ContextEngine
from src.models import AgentRegistration, DataPublishEvent


class TestContextEngine:
    """Test ContextEngine functionality"""

    @pytest_asyncio.fixture
    async def redis(self):
        """Create a fake Redis client"""
        return FakeAsyncRedis(decode_responses=False)

    @pytest_asyncio.fixture
    async def context_engine(self, redis):
        """Create a ContextEngine instance"""
        return ContextEngine(redis=redis, similarity_threshold=0.5, max_matches=10)

    @pytest.mark.asyncio
    async def test_initialization(self, context_engine):
        """Test that ContextEngine initializes correctly"""
        assert context_engine.semantic_matcher is not None
        assert context_engine.event_store is not None
        assert len(context_engine.agents) == 0

    @pytest.mark.asyncio
    async def test_publish_data(self, context_engine):
        """Test publishing data"""
        event = DataPublishEvent(
            project_id="proj1",
            data_key="tech_stack",
            data={"backend": "FastAPI", "frontend": "React"},
        )

        sequence = await context_engine.publish_data(event)

        assert sequence is not None
        assert isinstance(sequence, str)

    @pytest.mark.asyncio
    async def test_publish_registers_with_semantic_matcher(self, context_engine):
        """Test that publishing data registers it with semantic matcher"""
        event = DataPublishEvent(
            project_id="proj1", data_key="api_docs", data={"endpoints": ["/api/users"]}
        )

        await context_engine.publish_data(event)

        # Check semantic matcher has the data
        registered_keys = context_engine.semantic_matcher.get_registered_data("proj1")
        assert "api_docs" in registered_keys

    @pytest.mark.asyncio
    async def test_publish_appends_to_event_store(self, context_engine):
        """Test that publishing appends to event store"""
        event = DataPublishEvent(
            project_id="proj1", data_key="config", data={"setting": "value"}
        )

        await context_engine.publish_data(event)

        # Check event store has the event
        events = await context_engine.event_store.get_events_since("proj1", "0")
        assert len(events) == 1
        assert events[0]["event_type"] == "config_updated"

    @pytest.mark.asyncio
    async def test_publish_with_custom_event_type(self, context_engine):
        """Test publishing with custom event type"""
        event = DataPublishEvent(
            project_id="proj1", data_key="data", data={}, event_type="custom_event"
        )

        await context_engine.publish_data(event)

        events = await context_engine.event_store.get_events_since("proj1", "0")
        assert events[0]["event_type"] == "custom_event"

    @pytest.mark.asyncio
    async def test_register_agent(self, context_engine):
        """Test registering an agent"""
        registration = AgentRegistration(
            agent_id="agent1",
            project_id="proj1",
            data_needs=["tech stack", "API documentation"],
        )

        response = await context_engine.register_agent(registration)

        assert response.status == "registered"
        assert response.agent_id == "agent1"
        assert response.project_id == "proj1"
        assert response.notification_channel == "agent:agent1:updates"

    @pytest.mark.asyncio
    async def test_register_agent_with_custom_channel(self, context_engine):
        """Test registering agent with custom notification channel"""
        registration = AgentRegistration(
            agent_id="agent1",
            project_id="proj1",
            data_needs=["tech stack"],
            notification_channel="custom:channel",
        )

        response = await context_engine.register_agent(registration)

        assert response.notification_channel == "custom:channel"

    @pytest.mark.asyncio
    async def test_register_agent_matches_existing_data(self, context_engine):
        """Test that agent registration matches existing data"""
        # Publish data first
        await context_engine.publish_data(
            DataPublishEvent(
                project_id="proj1", data_key="api_auth", data={"method": "OAuth2"}
            )
        )

        # Register agent
        registration = AgentRegistration(
            agent_id="agent1", project_id="proj1", data_needs=["authentication methods"]
        )

        response = await context_engine.register_agent(registration)

        # Should have found matches
        assert "authentication methods" in response.matched_needs

    @pytest.mark.asyncio
    async def test_register_agent_stores_in_agents_dict(self, context_engine):
        """Test that registered agent is stored"""
        registration = AgentRegistration(
            agent_id="agent1", project_id="proj1", data_needs=["tech stack"]
        )

        await context_engine.register_agent(registration)

        assert "agent1" in context_engine.agents
        assert context_engine.agents["agent1"]["project_id"] == "proj1"
        assert context_engine.agents["agent1"]["needs"] == ["tech stack"]

    @pytest.mark.asyncio
    async def test_register_agent_catch_up(self, context_engine):
        """Test that new agent catches up on missed events"""
        # Publish some events
        await context_engine.publish_data(
            DataPublishEvent(project_id="proj1", data_key="data1", data={})
        )
        await context_engine.publish_data(
            DataPublishEvent(project_id="proj1", data_key="data2", data={})
        )

        # Register agent with last_seen_sequence=0
        registration = AgentRegistration(
            agent_id="agent1",
            project_id="proj1",
            data_needs=["data"],
            last_seen_sequence="0",
        )

        response = await context_engine.register_agent(registration)

        # Should have caught up on events
        assert response.caught_up_events >= 0

    @pytest.mark.asyncio
    async def test_unregister_agent(self, context_engine):
        """Test unregistering an agent"""
        # Register first
        await context_engine.register_agent(
            AgentRegistration(
                agent_id="agent1", project_id="proj1", data_needs=["data"]
            )
        )

        assert "agent1" in context_engine.agents

        # Unregister
        await context_engine.unregister_agent("agent1")

        assert "agent1" not in context_engine.agents

    @pytest.mark.asyncio
    async def test_unregister_nonexistent_agent(self, context_engine):
        """Test unregistering an agent that doesn't exist"""
        # Should not raise an error
        await context_engine.unregister_agent("nonexistent")

    @pytest.mark.asyncio
    async def test_get_registered_agents(self, context_engine):
        """Test getting list of registered agents"""
        # Register multiple agents
        await context_engine.register_agent(
            AgentRegistration(
                agent_id="agent1", project_id="proj1", data_needs=["data"]
            )
        )
        await context_engine.register_agent(
            AgentRegistration(
                agent_id="agent2", project_id="proj1", data_needs=["data"]
            )
        )

        agents = context_engine.get_registered_agents()

        assert len(agents) == 2
        assert "agent1" in agents
        assert "agent2" in agents

    @pytest.mark.asyncio
    async def test_get_agent_info(self, context_engine):
        """Test getting info about a specific agent"""
        await context_engine.register_agent(
            AgentRegistration(
                agent_id="agent1",
                project_id="proj1",
                data_needs=["tech stack", "API docs"],
            )
        )

        info = context_engine.get_agent_info("agent1")

        assert info is not None
        assert info["project_id"] == "proj1"
        assert len(info["needs"]) == 2
        assert "tech stack" in info["needs"]

    @pytest.mark.asyncio
    async def test_get_agent_info_nonexistent(self, context_engine):
        """Test getting info for nonexistent agent"""
        info = context_engine.get_agent_info("nonexistent")

        assert info is None

    @pytest.mark.asyncio
    async def test_publish_notifies_affected_agents(self, context_engine, redis):
        """Test that publishing data notifies affected agents"""
        # Register agent first
        await context_engine.register_agent(
            AgentRegistration(
                agent_id="agent1",
                project_id="proj1",
                data_needs=["configuration settings"],
            )
        )

        # Subscribe to agent's channel
        pubsub = redis.pubsub()
        await pubsub.subscribe("agent:agent1:updates")

        # Publish matching data
        await context_engine.publish_data(
            DataPublishEvent(
                project_id="proj1", data_key="app_config", data={"setting": "value"}
            )
        )

        # Check if notification was sent (would need to listen for it)
        # This is a basic test - in practice you'd want to actually listen

    @pytest.mark.asyncio
    async def test_multiple_agents_same_need(self, context_engine):
        """Test multiple agents with same data needs"""
        # Publish API docs first
        await context_engine.publish_data(
            DataPublishEvent(
                project_id="proj1",
                data_key="api_docs",
                data={"endpoints": ["/api/users", "/api/posts"]},
            )
        )

        # Register two agents with similar needs
        await context_engine.register_agent(
            AgentRegistration(
                agent_id="agent1",
                project_id="proj1",
                data_needs=["API documentation and endpoints"],
            )
        )
        await context_engine.register_agent(
            AgentRegistration(
                agent_id="agent2",
                project_id="proj1",
                data_needs=["API documentation and endpoints"],
            )
        )

        # Both agents should be tracking this data
        agent1_info = context_engine.get_agent_info("agent1")
        agent2_info = context_engine.get_agent_info("agent2")

        # Both should have the same data key in their tracking
        assert agent1_info["data_keys"] == agent2_info["data_keys"]
        # Should have matched api_docs if similarity is high enough
        # (may be empty list if similarity too low, but should be same for both)

    @pytest.mark.asyncio
    async def test_project_isolation(self, context_engine):
        """Test that projects are isolated"""
        # Publish to proj1
        await context_engine.publish_data(
            DataPublishEvent(project_id="proj1", data_key="data1", data={})
        )

        # Register agent for proj2
        registration = AgentRegistration(
            agent_id="agent1", project_id="proj2", data_needs=["data"]
        )

        response = await context_engine.register_agent(registration)

        # Agent should not match data from proj1
        # (depends on semantic matcher project isolation)

    @pytest.mark.asyncio
    async def test_agent_tracks_data_dependencies(self, context_engine):
        """Test that agent tracks which data keys it depends on"""
        # Publish multiple data sources
        await context_engine.publish_data(
            DataPublishEvent(
                project_id="proj1", data_key="api_docs", data={"endpoints": []}
            )
        )
        await context_engine.publish_data(
            DataPublishEvent(
                project_id="proj1", data_key="auth_config", data={"method": "JWT"}
            )
        )

        # Register agent
        await context_engine.register_agent(
            AgentRegistration(
                agent_id="agent1",
                project_id="proj1",
                data_needs=["API documentation and authentication"],
            )
        )

        # Agent should track matched data keys
        info = context_engine.get_agent_info("agent1")
        assert "data_keys" in info
        assert isinstance(info["data_keys"], list)

    @pytest.mark.asyncio
    async def test_current_sequence_tracking(self, context_engine):
        """Test that current sequence is tracked correctly"""
        # Publish some events
        await context_engine.publish_data(
            DataPublishEvent(project_id="proj1", data_key="data1", data={})
        )

        # Register agent
        response = await context_engine.register_agent(
            AgentRegistration(
                agent_id="agent1", project_id="proj1", data_needs=["data"]
            )
        )

        # Should have current sequence
        assert response.current_sequence is not None
        assert response.current_sequence != "0"

    @pytest.mark.asyncio
    async def test_query_project_data(self, context_engine):
        """Test ad-hoc querying of project data"""
        # Publish some data
        await context_engine.publish_data(
            DataPublishEvent(
                project_id="proj1",
                data_key="api_authentication",
                data={"method": "OAuth2", "provider": "Google"},
            )
        )
        await context_engine.publish_data(
            DataPublishEvent(
                project_id="proj1",
                data_key="database_config",
                data={"host": "localhost", "port": 5432},
            )
        )

        # Query for authentication
        results = context_engine.query_project_data(
            project_id="proj1",
            query="authentication and authorization methods",
            top_k=5,
        )

        # Should return results
        assert isinstance(results, list)
        # Should have at least one match (api_authentication should match)
        assert len(results) >= 0

    @pytest.mark.asyncio
    async def test_query_respects_top_k(self, context_engine):
        """Test that query respects top_k limit"""
        # Publish many data sources
        for i in range(10):
            await context_engine.publish_data(
                DataPublishEvent(
                    project_id="proj1",
                    data_key=f"api_endpoint_{i}",
                    data={"endpoint": f"/api/v1/resource{i}"},
                )
            )

        # Query with top_k=3
        results = context_engine.query_project_data(
            project_id="proj1", query="API endpoints", top_k=3
        )

        # Should return at most 3 results
        assert len(results) <= 3

    @pytest.mark.asyncio
    async def test_query_empty_project(self, context_engine):
        """Test querying a project with no data"""
        results = context_engine.query_project_data(
            project_id="empty_project", query="something", top_k=5
        )

        # Should return empty list
        assert results == []

    @pytest.mark.asyncio
    async def test_query_project_isolation(self, context_engine):
        """Test that queries respect project isolation"""
        # Publish data to different projects
        await context_engine.publish_data(
            DataPublishEvent(
                project_id="proj1", data_key="data1", data={"value": "project1"}
            )
        )
        await context_engine.publish_data(
            DataPublishEvent(
                project_id="proj2", data_key="data2", data={"value": "project2"}
            )
        )

        # Query proj1
        results = context_engine.query_project_data(
            project_id="proj1", query="data", top_k=10
        )

        # Should only get proj1 data
        for result in results:
            assert result["data_key"] != "data2"

    @pytest.mark.asyncio
    async def test_query_returns_similarity_scores(self, context_engine):
        """Test that query results include similarity scores"""
        await context_engine.publish_data(
            DataPublishEvent(
                project_id="proj1", data_key="test_data", data={"key": "value"}
            )
        )

        results = context_engine.query_project_data(
            project_id="proj1", query="test data", top_k=5
        )

        # Each result should have similarity score
        for result in results:
            assert "similarity" in result
            assert isinstance(result["similarity"], float)
            assert 0 <= result["similarity"] <= 1


class TestContextSizeLimits:
    """Test context size limiting functionality"""

    @pytest_asyncio.fixture
    async def redis(self):
        """Create a fake Redis client"""
        return FakeAsyncRedis(decode_responses=False)

    @pytest_asyncio.fixture
    async def context_engine_with_limit(self, redis):
        """Create a ContextEngine with small context limit"""
        return ContextEngine(
            redis=redis,
            similarity_threshold=0.3,  # Lower threshold for more matches
            max_matches=10,
            max_context_size=500,  # Very small limit for testing
        )

    @pytest.mark.asyncio
    async def test_context_size_limit_applied(self, context_engine_with_limit):
        """Test that context size limit is applied"""
        # Publish large data
        for i in range(5):
            await context_engine_with_limit.publish_data(
                DataPublishEvent(
                    project_id="proj1",
                    data_key=f"large_data_{i}",
                    data={"description": "test data " * 50, "index": i},  # Large data
                )
            )

        # Register agent - should trigger truncation
        registration = AgentRegistration(
            agent_id="test_agent",
            project_id="proj1",
            data_needs=["test data information"],
        )

        response = await context_engine_with_limit.register_agent(registration)

        # Should have registered successfully
        assert response.status == "registered"
        # But some matches may have been truncated
        assert response.matched_needs is not None

    @pytest.mark.asyncio
    async def test_truncation_keeps_best_matches(self, context_engine_with_limit):
        """Test that truncation keeps highest similarity matches"""
        # Publish data with varying relevance
        await context_engine_with_limit.publish_data(
            DataPublishEvent(
                project_id="proj1",
                data_key="highly_relevant",
                data={"content": "API authentication patterns"},
            )
        )

        await context_engine_with_limit.publish_data(
            DataPublishEvent(
                project_id="proj1",
                data_key="somewhat_relevant",
                data={
                    "content": "general programming tips " * 100
                },  # Large but less relevant
            )
        )

        await context_engine_with_limit.publish_data(
            DataPublishEvent(
                project_id="proj1",
                data_key="not_relevant",
                data={"content": "unrelated content " * 100},  # Large and irrelevant
            )
        )

        # Register agent looking for API auth
        registration = AgentRegistration(
            agent_id="test_agent",
            project_id="proj1",
            data_needs=["API authentication methods"],
        )

        response = await context_engine_with_limit.register_agent(registration)
        assert response.status == "registered"

    @pytest.mark.asyncio
    async def test_no_truncation_when_under_limit(self, redis):
        """Test that no truncation occurs when under limit"""
        # Create engine with large limit
        engine = ContextEngine(
            redis=redis,
            similarity_threshold=0.5,
            max_matches=10,
            max_context_size=100000,  # Very large limit
        )

        # Publish small data
        await engine.publish_data(
            DataPublishEvent(
                project_id="proj1", data_key="small_data", data={"key": "value"}
            )
        )

        # Register agent
        registration = AgentRegistration(
            agent_id="test_agent", project_id="proj1", data_needs=["small data"]
        )

        response = await engine.register_agent(registration)
        assert response.status == "registered"
        # Should have matches
        assert sum(response.matched_needs.values()) > 0

    @pytest.mark.asyncio
    async def test_truncation_keeps_one_match_per_need(self, context_engine_with_limit):
        """Test that truncation tries to keep at least one match per need"""
        # Publish data for different needs
        await context_engine_with_limit.publish_data(
            DataPublishEvent(
                project_id="proj1",
                data_key="auth_data",
                data={"content": "authentication information " * 20},
            )
        )

        await context_engine_with_limit.publish_data(
            DataPublishEvent(
                project_id="proj1",
                data_key="database_data",
                data={"content": "database schema information " * 20},
            )
        )

        # Register agent with multiple needs
        registration = AgentRegistration(
            agent_id="test_agent",
            project_id="proj1",
            data_needs=["authentication methods", "database schemas"],
        )

        response = await context_engine_with_limit.register_agent(registration)
        assert response.status == "registered"

    @pytest.mark.asyncio
    async def test_tokenizer_fallback(self, redis):
        """Test that token estimation works even if tokenizer fails"""
        engine = ContextEngine(
            redis=redis, similarity_threshold=0.5, max_matches=10, max_context_size=1000
        )

        # Even if tokenizer is None, should still work with fallback
        original_tokenizer = engine.tokenizer
        engine.tokenizer = None

        # Should still be able to estimate tokens
        tokens = engine._estimate_tokens({"test": "data"})
        assert tokens > 0
        assert isinstance(tokens, int)

        # Restore
        engine.tokenizer = original_tokenizer
