"""
Redis Pub/Sub Manager

Provides Redis connections specifically for pub/sub operations.
All data storage has moved to PostgreSQL - Redis is only used for real-time notifications.
"""

import os
from typing import Optional

from redis.asyncio import Redis, ConnectionPool
from redis.asyncio.sentinel import Sentinel

from src.core.logging import get_logger

logger = get_logger(__name__)


class PubSubManager:
    """
    Manages Redis connections for pub/sub operations only.

    All data storage is handled by PostgreSQL. This manager provides
    Redis connections specifically for real-time agent notifications.

    Configuration via environment variables:
    - REDIS_MODE: 'standalone' (default) or 'sentinel'
    - REDIS_URL: Redis URL for standalone mode (e.g., redis://redis:6379)
    - REDIS_SENTINEL_HOSTS: Comma-separated Sentinel hosts for Sentinel mode
    - REDIS_SENTINEL_MASTER: Master name for Sentinel (e.g., mymaster)
    - REDIS_SENTINEL_PASSWORD: Optional password for Sentinel
    - REDIS_PASSWORD: Optional password for Redis
    """

    def __init__(self):
        self.mode = os.getenv("REDIS_MODE", "standalone").lower()
        self.redis: Optional[Redis] = None
        self.sentinel: Optional[Sentinel] = None
        self._is_connected = False

    async def connect(self) -> Redis:
        """
        Create and return Redis connection for pub/sub.

        Returns:
            Redis client instance

        Raises:
            ValueError: If configuration is invalid
            ConnectionError: If unable to connect
        """
        if self._is_connected and self.redis:
            return self.redis

        if self.mode == "sentinel":
            return await self._connect_sentinel()
        elif self.mode == "standalone":
            return await self._connect_standalone()
        else:
            raise ValueError(f"Invalid REDIS_MODE: {self.mode}. Must be 'standalone' or 'sentinel'")

    async def _connect_standalone(self) -> Redis:
        """Connect to standalone Redis instance."""
        redis_url = os.getenv("REDIS_URL", "redis://redis:6379")
        max_connections = int(os.getenv("REDIS_MAX_CONNECTIONS", "20"))  # Lower for pub/sub only
        socket_timeout = int(os.getenv("REDIS_SOCKET_TIMEOUT", "5"))
        socket_connect_timeout = int(os.getenv("REDIS_SOCKET_CONNECT_TIMEOUT", "5"))

        logger.info("Connecting to Redis for pub/sub (standalone mode)", redis_url=redis_url)

        try:
            pool = ConnectionPool.from_url(
                redis_url,
                max_connections=max_connections,
                socket_timeout=socket_timeout,
                socket_connect_timeout=socket_connect_timeout,
                socket_keepalive=True,
                retry_on_timeout=True,
                decode_responses=False
            )

            redis = Redis(connection_pool=pool)
            await redis.ping()

            logger.info("Connected to Redis for pub/sub", redis_url=redis_url)

            self.redis = redis
            self._is_connected = True
            return redis

        except Exception as e:
            logger.error("Failed to connect to Redis for pub/sub", error=str(e))
            raise ConnectionError(f"Failed to connect to Redis: {e}") from e

    async def _connect_sentinel(self) -> Redis:
        """Connect to Redis via Sentinel for high availability."""
        sentinel_hosts_str = os.getenv("REDIS_SENTINEL_HOSTS")
        if not sentinel_hosts_str:
            raise ValueError("REDIS_SENTINEL_HOSTS is required when REDIS_MODE=sentinel")

        master_name = os.getenv("REDIS_SENTINEL_MASTER", "mymaster")
        sentinel_password = os.getenv("REDIS_SENTINEL_PASSWORD")
        redis_password = os.getenv("REDIS_PASSWORD")
        redis_db = int(os.getenv("REDIS_DB", "0"))
        socket_timeout = int(os.getenv("REDIS_SOCKET_TIMEOUT", "5"))
        socket_connect_timeout = int(os.getenv("REDIS_SOCKET_CONNECT_TIMEOUT", "5"))
        max_connections = int(os.getenv("REDIS_MAX_CONNECTIONS", "20"))

        # Parse Sentinel hosts
        sentinel_hosts = []
        for host_port in sentinel_hosts_str.split(","):
            host_port = host_port.strip()
            if ":" in host_port:
                host, port = host_port.rsplit(":", 1)
                sentinel_hosts.append((host, int(port)))
            else:
                sentinel_hosts.append((host_port, 26379))

        logger.info("Connecting to Redis via Sentinel for pub/sub", master_name=master_name)

        try:
            sentinel = Sentinel(
                sentinel_hosts,
                sentinel_kwargs={
                    'password': sentinel_password,
                    'socket_timeout': socket_timeout,
                    'socket_connect_timeout': socket_connect_timeout,
                    'socket_keepalive': True,
                },
                password=redis_password,
                db=redis_db,
                decode_responses=False
            )

            redis = sentinel.master_for(
                master_name,
                socket_timeout=socket_timeout,
                socket_connect_timeout=socket_connect_timeout,
                socket_keepalive=True,
                connection_pool_kwargs={'max_connections': max_connections}
            )

            await redis.ping()
            master_info = await sentinel.discover_master(master_name)

            logger.info("Connected to Redis via Sentinel for pub/sub",
                       master_name=master_name,
                       master_host=master_info[0])

            self.redis = redis
            self.sentinel = sentinel
            self._is_connected = True
            return redis

        except Exception as e:
            logger.error("Failed to connect to Redis via Sentinel", error=str(e))
            raise ConnectionError(f"Failed to connect to Redis via Sentinel: {e}") from e

    async def publish(self, channel: str, message: bytes) -> int:
        """
        Publish a message to a channel.

        Args:
            channel: Channel name
            message: Message bytes to publish

        Returns:
            Number of subscribers that received the message
        """
        if not self.redis:
            raise RuntimeError("Not connected to Redis. Call connect() first.")
        return await self.redis.publish(channel, message)

    async def subscribe(self, *channels: str):
        """
        Subscribe to one or more channels.

        Args:
            channels: Channel names to subscribe to

        Returns:
            PubSub instance for receiving messages
        """
        if not self.redis:
            raise RuntimeError("Not connected to Redis. Call connect() first.")
        pubsub = self.redis.pubsub()
        await pubsub.subscribe(*channels)
        return pubsub

    @property
    def is_connected(self) -> bool:
        """Check if connected to Redis."""
        return self._is_connected

    async def health_check(self) -> dict:
        """
        Perform a health check on the Redis connection.

        Returns:
            dict with status and latency
        """
        import time

        if not self._is_connected or not self.redis:
            return {"status": "unhealthy", "error": "Not connected"}

        try:
            start = time.perf_counter()
            await self.redis.ping()
            latency_ms = (time.perf_counter() - start) * 1000

            status = "healthy" if latency_ms < 100 else "degraded"
            return {
                "status": status,
                "latency_ms": round(latency_ms, 2),
                "mode": self.mode,
            }
        except Exception as e:
            return {"status": "unhealthy", "error": str(e)}

    async def close(self):
        """Close Redis connection."""
        if self.redis:
            await self.redis.close()
            self._is_connected = False
            logger.info("Redis pub/sub connection closed")


# Global pub/sub manager instance
_pubsub_manager: Optional[PubSubManager] = None


async def get_pubsub() -> PubSubManager:
    """Get the global pub/sub manager instance."""
    global _pubsub_manager
    if _pubsub_manager is None:
        _pubsub_manager = PubSubManager()
    return _pubsub_manager


async def init_pubsub() -> PubSubManager:
    """Initialize and connect the global pub/sub manager."""
    global _pubsub_manager
    _pubsub_manager = PubSubManager()
    await _pubsub_manager.connect()
    return _pubsub_manager


async def close_pubsub() -> None:
    """Close the global pub/sub connection."""
    global _pubsub_manager
    if _pubsub_manager:
        await _pubsub_manager.close()
        _pubsub_manager = None


# Backwards compatibility - keep create_redis_connection for existing code
async def create_redis_connection() -> Redis:
    """
    Factory function to create Redis connection.

    This maintains backwards compatibility with existing code.
    New code should use init_pubsub() instead.
    """
    manager = await get_pubsub()
    return await manager.connect()
