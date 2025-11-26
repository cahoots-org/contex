"""
Data versioning API built on event sourcing.

Instead of separate versioning system, versions are derived from event stream.
"""

from fastapi import APIRouter, Request, HTTPException
from typing import List, Optional
from src.core.logging import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/api/v1/versions", tags=["Versioning"])


@router.get("/projects/{project_id}/data/{data_key}/history")
async def get_version_history(
    project_id: str,
    data_key: str,
    request: Request,
    limit: int = 100,
):
    """
    Get version history for a data key (from event stream).

    Args:
        project_id: Project identifier
        data_key: Data key
        limit: Maximum number of versions to return

    Returns:
        List of versions (events) for the data key
    """
    try:
        engine = request.app.state.context_engine

        # Get all events for this project
        all_events = await engine.event_store.get_events(project_id, since="0", count=10000)

        # Filter to events for this data_key
        versions = []
        for event in all_events:
            event_data = event.get("data", {})
            if isinstance(event_data, str):
                import json
                try:
                    event_data = json.loads(event_data)
                except:
                    continue

            if event_data.get("data_key") == data_key:
                versions.append({
                    "sequence": event.get("id"),
                    "timestamp": event.get("id").split("-")[0] if "-" in event.get("id", "") else event.get("id"),
                    "data": event_data.get("data"),
                    "data_format": event_data.get("data_format", "json"),
                    "description": event_data.get("description"),
                    "event_type": event_data.get("event_type"),
                })

        # Sort by sequence (most recent first) and limit
        versions.sort(key=lambda x: x["sequence"], reverse=True)
        versions = versions[:limit]

        return {
            "project_id": project_id,
            "data_key": data_key,
            "versions": versions,
            "count": len(versions),
        }

    except Exception as e:
        logger.error("Failed to get version history",
                    project_id=project_id,
                    data_key=data_key,
                    error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/projects/{project_id}/data/{data_key}/version/{sequence}")
async def get_specific_version(
    project_id: str,
    data_key: str,
    sequence: str,
    request: Request,
):
    """
    Get a specific version of data by sequence number.

    Args:
        project_id: Project identifier
        data_key: Data key
        sequence: Event sequence number

    Returns:
        Data at that specific version
    """
    try:
        engine = request.app.state.context_engine

        # Get events up to this sequence
        all_events = await engine.event_store.get_events(project_id, since="0", count=10000)

        # Find the specific version
        for event in all_events:
            if event.get("id") == sequence:
                event_data = event.get("data", {})
                if isinstance(event_data, str):
                    import json
                    try:
                        event_data = json.loads(event_data)
                    except:
                        raise HTTPException(status_code=500, detail="Failed to parse event data")

                if event_data.get("data_key") == data_key:
                    return {
                        "project_id": project_id,
                        "data_key": data_key,
                        "sequence": sequence,
                        "timestamp": sequence.split("-")[0] if "-" in sequence else sequence,
                        "data": event_data.get("data"),
                        "data_format": event_data.get("data_format", "json"),
                        "description": event_data.get("description"),
                    }

        raise HTTPException(status_code=404, detail=f"Version {sequence} not found for {data_key}")

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to get specific version",
                    project_id=project_id,
                    data_key=data_key,
                    sequence=sequence,
                    error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/projects/{project_id}/data/{data_key}/diff")
async def diff_versions(
    project_id: str,
    data_key: str,
    from_sequence: str,
    to_sequence: str,
    request: Request,
):
    """
    Compare two versions of data.

    Args:
        project_id: Project identifier
        data_key: Data key
        from_sequence: Starting version sequence
        to_sequence: Ending version sequence

    Returns:
        Diff between the two versions
    """
    try:
        # Get both versions
        from_version = await get_specific_version(project_id, data_key, from_sequence, request)
        to_version = await get_specific_version(project_id, data_key, to_sequence, request)

        return {
            "project_id": project_id,
            "data_key": data_key,
            "from_sequence": from_sequence,
            "to_sequence": to_sequence,
            "from_data": from_version.get("data"),
            "to_data": to_version.get("data"),
            "changed": from_version.get("data") != to_version.get("data"),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to diff versions",
                    project_id=project_id,
                    data_key=data_key,
                    error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/projects/{project_id}/data/{data_key}/restore/{sequence}")
async def restore_version(
    project_id: str,
    data_key: str,
    sequence: str,
    request: Request,
):
    """
    Restore data to a specific version (creates new event with old data).

    Args:
        project_id: Project identifier
        data_key: Data key
        sequence: Version sequence to restore to

    Returns:
        New event sequence after restoration
    """
    try:
        # Get the version to restore
        version = await get_specific_version(project_id, data_key, sequence, request)

        # Publish the old data as a new event (restoration)
        from src.core.models import DataPublishEvent
        engine = request.app.state.context_engine

        restore_event = DataPublishEvent(
            project_id=project_id,
            data_key=data_key,
            data=version.get("data"),
            data_format=version.get("data_format"),
            event_type=f"data.restored.{data_key}",
        )
        restore_event.description = f"Restored to version {sequence}"

        new_sequence = await engine.publish_data(restore_event)

        return {
            "project_id": project_id,
            "data_key": data_key,
            "restored_from": sequence,
            "new_sequence": new_sequence,
            "data": version.get("data"),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to restore version",
                    project_id=project_id,
                    data_key=data_key,
                    sequence=sequence,
                    error=str(e))
        raise HTTPException(status_code=500, detail=str(e))
