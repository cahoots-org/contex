"""
Hybrid search using Reciprocal Rank Fusion (RRF) algorithm.

Combines lexical (BM25) and semantic (vector) search using rank-based fusion
rather than score normalization, providing more robust result merging.

References:
- Cormack, G. V., Clarke, C. L., & Buettcher, S. (2009).
  "Reciprocal rank fusion outperforms condorcet and individual rank learning methods."
"""

import os
from typing import List, Dict, Any, Optional, Tuple
import numpy as np
from opensearchpy import OpenSearch

from .semantic_matcher import SemanticDataMatcher


class RankFusionSearch:
    """
    Hybrid search using Reciprocal Rank Fusion (RRF) algorithm.

    RRF combines multiple ranked lists by computing reciprocal ranks,
    which is more robust than score-based fusion and doesn't require
    score normalization or weight tuning.

    Formula: RRF_score = Σ 1/(k + rank_i)
    where k is a constant (typically 60) and rank_i is the rank in list i.
    """

    def __init__(
        self,
        semantic_matcher: SemanticDataMatcher,
        opensearch_url: Optional[str] = None,
        rrf_k: int = 60,  # Standard RRF constant
        vector_boost: float = 1.0,  # Boost factor for vector results
    ):
        """
        Initialize rank fusion search.

        Args:
            semantic_matcher: Semantic matcher for vector embeddings
            opensearch_url: OpenSearch connection URL
            rrf_k: RRF constant (default: 60, standard value)
            vector_boost: Multiplier for vector rank contributions (default: 1.0)
        """
        self.semantic_matcher = semantic_matcher
        self.rrf_k = rrf_k
        self.vector_boost = vector_boost

        # Initialize OpenSearch connection
        os_url = opensearch_url or os.getenv("OPENSEARCH_URL", "http://localhost:9200")
        self.client = OpenSearch(
            hosts=[os_url],
            http_compress=True,
            use_ssl=False,
            verify_certs=False,
            ssl_assert_hostname=False,
            ssl_show_warn=False
        )
        self.index_name = "contex-hybrid-index"

        # Initialize index
        self._initialize_index()

        print(f"[RankFusionSearch] Connected to OpenSearch at {os_url}")
        print(f"[RankFusionSearch] RRF constant k={rrf_k}, vector_boost={vector_boost}")

    def _initialize_index(self) -> None:
        """Initialize OpenSearch index with appropriate mappings."""
        if self.client.indices.exists(index=self.index_name):
            print(f"[RankFusionSearch] Index {self.index_name} exists")
            return

        print(f"[RankFusionSearch] Creating index {self.index_name}")

        # Index configuration
        index_config = {
            "settings": {
                "number_of_shards": 1,
                "number_of_replicas": 0,
                "index": {
                    "knn": True,
                    "knn.algo_param.ef_search": 100,  # HNSW parameter
                }
            },
            "mappings": {
                "properties": {
                    "project_id": {"type": "keyword"},
                    "data_key": {"type": "keyword"},
                    "content": {
                        "type": "text",
                        "analyzer": "standard",
                        "fields": {
                            "keyword": {"type": "keyword"}
                        }
                    },
                    "metadata": {"type": "text"},
                    "format": {"type": "keyword"},
                    "is_structured": {"type": "boolean"},
                    "vector": {
                        "type": "knn_vector",
                        "dimension": 384,
                        "method": {
                            "name": "hnsw",
                            "space_type": "cosinesimil",
                            "engine": "nmslib",
                            "parameters": {
                                "ef_construction": 128,
                                "m": 24
                            }
                        }
                    }
                }
            }
        }

        self.client.indices.create(index=self.index_name, body=index_config)
        print(f"[RankFusionSearch] Index created successfully")

    async def index_document(
        self,
        project_id: str,
        data_key: str,
        content: str,
        metadata: str,
        vector: List[float],
        data_format: str,
        is_structured: bool
    ) -> None:
        """
        Index a document for hybrid search.

        Args:
            project_id: Project identifier
            data_key: Unique data key
            content: Searchable text content
            metadata: Additional metadata
            vector: Embedding vector
            data_format: Data format type
            is_structured: Whether data is structured
        """
        doc_id = f"{project_id}::{data_key}"

        document = {
            "project_id": project_id,
            "data_key": data_key,
            "content": content,
            "metadata": metadata,
            "vector": vector,
            "format": data_format,
            "is_structured": is_structured
        }

        self.client.index(
            index=self.index_name,
            id=doc_id,
            body=document,
            refresh=True
        )
        print(f"[RankFusionSearch] Indexed document: {doc_id}")

    async def hybrid_search(
        self,
        project_id: str,
        query: str,
        top_k: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Perform hybrid search using RRF algorithm.

        Args:
            project_id: Project to search within
            query: Search query
            top_k: Number of results to return

        Returns:
            Ranked list of results with RRF scores
        """
        # Generate query embedding
        query_vector = self.semantic_matcher.model.encode(query).tolist()

        # Execute both searches in parallel
        lexical_results = self._lexical_search(project_id, query, top_k * 2)
        vector_results = self._vector_search(project_id, query_vector, top_k * 2)

        # Apply Reciprocal Rank Fusion
        fused_results = self._reciprocal_rank_fusion(
            lexical_results,
            vector_results,
            top_k
        )

        print(f"[RankFusionSearch] Fused {len(fused_results)} results for: {query}")
        return fused_results

    def _lexical_search(
        self,
        project_id: str,
        query: str,
        size: int
    ) -> List[Tuple[str, int]]:
        """
        Perform lexical (BM25) search.

        Args:
            project_id: Project filter
            query: Search query
            size: Number of results

        Returns:
            List of (doc_id, rank) tuples
        """
        query_body = {
            "query": {
                "bool": {
                    "must": [
                        {"term": {"project_id": project_id}},
                        {
                            "multi_match": {
                                "query": query,
                                "fields": ["content^3", "metadata"],
                                "type": "best_fields",
                                "operator": "or"
                            }
                        }
                    ]
                }
            },
            "size": size
        }

        response = self.client.search(index=self.index_name, body=query_body)

        # Return (doc_id, rank) tuples
        results = [
            (hit["_id"], idx)
            for idx, hit in enumerate(response["hits"]["hits"])
        ]

        print(f"[RankFusionSearch] Lexical search: {len(results)} results")
        return results

    def _vector_search(
        self,
        project_id: str,
        query_vector: List[float],
        size: int
    ) -> List[Tuple[str, int]]:
        """
        Perform vector similarity search.

        Args:
            project_id: Project filter
            query_vector: Query embedding
            size: Number of results

        Returns:
            List of (doc_id, rank) tuples
        """
        query_body = {
            "size": size,
            "query": {
                "bool": {
                    "must": {
                        "knn": {
                            "vector": {
                                "vector": query_vector,
                                "k": size
                            }
                        }
                    },
                    "filter": {
                        "term": {"project_id": project_id}
                    }
                }
            }
        }

        response = self.client.search(index=self.index_name, body=query_body)

        # Return (doc_id, rank) tuples
        results = [
            (hit["_id"], idx)
            for idx, hit in enumerate(response["hits"]["hits"])
        ]

        print(f"[RankFusionSearch] Vector search: {len(results)} results")
        return results

    def _reciprocal_rank_fusion(
        self,
        lexical_ranks: List[Tuple[str, int]],
        vector_ranks: List[Tuple[str, int]],
        top_k: int
    ) -> List[Dict[str, Any]]:
        """
        Combine rankings using Reciprocal Rank Fusion algorithm.

        RRF Score = Σ 1/(k + rank_i)

        This approach is more robust than weighted score combinations
        as it doesn't require score normalization or parameter tuning.

        Args:
            lexical_ranks: List of (doc_id, rank) from lexical search
            vector_ranks: List of (doc_id, rank) from vector search
            top_k: Number of top results to return

        Returns:
            Fused and ranked results
        """
        # Build rank maps
        lexical_map = {doc_id: rank for doc_id, rank in lexical_ranks}
        vector_map = {doc_id: rank for doc_id, rank in vector_ranks}

        # Get all unique document IDs
        all_doc_ids = set(lexical_map.keys()) | set(vector_map.keys())

        # Calculate RRF scores
        rrf_scores = {}
        for doc_id in all_doc_ids:
            score = 0.0

            # Lexical contribution
            if doc_id in lexical_map:
                score += 1.0 / (self.rrf_k + lexical_map[doc_id])

            # Vector contribution (with boost)
            if doc_id in vector_map:
                score += self.vector_boost / (self.rrf_k + vector_map[doc_id])

            rrf_scores[doc_id] = score

        # Sort by RRF score (descending)
        sorted_docs = sorted(
            rrf_scores.items(),
            key=lambda x: x[1],
            reverse=True
        )[:top_k]

        # Retrieve full documents
        results = []
        for doc_id, rrf_score in sorted_docs:
            try:
                doc = self.client.get(index=self.index_name, id=doc_id)
                source = doc["_source"]

                results.append({
                    "data_key": source["data_key"],
                    "rrf_score": float(rrf_score),
                    "lexical_rank": lexical_map.get(doc_id, -1),
                    "vector_rank": vector_map.get(doc_id, -1),
                    "similarity": float(rrf_score),  # For compatibility
                })
            except Exception as e:
                print(f"[RankFusionSearch] Error retrieving doc {doc_id}: {e}")
                continue

        return results

    def get_index_stats(self) -> Dict[str, Any]:
        """
        Get statistics about the search index.

        Returns:
            Dictionary with index statistics
        """
        try:
            stats = self.client.indices.stats(index=self.index_name)
            count = self.client.count(index=self.index_name)

            return {
                "index_name": self.index_name,
                "document_count": count["count"],
                "size_bytes": stats["_all"]["total"]["store"]["size_in_bytes"],
            }
        except Exception as e:
            return {
                "error": str(e)
            }
