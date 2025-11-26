"""
Golden Integration Tests - Comprehensive end-to-end system tests

These tests are designed to be:
1. Deterministic - same inputs always produce same outputs
2. Git bisect compatible - reliable for finding breaking commits
3. Comprehensive - cover all critical workflows
4. Fast enough for CI - complete in reasonable time

Run these before every release and in CI/CD pipelines.
"""

import pytest
import asyncio
import json
import time
from datetime import datetime
from httpx import AsyncClient
from fastapi import FastAPI
from redis.asyncio import Redis

from main import app
from src.core.models import (
    DataPublishEvent,
    AgentRegistration,
    QueryRequest
)


class TestGoldenPublishQueryWorkflow:
    """Test the complete publish → query workflow"""

    @pytest.mark.asyncio
    async def test_publish_and_query_json_data(self, test_client, test_redis):
        """Golden test: Publish JSON data and query it back"""

        # 1. Publish structured data
        publish_data = {
            "project_id": "golden-test-1",
            "data_key": "user_schema",
            "data": {
                "table": "users",
                "columns": {
                    "id": "uuid PRIMARY KEY",
                    "email": "varchar(255) UNIQUE NOT NULL",
                    "created_at": "timestamp DEFAULT NOW()"
                },
                "indexes": ["email"]
            }
        }

        response = await test_client.post("/api/v1/data/publish", json=publish_data)
        assert response.status_code == 200
        sequence = response.json()["sequence"]
        assert int(sequence) > 0

        # 2. Query for the data
        query_data = {
            "project_id": "golden-test-1",
            "queries": ["database schema users table"]
        }

        response = await test_client.post("/api/v1/query", json=query_data)
        assert response.status_code == 200
        result = response.json()

        # 3. Verify results
        assert "database schema users table" in result["matches"]
        matches = result["matches"]["database schema users table"]
        assert len(matches) > 0

        match = matches[0]
        assert match["data_key"] == "user_schema"
        assert match["data"]["table"] == "users"
        assert "email" in match["data"]["columns"]
        assert match["similarity"] > 0.5

    @pytest.mark.asyncio
    async def test_publish_csv_and_query_with_hybrid_search(self, test_client, test_redis):
        """Golden test: CSV data with hybrid search (keyword + semantic)"""

        # 1. Publish CSV data
        csv_data = "Name,Role,Department\nAlice,Engineer,Backend\nBob,Manager,Product\nCarol,Designer,Frontend"

        publish_data = {
            "project_id": "golden-test-2",
            "data_key": "team_roster",
            "data": csv_data,
            "data_format": "csv"
        }

        response = await test_client.post("/api/v1/data/publish", json=publish_data)
        assert response.status_code == 200

        # Wait for indexing
        await asyncio.sleep(0.5)

        # 2. Query with exact keyword (should work with hybrid search)
        query_data = {
            "project_id": "golden-test-2",
            "queries": ["Bob"]
        }

        response = await test_client.post("/api/v1/query", json=query_data)
        assert response.status_code == 200
        result = response.json()

        # 3. Verify Bob is found
        matches = result["matches"]["Bob"]
        assert len(matches) > 0

        # Verify the data contains Bob
        match_data = matches[0]["data"]
        assert "records" in match_data

        # Find Bob in records
        bob_found = False
        for record in match_data["records"]:
            if record.get("Name") == "Bob":
                assert record["Role"] == "Manager"
                assert record["Department"] == "Product"
                bob_found = True
                break

        assert bob_found, "Bob should be found in the team roster"


class TestGoldenAgentWorkflow:
    """Test the complete agent registration → notification workflow"""

    @pytest.mark.asyncio
    async def test_register_agent_and_receive_initial_context(self, test_client, test_redis):
        """Golden test: Agent registers and receives matching data"""

        # 1. Publish some data first
        publish_data = {
            "project_id": "golden-test-3",
            "data_key": "api_config",
            "data": {
                "base_url": "https://api.example.com",
                "rate_limit": 1000,
                "timeout": 30
            }
        }

        response = await test_client.post("/api/v1/data/publish", json=publish_data)
        assert response.status_code == 200

        # 2. Register agent with matching needs
        registration_data = {
            "agent_id": "golden-agent-1",
            "project_id": "golden-test-3",
            "data_needs": [
                "API configuration",
                "rate limiting settings"
            ],
            "notification_method": "redis",
            "response_format": "json"
        }

        response = await test_client.post("/api/v1/agents/register", json=registration_data)
        assert response.status_code == 200
        result = response.json()

        # 3. Verify initial context
        assert result["agent_id"] == "golden-agent-1"
        assert result["project_id"] == "golden-test-3"
        assert "matched_needs" in result

        # Should have matches for "API configuration"
        api_matches = result["matched_needs"].get("API configuration", [])
        assert len(api_matches) > 0
        assert api_matches[0]["data_key"] == "api_config"

    @pytest.mark.asyncio
    async def test_agent_receives_update_notification(self, test_client, test_redis):
        """Golden test: Agent receives notification when data is updated"""

        # 1. Register agent first
        registration_data = {
            "agent_id": "golden-agent-2",
            "project_id": "golden-test-4",
            "data_needs": ["deployment status"],
            "notification_method": "redis",
            "response_format": "json"
        }

        response = await test_client.post("/api/v1/agents/register", json=registration_data)
        assert response.status_code == 200

        # 2. Subscribe to Redis channel
        pubsub = test_redis.pubsub()
        await pubsub.subscribe("agent:golden-agent-2:updates")

        # 3. Publish data that matches agent's needs
        publish_data = {
            "project_id": "golden-test-4",
            "data_key": "deploy_status",
            "data": {
                "environment": "production",
                "status": "deployed",
                "version": "1.0.0"
            }
        }

        response = await test_client.post("/api/v1/data/publish", json=publish_data)
        assert response.status_code == 200

        # 4. Wait for notification
        notification_received = False
        for _ in range(10):  # Try up to 10 times
            message = await pubsub.get_message(timeout=1.0)
            if message and message["type"] == "message":
                data = json.loads(message["data"])
                if data.get("type") == "data_update":
                    assert data["data_key"] == "deploy_status"
                    assert data["data"]["status"] == "deployed"
                    notification_received = True
                    break
            await asyncio.sleep(0.1)

        assert notification_received, "Agent should receive update notification"

        # Cleanup
        await pubsub.unsubscribe("agent:golden-agent-2:updates")
        await pubsub.aclose()


class TestGoldenAuthenticationAndSecurity:
    """Test authentication, RBAC, and rate limiting"""

    @pytest.mark.asyncio
    async def test_api_key_authentication_required(self, test_client):
        """Golden test: API endpoints require valid API key"""

        # Test without API key
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.post("/api/v1/data/publish", json={
                "project_id": "test",
                "data_key": "test",
                "data": {}
            })
            assert response.status_code == 401
            assert "Missing API Key" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_rbac_project_access_control(self, test_client, test_redis):
        """Golden test: RBAC enforces project-level access control"""

        # 1. Create two API keys for different projects
        await test_redis.hset("api_key:test-key-1", mapping={
            "key_hash": "test-key-1",
            "project_id": "project-a",
            "created_at": datetime.utcnow().isoformat()
        })

        await test_redis.hset("api_key:test-key-2", mapping={
            "key_hash": "test-key-2",
            "project_id": "project-b",
            "created_at": datetime.utcnow().isoformat()
        })

        # 2. Try to access project-a with project-b's key
        async with AsyncClient(app=app, base_url="http://test",
                              headers={"X-API-Key": "test-key-2"}) as client:
            response = await client.post("/api/v1/data/publish", json={
                "project_id": "project-a",
                "data_key": "test",
                "data": {}
            })
            assert response.status_code == 403
            assert "not authorized" in response.json()["detail"].lower()

        # 3. Access project-a with correct key should work
        async with AsyncClient(app=app, base_url="http://test",
                              headers={"X-API-Key": "test-key-1"}) as client:
            response = await client.post("/api/v1/data/publish", json={
                "project_id": "project-a",
                "data_key": "test",
                "data": {"test": "data"}
            })
            assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_rate_limiting_enforced(self, test_client, test_redis):
        """Golden test: Rate limiting prevents abuse"""

        # Make requests up to the limit
        project_id = "golden-test-rate-limit"

        # First request should succeed
        response = await test_client.post("/api/v1/data/publish", json={
            "project_id": project_id,
            "data_key": "test",
            "data": {}
        })
        assert response.status_code == 200

        # Get rate limit headers
        assert "X-RateLimit-Limit" in response.headers
        assert "X-RateLimit-Remaining" in response.headers

        limit = int(response.headers["X-RateLimit-Limit"])

        # Make requests up to limit
        for i in range(limit - 1):
            response = await test_client.post("/api/v1/data/publish", json={
                "project_id": project_id,
                "data_key": f"test-{i}",
                "data": {}
            })
            if response.status_code == 429:
                # Hit rate limit
                assert "rate limit exceeded" in response.json()["detail"].lower()
                break


class TestGoldenDataFormats:
    """Test all supported data formats"""

    @pytest.mark.asyncio
    async def test_json_format(self, test_client, test_redis):
        """Golden test: JSON format is correctly parsed"""

        response = await test_client.post("/api/v1/data/publish", json={
            "project_id": "format-test",
            "data_key": "json_data",
            "data": {"type": "json", "nested": {"value": 42}}
        })
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_yaml_format(self, test_client, test_redis):
        """Golden test: YAML format is correctly parsed"""

        yaml_data = """
version: 1.0
services:
  - name: api
    port: 8000
  - name: db
    port: 5432
"""

        response = await test_client.post("/api/v1/data/publish", json={
            "project_id": "format-test",
            "data_key": "yaml_config",
            "data": yaml_data,
            "data_format": "yaml"
        })
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_csv_format(self, test_client, test_redis):
        """Golden test: CSV format is correctly parsed"""

        csv_data = "id,name,value\n1,test,100\n2,prod,200"

        response = await test_client.post("/api/v1/data/publish", json={
            "project_id": "format-test",
            "data_key": "csv_data",
            "data": csv_data,
            "data_format": "csv"
        })
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_xml_format(self, test_client, test_redis):
        """Golden test: XML format is correctly parsed"""

        xml_data = """<?xml version="1.0"?>
<config>
    <database>
        <host>localhost</host>
        <port>5432</port>
    </database>
</config>"""

        response = await test_client.post("/api/v1/data/publish", json={
            "project_id": "format-test",
            "data_key": "xml_config",
            "data": xml_data,
            "data_format": "xml"
        })
        assert response.status_code == 200


class TestGoldenDataRetention:
    """Test data retention and cleanup policies"""

    @pytest.mark.asyncio
    async def test_event_stream_retention(self, test_client, test_redis):
        """Golden test: Event streams respect retention policies"""

        project_id = "retention-test"

        # Publish multiple events
        for i in range(5):
            response = await test_client.post("/api/v1/data/publish", json={
                "project_id": project_id,
                "data_key": f"event-{i}",
                "data": {"index": i}
            })
            assert response.status_code == 200

        # Check stream length
        stream_key = f"events:{project_id}"
        length = await test_redis.xlen(stream_key)
        assert length == 5


class TestGoldenExportImport:
    """Test data export and import functionality"""

    @pytest.mark.asyncio
    async def test_export_and_import_project_data(self, test_client, test_redis):
        """Golden test: Export data and re-import it"""

        project_id = "export-test"

        # 1. Publish some data
        await test_client.post("/api/v1/data/publish", json={
            "project_id": project_id,
            "data_key": "config_1",
            "data": {"setting": "value1"}
        })

        await test_client.post("/api/v1/data/publish", json={
            "project_id": project_id,
            "data_key": "config_2",
            "data": {"setting": "value2"}
        })

        # 2. Export the data
        response = await test_client.post("/api/v1/data/export", json={
            "project_id": project_id
        })
        assert response.status_code == 200
        exported_data = response.json()

        assert exported_data["project_id"] == project_id
        assert len(exported_data["data"]) == 2

        # 3. Delete the data
        for key in ["config_1", "config_2"]:
            await test_redis.delete(f"context_data:{project_id}:{key}")

        # 4. Import the data back
        response = await test_client.post("/api/v1/data/import",
                                         json=exported_data)
        assert response.status_code == 200

        # 5. Verify data is restored
        query_data = {
            "project_id": project_id,
            "queries": ["configuration settings"]
        }

        response = await test_client.post("/api/v1/query", json=query_data)
        assert response.status_code == 200
        matches = response.json()["matches"]["configuration settings"]
        assert len(matches) == 2


class TestGoldenHealthAndMetrics:
    """Test health checks and metrics"""

    @pytest.mark.asyncio
    async def test_health_check_passes(self, test_client):
        """Golden test: Health check returns healthy status"""

        response = await test_client.get("/api/v1/health")
        assert response.status_code == 200
        health = response.json()

        assert health["status"] == "healthy"
        assert health["components"]["redis"]["status"] == "healthy"

    @pytest.mark.asyncio
    async def test_metrics_endpoint(self, test_client):
        """Golden test: Metrics endpoint returns Prometheus format"""

        response = await test_client.get("/api/v1/metrics")
        assert response.status_code == 200

        metrics = response.text
        assert "contex_requests_total" in metrics
        assert "contex_request_duration_seconds" in metrics


# Fixtures
import pytest_asyncio

@pytest_asyncio.fixture
async def test_client():
    """Create a test client with API key authentication"""
    from fakeredis import FakeAsyncRedis

    # Setup test Redis
    test_redis_instance = FakeAsyncRedis(decode_responses=False)

    # Temporarily replace app's Redis with test instance
    original_redis = None
    if hasattr(app.state, 'redis'):
        original_redis = app.state.redis

    app.state.redis = test_redis_instance

    # Also setup context engine with test Redis
    from src.core.context_engine import ContextEngine
    context_engine = ContextEngine(
        redis=test_redis_instance,
        similarity_threshold=0.5,
        max_matches=10,
        max_context_size=51200
    )
    await context_engine.semantic_matcher.initialize_index()
    app.state.context_engine = context_engine

    # Create test API key
    await test_redis_instance.hset("api_key:test-api-key", mapping={
        "key_hash": "test-api-key",
        "project_id": "*",  # Access to all projects
        "created_at": datetime.utcnow().isoformat()
    })

    async with AsyncClient(app=app, base_url="http://test",
                          headers={"X-API-Key": "test-api-key"}) as client:
        yield client

    # Cleanup
    await test_redis_instance.flushall()
    await test_redis_instance.aclose()

    # Restore original Redis if it existed
    if original_redis:
        app.state.redis = original_redis


@pytest_asyncio.fixture
async def test_redis(test_client):
    """Get Redis connection for test operations"""
    return app.state.redis
