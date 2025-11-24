"""Data export/import functionality for Contex projects"""

import json
import time
from typing import Dict, List, Any, Optional, Literal
from redis.asyncio import Redis
from .logging import get_logger
import toon_format as toon

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

    def __init__(self, redis: Redis):
        """
        Initialize export/import manager.

        Args:
            redis: Redis connection
        """
        self.redis = redis
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

        # Export events from stream
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
        """Export all events from Redis stream"""
        stream_key = f"events:{project_id}"
        events = []

        try:
            # Check if stream exists
            exists = await self.redis.exists(stream_key)
            if not exists:
                return events

            # Read all events from stream (using XREAD with - to get all)
            # Get stream info first
            stream_info = await self.redis.xinfo_stream(stream_key)
            if stream_info.get("length", 0) == 0:
                return events

            # Read all entries
            stream_data = await self.redis.xrange(stream_key, "-", "+")

            for event_id, event_data in stream_data:
                # Decode bytes if necessary
                decoded_data = {}
                for key, value in event_data.items():
                    key_str = key.decode() if isinstance(key, bytes) else key
                    value_str = value.decode() if isinstance(value, bytes) else value
                    decoded_data[key_str] = value_str

                events.append({
                    "event_id": event_id.decode() if isinstance(event_id, bytes) else event_id,
                    "data": decoded_data
                })

        except Exception as e:
            logger.error("Failed to export events", project_id=project_id, error=str(e))

        return events

    async def _export_embeddings(self, project_id: str) -> List[Dict[str, Any]]:
        """Export all embeddings for the project"""
        embeddings = []

        try:
            # Scan for embedding keys matching this project
            pattern = f"embedding:{project_id}:*"
            cursor = 0

            while True:
                cursor, keys = await self.redis.scan(cursor, match=pattern, count=100)

                for key in keys:
                    # Get the embedding data
                    embedding_data = await self.redis.hgetall(key)

                    if embedding_data:
                        # Decode all fields
                        decoded = {}
                        for field, value in embedding_data.items():
                            field_str = field.decode() if isinstance(field, bytes) else field
                            value_str = value.decode() if isinstance(value, bytes) else value
                            decoded[field_str] = value_str

                        key_str = key.decode() if isinstance(key, bytes) else key
                        embeddings.append({
                            "key": key_str,
                            "data": decoded
                        })

                if cursor == 0:
                    break

        except Exception as e:
            logger.error("Failed to export embeddings", project_id=project_id, error=str(e))

        return embeddings

    async def _export_agents(self, project_id: str) -> List[Dict[str, Any]]:
        """Export all agent registrations for the project"""
        agents = []

        try:
            # Scan for agent keys
            cursor = 0
            agent_ids = set()

            while True:
                cursor, keys = await self.redis.scan(cursor, match="agent:*:data", count=100)

                for key in keys:
                    key_str = key.decode() if isinstance(key, bytes) else key
                    # Extract agent_id from key (agent:{agent_id}:data)
                    agent_id = key_str.replace("agent:", "").replace(":data", "")
                    agent_ids.add(agent_id)

                if cursor == 0:
                    break

            # Get data for each agent
            for agent_id in agent_ids:
                agent_data = await self.redis.get(f"agent:{agent_id}:data")
                last_seen = await self.redis.get(f"agent:{agent_id}:last_seen")
                needs = await self.redis.get(f"agent:{agent_id}:needs")

                if agent_data:
                    # Decode and parse
                    agent_data_str = agent_data.decode() if isinstance(agent_data, bytes) else agent_data
                    parsed_data = json.loads(agent_data_str)

                    # Only include agents for this project
                    if parsed_data.get("project_id") == project_id:
                        agent_export = {
                            "agent_id": agent_id,
                            "data": parsed_data,
                        }

                        if last_seen:
                            last_seen_str = last_seen.decode() if isinstance(last_seen, bytes) else last_seen
                            agent_export["last_seen"] = last_seen_str

                        if needs:
                            needs_str = needs.decode() if isinstance(needs, bytes) else needs
                            agent_export["needs"] = needs_str

                        agents.append(agent_export)

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

        # Check if project exists
        project_id = parsed_data["project_id"]
        stream_key = f"events:{project_id}"
        exists = await self.redis.exists(stream_key)

        if exists and not overwrite:
            return {
                "status": "error",
                "message": f"Project {project_id} already exists. Use overwrite=True to replace."
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
                parsed_data["data"]["embeddings"],
                overwrite
            )

        # Import agents
        if "agents" in parsed_data["data"]:
            stats["agents_imported"] = await self._import_agents(
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
            "warnings": warnings
        }

    async def _import_events(
        self,
        project_id: str,
        events: List[Dict[str, Any]],
        overwrite: bool = False
    ) -> int:
        """Import events to Redis stream"""
        stream_key = f"events:{project_id}"

        try:
            # If overwriting, delete existing stream
            if overwrite:
                await self.redis.delete(stream_key)

            # Add events to stream
            imported = 0
            for event in events:
                event_data = event.get("data", {})
                # Add to stream (let Redis generate new IDs)
                await self.redis.xadd(stream_key, event_data)
                imported += 1

            logger.info("Imported events", project_id=project_id, count=imported)
            return imported

        except Exception as e:
            logger.error("Failed to import events", project_id=project_id, error=str(e))
            return 0

    async def _import_embeddings(
        self,
        embeddings: List[Dict[str, Any]],
        overwrite: bool = False
    ) -> int:
        """Import embeddings to Redis"""
        try:
            imported = 0
            for embedding in embeddings:
                key = embedding.get("key")
                data = embedding.get("data", {})

                if key:
                    # Check if exists
                    exists = await self.redis.exists(key)
                    if exists and not overwrite:
                        continue

                    # Store as hash
                    await self.redis.hset(key, mapping=data)
                    imported += 1

            logger.info("Imported embeddings", count=imported)
            return imported

        except Exception as e:
            logger.error("Failed to import embeddings", error=str(e))
            return 0

    async def _import_agents(
        self,
        agents: List[Dict[str, Any]],
        overwrite: bool = False
    ) -> int:
        """Import agent registrations"""
        try:
            imported = 0
            for agent in agents:
                agent_id = agent.get("agent_id")
                data = agent.get("data", {})
                last_seen = agent.get("last_seen")
                needs = agent.get("needs")

                if agent_id:
                    # Check if exists
                    exists = await self.redis.exists(f"agent:{agent_id}:data")
                    if exists and not overwrite:
                        continue

                    # Store agent data
                    await self.redis.set(f"agent:{agent_id}:data", json.dumps(data))

                    if last_seen:
                        await self.redis.set(f"agent:{agent_id}:last_seen", last_seen)

                    if needs:
                        await self.redis.set(f"agent:{agent_id}:needs", needs)

                    imported += 1

            logger.info("Imported agents", count=imported)
            return imported

        except Exception as e:
            logger.error("Failed to import agents", error=str(e))
            return 0
