"""Embedding cache for improved query performance"""

import hashlib
import json
import numpy as np
from typing import Optional
from redis.asyncio import Redis
from src.core.logging import get_logger

logger = get_logger(__name__)


class EmbeddingCache:
    """
    Redis-backed cache for embeddings to reduce computation time.

    Features:
    - SHA256-based content hashing
    - Configurable TTL (default: 1 hour)
    - Cache hit/miss metrics
    - Automatic serialization/deserialization
    - Memory-efficient storage

    Usage:
        cache = EmbeddingCache(redis)

        # Try to get from cache
        embedding = await cache.get("some text")
        if embedding is None:
            # Cache miss - compute embedding
            embedding = model.encode("some text")
            await cache.set("some text", embedding)
    """

    CACHE_PREFIX = "embedding_cache:"
    DEFAULT_TTL = 3600  # 1 hour

    def __init__(self, redis: Redis, ttl: int = DEFAULT_TTL):
        """
        Initialize embedding cache.

        Args:
            redis: Redis client instance
            ttl: Time-to-live for cache entries in seconds
        """
        self.redis = redis
        self.ttl = ttl

        # Import metrics (lazy to avoid circular deps)
        self._metrics_imported = False
        self._cache_hits = None
        self._cache_misses = None

        logger.info(f"Embedding cache initialized (TTL: {ttl}s)")

    def _import_metrics(self):
        """Lazy import metrics"""
        if not self._metrics_imported:
            try:
                from src.core.metrics import (
                    embedding_cache_hits_total,
                    embedding_cache_misses_total
                )
                self._cache_hits = embedding_cache_hits_total
                self._cache_misses = embedding_cache_misses_total
                self._metrics_imported = True
            except (ImportError, AttributeError):
                pass

    def _get_cache_key(self, text: str) -> str:
        """
        Generate cache key from text content.

        Uses SHA256 hash to ensure consistent key length
        and avoid storing PII in cache keys.

        Args:
            text: Input text to hash

        Returns:
            Cache key string
        """
        text_hash = hashlib.sha256(text.encode('utf-8')).hexdigest()
        return f"{self.CACHE_PREFIX}{text_hash}"

    async def get(self, text: str) -> Optional[np.ndarray]:
        """
        Get embedding from cache.

        Args:
            text: Text that was embedded

        Returns:
            Cached embedding array or None if not found
        """
        cache_key = self._get_cache_key(text)

        try:
            cached_bytes = await self.redis.get(cache_key)

            if cached_bytes is None:
                # Cache miss
                self._import_metrics()
                if self._cache_misses:
                    self._cache_misses.inc()
                logger.debug(f"Embedding cache MISS: {text[:50]}...")
                return None

            # Cache hit
            self._import_metrics()
            if self._cache_hits:
                self._cache_hits.inc()

            # Deserialize
            embedding = np.frombuffer(cached_bytes, dtype=np.float32)
            logger.debug(f"Embedding cache HIT: {text[:50]}...")
            return embedding

        except Exception as e:
            logger.warning(f"Embedding cache get error: {e}")
            return None

    async def set(self, text: str, embedding: np.ndarray) -> bool:
        """
        Store embedding in cache.

        Args:
            text: Text that was embedded
            embedding: Embedding array to cache

        Returns:
            True if successful, False otherwise
        """
        cache_key = self._get_cache_key(text)

        try:
            # Serialize embedding
            embedding_bytes = embedding.astype(np.float32).tobytes()

            # Store with TTL
            await self.redis.setex(
                cache_key,
                self.ttl,
                embedding_bytes
            )

            logger.debug(f"Embedding cached: {text[:50]}... (TTL: {self.ttl}s)")
            return True

        except Exception as e:
            logger.warning(f"Embedding cache set error: {e}")
            return False

    async def delete(self, text: str) -> bool:
        """
        Delete embedding from cache.

        Args:
            text: Text whose embedding should be deleted

        Returns:
            True if deleted, False otherwise
        """
        cache_key = self._get_cache_key(text)

        try:
            result = await self.redis.delete(cache_key)
            return result > 0
        except Exception as e:
            logger.warning(f"Embedding cache delete error: {e}")
            return False

    async def clear(self, pattern: Optional[str] = None) -> int:
        """
        Clear cache entries.

        Args:
            pattern: Optional pattern to match keys (default: all cache entries)

        Returns:
            Number of keys deleted
        """
        if pattern is None:
            pattern = f"{self.CACHE_PREFIX}*"

        try:
            # Find matching keys
            keys = []
            async for key in self.redis.scan_iter(match=pattern, count=100):
                keys.append(key)

            if not keys:
                return 0

            # Delete in batch
            deleted = await self.redis.delete(*keys)
            logger.info(f"Cleared {deleted} cache entries")
            return deleted

        except Exception as e:
            logger.error(f"Embedding cache clear error: {e}")
            return 0

    async def get_stats(self) -> dict:
        """
        Get cache statistics.

        Returns:
            Dict with cache statistics
        """
        try:
            # Count cache entries
            count = 0
            async for _ in self.redis.scan_iter(
                match=f"{self.CACHE_PREFIX}*",
                count=1000
            ):
                count += 1

            # Get memory usage (approximate)
            info = await self.redis.info('memory')

            return {
                "entries": count,
                "ttl_seconds": self.ttl,
                "memory_used_mb": round(info.get('used_memory', 0) / 1024 / 1024, 2),
            }

        except Exception as e:
            logger.error(f"Error getting cache stats: {e}")
            return {
                "entries": 0,
                "ttl_seconds": self.ttl,
                "memory_used_mb": 0,
                "error": str(e)
            }
