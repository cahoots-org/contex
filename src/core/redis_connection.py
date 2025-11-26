"""Redis connection management with Sentinel support for high availability"""

import os
from typing import Optional
from redis.asyncio import Redis, ConnectionPool
from redis.asyncio.sentinel import Sentinel
from src.core.logging import get_logger

logger = get_logger(__name__)


class RedisConnectionManager:
    """
    Manages Redis connections with support for both standalone and Sentinel modes.

    Sentinel mode provides high availability with automatic failover:
    - Monitors master/replica health
    - Automatic master failover on failure
    - Service discovery for clients

    Configuration via environment variables:
    - REDIS_MODE: 'standalone' (default) or 'sentinel'
    - REDIS_URL: Redis URL for standalone mode (e.g., redis://redis:6379)
    - REDIS_SENTINEL_HOSTS: Comma-separated Sentinel hosts for Sentinel mode (e.g., sentinel1:26379,sentinel2:26379)
    - REDIS_SENTINEL_MASTER: Master name for Sentinel (e.g., mymaster)
    - REDIS_SENTINEL_PASSWORD: Optional password for Sentinel
    - REDIS_PASSWORD: Optional password for Redis
    - REDIS_DB: Database number (default: 0)
    - REDIS_MAX_CONNECTIONS: Max connection pool size (default: 50)
    - REDIS_SOCKET_TIMEOUT: Socket timeout in seconds (default: 5)
    - REDIS_SOCKET_CONNECT_TIMEOUT: Connection timeout in seconds (default: 5)
    - REDIS_SOCKET_KEEPALIVE: Enable TCP keepalive (default: true)
    - REDIS_RETRY_ON_TIMEOUT: Retry on timeout (default: true)
    - REDIS_HEALTH_CHECK_INTERVAL: Health check interval in seconds (default: 30)
    """

    def __init__(self):
        self.mode = os.getenv("REDIS_MODE", "standalone").lower()
        self.redis: Optional[Redis] = None
        self.sentinel: Optional[Sentinel] = None

    async def connect(self) -> Redis:
        """
        Create and return Redis connection based on configuration.

        Returns:
            Redis client instance

        Raises:
            ValueError: If configuration is invalid
            ConnectionError: If unable to connect
        """
        if self.mode == "sentinel":
            return await self._connect_sentinel()
        elif self.mode == "standalone":
            return await self._connect_standalone()
        else:
            raise ValueError(f"Invalid REDIS_MODE: {self.mode}. Must be 'standalone' or 'sentinel'")

    async def _connect_standalone(self) -> Redis:
        """Connect to standalone Redis instance"""
        redis_url = os.getenv("REDIS_URL", "redis://redis:6379")
        max_connections = int(os.getenv("REDIS_MAX_CONNECTIONS", "50"))
        socket_timeout = int(os.getenv("REDIS_SOCKET_TIMEOUT", "5"))
        socket_connect_timeout = int(os.getenv("REDIS_SOCKET_CONNECT_TIMEOUT", "5"))
        socket_keepalive = os.getenv("REDIS_SOCKET_KEEPALIVE", "true").lower() == "true"
        retry_on_timeout = os.getenv("REDIS_RETRY_ON_TIMEOUT", "true").lower() == "true"
        health_check_interval = int(os.getenv("REDIS_HEALTH_CHECK_INTERVAL", "30"))

        logger.info("Connecting to Redis in standalone mode",
                   redis_url=redis_url,
                   max_connections=max_connections)

        try:
            pool = ConnectionPool.from_url(
                redis_url,
                max_connections=max_connections,
                socket_timeout=socket_timeout,
                socket_connect_timeout=socket_connect_timeout,
                socket_keepalive=socket_keepalive,
                retry_on_timeout=retry_on_timeout,
                health_check_interval=health_check_interval,
                decode_responses=False
            )

            redis = Redis(connection_pool=pool)

            # Test connection
            await redis.ping()

            logger.info("Connected to Redis in standalone mode",
                       redis_url=redis_url,
                       max_connections=max_connections,
                       socket_timeout=socket_timeout,
                       socket_keepalive=socket_keepalive)

            self.redis = redis
            return redis

        except Exception as e:
            logger.error("Failed to connect to Redis in standalone mode",
                        error=str(e),
                        redis_url=redis_url)
            raise ConnectionError(f"Failed to connect to Redis: {e}") from e

    async def _connect_sentinel(self) -> Redis:
        """Connect to Redis via Sentinel for high availability"""
        sentinel_hosts_str = os.getenv("REDIS_SENTINEL_HOSTS")
        if not sentinel_hosts_str:
            raise ValueError("REDIS_SENTINEL_HOSTS is required when REDIS_MODE=sentinel")

        master_name = os.getenv("REDIS_SENTINEL_MASTER", "mymaster")
        sentinel_password = os.getenv("REDIS_SENTINEL_PASSWORD")
        redis_password = os.getenv("REDIS_PASSWORD")
        redis_db = int(os.getenv("REDIS_DB", "0"))
        socket_timeout = int(os.getenv("REDIS_SOCKET_TIMEOUT", "5"))
        socket_connect_timeout = int(os.getenv("REDIS_SOCKET_CONNECT_TIMEOUT", "5"))
        socket_keepalive = os.getenv("REDIS_SOCKET_KEEPALIVE", "true").lower() == "true"
        max_connections = int(os.getenv("REDIS_MAX_CONNECTIONS", "50"))

        # Parse Sentinel hosts (format: "host1:port1,host2:port2,...")
        sentinel_hosts = []
        for host_port in sentinel_hosts_str.split(","):
            host_port = host_port.strip()
            if ":" in host_port:
                host, port = host_port.rsplit(":", 1)
                sentinel_hosts.append((host, int(port)))
            else:
                sentinel_hosts.append((host_port, 26379))  # Default Sentinel port

        logger.info("Connecting to Redis via Sentinel",
                   sentinel_hosts=sentinel_hosts,
                   master_name=master_name,
                   redis_db=redis_db)

        try:
            # Create Sentinel connection
            sentinel = Sentinel(
                sentinel_hosts,
                sentinel_kwargs={
                    'password': sentinel_password,
                    'socket_timeout': socket_timeout,
                    'socket_connect_timeout': socket_connect_timeout,
                    'socket_keepalive': socket_keepalive,
                },
                password=redis_password,
                db=redis_db,
                decode_responses=False
            )

            # Get master connection with connection pool
            redis = sentinel.master_for(
                master_name,
                socket_timeout=socket_timeout,
                socket_connect_timeout=socket_connect_timeout,
                socket_keepalive=socket_keepalive,
                connection_pool_kwargs={
                    'max_connections': max_connections,
                    'health_check_interval': int(os.getenv("REDIS_HEALTH_CHECK_INTERVAL", "30")),
                }
            )

            # Test connection
            await redis.ping()

            # Discover master info
            master_info = await sentinel.discover_master(master_name)

            logger.info("Connected to Redis via Sentinel",
                       master_name=master_name,
                       master_host=master_info[0],
                       master_port=master_info[1],
                       sentinel_count=len(sentinel_hosts),
                       max_connections=max_connections)

            self.redis = redis
            self.sentinel = sentinel
            return redis

        except Exception as e:
            logger.error("Failed to connect to Redis via Sentinel",
                        error=str(e),
                        sentinel_hosts=sentinel_hosts,
                        master_name=master_name)
            raise ConnectionError(f"Failed to connect to Redis via Sentinel: {e}") from e

    async def get_master_info(self) -> Optional[dict]:
        """
        Get current master information (only available in Sentinel mode).

        Returns:
            Dictionary with master host, port, and status, or None if not in Sentinel mode
        """
        if self.mode != "sentinel" or not self.sentinel:
            return None

        try:
            master_name = os.getenv("REDIS_SENTINEL_MASTER", "mymaster")
            master_info = await self.sentinel.discover_master(master_name)

            return {
                "master_name": master_name,
                "host": master_info[0],
                "port": master_info[1],
                "mode": "sentinel"
            }
        except Exception as e:
            logger.error("Failed to get master info", error=str(e))
            return None

    async def get_replica_info(self) -> Optional[list]:
        """
        Get current replica information (only available in Sentinel mode).

        Returns:
            List of replica dictionaries with host and port, or None if not in Sentinel mode
        """
        if self.mode != "sentinel" or not self.sentinel:
            return None

        try:
            master_name = os.getenv("REDIS_SENTINEL_MASTER", "mymaster")
            replicas = await self.sentinel.discover_slaves(master_name)

            return [
                {
                    "host": replica[0],
                    "port": replica[1]
                }
                for replica in replicas
            ]
        except Exception as e:
            logger.error("Failed to get replica info", error=str(e))
            return None

    async def close(self):
        """Close Redis connection"""
        if self.redis:
            await self.redis.close()
            logger.info("Redis connection closed")


async def create_redis_connection() -> Redis:
    """
    Factory function to create Redis connection based on environment configuration.

    This is the main entry point for creating Redis connections throughout the application.

    Returns:
        Redis client instance

    Raises:
        ValueError: If configuration is invalid
        ConnectionError: If unable to connect

    Example:
        ```python
        redis = await create_redis_connection()
        await redis.ping()
        ```
    """
    manager = RedisConnectionManager()
    return await manager.connect()
