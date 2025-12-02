"""Semantic data matching using embeddings for agent context discovery"""

import os
import numpy as np
import json
from typing import Dict, List, Any, Optional
from sentence_transformers import SentenceTransformer
from redis.asyncio import Redis
from redis.commands.search.field import VectorField, TextField, TagField
from redis.commands.search.index_definition import IndexDefinition, IndexType
from redis.commands.search.query import Query

from .node_converter import NodeConverter
from .embedding_cache import EmbeddingCache


class SemanticDataMatcher:
    """
    Matches agent semantic needs to available project data using embeddings.

    Uses Redis Stack with RediSearch for persistent vector storage and similarity search.

    Key features:
    - Redis-backed persistent storage (survives restarts)
    - Native vector similarity search (no in-memory registry)
    - Auto-generates descriptions from data structure
    - Fast embedding-based similarity matching
    - Handles schema evolution gracefully
    """

    INDEX_NAME = "context_embeddings_idx"
    KEY_PREFIX = "embedding:"

    def __init__(
        self,
        redis: Redis,
        model_name: str = "all-MiniLM-L6-v2",
        similarity_threshold: float = 0.35,
        max_matches: int = 10,
    ):
        """
        Initialize semantic matcher.

        Args:
            redis: Redis connection
            model_name: SentenceTransformer model (~80MB)
            similarity_threshold: Minimum similarity to match (0-1)
            max_matches: Maximum matches to return per need
        """
        print(f"[SemanticMatcher] Loading model: {model_name}")
        self.redis = redis
        self.model_name = model_name  # Store for health checks
        self.model = SentenceTransformer(model_name)
        self.threshold = similarity_threshold
        self.max_matches = max_matches
        self.embedding_dim = 384  # all-MiniLM-L6-v2 embedding dimension
        self.node_converter = NodeConverter()

        # Initialize embedding cache
        cache_ttl = int(os.getenv("EMBEDDING_CACHE_TTL", "3600"))
        self.embedding_cache = EmbeddingCache(redis, ttl=cache_ttl)
        print(f"[SemanticMatcher] Embedding cache enabled (TTL: {cache_ttl}s)")

        # Initialize hybrid search if enabled
        self.hybrid_search = None
        if os.getenv("HYBRID_SEARCH_ENABLED", "false").lower() == "true":
            try:
                from .hybrid_search import RankFusionSearch

                keyword_threshold = float(os.getenv("KEYWORD_THRESHOLD", "5.0"))
                vector_threshold = float(os.getenv("VECTOR_THRESHOLD", "0.7"))

                self.hybrid_search = RankFusionSearch(
                    semantic_matcher=self,
                    keyword_threshold=keyword_threshold,
                    vector_threshold=vector_threshold
                )
                print(f"[SemanticMatcher] ✓ Hybrid search enabled")
            except Exception as e:
                print(f"[SemanticMatcher] ⚠ Failed to initialize hybrid search: {e}")
                self.hybrid_search = None

        print(
            f"[SemanticMatcher] ✓ Model loaded (threshold: {similarity_threshold}, max_matches: {max_matches})"
        )

    async def initialize_index(self):
        """Create RediSearch index for vector similarity search"""
        try:
            # Try to get existing index info
            await self.redis.ft(self.INDEX_NAME).info()
            print(f"[SemanticMatcher] ✓ Index '{self.INDEX_NAME}' already exists")
        except Exception as e:
            # Index doesn't exist or error occurred, create it
            print(f"[SemanticMatcher] Index not found (reason: {type(e).__name__}), creating '{self.INDEX_NAME}'...")

            schema = (
                VectorField(
                    "embedding",
                    "FLAT",
                    {
                        "TYPE": "FLOAT32",
                        "DIM": self.embedding_dim,
                        "DISTANCE_METRIC": "COSINE",
                    },
                ),
                TagField("project_id"),  # TAG for exact matching
                TextField("data_key"),
                TextField("description"),
                TextField("data"),  # JSON string
            )

            definition = IndexDefinition(
                prefix=[self.KEY_PREFIX],
                index_type=IndexType.HASH,
            )

            await self.redis.ft(self.INDEX_NAME).create_index(
                fields=schema,
                definition=definition,
            )
            print(f"[SemanticMatcher] ✓ Created index '{self.INDEX_NAME}'")

    async def register_data(
        self,
        project_id: str,
        data_key: str,
        data: Any,
        format_hint: Optional[str] = None
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
            print(f"[SemanticMatcher] ⚠ Failed to parse {project_id}:{data_key}: {parse_result.error}")
            return

        nodes = parse_result.nodes
        if not nodes:
            print(f"[SemanticMatcher] ⚠ No nodes extracted from {project_id}:{data_key}")
            return

        print(f"[SemanticMatcher] Parsed {project_id}:{data_key} into {len(nodes)} nodes [{parse_result.format_name}]")

        # Store original data for context
        data_original = data if isinstance(data, str) else json.dumps(data)

        # Index each node separately
        for node in nodes:
            # Generate node key (combine data_key with node path)
            node_key = f"{data_key}.{node.path}" if node.path else data_key

            # Get embedding text from node
            embedding_text = node.get_text_content()

            # Try cache first, then generate embedding
            embedding = await self.embedding_cache.get(embedding_text)
            if embedding is None:
                embedding = self.model.encode(embedding_text)
                await self.embedding_cache.set(embedding_text, embedding)

            # Store in Redis
            redis_key = f"{self.KEY_PREFIX}{project_id}:{node_key}"

            await self.redis.hset(
                redis_key,
                mapping={
                    "project_id": project_id,
                    "data_key": data_key,  # Original data_key
                    "node_key": node_key,  # Node-specific key
                    "node_path": node.path,  # Path within data
                    "node_type": node.node_type.value,  # Type of node
                    "description": embedding_text,  # Text used for embedding
                    "data": json.dumps(node.content),  # Just this node's content
                    "data_original": data_original,  # Full original for context
                    "data_format": parse_result.format_name,
                    "embedding": embedding.astype(np.float32).tobytes(),
                },
            )

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
                        is_structured=node.node_type in [node.node_type.OBJECT, node.node_type.ROW]
                    )
                except Exception as e:
                    print(f"[SemanticMatcher] ⚠ Failed to index node in OpenSearch: {e}")

        print(f"[SemanticMatcher] ✓ Registered: {project_id}:{data_key} ({len(nodes)} nodes)")
        if nodes and len(nodes) <= 5:
            for node in nodes:
                print(f"[SemanticMatcher]   - {node.path} ({node.node_type.value})")

    def _escape_tag_value(self, value: str) -> str:
        """
        Escape special characters for RediSearch TAG queries.

        TAG queries require escaping these characters: , . < > { } [ ] " ' : ; ! @ # $ % ^ & * ( ) - + = ~ |

        Args:
            value: Tag value to escape

        Returns:
            Escaped value
        """
        special_chars = r',.<>{}[]"\':;!@#$%^&*()-+=~|'
        for char in special_chars:
            value = value.replace(char, f"\\{char}")
        return value

    async def match_agent_needs(
        self, project_id: str, needs: List[str]
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        Match agent semantic needs to available data.

        Uses hybrid search (BM25 + kNN) if enabled, otherwise falls back to pure vector search.

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
            print(f"[SemanticMatcher] Matching need: '{need}'")

            # Use hybrid search if enabled
            if self.hybrid_search:
                try:
                    results = await self.hybrid_search.hybrid_search(
                        project_id=project_id,
                        query=need,
                        top_k=self.max_matches * 2  # Get more candidates for threshold filtering
                    )

                    # Fetch full data for all hybrid search results
                    # (RRF scores are not comparable to cosine similarity thresholds)
                    candidates = []
                    for result in results:
                        similarity = result["similarity"]

                        # Always include hybrid search results (already ranked by RRF)
                        if True:
                            # Fetch full data from Redis
                            redis_key = f"{self.KEY_PREFIX}{project_id}:{result['data_key']}"
                            data_info = await self.redis.hgetall(redis_key)

                            if data_info:
                                def decode(val):
                                    return val.decode() if isinstance(val, bytes) else val

                                data_str = decode(data_info.get(b"data") or data_info.get("data", "{}"))
                                description = decode(data_info.get(b"description") or data_info.get("description", ""))

                                candidates.append({
                                    "data_key": result["data_key"],
                                    "similarity": float(similarity),
                                    "data": json.loads(data_str),
                                    "description": description,
                                })

                    # Sort by similarity (highest first) and limit
                    candidates.sort(key=lambda x: x["similarity"], reverse=True)
                    top_matches = candidates[: self.max_matches]
                    matches[need] = top_matches

                    # Log results
                    if top_matches:
                        print(f"[SemanticMatcher]   Found {len(top_matches)} matches (hybrid):")
                        for match in top_matches:
                            print(
                                f"[SemanticMatcher]     - {match['data_key']} (similarity: {match['similarity']:.3f})"
                            )
                    else:
                        print(
                            f"[SemanticMatcher]   ⚠ No matches found (threshold: {self.threshold})"
                        )

                except Exception as e:
                    print(f"[SemanticMatcher]   ⚠ Hybrid search error: {e}")
                    print(f"[SemanticMatcher]   Falling back to vector-only search")
                    # Fall through to vector-only search below
                    self.hybrid_search = None  # Disable for future queries to avoid repeated errors

            # Fall back to vector-only search (original implementation)
            if not self.hybrid_search:
                # Try cache first, then embed agent need (5-10ms without cache, <1ms with cache)
                need_embedding = await self.embedding_cache.get(need)
                if need_embedding is None:
                    need_embedding = self.model.encode(need)
                    await self.embedding_cache.set(need, need_embedding)

                # Use RediSearch KNN query for vector similarity search
                query_embedding = need_embedding.astype(np.float32).tobytes()

                # Escape special characters in project_id for TAG query
                escaped_project_id = self._escape_tag_value(project_id)

                # KNN query with project filter (TAG field uses curly braces)
                q = (
                    Query(f"@project_id:{{{escaped_project_id}}} => [KNN {self.max_matches * 2} @embedding $vec AS score]")
                    .sort_by("score")
                    .return_fields("project_id", "data_key", "description", "data", "score")
                    .dialect(2)
                )

                try:
                    results = await self.redis.ft(self.INDEX_NAME).search(
                        q,
                        query_params={"vec": query_embedding}
                    )

                    # Process results
                    candidates = []
                    for doc in results.docs:
                        # RediSearch returns distance, convert to similarity
                        # COSINE distance is 1 - similarity, so similarity = 1 - distance
                        similarity = 1 - float(doc.score)

                        # Include if above threshold
                        if similarity >= self.threshold:
                            candidates.append({
                                "data_key": doc.data_key,
                                "similarity": float(similarity),
                                "data": json.loads(doc.data),
                                "description": doc.description,
                            })

                    # Sort by similarity (highest first) and limit
                    candidates.sort(key=lambda x: x["similarity"], reverse=True)
                    top_matches = candidates[: self.max_matches]
                    matches[need] = top_matches

                    # Log results
                    if top_matches:
                        print(f"[SemanticMatcher]   Found {len(top_matches)} matches (vector-only):")
                        for match in top_matches:
                            print(
                                f"[SemanticMatcher]     - {match['data_key']} (similarity: {match['similarity']:.3f})"
                            )
                    else:
                        print(
                            f"[SemanticMatcher]   ⚠ No matches found (threshold: {self.threshold})"
                        )

                except Exception as e:
                    print(f"[SemanticMatcher]   ⚠ Search error: {e}")
                    matches[need] = []

        return matches

    def _auto_describe(self, data_key: str, data: Dict[str, Any]) -> str:
        """
        Auto-generate natural language description from data structure.

        Args:
            data_key: Data identifier
            data: Data dict

        Returns:
            Description like: "tech_stack with fields: backend, frontend, database"
        """
        # Just return the data key without field listing
        # (field listing was confusing in the UI)
        return data_key

    def _flatten_dict(self, d: Dict[str, Any], prefix: str = "") -> Dict[str, Any]:
        """
        Flatten nested dict to extract field paths.

        Example:
            {"tech": {"backend": "Python"}} -> {"tech.backend": "Python"}
        """
        items = {}

        for key, value in d.items():
            new_key = f"{prefix}.{key}" if prefix else key

            if isinstance(value, dict) and value:
                items.update(self._flatten_dict(value, new_key))
            elif isinstance(value, list) and value and isinstance(value[0], dict):
                # For arrays of objects, describe structure
                items[f"{new_key}[*]"] = "array"
            else:
                items[new_key] = value

        return items

    async def get_registered_data(self, project_id: str) -> List[str]:
        """Get all registered data keys for a project (unique data_key values)"""
        # Search for all documents with this project_id
        escaped_project_id = self._escape_tag_value(project_id)
        q = Query(f"@project_id:{{{escaped_project_id}}}").return_fields("data_key").paging(0, 1000)

        try:
            results = await self.redis.ft(self.INDEX_NAME).search(q)
            # Return unique data_key values (deduplicate since we have multiple nodes per data_key)
            data_keys = list(set(doc.data_key for doc in results.docs))
            return sorted(data_keys)
        except Exception as e:
            # Log error but return empty list (index may not exist yet)
            print(f"[SemanticMatcher] Warning: Failed to list keys for project {project_id}: {type(e).__name__}: {e}")
            return []

    async def clear_project(self, project_id: str):
        """Remove all data for a project"""
        # Get all keys for this project
        keys_pattern = f"{self.KEY_PREFIX}{project_id}:*"
        cursor = 0
        deleted_count = 0

        while True:
            cursor, keys = await self.redis.scan(cursor, match=keys_pattern, count=100)
            if keys:
                await self.redis.delete(*keys)
                deleted_count += len(keys)

            if cursor == 0:
                break

        print(
            f"[SemanticMatcher] Cleared {deleted_count} entries for project {project_id}"
        )
