"""Graceful shutdown handler for Contex"""

import signal
import asyncio
from typing import Optional, Callable
from src.core.logging import get_logger

logger = get_logger(__name__)


class GracefulShutdown:
    """
    Handles graceful shutdown of the application.
    
    Features:
    - Handles SIGTERM and SIGINT signals
    - Drains in-flight requests
    - Closes connections cleanly
    - Configurable shutdown timeout
    
    Usage:
        shutdown_handler = GracefulShutdown(
            shutdown_timeout=30.0,
            on_shutdown=cleanup_function
        )
        shutdown_handler.setup()
    """
    
    def __init__(
        self,
        shutdown_timeout: float = 30.0,
        on_shutdown: Optional[Callable] = None
    ):
        """
        Initialize graceful shutdown handler.
        
        Args:
            shutdown_timeout: Maximum time to wait for shutdown (seconds)
            on_shutdown: Optional async callback to run on shutdown
        """
        self.shutdown_timeout = shutdown_timeout
        self.on_shutdown = on_shutdown
        self.shutdown_event = asyncio.Event()
        self.is_shutting_down = False
        
        logger.info("Graceful shutdown handler initialized",
                   timeout=shutdown_timeout)
    
    def setup(self):
        """Setup signal handlers"""
        # Handle SIGTERM (Kubernetes sends this)
        signal.signal(signal.SIGTERM, self._signal_handler)
        
        # Handle SIGINT (Ctrl+C)
        signal.signal(signal.SIGINT, self._signal_handler)
        
        logger.info("Signal handlers registered (SIGTERM, SIGINT)")
    
    def _signal_handler(self, signum, frame):
        """Handle shutdown signals"""
        signal_name = signal.Signals(signum).name
        logger.warning(f"Received {signal_name}, initiating graceful shutdown...")
        
        if self.is_shutting_down:
            logger.warning("Shutdown already in progress, ignoring signal")
            return
        
        self.is_shutting_down = True
        self.shutdown_event.set()
    
    async def wait_for_shutdown(self):
        """Wait for shutdown signal"""
        await self.shutdown_event.wait()
    
    async def shutdown(self):
        """
        Perform graceful shutdown.
        
        Returns:
            True if shutdown completed successfully, False if timeout
        """
        if not self.is_shutting_down:
            logger.info("Shutdown requested programmatically")
            self.is_shutting_down = True
        
        logger.info("Starting graceful shutdown",
                   timeout=self.shutdown_timeout)
        
        try:
            # Run custom shutdown callback if provided
            if self.on_shutdown:
                logger.info("Running shutdown callback...")
                await asyncio.wait_for(
                    self.on_shutdown(),
                    timeout=self.shutdown_timeout
                )
            
            logger.info("Graceful shutdown completed successfully")
            return True
            
        except asyncio.TimeoutError:
            logger.error("Shutdown timeout exceeded",
                        timeout=self.shutdown_timeout)
            return False
        
        except Exception as e:
            logger.error("Error during shutdown",
                        error=str(e),
                        exc_info=True)
            return False
    
    def is_shutdown_requested(self) -> bool:
        """Check if shutdown has been requested"""
        return self.is_shutting_down


async def drain_connections(
    redis,
    timeout: float = 10.0
):
    """
    Drain active connections gracefully.

    Args:
        redis: Redis client
        timeout: Maximum time to wait
    """
    logger.info("Draining connections...", timeout=timeout)

    try:
        # Wait a bit for in-flight requests to complete
        await asyncio.sleep(1.0)

        # Close Redis connection and connection pool
        if redis:
            logger.info("Closing Redis connection...")

            # Close the client connection
            await redis.aclose()

            # Close the connection pool if it exists
            if hasattr(redis, 'connection_pool') and redis.connection_pool:
                logger.info("Closing Redis connection pool...")
                await redis.connection_pool.disconnect()
                logger.info("Redis connection pool closed")

            logger.info("Redis connection closed")

        logger.info("Connection draining complete")

    except Exception as e:
        logger.error("Error draining connections",
                    error=str(e),
                    exc_info=True)
        raise


async def shutdown_cleanup(app_state):
    """
    Cleanup function for application shutdown.

    Args:
        app_state: FastAPI app.state object
    """
    logger.info("Running shutdown cleanup...")

    # Stop graceful degradation monitoring
    if hasattr(app_state, 'graceful_degradation') and app_state.graceful_degradation:
        logger.info("Stopping graceful degradation monitoring...")
        await app_state.graceful_degradation.stop_health_monitoring()

    # Close Redis connection
    if hasattr(app_state, 'redis') and app_state.redis:
        await drain_connections(app_state.redis)

    # Flush Sentry events
    try:
        from src.core.sentry_integration import flush as sentry_flush, is_initialized as sentry_is_initialized
        if sentry_is_initialized():
            logger.info("Flushing Sentry events...")
            sentry_flush(timeout=5.0)
    except ImportError:
        pass

    # Any other cleanup tasks
    logger.info("Shutdown cleanup complete")
