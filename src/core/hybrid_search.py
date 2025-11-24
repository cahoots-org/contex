"""
Hybrid search combining BM25 text search with kNN vector search.

Based on implementation from UpContent Content service.
"""

import os
from typing import List, Dict, Any, Optional
import numpy as np
from sklearn.preprocessing import StandardScaler
from opensearchpy import OpenSearch

from .semantic_matcher import SemanticDataMatcher


class HybridSearchMatcher:
    """
    Hybrid search matcher combining BM25 text search and kNN vector search.

    This class uses Elasticsearch to perform:
    - BM25: Traditional keyword-based relevance ranking
    - kNN: Vector similarity search using embeddings

    Results are normalized and combined with configurable weights.
    """

    def __init__(
        self,
        semantic_matcher: SemanticDataMatcher,
        elasticsearch_url: Optional[str] = None,
        bm25_weight: float = 0.7,
        knn_weight: float = 0.3,
    ):
        """
        Initialize hybrid search matcher.

        Args:
            semantic_matcher: Existing semantic matcher for embeddings
            elasticsearch_url: Elasticsearch connection URL
            bm25_weight: Weight for BM25 text search (default: 0.7)
            knn_weight: Weight for kNN vector search (default: 0.3)
        """
        self.semantic_matcher = semantic_matcher
        self.bm25_weight = bm25_weight
        self.knn_weight = knn_weight

        # Connect to OpenSearch
        es_url = elasticsearch_url or os.getenv("ELASTICSEARCH_URL", "http://localhost:9200")
        self.es = OpenSearch(
            hosts=[es_url],
            http_compress=True,
            use_ssl=False,
            verify_certs=False,
            ssl_assert_hostname=False,
            ssl_show_warn=False
        )
        self.index_name = "contex-data"

        # Ensure index exists
        self._ensure_index_exists()

        print(f"[HybridSearch] Connected to OpenSearch at {es_url}")
        print(f"[HybridSearch] Weights: BM25={bm25_weight}, kNN={knn_weight}")

    def _ensure_index_exists(self) -> None:
        """Create Elasticsearch index if it doesn't exist."""
        if self.es.indices.exists(index=self.index_name):
            print(f"[HybridSearch] Index {self.index_name} already exists")
            return

        print(f"[HybridSearch] Creating index {self.index_name}")

        # Index configuration with both text and vector fields
        index_body = {
            "settings": {
                "number_of_shards": 1,
                "number_of_replicas": 0,
                "index": {
                    "knn": True,  # Enable kNN search
                }
            },
            "mappings": {
                "properties": {
                    # Identification
                    "project_id": {"type": "keyword"},
                    "data_key": {"type": "keyword"},

                    # Text fields for BM25 search
                    "description": {"type": "text"},
                    "data_json": {"type": "text"},  # JSON representation for keyword search

                    # Metadata
                    "data_format": {"type": "keyword"},
                    "is_structured": {"type": "boolean"},

                    # Vector field for kNN search
                    "embedding": {
                        "type": "knn_vector",
                        "dimension": 384,  # all-MiniLM-L6-v2 dimension
                        "method": {
                            "name": "hnsw",
                            "space_type": "cosinesimil",
                            "engine": "nmslib"
                        }
                    }
                }
            }
        }

        self.es.indices.create(index=self.index_name, body=index_body)
        print(f"[HybridSearch] Successfully created index {self.index_name}")

    async def index_data(
        self,
        project_id: str,
        data_key: str,
        description: str,
        data_json: str,
        embedding: List[float],
        data_format: str,
        is_structured: bool
    ) -> None:
        """
        Index data into Elasticsearch for hybrid search.

        Args:
            project_id: Project identifier
            data_key: Data key
            description: Text description for BM25 search
            data_json: JSON representation for keyword matching
            embedding: Vector embedding for kNN search
            data_format: Format of the data
            is_structured: Whether data is structured
        """
        doc_id = f"{project_id}:{data_key}"

        doc = {
            "project_id": project_id,
            "data_key": data_key,
            "description": description,
            "data_json": data_json,
            "embedding": embedding,
            "data_format": data_format,
            "is_structured": is_structured
        }

        self.es.index(index=self.index_name, id=doc_id, body=doc, refresh=True)
        print(f"[HybridSearch] Indexed: {doc_id}")

    async def search(
        self,
        project_id: str,
        query: str,
        size: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Perform hybrid search combining BM25 and kNN.

        Args:
            project_id: Project to search within
            query: Search query string
            size: Number of results to return

        Returns:
            List of search results with combined scores
        """
        # Generate embedding for the query
        query_embedding = self.semantic_matcher.model.encode(query).tolist()

        # Perform BM25 search
        bm25_results = self._bm25_search(project_id, query, size)

        # Perform kNN search
        knn_results = self._knn_search(project_id, query_embedding, size)

        # Combine and normalize results
        combined_results = self._combine_results(
            bm25_results, knn_results, self.bm25_weight, self.knn_weight
        )

        print(f"[HybridSearch] Found {len(combined_results)} results for query: {query}")
        return combined_results[:size]

    def _bm25_search(self, project_id: str, query: str, size: int) -> List[Dict[str, Any]]:
        """
        Perform BM25 text search.

        Args:
            project_id: Project to search within
            query: Search query string
            size: Number of results

        Returns:
            List of results with BM25 scores
        """
        search_body = {
            "query": {
                "bool": {
                    "must": [
                        {
                            "term": {"project_id": project_id}
                        },
                        {
                            "multi_match": {
                                "query": query,
                                "fields": ["description^2", "data_json"],
                                "type": "best_fields"
                            }
                        }
                    ]
                }
            },
            "size": size
        }

        response = self.es.search(index=self.index_name, body=search_body)

        results = []
        for hit in response["hits"]["hits"]:
            results.append({
                "id": hit["_id"],
                "source": hit["_source"],
                "bm25_score": hit["_score"]
            })

        print(f"[HybridSearch] BM25 search returned {len(results)} results")
        return results

    def _knn_search(self, project_id: str, query_embedding: List[float], size: int) -> List[Dict[str, Any]]:
        """
        Perform kNN vector search.

        Args:
            project_id: Project to search within
            query_embedding: Query embedding vector
            size: Number of results

        Returns:
            List of results with kNN scores
        """
        search_body = {
            "size": size,
            "query": {
                "bool": {
                    "must": {
                        "knn": {
                            "embedding": {
                                "vector": query_embedding,
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

        response = self.es.search(index=self.index_name, body=search_body)

        results = []
        for hit in response["hits"]["hits"]:
            results.append({
                "id": hit["_id"],
                "source": hit["_source"],
                "knn_score": hit["_score"]
            })

        print(f"[HybridSearch] kNN search returned {len(results)} results")
        return results

    def _combine_results(
        self,
        bm25_results: List[Dict[str, Any]],
        knn_results: List[Dict[str, Any]],
        bm25_weight: float,
        knn_weight: float
    ) -> List[Dict[str, Any]]:
        """
        Combine BM25 and kNN results using weighted score normalization.

        Uses z-score normalization with min-max fallback.

        Args:
            bm25_results: Results from BM25 search
            knn_results: Results from kNN search
            bm25_weight: Weight for BM25 scores
            knn_weight: Weight for kNN scores

        Returns:
            Combined results sorted by final weighted score
        """
        # Create lookup maps by document ID
        bm25_map = {r["id"]: r for r in bm25_results}
        knn_map = {r["id"]: r for r in knn_results}

        # Get all unique document IDs
        all_ids = set(bm25_map.keys()) | set(knn_map.keys())

        if not all_ids:
            return []

        # Normalize scores
        bm25_scores = [r["bm25_score"] for r in bm25_results]
        knn_scores = [r["knn_score"] for r in knn_results]

        norm_bm25 = self._normalize_scores(bm25_scores)
        norm_knn = self._normalize_scores(knn_scores)

        # Build normalized score maps
        bm25_norm_map = {bm25_results[i]["id"]: norm_bm25[i] for i in range(len(bm25_results))}
        knn_norm_map = {knn_results[i]["id"]: norm_knn[i] for i in range(len(knn_results))}

        # Combine scores
        combined = []
        for doc_id in all_ids:
            bm25_score = bm25_norm_map.get(doc_id, 0.0)
            knn_score = knn_norm_map.get(doc_id, 0.0)

            final_score = (bm25_weight * bm25_score) + (knn_weight * knn_score)

            # Get source document from either result set
            source = bm25_map.get(doc_id, {}).get("source") or knn_map.get(doc_id, {}).get("source")

            combined.append({
                "data_key": source["data_key"],
                "bm25_score": float(bm25_score),
                "knn_score": float(knn_score),
                "final_score": float(final_score),
                "similarity": float(final_score)  # For compatibility with existing code
            })

        # Sort by final score descending
        combined.sort(key=lambda x: x["final_score"], reverse=True)

        return combined

    def _normalize_scores(self, scores: List[float]) -> List[float]:
        """
        Normalize scores using z-score normalization with min-max fallback.

        Args:
            scores: List of scores to normalize

        Returns:
            List of normalized scores
        """
        if not scores:
            return []

        if len(scores) == 1:
            return [1.0]

        # Try z-score normalization
        try:
            scaler = StandardScaler()
            scores_array = np.array(scores).reshape(-1, 1)
            normalized = scaler.fit_transform(scores_array).flatten()

            # Check for valid results
            if not np.isnan(normalized).any():
                # Scale to 0-1 range for consistency
                min_val = normalized.min()
                max_val = normalized.max()
                if max_val > min_val:
                    normalized = (normalized - min_val) / (max_val - min_val)
                return normalized.tolist()
        except Exception as e:
            print(f"[HybridSearch] Z-score normalization failed, using min-max: {e}")

        # Fallback to min-max normalization
        min_score = min(scores)
        max_score = max(scores)

        if max_score == min_score:
            return [1.0] * len(scores)

        return [(s - min_score) / (max_score - min_score) for s in scores]
