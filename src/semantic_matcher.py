"""Semantic data matching using embeddings for agent context discovery"""

import numpy as np
from typing import Dict, List, Any, Optional
from sentence_transformers import SentenceTransformer
from sklearn.metrics.pairwise import cosine_similarity


class SemanticDataMatcher:
    """
    Matches agent semantic needs to available project data using embeddings.

    Key features:
    - Auto-generates descriptions from data structure
    - Fast embedding-based similarity matching (~20ms)
    - Deterministic (same inputs = same outputs)
    - Handles schema evolution gracefully
    - Over-subscribes when similarity scores are close
    """

    def __init__(
        self,
        model_name: str = "all-MiniLM-L6-v2",
        similarity_threshold: float = 0.5,
        max_matches: int = 10,
    ):
        """
        Initialize semantic matcher.

        Args:
            model_name: SentenceTransformer model (~80MB)
            similarity_threshold: Minimum similarity to match (0-1)
            max_matches: Maximum matches to return per need
        """
        print(f"[SemanticMatcher] Loading model: {model_name}")
        self.model = SentenceTransformer(model_name)
        self.threshold = similarity_threshold
        self.max_matches = max_matches

        # Registry: "project_id:data_key" -> {embedding, data, description}
        self.registry: Dict[str, Dict[str, Any]] = {}

        print(
            f"[SemanticMatcher] ✓ Model loaded (threshold: {similarity_threshold}, max_matches: {max_matches})"
        )

    def register_data(self, project_id: str, data_key: str, data: Dict[str, Any]):
        """
        Register new project data for matching.

        Args:
            project_id: Project identifier
            data_key: Data identifier (e.g., "tech_stack", "event_model")
            data: The actual data
        """
        # Auto-generate description from data structure
        description = self._auto_describe(data_key, data)

        # Generate embedding (5-10ms)
        embedding = self.model.encode(description)

        # Store in registry
        registry_key = f"{project_id}:{data_key}"
        self.registry[registry_key] = {
            "embedding": embedding,
            "data": data,
            "description": description,
        }

        print(f"[SemanticMatcher] Registered: {registry_key}")
        print(f"[SemanticMatcher]   Description: {description[:100]}...")

    def match_agent_needs(
        self, project_id: str, needs: List[str]
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        Match agent semantic needs to available data.

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

            # Embed agent need (5-10ms)
            need_embedding = self.model.encode(need)

            # Find matching data sources
            candidates = []

            for registry_key, info in self.registry.items():
                # Only match data from this project
                if not registry_key.startswith(f"{project_id}:"):
                    continue

                # Compute similarity
                similarity = cosine_similarity(
                    need_embedding.reshape(1, -1), info["embedding"].reshape(1, -1)
                )[0][0]

                # Include if above threshold
                if similarity >= self.threshold:
                    data_key = registry_key.split(":", 1)[1]
                    candidates.append(
                        {
                            "data_key": data_key,
                            "similarity": float(similarity),
                            "data": info["data"],
                            "description": info["description"],
                        }
                    )

            # Sort by similarity (highest first)
            candidates.sort(key=lambda x: x["similarity"], reverse=True)

            # Take top N matches
            top_matches = candidates[: self.max_matches]
            matches[need] = top_matches

            # Log results
            if top_matches:
                print(f"[SemanticMatcher]   Found {len(top_matches)} matches:")
                for match in top_matches:
                    print(
                        f"[SemanticMatcher]     - {match['data_key']} (similarity: {match['similarity']:.3f})"
                    )
            else:
                print(
                    f"[SemanticMatcher]   ⚠ No matches found (threshold: {self.threshold})"
                )

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
        # Extract field paths
        fields = self._flatten_dict(data)

        # Limit to first 10 fields for brevity
        field_names = list(fields.keys())[:10]

        # Generate description
        if field_names:
            description = f"{data_key} containing fields: {', '.join(field_names)}"
        else:
            description = data_key

        return description

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

    def get_registered_data(self, project_id: str) -> List[str]:
        """Get all registered data keys for a project"""
        return [
            key.split(":", 1)[1]
            for key in self.registry.keys()
            if key.startswith(f"{project_id}:")
        ]

    def clear_project(self, project_id: str):
        """Remove all data for a project"""
        to_remove = [
            key for key in self.registry.keys() if key.startswith(f"{project_id}:")
        ]

        for key in to_remove:
            del self.registry[key]

        print(
            f"[SemanticMatcher] Cleared {len(to_remove)} entries for project {project_id}"
        )
