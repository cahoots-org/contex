"""Main Context Engine orchestrator"""

import json
import logging
import tiktoken
import toon_format as toon
from datetime import datetime
from typing import Dict, List, Any, Optional
from redis.asyncio import Redis

from .database import DatabaseManager
from .semantic_matcher import SemanticDataMatcher
from .event_store import EventStore
from .matcher import HybridMatcher
from .subscriptions import SubscriptionService
from .limits import clamp_top_k
from .models import DataPublishEvent

logger = logging.getLogger(__name__)


def _strip_nul_bytes(value: Any) -> Any:
    """Recursively remove NUL (0x00) from any strings in a published value.

    Postgres cannot store 0x00 in a text or JSONB column, so a single NUL byte
    anywhere in published content fails the INSERT and, with it, the whole
    publish/batch. The usual source is text decoded from a mis-detected binary
    file (e.g. a .pickle decoded with errors="replace", which keeps NUL because
    it is valid UTF-8). Strip it once here, at the boundary every connector and
    both MCP publish tools route through.
    """
    if isinstance(value, str):
        return value.replace("\x00", "")
    if isinstance(value, dict):
        return {k: _strip_nul_bytes(v) for k, v in value.items()}
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
    Real-time notifications: Redis pub/sub
    """

    def __init__(
        self,
        db: DatabaseManager,
        redis: Redis,
        similarity_threshold: float = 0.5,
        max_matches: int = 10,
        max_context_size: int = 51200,  # ~40% of 128k token context window
    ):
        self.db = db
        self.redis = redis
        self.semantic_matcher = SemanticDataMatcher(
            db=db,
            similarity_threshold=similarity_threshold,
            max_matches=max_matches
        )
        self.subscriptions = SubscriptionService(
            db, HybridMatcher(self.semantic_matcher), redis
        )
        self.event_store = EventStore(db)
        self.max_context_size = max_context_size

        # Initialize tokenizer for context size estimation (cl100k_base is GPT-4 tokenizer)
        try:
            self.tokenizer = tiktoken.get_encoding("cl100k_base")
        except Exception:
            # Fallback if tiktoken has issues
            self.tokenizer = None
            logger.warning("Tiktoken unavailable, context size limits disabled")

        logger.info("Initialized")
        if self.max_context_size:
            logger.debug("Max context size: %s tokens", self.max_context_size)

    async def initialize(self):
        """Initialize pgvector index for vector similarity search"""
        await self.semantic_matcher.initialize_index()

    def _format_data(self, data: Any, format: str = "toon") -> str:
        """
        Format data according to agent preference.

        Args:
            data: Data to format
            format: 'toon' or 'json'

        Returns:
            Formatted string
        """
        if format == "toon":
            try:
                return toon.encode(data)
            except NotImplementedError:
                # TOON encoder not yet available, fall back to JSON
                logger.warning("TOON format requested but not yet implemented, using JSON")
                return json.dumps(data, indent=2)
        else:
            return json.dumps(data, indent=2)

    def _estimate_tokens(self, data: Any) -> int:
        """
        Estimate token count for data.

        Args:
            data: Data to estimate tokens for

        Returns:
            Estimated token count
        """
        if not self.tokenizer:
            # Fallback: rough estimate of 4 chars per token
            return len(json.dumps(data)) // 4

        try:
            text = json.dumps(data)
            return len(self.tokenizer.encode(text))
        except Exception:
            # Fallback on error
            return len(json.dumps(data)) // 4

    def _truncate_matches(
        self, matches: Dict[str, List[Dict[str, Any]]], max_tokens: int
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        Truncate matches to fit within token budget.

        Strategy: Keep at least one match per need if possible, then
        distribute remaining budget proportionally by similarity scores.

        Args:
            matches: Dictionary mapping needs to their matches
            max_tokens: Maximum total tokens allowed

        Returns:
            Truncated matches dictionary
        """
        if not self.max_context_size or not matches:
            return matches

        # Calculate token cost for each match
        match_costs = []  # List of (need, match_idx, match, tokens)
        for need, need_matches in matches.items():
            for idx, match in enumerate(need_matches):
                tokens = self._estimate_tokens(match["data"])
                match_costs.append((need, idx, match, tokens))

        # Calculate total tokens
        total_tokens = sum(cost[3] for cost in match_costs)

        if total_tokens <= max_tokens:
            return matches  # No truncation needed

        logger.warning(
            "Context size (%s tokens) exceeds limit (%s tokens)", total_tokens, max_tokens
        )
        logger.warning("Truncating to fit budget...")

        # Phase 1: Keep highest similarity match from each need
        result = {need: [] for need in matches.keys()}
        budget_used = 0
        reserved_matches = set()

        for need, need_matches in matches.items():
            if not need_matches:
                continue

            # Keep the highest similarity match (first one, since they're sorted)
            best_match = need_matches[0]
            tokens = self._estimate_tokens(best_match["data"])

            if budget_used + tokens <= max_tokens:
                result[need].append(best_match)
                budget_used += tokens
                reserved_matches.add((need, 0))

        # Phase 2: Fill remaining budget with additional matches, sorted by similarity
        remaining_budget = max_tokens - budget_used

        # Get all non-reserved matches sorted by similarity
        candidate_matches = []
        for need, idx, match, tokens in match_costs:
            if (need, idx) not in reserved_matches:
                candidate_matches.append((need, match, tokens, match["similarity"]))

        # Sort by similarity (highest first)
        candidate_matches.sort(key=lambda x: x[3], reverse=True)

        # Add matches until budget exhausted
        for need, match, tokens, similarity in candidate_matches:
            if tokens <= remaining_budget:
                result[need].append(match)
                remaining_budget -= tokens

            if remaining_budget <= 0:
                break

        # Calculate final stats
        final_tokens = sum(
            self._estimate_tokens(match["data"])
            for need_matches in result.values()
            for match in need_matches
        )
        original_count = sum(len(need_matches) for need_matches in matches.values())
        truncated_count = sum(len(need_matches) for need_matches in result.values())

        logger.warning(
            "Kept %s/%s matches (%s tokens)", truncated_count, original_count, final_tokens
        )

        return result

    async def publish_data(
        self,
        event: DataPublishEvent,
        *,
        source: str = "api",
        actor: Optional[Dict[str, Any]] = None,
        tenant_id: Optional[str] = None,
    ) -> str:
        """
        Main app publishes data change (supports any format).

        Args:
            event: Data publish event
            source: Source of the publication (default: "api")
            actor: Actor performing the publication
            tenant_id: Tenant ID for multi-tenant scenarios

        Returns:
            Event sequence number
        """
        project_id = event.project_id
        data_key = event.data_key
        # Strip NUL bytes up front: Postgres rejects 0x00 in text/JSONB, and this
        # is the single path both register_data and the event store fan out from.
        data = _strip_nul_bytes(event.data)
        format_hint = event.data_format

        logger.debug("Publishing data: %s:%s", project_id, data_key)

        # 1. Register data with semantic matcher (normalizes and stores)
        await self.semantic_matcher.register_data(
            project_id, data_key, data, format_hint
        )

        # 2. Append to event store
        # For binary formats (PDF, DOCX), store metadata instead of raw bytes
        # since the event store uses JSON serialization
        if isinstance(data, (bytes, bytearray)):
            event_data = {
                data_key: {
                    "_binary": True,
                    "_format": format_hint,
                    "_size_bytes": len(data),
                }
            }
        else:
            event_data = {data_key: data}
        event_type = event.event_type or f"{data_key}_updated"
        sequence = await self.event_store.append_event(
            project_id, event_type, event_data,
            tenant_id=tenant_id, data_key=data_key, source=source, actor=actor,
        )

        # Reconcile persistent subscriptions against the new data (inline). This
        # is the notification path: MCP subscribers are pushed to over the
        # internal bridge as reconcile re-matches affected subscriptions.
        # A reconcile/matcher failure must not fail the publish (spec §6):
        # the event is already appended.
        try:
            await self.subscriptions.reconcile_project(project_id, data_key)
        except Exception:
            logger.exception(
                "subscription reconcile failed for %s/%s", project_id, data_key
            )

        return sequence

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
            project_id, [query], top_k=top_k, threshold=threshold, since=since
        )

        # Extract matches for the query
        results = matches.get(query, [])

        logger.debug("Found %s matches for query", len(results))

        return results
