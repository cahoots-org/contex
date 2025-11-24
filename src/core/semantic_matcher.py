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

from .data_normalizer import DataNormalizer


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
        similarity_threshold: float = 0.5,
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
        self.model = SentenceTransformer(model_name)
        self.threshold = similarity_threshold
        self.max_matches = max_matches
        self.embedding_dim = 384  # all-MiniLM-L6-v2 embedding dimension
        self.normalizer = DataNormalizer()

        # Initialize hybrid search if enabled
        self.hybrid_search = None
        if os.getenv("HYBRID_SEARCH_ENABLED", "false").lower() == "true":
            try:
                from .hybrid_search import RankFusionSearch

                rrf_k = int(os.getenv("RRF_K", "60"))
                vector_boost = float(os.getenv("VECTOR_BOOST", "1.0"))

                self.hybrid_search = RankFusionSearch(
                    semantic_matcher=self,
                    rrf_k=rrf_k,
                    vector_boost=vector_boost
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

        Args:
            project_id: Project identifier
            data_key: Data identifier (e.g., "tech_stack", "event_model")
            data: The actual data in any format (dict, YAML string, text, etc.)
            format_hint: Optional format hint ("json", "yaml", "toml", "text")
        """
        # Normalize data from any format
        normalized_data, detected_format, is_structured = self.normalizer.normalize(
            data, format_hint
        )

        # Generate embedding text based on data type
        embedding_text = self.normalizer.generate_embedding_text(
            data_key, normalized_data, is_structured
        )

        # Generate embedding (5-10ms)
        embedding = self.model.encode(embedding_text)

        # Store in Redis with metadata
        redis_key = f"{self.KEY_PREFIX}{project_id}:{data_key}"

        # Store original data as string if not already
        data_original = data if isinstance(data, str) else json.dumps(data)

        await self.redis.hset(
            redis_key,
            mapping={
                "project_id": project_id,
                "data_key": data_key,
                "description": embedding_text,  # What was embedded
                "data": json.dumps(normalized_data),  # Normalized form
                "data_original": data_original,  # Original input
                "data_format": detected_format,  # Detected format
                "is_structured": "true" if is_structured else "false",
                "embedding": embedding.astype(np.float32).tobytes(),
            },
        )

        # Also index into OpenSearch if hybrid search is enabled
        if self.hybrid_search:
            try:
                await self.hybrid_search.index_document(
                    project_id=project_id,
                    data_key=data_key,
                    content=embedding_text,
                    metadata=json.dumps(normalized_data),
                    vector=embedding.tolist(),
                    data_format=detected_format,
                    is_structured=is_structured
                )
                print(f"[SemanticMatcher] ✓ Indexed in OpenSearch: {project_id}:{data_key}")
            except Exception as e:
                print(f"[SemanticMatcher] ⚠ Failed to index in OpenSearch: {e}")

        format_label = f"{detected_format} ({'structured' if is_structured else 'unstructured'})"
        print(f"[SemanticMatcher] Registered: {project_id}:{data_key} [{format_label}]")
        print(f"[SemanticMatcher]   Embedding text: {embedding_text[:100]}...")

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
                # Embed agent need (5-10ms)
                need_embedding = self.model.encode(need)

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
        """Get all registered data keys for a project"""
        # Search for all documents with this project_id
        escaped_project_id = self._escape_tag_value(project_id)
        q = Query(f"@project_id:{{{escaped_project_id}}}").return_fields("data_key").paging(0, 1000)

        try:
            results = await self.redis.ft(self.INDEX_NAME).search(q)
            return [doc.data_key for doc in results.docs]
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
