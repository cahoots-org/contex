"""Shutdown cleanup for Contex"""

import sentry_sdk

from src.core.logging import get_logger

logger = get_logger(__name__)


async def shutdown_cleanup(app_state):
    """
    Cleanup function for application shutdown.

    Args:
        app_state: FastAPI app.state object
    """
    logger.info("Running shutdown cleanup...")

    # Close PostgreSQL connection
    if hasattr(app_state, 'db') and app_state.db:
        logger.info("Closing PostgreSQL connection...")
        try:
            await app_state.db.disconnect()
            logger.info("PostgreSQL connection closed")
        except Exception as e:
            logger.error("Error closing PostgreSQL connection", error=str(e))

    sentry_sdk.flush(timeout=5.0)

    logger.info("Shutdown cleanup complete")
