"""Main Context Engine orchestrator"""

import asyncio
import logging
import re
from datetime import datetime
from typing import Dict, List, Any, Optional, Tuple

from .database import DatabaseManager
from .semantic_matcher import SemanticDataMatcher
from .node_parsers import resolve_format
from .subscriptions import SubscriptionService
from .limits import clamp_top_k
from .models import DATA_KEY_PATTERN, DataPublishEvent
from .notifier import Notifier

logger = logging.getLogger(__name__)


def _strip_nul_bytes(value: Any) -> Any:
    """Recursively remove NUL (0x00), which Postgres rejects in text/JSONB columns."""
    if isinstance(value, str):
        return value.replace("\x00", "")
    if isinstance(value, dict):
        return {_strip_nul_bytes(k): _strip_nul_bytes(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_strip_nul_bytes(v) for v in value]
    return value


class ContextEngine:
    """
    Context Engine: Embedding-based semantic matching for agent context discovery.

    Architecture:
    1. Main app publishes data changes
    2. Context Engine embeds + registers data
    3. Agents register with semantic needs
    4. Context Engine matches needs to data (embedding similarity)
    5. Agents receive matched data + subscribe to updates
    6. Real-time updates via pub/sub

    Data storage: PostgreSQL with pgvector
    Real-time notifications: Postgres LISTEN/NOTIFY
    """

    def __init__(
        self,
        db: DatabaseManager,
        similarity_threshold: float = 0.5,
        max_matches: int = 10,
        embed_model: str = "thenlper/gte-base",
        notifier: Optional[Notifier] = None,
    ):
        self.db = db
        self.notifier = notifier
        self.semantic_matcher = SemanticDataMatcher(
            db=db,
            model_name=embed_model,
            similarity_threshold=similarity_threshold,
            max_matches=max_matches
        )
        self.subscriptions = SubscriptionService(
            db, self.semantic_matcher,
            encoder=self.semantic_matcher.model,
            default_threshold=self.semantic_matcher.threshold,
            default_top_k=self.semantic_matcher.max_matches,
        )
        # Background reconcile per project: one running, the next one's keys pending.
        self._reconcile_pending: Dict[str, set] = {}
        self._reconcile_tasks: Dict[str, asyncio.Task] = {}

        logger.info("Initialized")

    def _reconcile_later(self, project_id: str, keys: set) -> None:
        """Reconcile ``keys`` in the background, merged into the project's next run."""
        self._reconcile_pending.setdefault(project_id, set()).update(keys)
        if project_id not in self._reconcile_tasks:
            self._reconcile_tasks[project_id] = asyncio.create_task(self._run_reconciles(project_id))

    async def _run_reconciles(self, project_id: str) -> None:
        try:
            while keys := self._reconcile_pending.pop(project_id, None):
                try:
                    await self.subscriptions.reconcile_project(project_id, keys)
                except Exception:
                    logger.exception("subscription reconcile failed for %s", project_id)
        finally:
            del self._reconcile_tasks[project_id]

    async def publish_data(self, event: DataPublishEvent) -> None:
        """Publish one document (any format); raises ValueError if it can't be read."""
        _, failed = await self.publish_data_batch([event])
        if failed:
            raise ValueError(failed[0]["error"])

    async def publish_data_batch(
        self,
        events: List[DataPublishEvent],
        *,
        origin: Optional[str] = None,
    ) -> Tuple[List[str], List[Dict[str, str]]]:
        """Publish many events for one project, embedding them in a single pass.

        ``origin`` tags the documents with the source stream that published them.
        Returns the published data_keys in input order, and a
        ``{data_key, error}`` for each event that could not be published.
        """
        if not events:
            return [], []
        project_id = events[0].project_id
        if any(e.project_id != project_id for e in events):
            raise ValueError("publish_data_batch events must share one project_id")

        # A generic or absent format is inferred from the key's extension, but
        # only for documents; structured data keeps its implicit format.
        prepared = [
            (e, _strip_nul_bytes(e.data),
             resolve_format(e.data_format, e.data_key if isinstance(e.data, (str, bytes)) else None))
            for e in events
        ]

        # 1. Register data with semantic matcher (normalizes and stores)
        errors = await self.semantic_matcher.register_data_batch(
            project_id, [(e.data_key, data, fmt) for e, data, fmt in prepared],
            {e.data_key: e.published_at for e in events if e.published_at is not None},
            origin=origin,
        )

        published = [e.data_key for e, error in zip(events, errors) if error is None]
        failed = [{"data_key": e.data_key, "error": error} for e, error in zip(events, errors) if error]

        if published:
            self._reconcile_later(project_id, set(published))

        return published, failed

    async def list_keys(
        self, project_id: str, origin: str, after: Optional[str] = None, limit: int = 1000,
    ) -> List[str]:
        """Document keys last published by ``origin``, in order, after ``after``."""
        return await self.semantic_matcher.list_keys(project_id, origin, after, limit)

    async def delete_data(self, project_id: str, data_keys: List[str]) -> Dict[str, List[str]]:
        """Delete documents by key; subscriptions catch up in the background.

        Returns the requested keys split into ``deleted`` and ``missing``. A
        key publish would reject can't exist, so it is reported missing.
        """
        keys = list(dict.fromkeys(data_keys))
        valid = [k for k in keys if re.match(DATA_KEY_PATTERN, k)]
        deleted = set(await self.semantic_matcher.delete_data_keys(project_id, valid))
        if deleted:
            self._reconcile_later(project_id, deleted)
        return {
            "deleted": [k for k in keys if k in deleted],
            "missing": [k for k in keys if k not in deleted],
        }

    async def query_project_data(
        self, project_id: str, query: str, top_k: int = 5,
        threshold: Optional[float] = None, since: Optional[datetime] = None,
    ) -> List[Dict[str, Any]]:
        """
        Ad-hoc semantic query of project data without agent registration.

        This allows one-off queries for specific information without the overhead
        of registering an agent.

        Args:
            project_id: Project identifier
            query: Natural language query
            top_k: Maximum number of results to return
            threshold: Optional similarity threshold override (0-1)
            since: Optional cutoff; only match data created or updated on or
                after this time.

        Returns:
            List of matched data sources with similarity scores
        """
        logger.debug("Ad-hoc query for project %s: '%s'", project_id, query)

        top_k = clamp_top_k(top_k)
        # Pass per-request top_k/threshold through instead of mutating the shared
        # matcher, so concurrent ad-hoc queries can't corrupt each other (#105).
        matches = await self.semantic_matcher.match_agent_needs(
            project_id, [query], top_k=top_k, threshold=threshold, since=since,
            rerank=True,
        )

        # Extract matches for the query
        results = matches.get(query, [])

        logger.debug("Found %s matches for query", len(results))

        return results
