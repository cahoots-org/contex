"""Tests for data export/import functionality"""

import pytest
import pytest_asyncio
import json
import time

from src.core.export_import import ExportImportManager


@pytest_asyncio.fixture
async def export_import_manager(redis):
    """Create ExportImportManager for testing"""
    return ExportImportManager(redis=redis)


@pytest_asyncio.fixture
async def sample_project_data(redis):
    """Create sample project data for testing"""
    project_id = "test_project"

    # Add some events to the stream
    stream_key = f"events:{project_id}"
    for i in range(5):
        await redis.xadd(stream_key, {
            "data_key": f"key_{i}",
            "data": json.dumps({"value": i, "test": True}),
            "timestamp": str(time.time())
        })

    # Add some embeddings
    for i in range(3):
        embedding_key = f"embedding:{project_id}:key_{i}"
        await redis.hset(embedding_key, mapping={
            "data_key": f"key_{i}",
            "embedding": json.dumps([0.1, 0.2, 0.3]),
            "description": f"Test embedding {i}"
        })

    # Add some agent registrations
    for i in range(2):
        agent_id = f"agent_{i}"
        agent_data = {
            "project_id": project_id,
            "agent_id": agent_id,
            "needs": ["test data"]
        }
        await redis.set(f"agent:{agent_id}:data", json.dumps(agent_data))
        await redis.set(f"agent:{agent_id}:last_seen", str(time.time()))
        await redis.set(f"agent:{agent_id}:needs", json.dumps(["test data"]))

    return project_id


class TestExportImportManager:
    """Test ExportImportManager class"""

    @pytest.mark.asyncio
    async def test_initialization(self, redis):
        """Test manager initialization"""
        manager = ExportImportManager(redis=redis)
        assert manager.redis == redis

    @pytest.mark.asyncio
    async def test_export_empty_project(self, redis, export_import_manager):
        """Test exporting a project with no data"""
        result = await export_import_manager.export_project("empty_project")

        # Parse JSON result
        data = json.loads(result)

        assert data["project_id"] == "empty_project"
        assert "export_timestamp" in data
        assert data["version"] == "1.0"
        assert data["data"]["events"] == []
        assert data["data"]["embeddings"] == []
        assert data["data"]["agents"] == []

    @pytest.mark.asyncio
    async def test_export_project_with_data(self, redis, export_import_manager, sample_project_data):
        """Test exporting a project with data"""
        result = await export_import_manager.export_project(sample_project_data)

        # Parse JSON result
        data = json.loads(result)

        assert data["project_id"] == sample_project_data
        assert len(data["data"]["events"]) == 5
        assert len(data["data"]["embeddings"]) == 3
        assert len(data["data"]["agents"]) == 2

    @pytest.mark.asyncio
    async def test_export_events_only(self, redis, export_import_manager, sample_project_data):
        """Test exporting only events"""
        result = await export_import_manager.export_project(
            sample_project_data,
            include_events=True,
            include_embeddings=False,
            include_agents=False
        )

        data = json.loads(result)

        assert len(data["data"]["events"]) == 5
        assert "embeddings" not in data["data"]
        assert "agents" not in data["data"]

    @pytest.mark.asyncio
    async def test_export_embeddings_only(self, redis, export_import_manager, sample_project_data):
        """Test exporting only embeddings"""
        result = await export_import_manager.export_project(
            sample_project_data,
            include_events=False,
            include_embeddings=True,
            include_agents=False
        )

        data = json.loads(result)

        assert "events" not in data["data"]
        assert len(data["data"]["embeddings"]) == 3
        assert "agents" not in data["data"]

    @pytest.mark.asyncio
    async def test_export_agents_only(self, redis, export_import_manager, sample_project_data):
        """Test exporting only agents"""
        result = await export_import_manager.export_project(
            sample_project_data,
            include_events=False,
            include_embeddings=False,
            include_agents=True
        )

        data = json.loads(result)

        assert "events" not in data["data"]
        assert "embeddings" not in data["data"]
        assert len(data["data"]["agents"]) == 2

    @pytest.mark.asyncio
    async def test_export_json_format(self, redis, export_import_manager, sample_project_data):
        """Test exporting in JSON format"""
        result = await export_import_manager.export_project(
            sample_project_data,
            format="json"
        )

        # Should be valid JSON
        data = json.loads(result)
        assert isinstance(data, dict)

    @pytest.mark.asyncio
    async def test_export_toon_format(self, redis, export_import_manager, sample_project_data):
        """Test TOON format export works"""
        result = await export_import_manager.export_project(
            sample_project_data,
            format="toon"
        )

        # TOON format is implemented and returns TOON string (not JSON)
        assert isinstance(result, str)
        assert len(result) > 0
        # TOON format contains key-value pairs with colons
        assert ":" in result
        assert "test_project" in result


class TestImportValidation:
    """Test import validation"""

    @pytest.mark.asyncio
    async def test_validate_valid_data(self, redis, export_import_manager):
        """Test validation of valid data"""
        valid_data = json.dumps({
            "project_id": "test_project",
            "version": "1.0",
            "data": {
                "events": [],
                "embeddings": [],
                "agents": []
            }
        })

        result = await export_import_manager.import_project(
            valid_data,
            format="json",
            validate_only=True
        )

        assert result["status"] == "success"
        assert result["validation"]["valid"] is True
        assert len(result["validation"]["errors"]) == 0

    @pytest.mark.asyncio
    async def test_validate_missing_project_id(self, redis, export_import_manager):
        """Test validation catches missing project_id"""
        invalid_data = json.dumps({
            "version": "1.0",
            "data": {}
        })

        result = await export_import_manager.import_project(
            invalid_data,
            format="json",
            validate_only=True
        )

        assert result["status"] == "error"
        assert result["validation"]["valid"] is False
        assert any("project_id" in error for error in result["validation"]["errors"])

    @pytest.mark.asyncio
    async def test_validate_missing_data_field(self, redis, export_import_manager):
        """Test validation catches missing data field"""
        invalid_data = json.dumps({
            "project_id": "test_project",
            "version": "1.0"
        })

        result = await export_import_manager.import_project(
            invalid_data,
            format="json",
            validate_only=True
        )

        assert result["status"] == "error"
        assert result["validation"]["valid"] is False
        assert any("data" in error for error in result["validation"]["errors"])

    @pytest.mark.asyncio
    async def test_validate_invalid_events_structure(self, redis, export_import_manager):
        """Test validation catches invalid events structure"""
        invalid_data = json.dumps({
            "project_id": "test_project",
            "version": "1.0",
            "data": {
                "events": "not a list"
            }
        })

        result = await export_import_manager.import_project(
            invalid_data,
            format="json",
            validate_only=True
        )

        assert result["status"] == "error"
        assert result["validation"]["valid"] is False
        assert any("events" in error for error in result["validation"]["errors"])


class TestImportExport:
    """Test complete export/import cycle"""

    @pytest.mark.asyncio
    async def test_export_import_roundtrip(self, redis, export_import_manager, sample_project_data):
        """Test exporting and importing data maintains integrity"""
        # Export
        exported = await export_import_manager.export_project(sample_project_data)

        # Clear data
        await redis.flushdb()

        # Import
        result = await export_import_manager.import_project(
            exported,
            format="json",
            overwrite=False
        )

        assert result["status"] == "success"
        assert result["stats"]["events_imported"] == 5
        assert result["stats"]["embeddings_imported"] == 3
        assert result["stats"]["agents_imported"] == 2

    @pytest.mark.asyncio
    async def test_import_existing_project_no_overwrite(self, redis, export_import_manager, sample_project_data):
        """Test importing fails when project exists and overwrite=False"""
        # Export
        exported = await export_import_manager.export_project(sample_project_data)

        # Try to import (project already exists)
        result = await export_import_manager.import_project(
            exported,
            format="json",
            overwrite=False
        )

        assert result["status"] == "error"
        assert "already exists" in result["message"]

    @pytest.mark.asyncio
    async def test_import_existing_project_with_overwrite(self, redis, export_import_manager, sample_project_data):
        """Test importing overwrites when overwrite=True"""
        # Export
        exported = await export_import_manager.export_project(sample_project_data)

        # Import with overwrite
        result = await export_import_manager.import_project(
            exported,
            format="json",
            overwrite=True
        )

        assert result["status"] == "success"
        assert result["stats"]["events_imported"] == 5

    @pytest.mark.asyncio
    async def test_import_events(self, redis, export_import_manager):
        """Test importing events"""
        data = json.dumps({
            "project_id": "new_project",
            "version": "1.0",
            "data": {
                "events": [
                    {
                        "event_id": "0-0",
                        "data": {"key": "value1"}
                    },
                    {
                        "event_id": "0-1",
                        "data": {"key": "value2"}
                    }
                ]
            }
        })

        result = await export_import_manager.import_project(data, format="json")

        assert result["status"] == "success"
        assert result["stats"]["events_imported"] == 2

        # Verify events are in Redis
        stream_key = "events:new_project"
        stream_info = await redis.xinfo_stream(stream_key)
        assert stream_info["length"] == 2

    @pytest.mark.asyncio
    async def test_import_embeddings(self, redis, export_import_manager):
        """Test importing embeddings"""
        data = json.dumps({
            "project_id": "new_project",
            "version": "1.0",
            "data": {
                "embeddings": [
                    {
                        "key": "embedding:new_project:test",
                        "data": {
                            "data_key": "test",
                            "embedding": "[0.1, 0.2]"
                        }
                    }
                ]
            }
        })

        result = await export_import_manager.import_project(data, format="json")

        assert result["status"] == "success"
        assert result["stats"]["embeddings_imported"] == 1

        # Verify embedding is in Redis
        embedding_data = await redis.hgetall("embedding:new_project:test")
        assert embedding_data

    @pytest.mark.asyncio
    async def test_import_agents(self, redis, export_import_manager):
        """Test importing agents"""
        data = json.dumps({
            "project_id": "new_project",
            "version": "1.0",
            "data": {
                "agents": [
                    {
                        "agent_id": "test_agent",
                        "data": {
                            "project_id": "new_project",
                            "agent_id": "test_agent"
                        },
                        "last_seen": str(time.time()),
                        "needs": json.dumps(["test"])
                    }
                ]
            }
        })

        result = await export_import_manager.import_project(data, format="json")

        assert result["status"] == "success"
        assert result["stats"]["agents_imported"] == 1

        # Verify agent is in Redis
        agent_data = await redis.get("agent:test_agent:data")
        assert agent_data


class TestExportImportEdgeCases:
    """Test edge cases for export/import"""

    @pytest.mark.asyncio
    async def test_export_nonexistent_project(self, redis, export_import_manager):
        """Test exporting a project that doesn't exist"""
        result = await export_import_manager.export_project("nonexistent")

        data = json.loads(result)
        assert data["project_id"] == "nonexistent"
        assert data["data"]["events"] == []

    @pytest.mark.asyncio
    async def test_import_invalid_json(self, redis, export_import_manager):
        """Test importing invalid JSON"""
        with pytest.raises(json.JSONDecodeError):
            await export_import_manager.import_project("not valid json", format="json")

    @pytest.mark.asyncio
    async def test_export_filters_agents_by_project(self, redis, export_import_manager):
        """Test that export only includes agents for the specified project"""
        # Create agents for different projects
        await redis.set("agent:agent1:data", json.dumps({
            "project_id": "project1",
            "agent_id": "agent1"
        }))
        await redis.set("agent:agent2:data", json.dumps({
            "project_id": "project2",
            "agent_id": "agent2"
        }))

        # Export project1
        result = await export_import_manager.export_project("project1")
        data = json.loads(result)

        # Should only have agent1
        assert len(data["data"]["agents"]) == 1
        assert data["data"]["agents"][0]["agent_id"] == "agent1"
