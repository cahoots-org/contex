"""Tests for data export/import functionality with PostgreSQL"""

import pytest
import pytest_asyncio
import json
import time

from src.core.export_import import ExportImportManager
from src.core.event_store import EventStore
from src.core.db_models import Embedding, AgentRegistration
from sqlalchemy import select


@pytest_asyncio.fixture
async def export_import_manager(db):
    """Create ExportImportManager for testing"""
    return ExportImportManager(db=db)


@pytest_asyncio.fixture
async def sample_project_data(db):
    """Create sample project data for testing"""
    project_id = "test_project"

    # Add some events using EventStore
    event_store = EventStore(db)
    for i in range(5):
        await event_store.append_event(
            project_id,
            f"event_type_{i}",
            {"value": i, "test": True}
        )

    # Add some embeddings
    async with db.session() as session:
        for i in range(3):
            embedding = Embedding(
                project_id=project_id,
                data_key=f"key_{i}",
                node_key=f"key_{i}",
                node_path=f"/path/to/key_{i}",
                node_type="test",
                description=f"Test embedding {i}",
                data={"value": i},
                data_format="json",
                embedding=[0.1] * 384,  # Dummy embedding
            )
            session.add(embedding)

        # Add some agent registrations
        for i in range(2):
            agent = AgentRegistration(
                agent_id=f"agent_{i}",
                project_id=project_id,
                needs=["test data"],
                notification_method="redis",
                response_format="json",
            )
            session.add(agent)

    return project_id


class TestExportImportManager:
    """Test ExportImportManager class"""

    @pytest.mark.asyncio
    async def test_initialization(self, db):
        """Test manager initialization"""
        manager = ExportImportManager(db=db)
        assert manager.db == db

    @pytest.mark.asyncio
    async def test_export_empty_project(self, db, export_import_manager):
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
    async def test_export_project_with_data(self, db, export_import_manager, sample_project_data):
        """Test exporting a project with data"""
        result = await export_import_manager.export_project(sample_project_data)

        # Parse JSON result
        data = json.loads(result)

        assert data["project_id"] == sample_project_data
        assert len(data["data"]["events"]) == 5
        assert len(data["data"]["embeddings"]) == 3
        assert len(data["data"]["agents"]) == 2

    @pytest.mark.asyncio
    async def test_export_events_only(self, db, export_import_manager, sample_project_data):
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
    async def test_export_embeddings_only(self, db, export_import_manager, sample_project_data):
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
    async def test_export_agents_only(self, db, export_import_manager, sample_project_data):
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
    async def test_export_json_format(self, db, export_import_manager, sample_project_data):
        """Test exporting in JSON format"""
        result = await export_import_manager.export_project(
            sample_project_data,
            format="json"
        )

        # Should be valid JSON
        data = json.loads(result)
        assert isinstance(data, dict)


class TestImportValidation:
    """Test import validation"""

    @pytest.mark.asyncio
    async def test_validate_valid_data(self, db, export_import_manager):
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
    async def test_validate_missing_project_id(self, db, export_import_manager):
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
    async def test_validate_missing_data_field(self, db, export_import_manager):
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
    async def test_validate_invalid_events_structure(self, db, export_import_manager):
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
    async def test_export_import_roundtrip(self, db, export_import_manager, sample_project_data):
        """Test exporting and importing data maintains integrity"""
        # Export
        exported = await export_import_manager.export_project(sample_project_data)

        # Clear data for this project
        from sqlalchemy import delete
        from src.core.db_models import Event, Embedding as EmbeddingModel, AgentRegistration as AgentModel
        async with db.session() as session:
            await session.execute(delete(Event).where(Event.project_id == sample_project_data))
            await session.execute(delete(EmbeddingModel).where(EmbeddingModel.project_id == sample_project_data))
            await session.execute(delete(AgentModel).where(AgentModel.project_id == sample_project_data))

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
    async def test_import_existing_project_no_overwrite(self, db, export_import_manager, sample_project_data):
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
        assert "already" in result["message"].lower()

    @pytest.mark.asyncio
    async def test_import_existing_project_with_overwrite(self, db, export_import_manager, sample_project_data):
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
    async def test_import_events(self, db, export_import_manager):
        """Test importing events"""
        data = json.dumps({
            "project_id": "new_project",
            "version": "1.0",
            "data": {
                "events": [
                    {
                        "event_id": "1",
                        "event_type": "test",
                        "data": {"key": "value1"}
                    },
                    {
                        "event_id": "2",
                        "event_type": "test",
                        "data": {"key": "value2"}
                    }
                ]
            }
        })

        result = await export_import_manager.import_project(data, format="json")

        assert result["status"] == "success"
        assert result["stats"]["events_imported"] == 2

        # Verify events are in PostgreSQL
        from src.core.db_models import Event
        async with db.session() as session:
            stmt = select(Event).where(Event.project_id == "new_project")
            res = await session.execute(stmt)
            events = res.scalars().all()
            assert len(events) == 2

    @pytest.mark.asyncio
    async def test_import_embeddings(self, db, export_import_manager):
        """Test importing embeddings"""
        data = json.dumps({
            "project_id": "new_project_emb",
            "version": "1.0",
            "data": {
                "embeddings": [
                    {
                        "key": "test_key",
                        "data_key": "test_key",
                        "node_path": "/path/to/test",
                        "node_type": "test",
                        "description": "Test embedding",
                        "data": {"test": "data"},
                        "data_format": "json"
                    }
                ]
            }
        })

        result = await export_import_manager.import_project(data, format="json")

        assert result["status"] == "success"
        assert result["stats"]["embeddings_imported"] == 1

        # Verify embedding is in PostgreSQL
        async with db.session() as session:
            stmt = select(Embedding).where(Embedding.project_id == "new_project_emb")
            res = await session.execute(stmt)
            embeddings = res.scalars().all()
            assert len(embeddings) == 1

    @pytest.mark.asyncio
    async def test_import_agents(self, db, export_import_manager):
        """Test importing agents"""
        data = json.dumps({
            "project_id": "new_project_agent",
            "version": "1.0",
            "data": {
                "agents": [
                    {
                        "agent_id": "test_agent",
                        "data": {
                            "project_id": "new_project_agent",
                            "needs": ["test"],
                            "notification_method": "redis",
                            "response_format": "json"
                        },
                        "last_seen": None,
                        "last_sequence": None
                    }
                ]
            }
        })

        result = await export_import_manager.import_project(data, format="json")

        assert result["status"] == "success"
        assert result["stats"]["agents_imported"] == 1

        # Verify agent is in PostgreSQL
        async with db.session() as session:
            stmt = select(AgentRegistration).where(AgentRegistration.agent_id == "test_agent")
            res = await session.execute(stmt)
            agents = res.scalars().all()
            assert len(agents) == 1


class TestExportImportEdgeCases:
    """Test edge cases for export/import"""

    @pytest.mark.asyncio
    async def test_export_nonexistent_project(self, db, export_import_manager):
        """Test exporting a project that doesn't exist"""
        result = await export_import_manager.export_project("nonexistent")

        data = json.loads(result)
        assert data["project_id"] == "nonexistent"
        assert data["data"]["events"] == []

    @pytest.mark.asyncio
    async def test_import_invalid_json(self, db, export_import_manager):
        """Test importing invalid JSON"""
        with pytest.raises(json.JSONDecodeError):
            await export_import_manager.import_project("not valid json", format="json")

    @pytest.mark.asyncio
    async def test_export_filters_agents_by_project(self, db, export_import_manager):
        """Test that export only includes agents for the specified project"""
        # Create agents for different projects
        async with db.session() as session:
            agent1 = AgentRegistration(
                agent_id="agent1_filter",
                project_id="project1",
                needs=["data"],
                notification_method="redis",
            )
            agent2 = AgentRegistration(
                agent_id="agent2_filter",
                project_id="project2",
                needs=["data"],
                notification_method="redis",
            )
            session.add(agent1)
            session.add(agent2)

        # Export project1
        result = await export_import_manager.export_project("project1")
        data = json.loads(result)

        # Should only have agent1
        assert len(data["data"]["agents"]) == 1
        assert data["data"]["agents"][0]["agent_id"] == "agent1_filter"
