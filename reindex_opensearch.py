#!/usr/bin/env python
"""Re-index existing Redis data into OpenSearch"""

import asyncio
import json
import os
from redis.asyncio import Redis
from src.core.semantic_matcher import SemanticDataMatcher

async def reindex_all_data():
    """Re-index all data from Redis into OpenSearch"""

    # Connect to Redis
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
    redis = Redis.from_url(redis_url, decode_responses=False)

    try:
        # Initialize semantic matcher (which will initialize hybrid search)
        matcher = SemanticDataMatcher(redis)
        await matcher.initialize_index()

        print("[ReIndex] Starting re-indexing process...")

        # Get all data keys from Redis
        pattern = f"{matcher.KEY_PREFIX}*"
        cursor = 0
        total_indexed = 0

        while True:
            cursor, keys = await redis.scan(cursor, match=pattern, count=100)

            for key in keys:
                if isinstance(key, bytes):
                    key = key.decode()

                # Get data info
                data_info = await redis.hgetall(key)

                if not data_info:
                    continue

                # Extract fields
                project_id = data_info.get(b"project_id") or data_info.get("project_id", "")
                data_key = data_info.get(b"data_key") or data_info.get("data_key", "")
                description = data_info.get(b"description") or data_info.get("description", "")
                data_str = data_info.get(b"data") or data_info.get("data", "{}")
                data_format = data_info.get(b"data_format") or data_info.get("data_format", "json")
                is_structured_str = data_info.get(b"is_structured") or data_info.get("is_structured", "true")
                embedding_bytes = data_info.get(b"embedding") or data_info.get("embedding", b"")

                # Decode bytes if needed
                if isinstance(project_id, bytes):
                    project_id = project_id.decode()
                if isinstance(data_key, bytes):
                    data_key = data_key.decode()
                if isinstance(description, bytes):
                    description = description.decode()
                if isinstance(data_str, bytes):
                    data_str = data_str.decode()
                if isinstance(data_format, bytes):
                    data_format = data_format.decode()
                if isinstance(is_structured_str, bytes):
                    is_structured_str = is_structured_str.decode()

                is_structured = is_structured_str.lower() == "true"

                # Convert embedding bytes to list
                import numpy as np
                if embedding_bytes:
                    embedding_array = np.frombuffer(embedding_bytes, dtype=np.float32)
                    embedding_list = embedding_array.tolist()
                else:
                    # Re-generate embedding if missing
                    embedding_array = matcher.model.encode(description)
                    embedding_list = embedding_array.tolist()

                # Index into OpenSearch
                if matcher.hybrid_search:
                    try:
                        await matcher.hybrid_search.index_document(
                            project_id=project_id,
                            data_key=data_key,
                            content=description,
                            metadata=data_str,
                            vector=embedding_list,
                            data_format=data_format,
                            is_structured=is_structured
                        )
                        print(f"[ReIndex] ✓ Indexed: {project_id}:{data_key}")
                        total_indexed += 1
                    except Exception as e:
                        print(f"[ReIndex] ✗ Failed to index {project_id}:{data_key}: {e}")
                else:
                    print("[ReIndex] ✗ Hybrid search not enabled")
                    return

            if cursor == 0:
                break

        print(f"\n[ReIndex] ✓ Completed! Indexed {total_indexed} documents into OpenSearch")

    finally:
        await redis.aclose()

if __name__ == "__main__":
    asyncio.run(reindex_all_data())
