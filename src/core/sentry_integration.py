"""Sentry integration for error tracking and performance monitoring"""

import os
from typing import Optional, Dict, Any
from src.core.logging import get_logger

logger = get_logger(__name__)

# Global flag to track if Sentry is initialized
_sentry_initialized = False
_sentry_sdk = None


def init_sentry(
    dsn: Optional[str] = None,
    environment: Optional[str] = None,
    release: Optional[str] = None,
    sample_rate: float = 1.0,
    traces_sample_rate: float = 0.1,
    profiles_sample_rate: float = 0.1,
    enable_tracing: bool = True,
    debug: bool = False
) -> bool:
    """
    Initialize Sentry SDK for error tracking and performance monitoring.

    Args:
        dsn: Sentry DSN (Data Source Name). If not provided, reads from SENTRY_DSN env var.
        environment: Environment name (e.g., 'production', 'staging'). Defaults to SENTRY_ENVIRONMENT.
        release: Release version. Defaults to SENTRY_RELEASE or 'contex@0.2.0'.
        sample_rate: Error sampling rate (0.0-1.0). Default: 1.0 (100%)
        traces_sample_rate: Performance traces sampling rate. Default: 0.1 (10%)
        profiles_sample_rate: Profiling sampling rate. Default: 0.1 (10%)
        enable_tracing: Enable performance monitoring. Default: True
        debug: Enable Sentry debug mode. Default: False

    Returns:
        True if Sentry was initialized successfully, False otherwise

    Example:
        ```python
        # Basic initialization
        init_sentry(dsn="https://xxx@sentry.io/123")

        # With custom configuration
        init_sentry(
            dsn="https://xxx@sentry.io/123",
            environment="production",
            release="contex@0.2.0",
            traces_sample_rate=0.2
        )
        ```
    """
    global _sentry_initialized, _sentry_sdk

    # Get DSN from parameter or environment
    dsn = dsn or os.getenv("SENTRY_DSN")
    if not dsn:
        logger.info("Sentry DSN not configured, skipping initialization")
        return False

    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.starlette import StarletteIntegration
        from sentry_sdk.integrations.redis import RedisIntegration
        from sentry_sdk.integrations.logging import LoggingIntegration
        from sentry_sdk.integrations.asyncio import AsyncioIntegration

        _sentry_sdk = sentry_sdk

        # Get configuration from environment with fallbacks
        environment = environment or os.getenv("SENTRY_ENVIRONMENT", "development")
        release = release or os.getenv("SENTRY_RELEASE", "contex@0.2.0")

        # Override from environment if set
        sample_rate = float(os.getenv("SENTRY_SAMPLE_RATE", str(sample_rate)))
        traces_sample_rate = float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", str(traces_sample_rate)))
        profiles_sample_rate = float(os.getenv("SENTRY_PROFILES_SAMPLE_RATE", str(profiles_sample_rate)))

        # Build integrations list
        integrations = [
            StarletteIntegration(transaction_style="endpoint"),
            FastApiIntegration(transaction_style="endpoint"),
            AsyncioIntegration(),
            LoggingIntegration(
                level=None,  # Don't capture log messages
                event_level=40  # Only capture ERROR and above
            ),
        ]

        # Add Redis integration if available
        try:
            integrations.append(RedisIntegration())
        except Exception:
            logger.debug("Redis integration not available for Sentry")

        # Initialize Sentry SDK
        sentry_sdk.init(
            dsn=dsn,
            environment=environment,
            release=release,
            sample_rate=sample_rate,
            traces_sample_rate=traces_sample_rate if enable_tracing else 0.0,
            profiles_sample_rate=profiles_sample_rate if enable_tracing else 0.0,
            integrations=integrations,
            debug=debug,

            # Don't send PII by default
            send_default_pii=False,

            # Attach stack traces to messages
            attach_stacktrace=True,

            # Before send hook for filtering/enriching events
            before_send=_before_send,

            # Before send transaction hook
            before_send_transaction=_before_send_transaction,
        )

        _sentry_initialized = True

        logger.info("Sentry initialized successfully",
                   environment=environment,
                   release=release,
                   sample_rate=sample_rate,
                   traces_sample_rate=traces_sample_rate if enable_tracing else 0.0)

        return True

    except ImportError:
        logger.warning("sentry-sdk not installed, skipping Sentry initialization")
        return False
    except Exception as e:
        logger.error("Failed to initialize Sentry", error=str(e))
        return False


def _before_send(event: Dict[str, Any], hint: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Process event before sending to Sentry.

    Used for:
    - Filtering out sensitive data
    - Enriching events with additional context
    - Dropping unwanted events

    Args:
        event: Sentry event dictionary
        hint: Additional context about the event

    Returns:
        Modified event or None to drop the event
    """
    # Filter out health check errors (too noisy)
    if "exception" in event:
        exception_values = event.get("exception", {}).get("values", [])
        for exc in exception_values:
            exc_type = exc.get("type", "")

            # Skip common transient errors
            if exc_type in ("ConnectionResetError", "BrokenPipeError"):
                return None

    # Scrub sensitive data from request
    if "request" in event:
        request = event["request"]

        # Remove sensitive headers
        if "headers" in request:
            sensitive_headers = ["authorization", "x-api-key", "cookie", "x-forwarded-for"]
            request["headers"] = {
                k: "[Filtered]" if k.lower() in sensitive_headers else v
                for k, v in request.get("headers", {}).items()
            }

        # Remove sensitive query params
        if "query_string" in request:
            # Keep query string but remove any api_key parameters
            request["query_string"] = "[Filtered]" if "api_key" in str(request.get("query_string", "")).lower() else request.get("query_string")

    return event


def _before_send_transaction(event: Dict[str, Any], hint: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Process transaction before sending to Sentry.

    Used for:
    - Filtering out health check transactions
    - Enriching transactions with additional data

    Args:
        event: Sentry transaction dictionary
        hint: Additional context

    Returns:
        Modified transaction or None to drop it
    """
    # Filter out health check transactions (too noisy)
    transaction = event.get("transaction", "")
    if any(path in transaction for path in ["/health", "/metrics", "/ready", "/live"]):
        return None

    return event


def capture_exception(exception: Exception, **extra_context):
    """
    Capture an exception and send to Sentry.

    Args:
        exception: The exception to capture
        **extra_context: Additional context to include

    Example:
        ```python
        try:
            risky_operation()
        except Exception as e:
            capture_exception(e, user_id="123", action="data_publish")
        ```
    """
    if not _sentry_initialized or not _sentry_sdk:
        logger.debug("Sentry not initialized, skipping exception capture")
        return

    with _sentry_sdk.push_scope() as scope:
        for key, value in extra_context.items():
            scope.set_extra(key, value)

        _sentry_sdk.capture_exception(exception)


def capture_message(message: str, level: str = "info", **extra_context):
    """
    Capture a message and send to Sentry.

    Args:
        message: The message to capture
        level: Message level (debug, info, warning, error, fatal)
        **extra_context: Additional context to include

    Example:
        ```python
        capture_message(
            "User exceeded rate limit",
            level="warning",
            user_id="123",
            limit=100
        )
        ```
    """
    if not _sentry_initialized or not _sentry_sdk:
        logger.debug("Sentry not initialized, skipping message capture")
        return

    with _sentry_sdk.push_scope() as scope:
        for key, value in extra_context.items():
            scope.set_extra(key, value)

        _sentry_sdk.capture_message(message, level=level)


def set_user(user_id: str, **user_data):
    """
    Set the current user context for Sentry.

    Args:
        user_id: User identifier
        **user_data: Additional user data (email, username, etc.)

    Example:
        ```python
        set_user(
            user_id="user-123",
            email="user@example.com",
            project_id="proj-456"
        )
        ```
    """
    if not _sentry_initialized or not _sentry_sdk:
        return

    _sentry_sdk.set_user({
        "id": user_id,
        **user_data
    })


def set_tag(key: str, value: str):
    """
    Set a tag for the current scope.

    Tags are indexed and searchable in Sentry.

    Args:
        key: Tag key
        value: Tag value

    Example:
        ```python
        set_tag("project_id", "proj-123")
        set_tag("api_version", "v1")
        ```
    """
    if not _sentry_initialized or not _sentry_sdk:
        return

    _sentry_sdk.set_tag(key, value)


def set_context(name: str, context: Dict[str, Any]):
    """
    Set additional context for the current scope.

    Args:
        name: Context name
        context: Context dictionary

    Example:
        ```python
        set_context("agent", {
            "agent_id": "agent-123",
            "project_id": "proj-456",
            "notification_method": "webhook"
        })
        ```
    """
    if not _sentry_initialized or not _sentry_sdk:
        return

    _sentry_sdk.set_context(name, context)


def start_transaction(name: str, op: str = "task"):
    """
    Start a new transaction for performance monitoring.

    Args:
        name: Transaction name
        op: Operation type

    Returns:
        Transaction context manager or None if Sentry not initialized

    Example:
        ```python
        with start_transaction("process_data", op="task") as transaction:
            with transaction.start_child(op="db", description="fetch data"):
                data = fetch_data()
            with transaction.start_child(op="process", description="transform"):
                result = transform(data)
        ```
    """
    if not _sentry_initialized or not _sentry_sdk:
        # Return a dummy context manager
        from contextlib import nullcontext
        return nullcontext()

    return _sentry_sdk.start_transaction(name=name, op=op)


def is_initialized() -> bool:
    """Check if Sentry is initialized"""
    return _sentry_initialized


def flush(timeout: float = 2.0):
    """
    Flush pending events to Sentry.

    Call this before application shutdown to ensure all events are sent.

    Args:
        timeout: Maximum time to wait for flush (seconds)
    """
    if _sentry_initialized and _sentry_sdk:
        _sentry_sdk.flush(timeout=timeout)
        logger.debug("Sentry events flushed")
