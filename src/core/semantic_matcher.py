"""Semantic data matching using embeddings for agent context discovery"""

import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

import numpy as np
from sentence_transformers import SentenceTransformer
from sqlalchemy import delete, func, select, text

from src.core.database import DatabaseManager
from src.core.logging import get_logger
from src.core.node_converter import NodeConverter

# Conditionally import pgvector-dependent models
VECTOR_STORE = os.getenv("VECTOR_STORE", "pgvector")
if VECTOR_STORE == "pgvector":
    from src.core.db_models import Embedding

logger = get_logger(__name__)


class SemanticDataMatcher:
    """
    Matches agent semantic needs to available project data using embeddings.

    Uses PostgreSQL with pgvector for persistent vector storage and similarity search.

    Key features:
    - PostgreSQL-backed persistent storage (survives restarts)
    - Native vector similarity search via pgvector
    - Auto-generates descriptions from data structure
    - Fast embedding-based similarity matching
    - Handles schema evolution gracefully
    """

    def __init__(
        self,
        db: DatabaseManager,
        model_name: str = "all-MiniLM-L6-v2",
        similarity_threshold: float = 0.35,
        max_matches: int = 10,
    ):
        """
        Initialize semantic matcher.

        Args:
            db: Database manager instance
            model_name: SentenceTransformer model (~80MB)
            similarity_threshold: Minimum similarity to match (0-1)
            max_matches: Maximum matches to return per need
        """
        logger.info("Loading embedding model", model_name=model_name)
        self.db = db
        self.model_name = model_name
        self.model = SentenceTransformer(model_name)
        self.threshold = similarity_threshold
        self.max_matches = max_matches
        self.embedding_dim = 384  # all-MiniLM-L6-v2 embedding dimension
        self.node_converter = NodeConverter()

        # Initialize hybrid search if enabled
        self.hybrid_search = None
        if os.getenv("HYBRID_SEARCH_ENABLED", "false").lower() == "true":
            try:
                from src.core.hybrid_search import RankFusionSearch

                keyword_threshold = float(os.getenv("KEYWORD_THRESHOLD", "5.0"))
                vector_threshold = float(os.getenv("VECTOR_THRESHOLD", "0.7"))

                self.hybrid_search = RankFusionSearch(
                    semantic_matcher=self,
                    keyword_threshold=keyword_threshold,
                    vector_threshold=vector_threshold,
                )
                logger.info("Hybrid search enabled")
            except Exception as e:
                logger.warning("Failed to initialize hybrid search", error=str(e))
                self.hybrid_search = None

        logger.info(
            "Semantic matcher initialized",
            threshold=similarity_threshold,
            max_matches=max_matches,
        )

    async def initialize_index(self):
        """
        Ensure vector storage backend is ready.

        This is called at startup to verify the database is ready for vector search.
        """
        if VECTOR_STORE == "opensearch":
            # OpenSearch-only mode - no pgvector needed
            if self.hybrid_search:
                logger.info("Using OpenSearch-only mode for vector storage")
            else:
                logger.warning("OpenSearch-only mode but hybrid_search not initialized!")
            return

        # pgvector mode - verify extension exists
        async with self.db.session() as session:
            try:
                # Verify pgvector extension exists
                await session.execute(text("SELECT 'vector'::regtype"))
                logger.info("pgvector extension verified")
            except Exception as e:
                logger.error("pgvector extension not available", error=str(e))
                raise RuntimeError("pgvector extension is required but not installed. Set VECTOR_STORE=opensearch to use OpenSearch instead.") from e

    async def register_data(
        self,
        project_id: str,
        data_key: str,
        data: Any,
        format_hint: Optional[str] = None,
    ):
        """
        Register new project data for matching (supports any format).

        Data is automatically parsed into Nodes for granular matching:
        - JSON/YAML: Each object in arrays becomes a node
        - Markdown: Headings, paragraphs, code blocks become nodes
        - CSV: Each row becomes a node
        - Plain text: Sentences or paragraphs become nodes

        Args:
            project_id: Project identifier
            data_key: Data identifier (e.g., "tech_stack", "event_model")
            data: The actual data in any format (dict, YAML string, text, etc.)
            format_hint: Optional format hint ("json", "yaml", "markdown", "text")
        """
        # Parse data into nodes
        parse_result = self.node_converter.parse(data, format_hint)

        if not parse_result.success:
            logger.warning(
                "Failed to parse data",
                project_id=project_id,
                data_key=data_key,
                error=parse_result.error,
            )
            return

        nodes = parse_result.nodes
        if not nodes:
            logger.warning("No nodes extracted from data", project_id=project_id, data_key=data_key)
            return

        logger.debug(
            "Parsed data into nodes",
            project_id=project_id,
            data_key=data_key,
            node_count=len(nodes),
            format=parse_result.format_name,
        )

        # Store original data for context
        data_original = data if isinstance(data, str) else json.dumps(data)

        # OpenSearch-only mode - skip PostgreSQL entirely
        if VECTOR_STORE == "opensearch":
            if not self.hybrid_search:
                logger.error("OpenSearch-only mode requires hybrid_search to be enabled")
                return

            for node in nodes:
                # Generate node key (combine data_key with node path)
                node_key = f"{data_key}.{node.path}" if node.path else data_key

                # Get embedding text from node
                embedding_text = node.get_text_content()

                # Generate embedding
                embedding = self.model.encode(embedding_text)

                node_data = node.content if isinstance(node.content, dict) else {"value": node.content}

                try:
                    await self.hybrid_search.index_document(
                        project_id=project_id,
                        data_key=node_key,
                        content=embedding_text,
                        metadata=json.dumps(node.metadata),
                        vector=embedding.tolist(),
                        data_format=parse_result.format_name,
                        is_structured=node.node_type.value in ["object", "row"],
                        data=node_data,
                        data_original=data_original,
                        node_path=node.path,
                        node_type=node.node_type.value,
                    )
                except Exception as e:
                    logger.warning("Failed to index node in OpenSearch", error=str(e))

            logger.info(
                "Registered data (OpenSearch-only)",
                project_id=project_id,
                data_key=data_key,
                node_count=len(nodes),
            )
            return

        # pgvector mode - store in PostgreSQL
        async with self.db.session() as session:
            for node in nodes:
                # Generate node key (combine data_key with node path)
                node_key = f"{data_key}.{node.path}" if node.path else data_key

                # Get embedding text from node
                embedding_text = node.get_text_content()

                # Generate embedding
                embedding = self.model.encode(embedding_text)

                # Check if embedding exists
                result = await session.execute(
                    select(Embedding)
                    .where(Embedding.project_id == project_id)
                    .where(Embedding.node_key == node_key)
                )
                existing = result.scalar_one_or_none()

                if existing:
                    # Update existing embedding
                    existing.data_key = data_key
                    existing.node_path = node.path
                    existing.node_type = node.node_type.value
                    existing.description = embedding_text
                    existing.data = node.content if isinstance(node.content, dict) else {"value": node.content}
                    existing.data_original = data_original
                    existing.data_format = parse_result.format_name
                    existing.embedding = embedding.tolist()
                    existing.updated_at = datetime.utcnow()
                else:
                    # Create new embedding
                    new_embedding = Embedding(
                        project_id=project_id,
                        data_key=data_key,
                        node_key=node_key,
                        node_path=node.path,
                        node_type=node.node_type.value,
                        description=embedding_text,
                        data=node.content if isinstance(node.content, dict) else {"value": node.content},
                        data_original=data_original,
                        data_format=parse_result.format_name,
                        embedding=embedding.tolist(),
                    )
                    session.add(new_embedding)

                # Also index into OpenSearch if hybrid search is enabled
                if self.hybrid_search:
                    try:
                        await self.hybrid_search.index_document(
                            project_id=project_id,
                            data_key=node_key,
                            content=embedding_text,
                            metadata=json.dumps(node.metadata),
                            vector=embedding.tolist(),
                            data_format=parse_result.format_name,
                            is_structured=node.node_type.value in ["object", "row"],
                        )
                    except Exception as e:
                        logger.warning("Failed to index node in OpenSearch", error=str(e))

        logger.info(
            "Registered data",
            project_id=project_id,
            data_key=data_key,
            node_count=len(nodes),
        )

    async def match_agent_needs(
        self, project_id: str, needs: List[str]
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        Match agent semantic needs to available data.

        Uses hybrid search (BM25 + kNN) if enabled, otherwise uses pgvector similarity search.
        In OpenSearch-only mode, all data is retrieved from OpenSearch.

        Args:
            project_id: Project identifier
            needs: List of semantic needs (natural language)

        Returns:
            Dict mapping needs to matched data sources:
            {
                "need description": [
                    {"data_key": "...", "similarity": 0.85, "data": {...}},
                    ...
                ]
            }
        """
        matches = {}

        for need in needs:
            logger.debug("Matching need", need=need, project_id=project_id)

            # Use hybrid search if enabled (required for OpenSearch-only mode)
            if self.hybrid_search:
                try:
                    results = await self.hybrid_search.hybrid_search(
                        project_id=project_id,
                        query=need,
                        top_k=self.max_matches * 2,
                    )

                    candidates = []
                    for result in results:
                        # In OpenSearch-only mode, get data directly from OpenSearch
                        if VECTOR_STORE == "opensearch":
                            # Fetch full document from OpenSearch
                            doc_id = f"{project_id}::{result['data_key']}"
                            try:
                                doc = self.hybrid_search.client.get(
                                    index=self.hybrid_search.index_name,
                                    id=doc_id
                                )
                                source = doc["_source"]
                                candidates.append({
                                    "data_key": result["data_key"],
                                    "similarity": float(result["similarity"]),
                                    "data": source.get("data", {}),
                                    "description": source.get("description", source.get("content", "")),
                                })
                            except Exception as e:
                                logger.warning(f"Failed to fetch document {doc_id} from OpenSearch", error=str(e))
                        else:
                            # Fetch full data from PostgreSQL database
                            async with self.db.session() as session:
                                db_result = await session.execute(
                                    select(Embedding)
                                    .where(Embedding.project_id == project_id)
                                    .where(Embedding.node_key == result["data_key"])
                                )
                                embedding_row = db_result.scalar_one_or_none()

                                if embedding_row:
                                    candidates.append({
                                        "data_key": result["data_key"],
                                        "similarity": float(result["similarity"]),
                                        "data": embedding_row.data,
                                        "description": embedding_row.description,
                                    })

                    candidates.sort(key=lambda x: x["similarity"], reverse=True)
                    matches[need] = candidates[: self.max_matches]

                    logger.debug(
                        "Hybrid search matches",
                        need=need,
                        count=len(matches[need]),
                    )
                    continue

                except Exception as e:
                    logger.warning("Hybrid search error, falling back to vector search", error=str(e))
                    if VECTOR_STORE == "opensearch":
                        # Can't fall back if in OpenSearch-only mode
                        matches[need] = []
                        continue
                    self.hybrid_search = None

            # Vector-only search using pgvector (not available in OpenSearch-only mode)
            if VECTOR_STORE == "opensearch":
                logger.warning("OpenSearch-only mode but hybrid_search not available")
                matches[need] = []
                continue

            need_embedding = self.model.encode(need)

            async with self.db.session() as session:
                # pgvector cosine distance query
                # cosine_distance returns distance (0 = identical, 2 = opposite)
                # similarity = 1 - distance (for normalized vectors)
                result = await session.execute(
                    select(
                        Embedding,
                        (1 - Embedding.embedding.cosine_distance(need_embedding.tolist())).label("similarity"),
                    )
                    .where(Embedding.project_id == project_id)
                    .order_by(Embedding.embedding.cosine_distance(need_embedding.tolist()))
                    .limit(self.max_matches * 2)
                )

                candidates = []
                for row in result:
                    embedding_obj = row[0]
                    similarity = float(row[1])

                    if similarity >= self.threshold:
                        candidates.append({
                            "data_key": embedding_obj.node_key,
                            "similarity": similarity,
                            "data": embedding_obj.data,
                            "description": embedding_obj.description,
                        })

                # Sort and limit
                candidates.sort(key=lambda x: x["similarity"], reverse=True)
                matches[need] = candidates[: self.max_matches]

                if matches[need]:
                    logger.debug(
                        "Vector search matches",
                        need=need,
                        count=len(matches[need]),
                        top_similarity=matches[need][0]["similarity"] if matches[need] else 0,
                    )
                else:
                    logger.debug(
                        "No matches found",
                        need=need,
                        threshold=self.threshold,
                    )

        return matches

    async def get_registered_data(self, project_id: str) -> List[str]:
        """Get all registered data keys for a project (unique data_key values)."""
        if VECTOR_STORE == "opensearch" and self.hybrid_search:
            # Get data keys from OpenSearch
            try:
                query = {
                    "size": 0,
                    "query": {"term": {"project_id": project_id}},
                    "aggs": {
                        "data_keys": {
                            "terms": {"field": "data_key", "size": 1000}
                        }
                    }
                }
                result = self.hybrid_search.client.search(
                    index=self.hybrid_search.index_name,
                    body=query
                )
                buckets = result.get("aggregations", {}).get("data_keys", {}).get("buckets", [])
                # Extract base data keys (without node paths)
                data_keys = set()
                for bucket in buckets:
                    key = bucket["key"]
                    # Get base key (before first dot if it's a node path)
                    base_key = key.split(".")[0] if "." in key else key
                    data_keys.add(base_key)
                return sorted(list(data_keys))
            except Exception as e:
                logger.warning(f"Failed to get data keys from OpenSearch: {e}")
                return []

        async with self.db.session() as session:
            result = await session.execute(
                select(Embedding.data_key)
                .where(Embedding.project_id == project_id)
                .distinct()
            )
            return sorted([row[0] for row in result])

    async def clear_project(self, project_id: str) -> int:
        """
        Remove all data for a project.

        Args:
            project_id: Project identifier

        Returns:
            Number of deleted entries
        """
        deleted_count = 0

        # Clear from OpenSearch if available
        if self.hybrid_search:
            try:
                result = self.hybrid_search.client.delete_by_query(
                    index=self.hybrid_search.index_name,
                    body={"query": {"term": {"project_id": project_id}}}
                )
                deleted_count = result.get("deleted", 0)
                logger.info(f"Cleared {deleted_count} documents from OpenSearch for project {project_id}")
            except Exception as e:
                logger.warning(f"Failed to clear OpenSearch data: {e}")

        # Also clear from PostgreSQL if not in OpenSearch-only mode
        if VECTOR_STORE != "opensearch":
            async with self.db.session() as session:
                result = await session.execute(
                    delete(Embedding).where(Embedding.project_id == project_id)
                )
                pg_deleted = result.rowcount
                if pg_deleted > deleted_count:
                    deleted_count = pg_deleted

        logger.info(
            "Cleared project embeddings",
            project_id=project_id,
            deleted_count=deleted_count,
        )

        return deleted_count

    async def get_embedding_count(self, project_id: str) -> int:
        """Get total number of embeddings for a project."""
        if VECTOR_STORE == "opensearch" and self.hybrid_search:
            try:
                result = self.hybrid_search.client.count(
                    index=self.hybrid_search.index_name,
                    body={"query": {"term": {"project_id": project_id}}}
                )
                return result.get("count", 0)
            except Exception as e:
                logger.warning(f"Failed to get count from OpenSearch: {e}")
                return 0

        async with self.db.session() as session:
            result = await session.execute(
                select(func.count(Embedding.id))
                .where(Embedding.project_id == project_id)
            )
            return result.scalar() or 0

    def _auto_describe(self, data_key: str, data: Dict[str, Any]) -> str:
        """Auto-generate natural language description from data structure."""
        return data_key

    def _flatten_dict(self, d: Dict[str, Any], prefix: str = "") -> Dict[str, Any]:
        """Flatten nested dict to extract field paths."""
        items = {}

        for key, value in d.items():
            new_key = f"{prefix}.{key}" if prefix else key

            if isinstance(value, dict) and value:
                items.update(self._flatten_dict(value, new_key))
            elif isinstance(value, list) and value and isinstance(value[0], dict):
                items[f"{new_key}[*]"] = "array"
            else:
                items[new_key] = value

        return items
