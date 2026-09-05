"""
Data versioning API built on event sourcing.

Instead of separate versioning system, versions are derived from event stream.
"""

from fastapi import APIRouter, Request, HTTPException
from src.core.logging import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/api/v1/versions", tags=["Versioning"])


def _extract_version(event: dict, data_key: str) -> dict | None:
    """
    Project an event onto a version record, or return ``None`` if the event
    does not concern ``data_key``.

    Events have the shape ``{"sequence", "event_type", "data": {data_key: value}}``,
    so an event is relevant iff ``data_key`` is present in its ``data`` dict.
    """
    event_data = event.get("data") or {}
    if not isinstance(event_data, dict) or data_key not in event_data:
        return None

    return {
        "sequence": event.get("sequence"),
        "data": event_data.get(data_key),
        "event_type": event.get("event_type"),
    }


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

        # TODO(#120): full-scan of the project's event log. This loads up to
        # 10k events into memory and filters in Python; replace with a
        # key-scoped query once #120 lands.
        all_events = await engine.event_store.get_all_events(project_id, count=10000)

        versions = []
        for event in all_events:
            version = _extract_version(event, data_key)
            if version is not None:
                versions.append(version)

        # Sort by sequence (most recent first) and limit. Sequences are
        # monotonic integers-as-strings, so sort numerically.
        versions.sort(key=lambda v: int(v["sequence"]), reverse=True)
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

        # TODO(#120): full-scan of the project's event log to find one sequence;
        # replace with a direct sequence lookup once #120 lands.
        all_events = await engine.event_store.get_all_events(project_id, count=10000)

        for event in all_events:
            if event.get("sequence") != sequence:
                continue

            version = _extract_version(event, data_key)
            if version is not None:
                return {
                    "project_id": project_id,
                    "data_key": data_key,
                    "sequence": sequence,
                    "data": version["data"],
                    "event_type": version["event_type"],
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

    SECURITY: This is an UNAUTHENTICATED mutation. Anyone who can reach this
    endpoint can restore a data key to an arbitrary prior version. Auth is
    deliberately out of scope here and is tracked by issue #72 (Tier-0 auth
    foundation); wire an authorization check in once #72 lands.

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
            event_type=f"data.restored.{data_key}",
        )

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
