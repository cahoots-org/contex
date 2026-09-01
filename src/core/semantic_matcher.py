"""Semantic data matching using embeddings for agent context discovery"""

import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

import numpy as np
from sentence_transformers import SentenceTransformer
from sqlalchemy import delete, func, select, text

from src.core.database import DatabaseManager
from src.core.db_models import Embedding
from src.core.hybrid_search_service import HybridSearchService
from src.core.lexical_search import PgFtsLexical
from src.core.logging import get_logger
from src.core.node_converter import NodeConverter
from src.core.vector_search import PgVectorSearch

logger = get_logger(__name__)


class SemanticDataMatcher:
    """
    Matches agent semantic needs to available project data using embeddings.

    Uses PostgreSQL with pgvector for persistent vector storage and similarity search.

    Key features:
    - PostgreSQL-backed persistent storage (survives restarts)
    - Native vector similarity search via pgvector
    - Optional hybrid search (pgvector + Postgres FTS fused with RRF)
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

        # Initialize hybrid search if enabled: pgvector (vector) + Postgres FTS
        # (lexical) fused with backend-agnostic RRF. Single database, no extra
        # stateful services.
        self.hybrid_search = None
        if os.getenv("HYBRID_SEARCH_ENABLED", "false").lower() == "true":
            try:
                rrf_k = int(os.getenv("RRF_K", "60"))
                self.hybrid_search = HybridSearchService(
                    vector_search=PgVectorSearch(db, self.model),
                    lexical_search=PgFtsLexical(db),
                    k=rrf_k,
                )
                logger.info("Hybrid search enabled (pgvector + Postgres FTS, RRF)")
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
        async with self.db.session() as session:
            try:
                # Verify pgvector extension exists
                await session.execute(text("SELECT 'vector'::regtype"))
                logger.info("pgvector extension verified")
            except Exception as e:
                logger.error("pgvector extension not available", error=str(e))
                raise RuntimeError(
                    "pgvector extension is required but not installed."
                ) from e

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

        # pgvector mode - store in PostgreSQL. The lexical FTS index (search_text)
        # is a generated column maintained by Postgres, so no separate indexing.
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

        Uses hybrid search (pgvector + Postgres FTS fused with RRF) if enabled,
        otherwise uses pgvector cosine-similarity search.

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

            # Use hybrid search if enabled
            if self.hybrid_search:
                try:
                    fused = await self.hybrid_search.search(
                        project_id=project_id,
                        query=need,
                        top_k=self.max_matches * 2,
                    )

                    candidates = []
                    for node_key, fused_score in fused:
                        # Fetch full data from PostgreSQL database
                        async with self.db.session() as session:
                            db_result = await session.execute(
                                select(Embedding)
                                .where(Embedding.project_id == project_id)
                                .where(Embedding.node_key == node_key)
                            )
                            embedding_row = db_result.scalar_one_or_none()

                            if embedding_row:
                                candidates.append({
                                    "data_key": node_key,
                                    "similarity": float(fused_score),
                                    "data": embedding_row.data,
                                    "description": embedding_row.description,
                                })

                    matches[need] = candidates[: self.max_matches]

                    logger.debug(
                        "Hybrid search matches",
                        need=need,
                        count=len(matches[need]),
                    )
                    continue

                except Exception as e:
                    logger.warning("Hybrid search error, falling back to vector search", error=str(e))

            # Vector-only search using pgvector
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
        async with self.db.session() as session:
            result = await session.execute(
                delete(Embedding).where(Embedding.project_id == project_id)
            )
            deleted_count = result.rowcount

        logger.info(
            "Cleared project embeddings",
            project_id=project_id,
            deleted_count=deleted_count,
        )

        return deleted_count

    async def get_embedding_count(self, project_id: str) -> int:
        """Get total number of embeddings for a project."""
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
