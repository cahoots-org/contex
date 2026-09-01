"""Data export/import functionality for Contex projects"""

import json
import time
from typing import Dict, List, Any, Literal

import toon_format as toon
from sqlalchemy import select

from src.core.database import DatabaseManager
from src.core.db_models import Event, Embedding, AgentRegistration
from src.core.logging import get_logger

logger = get_logger(__name__)


class ExportImportManager:
    """
    Manages project data export and import operations.

    Features:
    - Export all project data (events, embeddings, agent registrations)
    - Import with validation
    - Support for JSON and TOON formats
    - Maintains data integrity
    """

    def __init__(self, db: DatabaseManager):
        """
        Initialize export/import manager.

        Args:
            db: Database manager
        """
        self.db = db
        logger.info("ExportImportManager initialized")

    async def export_project(
        self,
        project_id: str,
        format: Literal["json", "toon"] = "json",
        include_events: bool = True,
        include_embeddings: bool = True,
        include_agents: bool = True,
    ) -> str:
        """
        Export all project data.

        Args:
            project_id: Project identifier
            format: Export format (json or toon)
            include_events: Include event stream data
            include_embeddings: Include embeddings data
            include_agents: Include agent registrations

        Returns:
            Serialized project data in specified format
        """
        logger.info("Exporting project", project_id=project_id, format=format)

        export_data = {
            "project_id": project_id,
            "export_timestamp": time.time(),
            "version": "1.0",
            "data": {}
        }

        # Export events from PostgreSQL
        if include_events:
            events = await self._export_events(project_id)
            export_data["data"]["events"] = events
            logger.info("Exported events", project_id=project_id, count=len(events))

        # Export embeddings
        if include_embeddings:
            embeddings = await self._export_embeddings(project_id)
            export_data["data"]["embeddings"] = embeddings
            logger.info("Exported embeddings", project_id=project_id, count=len(embeddings))

        # Export agent registrations
        if include_agents:
            agents = await self._export_agents(project_id)
            export_data["data"]["agents"] = agents
            logger.info("Exported agents", project_id=project_id, count=len(agents))

        # Serialize to requested format
        if format == "toon":
            try:
                return toon.encode(export_data)
            except NotImplementedError:
                logger.warning("TOON format not yet implemented, falling back to JSON")
                return json.dumps(export_data, indent=2)
        else:
            return json.dumps(export_data, indent=2)

    async def _export_events(self, project_id: str) -> List[Dict[str, Any]]:
        """Export all events from PostgreSQL"""
        events = []

        try:
            async with self.db.session() as session:
                result = await session.execute(
                    select(Event)
                    .where(Event.project_id == project_id)
                    .order_by(Event.sequence.asc())
                )
                rows = result.scalars().all()

                for row in rows:
                    events.append({
                        "event_id": str(row.sequence),
                        "event_type": row.event_type,
                        "data": row.data,
                        "created_at": row.created_at.isoformat() if row.created_at else None
                    })

        except Exception as e:
            logger.error("Failed to export events", project_id=project_id, error=str(e))

        return events

    async def _export_embeddings(self, project_id: str) -> List[Dict[str, Any]]:
        """Export all embeddings for the project"""
        embeddings = []

        try:
            async with self.db.session() as session:
                result = await session.execute(
                    select(Embedding)
                    .where(Embedding.project_id == project_id)
                )
                rows = result.scalars().all()

                for row in rows:
                    embeddings.append({
                        "key": row.node_key,
                        "data_key": row.data_key,
                        "node_path": row.node_path,
                        "node_type": row.node_type,
                        "description": row.description,
                        "data": row.data,
                        "data_format": row.data_format,
                        # Don't include embedding vectors (large binary data)
                    })

        except Exception as e:
            logger.error("Failed to export embeddings", project_id=project_id, error=str(e))

        return embeddings

    async def _export_agents(self, project_id: str) -> List[Dict[str, Any]]:
        """Export all agent registrations for the project"""
        agents = []

        try:
            async with self.db.session() as session:
                result = await session.execute(
                    select(AgentRegistration)
                    .where(AgentRegistration.project_id == project_id)
                )
                rows = result.scalars().all()

                for row in rows:
                    agents.append({
                        "agent_id": row.agent_id,
                        "data": {
                            "project_id": row.project_id,
                            "tenant_id": row.tenant_id,
                            "needs": row.needs,
                            "notification_method": row.notification_method,
                            "response_format": row.response_format,
                            "notification_channel": row.notification_channel,
                            "webhook_url": row.webhook_url,
                            "data_keys": row.data_keys,
                        },
                        "last_seen": row.last_seen.isoformat() if row.last_seen else None,
                        "last_sequence": row.last_sequence,
                    })

        except Exception as e:
            logger.error("Failed to export agents", project_id=project_id, error=str(e))

        return agents

    async def import_project(
        self,
        data: str,
        format: Literal["json", "toon"] = "json",
        validate_only: bool = False,
        overwrite: bool = False,
    ) -> Dict[str, Any]:
        """
        Import project data.

        Args:
            data: Serialized project data
            format: Data format (json or toon)
            validate_only: If True, only validate without importing
            overwrite: If True, overwrite existing data

        Returns:
            Dict with import statistics and validation results
        """
        logger.info("Importing project", format=format, validate_only=validate_only)

        # Parse data
        if format == "toon":
            try:
                parsed_data = toon.decode(data)
            except NotImplementedError:
                logger.warning("TOON format not yet implemented, trying JSON")
                parsed_data = json.loads(data)
        else:
            parsed_data = json.loads(data)

        # Validate structure
        validation_result = self._validate_import_data(parsed_data)
        if not validation_result["valid"]:
            return {
                "status": "error",
                "validation": validation_result,
                "message": "Data validation failed"
            }

        if validate_only:
            return {
                "status": "success",
                "validation": validation_result,
                "message": "Validation passed (no data imported)"
            }

        # Check if project has existing data
        project_id = parsed_data["project_id"]

        async with self.db.session() as session:
            result = await session.execute(
                select(Event).where(Event.project_id == project_id).limit(1)
            )
            exists = result.scalar_one_or_none() is not None

        if exists and not overwrite:
            return {
                "status": "error",
                "message": f"Project {project_id} already has data. Use overwrite=True to replace."
            }

        # Import data
        stats = {
            "project_id": project_id,
            "events_imported": 0,
            "embeddings_imported": 0,
            "agents_imported": 0,
        }

        # Import events
        if "events" in parsed_data["data"]:
            stats["events_imported"] = await self._import_events(
                project_id,
                parsed_data["data"]["events"],
                overwrite
            )

        # Import embeddings
        if "embeddings" in parsed_data["data"]:
            stats["embeddings_imported"] = await self._import_embeddings(
                project_id,
                parsed_data["data"]["embeddings"],
                overwrite
            )

        # Import agents
        if "agents" in parsed_data["data"]:
            stats["agents_imported"] = await self._import_agents(
                project_id,
                parsed_data["data"]["agents"],
                overwrite
            )

        logger.info("Project import complete", **stats)

        return {
            "status": "success",
            "stats": stats,
            "validation": validation_result
        }

    def _validate_import_data(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Validate import data structure"""
        errors = []
        warnings = []

        # Check required fields
        if "project_id" not in data:
            errors.append("Missing required field: project_id")

        if "version" not in data:
            warnings.append("Missing version field")

        if "data" not in data:
            errors.append("Missing required field: data")
        else:
            # Check data sections
            data_section = data["data"]
            if not isinstance(data_section, dict):
                errors.append("'data' field must be a dictionary")
            else:
                # Validate events structure
                if "events" in data_section:
                    if not isinstance(data_section["events"], list):
                        errors.append("'events' must be a list")
                    else:
                        for i, event in enumerate(data_section["events"]):
                            if "event_id" not in event:
                                errors.append(f"Event {i} missing event_id")
                            if "data" not in event:
                                errors.append(f"Event {i} missing data")

                # Validate embeddings structure
                if "embeddings" in data_section:
                    if not isinstance(data_section["embeddings"], list):
                        errors.append("'embeddings' must be a list")

                # Validate agents structure
                if "agents" in data_section:
                    if not isinstance(data_section["agents"], list):
                        errors.append("'agents' must be a list")

        return {
            "valid": len(errors) == 0,
            "errors": errors,
            "warnings": warnings,
            "project_id": data.get("project_id"),
        }

    async def _import_events(
        self,
        project_id: str,
        events: List[Dict[str, Any]],
        overwrite: bool = False
    ) -> int:
        """Import events to PostgreSQL"""
        from sqlalchemy import delete, func

        try:
            async with self.db.session() as session:
                # If overwriting, delete existing events
                if overwrite:
                    await session.execute(
                        delete(Event).where(Event.project_id == project_id)
                    )

                # Get starting sequence number
                result = await session.execute(
                    select(func.coalesce(func.max(Event.sequence), 0) + 1)
                    .where(Event.project_id == project_id)
                )
                next_sequence = result.scalar()

                # Add events
                imported = 0
                for event in events:
                    event_data = event.get("data", {})
                    event_type = event.get("event_type", "imported")

                    new_event = Event(
                        project_id=project_id,
                        event_type=event_type,
                        data=event_data,
                        sequence=next_sequence + imported,
                    )
                    session.add(new_event)
                    imported += 1

            logger.info("Imported events", project_id=project_id, count=imported)
            return imported

        except Exception as e:
            logger.error("Failed to import events", project_id=project_id, error=str(e))
            return 0

    async def _import_embeddings(
        self,
        project_id: str,
        embeddings: List[Dict[str, Any]],
        overwrite: bool = False
    ) -> int:
        """Import embeddings to PostgreSQL"""
        from sqlalchemy import delete

        try:
            async with self.db.session() as session:
                # If overwriting, delete existing embeddings for this project
                if overwrite:
                    await session.execute(
                        delete(Embedding).where(Embedding.project_id == project_id)
                    )

                imported = 0
                for embedding in embeddings:
                    node_key = embedding.get("key") or embedding.get("node_key")
                    if not node_key:
                        continue

                    # Check if exists (when not overwriting)
                    if not overwrite:
                        result = await session.execute(
                            select(Embedding)
                            .where(Embedding.project_id == project_id)
                            .where(Embedding.node_key == node_key)
                        )
                        if result.scalar_one_or_none():
                            continue

                    # Create embedding (note: we don't import the actual vector)
                    new_embedding = Embedding(
                        project_id=project_id,
                        data_key=embedding.get("data_key", node_key),
                        node_key=node_key,
                        node_path=embedding.get("node_path"),
                        node_type=embedding.get("node_type"),
                        description=embedding.get("description", ""),
                        data=embedding.get("data", {}),
                        data_format=embedding.get("data_format", "json"),
                        embedding=[0.0] * 384,  # Placeholder - needs re-embedding
                    )
                    session.add(new_embedding)
                    imported += 1

            logger.info("Imported embeddings", count=imported)
            return imported

        except Exception as e:
            logger.error("Failed to import embeddings", error=str(e))
            return 0

    async def _import_agents(
        self,
        project_id: str,
        agents: List[Dict[str, Any]],
        overwrite: bool = False
    ) -> int:
        """Import agent registrations"""
        from sqlalchemy import delete

        try:
            async with self.db.session() as session:
                # If overwriting, delete existing agents for this project
                if overwrite:
                    await session.execute(
                        delete(AgentRegistration).where(AgentRegistration.project_id == project_id)
                    )

                imported = 0
                for agent in agents:
                    agent_id = agent.get("agent_id")
                    if not agent_id:
                        continue

                    data = agent.get("data", {})

                    # Check if exists (when not overwriting)
                    if not overwrite:
                        result = await session.execute(
                            select(AgentRegistration)
                            .where(AgentRegistration.agent_id == agent_id)
                        )
                        if result.scalar_one_or_none():
                            continue

                    # Create agent registration
                    new_agent = AgentRegistration(
                        agent_id=agent_id,
                        project_id=data.get("project_id", project_id),
                        tenant_id=data.get("tenant_id"),
                        needs=data.get("needs", []),
                        notification_method=data.get("notification_method", "redis"),
                        response_format=data.get("response_format", "json"),
                        notification_channel=data.get("notification_channel"),
                        webhook_url=data.get("webhook_url"),
                        data_keys=data.get("data_keys", []),
                        last_sequence=agent.get("last_sequence"),
                        data=data,
                    )
                    session.add(new_agent)
                    imported += 1

            logger.info("Imported agents", count=imported)
            return imported

        except Exception as e:
            logger.error("Failed to import agents", error=str(e))
            return 0
