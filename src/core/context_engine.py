"""Main Context Engine orchestrator"""

import asyncio
import json
import tiktoken
import toon_format as toon
from typing import Dict, List, Any, Optional
from redis.asyncio import Redis

from .database import DatabaseManager
from .semantic_matcher import SemanticDataMatcher
from .event_store import EventStore
from .webhook_dispatcher import WebhookDispatcher
from .models import (
    AgentRegistration,
    DataPublishEvent,
    RegistrationResponse,
    MatchedDataSource,
)


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
        self.event_store = EventStore(db)
        self.webhook_dispatcher = WebhookDispatcher()
        self.max_context_size = max_context_size

        # Initialize tokenizer for context size estimation (cl100k_base is GPT-4 tokenizer)
        try:
            self.tokenizer = tiktoken.get_encoding("cl100k_base")
        except Exception:
            # Fallback if tiktoken has issues
            self.tokenizer = None
            print(
                "[ContextEngine] ⚠ Tiktoken unavailable, context size limits disabled"
            )

        # Track registered agents: agent_id -> {project_id, needs, notification_method, ...}
        self.agents: Dict[str, Dict[str, Any]] = {}

        print("[ContextEngine] ✓ Initialized")
        if self.max_context_size:
            print(f"[ContextEngine]   Max context size: {self.max_context_size} tokens")

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
                print("[ContextEngine] ⚠ TOON format requested but not yet implemented, using JSON")
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

        print(
            f"[ContextEngine] ⚠ Context size ({total_tokens} tokens) exceeds limit ({max_tokens} tokens)"
        )
        print(f"[ContextEngine]   Truncating to fit budget...")

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

        print(
            f"[ContextEngine]   Kept {truncated_count}/{original_count} matches ({final_tokens} tokens)"
        )

        return result

    async def publish_data(self, event: DataPublishEvent) -> str:
        """
        Main app publishes data change (supports any format).

        Args:
            event: Data publish event

        Returns:
            Event sequence number
        """
        project_id = event.project_id
        data_key = event.data_key
        data = event.data
        format_hint = event.data_format

        print(f"[ContextEngine] Publishing data: {project_id}:{data_key}")

        # 1. Register data with semantic matcher (normalizes and stores)
        await self.semantic_matcher.register_data(
            project_id, data_key, data, format_hint
        )

        # 2. Append to event store
        event_type = event.event_type or f"{data_key}_updated"
        sequence = await self.event_store.append_event(
            project_id, event_type, {data_key: data}
        )

        # 3. Notify agents that depend on this data
        await self._notify_affected_agents(project_id, data_key, data, sequence)

        return sequence

    async def register_agent(
        self, registration: AgentRegistration
    ) -> RegistrationResponse:
        """
        Agent registers with semantic data needs.

        Args:
            registration: Agent registration request

        Returns:
            Registration response with matched data
        """
        agent_id = registration.agent_id
        project_id = registration.project_id
        needs = registration.data_needs
        last_seen = registration.last_seen_sequence or "0"

        print(f"[ContextEngine] Registering agent: {agent_id} (project: {project_id})")

        # 1. Match agent needs to available data
        matches = await self.semantic_matcher.match_agent_needs(project_id, needs)

        # 2. Truncate matches if they exceed context size limit
        if self.max_context_size:
            matches = self._truncate_matches(matches, self.max_context_size)

        # 3. Validate notification configuration
        if registration.notification_method == "webhook":
            if not registration.webhook_url:
                raise ValueError(
                    "webhook_url is required when notification_method='webhook'"
                )

        # 4. Determine notification channel (for Redis mode)
        notification_channel = (
            registration.notification_channel or f"agent:{agent_id}:updates"
        )

        # 5. Track which data keys this agent depends on
        data_keys = set()
        for need_matches in matches.values():
            for match in need_matches:
                data_keys.add(match["data_key"])

        # 6. Store agent registration
        self.agents[agent_id] = {
            "project_id": project_id,
            "needs": needs,
            "notification_method": registration.notification_method,
            "response_format": registration.response_format,  # TOON or JSON
            "channel": notification_channel,  # For Redis
            "webhook_url": (
                str(registration.webhook_url) if registration.webhook_url else None
            ),
            "webhook_secret": registration.webhook_secret,
            "data_keys": list(data_keys),
            "last_sequence": last_seen,
        }

        # 7. Send initial context to agent
        await self._send_initial_context(agent_id, matches, registration)

        # 8. Catch up missed events
        missed_events = await self.event_store.get_events_since(project_id, last_seen)

        for event in missed_events:
            await self.redis.publish(
                notification_channel,
                json.dumps(
                    {
                        "type": "event",
                        "sequence": event["sequence"],
                        "event_type": event["event_type"],
                        "data": event["data"],
                    }
                ),
            )

        # 9. Get current sequence
        current_sequence = await self.event_store.get_latest_sequence(project_id) or "0"

        # 10. Build response
        matched_counts = {
            need: len(need_matches) for need, need_matches in matches.items()
        }

        print(f"[ContextEngine] ✓ Agent {agent_id} registered:")
        print(f"[ContextEngine]   Needs: {len(needs)}")
        print(f"[ContextEngine]   Data keys: {len(data_keys)}")
        print(f"[ContextEngine]   Caught up: {len(missed_events)} events")

        return RegistrationResponse(
            status="registered",
            agent_id=agent_id,
            project_id=project_id,
            caught_up_events=len(missed_events),
            current_sequence=current_sequence,
            matched_needs=matched_counts,
            notification_channel=notification_channel,
        )

    async def _send_initial_context(
        self,
        agent_id: str,
        matches: Dict[str, List[Dict[str, Any]]],
        registration: AgentRegistration,
    ):
        """Send initial matched context to agent via Redis or webhook"""

        # Convert matches to MatchedDataSource format
        context = {}
        for need, need_matches in matches.items():
            context[need] = [
                MatchedDataSource(
                    data_key=match["data_key"],
                    similarity=match["similarity"],
                    data=match["data"],
                    description=match.get("description"),
                ).model_dump()
                for match in need_matches
            ]

        # Format the context according to agent's preference
        format_type = registration.response_format
        context_payload = {
            "type": "initial_context",
            "agent_id": agent_id,
            "format": format_type,
            "context": context,
        }

        # Serialize based on format
        if format_type == "toon":
            try:
                serialized_payload = toon.encode(context_payload)
            except NotImplementedError:
                # TOON encoder not yet available, fall back to JSON
                print(f"[ContextEngine] ⚠ TOON format requested but not yet implemented, using JSON for {agent_id}")
                serialized_payload = json.dumps(context_payload)
                format_type = "json"  # Update format type for logging
        else:
            serialized_payload = json.dumps(context_payload)

        # Send via appropriate method
        if registration.notification_method == "redis":
            # Redis pub/sub
            channel = registration.notification_channel or f"agent:{agent_id}:updates"
            await self.redis.publish(channel, serialized_payload)
            print(
                f"[ContextEngine] Sent initial context to {agent_id} via Redis ({format_type.upper()} format)"
            )

        elif registration.notification_method == "webhook":
            # HTTP webhook
            success = await self.webhook_dispatcher.send_initial_context(
                url=str(registration.webhook_url),
                agent_id=agent_id,
                context=context,
                secret=registration.webhook_secret,
            )
            if success:
                print(
                    f"[ContextEngine] Sent initial context to {agent_id} via webhook ({format_type.upper()} format)"
                )
            else:
                print(
                    f"[ContextEngine] ⚠ Failed to send initial context to {agent_id} via webhook"
                )

    async def _notify_affected_agents(
        self, project_id: str, data_key: str, data: Dict[str, Any], sequence: str
    ):
        """Notify agents that depend on this data key via Redis or webhook"""

        # Find agents that depend on this data
        affected_agents = [
            agent_id
            for agent_id, agent_info in self.agents.items()
            if agent_info["project_id"] == project_id
            and data_key in agent_info["data_keys"]
        ]

        if not affected_agents:
            print(f"[ContextEngine] No agents affected by {data_key}")
            return

        print(
            f"[ContextEngine] Notifying {len(affected_agents)} agents about {data_key} update"
        )

        for agent_id in affected_agents:
            agent_info = self.agents[agent_id]
            format_type = agent_info.get("response_format", "toon")

            # Build update payload
            update_payload = {
                "type": "data_update",
                "sequence": sequence,
                "data_key": data_key,
                "format": format_type,
                "data": data,
            }

            # Serialize based on format
            if format_type == "toon":
                try:
                    serialized_payload = toon.encode(update_payload)
                except NotImplementedError:
                    # TOON encoder not yet available, fall back to JSON
                    serialized_payload = json.dumps(update_payload)
            else:
                serialized_payload = json.dumps(update_payload)

            # Send via appropriate method
            if agent_info["notification_method"] == "redis":
                # Redis pub/sub
                await self.redis.publish(agent_info["channel"], serialized_payload)

            elif agent_info["notification_method"] == "webhook":
                # HTTP webhook (fire and forget - don't block on delivery)
                asyncio.create_task(
                    self.webhook_dispatcher.send_data_update(
                        url=agent_info["webhook_url"],
                        agent_id=agent_id,
                        sequence=sequence,
                        data_key=data_key,
                        data=data,
                        secret=agent_info.get("webhook_secret"),
                    )
                )

            # Update agent's last sequence
            agent_info["last_sequence"] = sequence

    async def unregister_agent(self, agent_id: str):
        """Remove agent registration"""
        if agent_id in self.agents:
            del self.agents[agent_id]
            print(f"[ContextEngine] Unregistered agent: {agent_id}")
        else:
            print(f"[ContextEngine] Agent {agent_id} not found")

    def get_registered_agents(self) -> List[str]:
        """Get list of registered agent IDs"""
        return list(self.agents.keys())

    def get_agent_info(self, agent_id: str) -> Optional[Dict[str, Any]]:
        """Get info about a registered agent"""
        return self.agents.get(agent_id)

    async def query_project_data(
        self, project_id: str, query: str, top_k: int = 5, threshold: Optional[float] = None
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

        Returns:
            List of matched data sources with similarity scores
        """
        print(f"[ContextEngine] Ad-hoc query for project {project_id}: '{query}'")

        # Use semantic matcher with temporary overrides
        original_max = self.semantic_matcher.max_matches
        original_threshold = self.semantic_matcher.threshold
        self.semantic_matcher.max_matches = top_k
        if threshold is not None:
            self.semantic_matcher.threshold = threshold

        try:
            # Match the query as if it were a single agent need
            matches = await self.semantic_matcher.match_agent_needs(project_id, [query])

            # Extract matches for the query
            results = matches.get(query, [])

            print(f"[ContextEngine] Found {len(results)} matches for query")

            return results

        finally:
            # Restore original values
            self.semantic_matcher.max_matches = original_max
            self.semantic_matcher.threshold = original_threshold
