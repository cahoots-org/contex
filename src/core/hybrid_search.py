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
    Hybrid search combining keyword (BM25) and semantic (vector) search.

    Returns documents that match EITHER keyword OR semantic search above their
    respective thresholds. This is simpler and more intuitive than score fusion.
    """

    def __init__(
        self,
        semantic_matcher: SemanticDataMatcher,
        opensearch_url: Optional[str] = None,
        keyword_threshold: float = 5.0,  # BM25 score threshold
        vector_threshold: float = 0.7,   # Cosine similarity threshold
    ):
        """
        Initialize hybrid search.

        Args:
            semantic_matcher: Semantic matcher for vector embeddings
            opensearch_url: OpenSearch connection URL
            keyword_threshold: Minimum BM25 score for keyword matches
            vector_threshold: Minimum cosine similarity for vector matches
        """
        self.semantic_matcher = semantic_matcher
        self.keyword_threshold = keyword_threshold
        self.vector_threshold = vector_threshold

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

        print(f"[HybridSearch] Connected to OpenSearch at {os_url}")
        print(f"[HybridSearch] Keyword threshold: {keyword_threshold}, Vector threshold: {vector_threshold}")

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
        top_k: int = 10,
        keyword_threshold: Optional[float] = None,
        vector_threshold: Optional[float] = None
    ) -> List[Dict[str, Any]]:
        """
        Perform hybrid search using union of keyword and vector results.

        Returns documents that match EITHER keyword search OR semantic search.

        Args:
            project_id: Project to search within
            query: Search query
            top_k: Number of results to return
            keyword_threshold: Minimum BM25 score for keyword matches (uses class default if None)
            vector_threshold: Minimum cosine similarity for vector matches (uses class default if None)

        Returns:
            Union of keyword and vector matches, sorted by best score
        """
        # Use class defaults if not specified
        if keyword_threshold is None:
            keyword_threshold = self.keyword_threshold
        if vector_threshold is None:
            vector_threshold = self.vector_threshold

        # Generate query embedding
        query_vector = self.semantic_matcher.model.encode(query).tolist()

        # Execute both searches
        lexical_results = self._lexical_search_with_scores(project_id, query, top_k * 2)
        vector_results = self._vector_search_with_scores(project_id, query_vector, top_k * 2)

        # Union: take documents that pass EITHER threshold
        results_map = {}

        # Add keyword matches
        for doc_id, bm25_score in lexical_results:
            if bm25_score >= keyword_threshold:
                results_map[doc_id] = {
                    "keyword_score": bm25_score,
                    "vector_score": 0.0,
                    "match_type": "keyword"
                }

        # Add vector matches
        for doc_id, vector_score in vector_results:
            if vector_score >= vector_threshold:
                if doc_id in results_map:
                    # Document matched both - update with vector score
                    results_map[doc_id]["vector_score"] = vector_score
                    results_map[doc_id]["match_type"] = "both"
                else:
                    results_map[doc_id] = {
                        "keyword_score": 0.0,
                        "vector_score": vector_score,
                        "match_type": "vector"
                    }

        # Sort by best score from either method
        sorted_docs = sorted(
            results_map.items(),
            key=lambda x: max(x[1]["keyword_score"] / 10.0, x[1]["vector_score"]),  # Normalize BM25 to ~0-1
            reverse=True
        )[:top_k]

        # Retrieve full documents
        results = []
        for doc_id, scores in sorted_docs:
            try:
                doc = self.client.get(index=self.index_name, id=doc_id)
                source = doc["_source"]

                # Use the better of the two scores, normalized to 0-1
                # BM25 scores: 0-3 = weak, 3-6 = good, 6+ = excellent
                # Map to: 0.5-0.7 = weak, 0.7-0.9 = good, 0.9-1.0 = excellent
                normalized_keyword = min(0.5 + (scores["keyword_score"] / 12.0), 1.0)
                normalized_vector = scores["vector_score"]
                final_score = max(normalized_keyword, normalized_vector)

                print(f"[HybridSearch]   {source['data_key']}: {scores['match_type']} - keyword={scores['keyword_score']:.2f}, vector={scores['vector_score']:.3f}, final={final_score:.3f}")

                results.append({
                    "data_key": source["data_key"],
                    "keyword_score": float(scores["keyword_score"]),
                    "vector_score": float(scores["vector_score"]),
                    "match_type": scores["match_type"],
                    "similarity": float(final_score),
                })
            except Exception as e:
                print(f"[HybridSearch] Error retrieving doc {doc_id}: {e}")
                continue

        print(f"[HybridSearch] Found {len(results)} results for: {query}")
        return results

    def _lexical_search_with_scores(
        self,
        project_id: str,
        query: str,
        size: int
    ) -> List[Tuple[str, float]]:
        """
        Perform lexical (BM25) search with scores.

        Args:
            project_id: Project filter
            query: Search query
            size: Number of results

        Returns:
            List of (doc_id, bm25_score) tuples
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

        # Return (doc_id, bm25_score) tuples
        results = [
            (hit["_id"], hit["_score"])
            for hit in response["hits"]["hits"]
        ]

        print(f"[HybridSearch] Keyword search: {len(results)} results")
        return results

    def _vector_search_with_scores(
        self,
        project_id: str,
        query_vector: List[float],
        size: int
    ) -> List[Tuple[str, float]]:
        """
        Perform vector similarity search with cosine scores.

        Args:
            project_id: Project filter
            query_vector: Query embedding
            size: Number of results

        Returns:
            List of (doc_id, cosine_similarity) tuples
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

        # OpenSearch kNN returns scores, need to convert to similarity
        # The score is already cosine similarity (1 = identical, 0 = orthogonal)
        results = [
            (hit["_id"], hit["_score"])
            for hit in response["hits"]["hits"]
        ]

        print(f"[HybridSearch] Vector search: {len(results)} results")
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
