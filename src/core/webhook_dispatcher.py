"""Webhook notification dispatcher for agent updates"""

import asyncio
import hashlib
import hmac
import json
import random
from typing import Dict, Any, Optional
import httpx
from src.core.circuit_breaker import get_circuit_breaker, CircuitBreakerOpen, CircuitBreakerConfig
from src.core.logging import get_logger

logger = get_logger(__name__)

# Lazy import metrics to avoid circular dependencies
_metrics_imported = False
_webhook_retries_total = None
_webhook_retry_delay_seconds = None


def _import_metrics():
    """Lazy import metrics to avoid circular dependencies"""
    global _metrics_imported, _webhook_retries_total, _webhook_retry_delay_seconds
    if not _metrics_imported:
        try:
            from src.core.metrics import webhook_retries_total, webhook_retry_delay_seconds
            _webhook_retries_total = webhook_retries_total
            _webhook_retry_delay_seconds = webhook_retry_delay_seconds
            _metrics_imported = True
        except ImportError:
            pass


class WebhookDispatcher:
    """
    Dispatches updates to agents via HTTP webhooks.

    Features:
    - HMAC signature verification
    - Retry logic with exponential backoff and jitter
    - Timeout handling
    - Structured logging with metrics
    - Circuit breaker for reliability
    """

    def __init__(
        self, timeout: float = 5.0, max_retries: int = 3, retry_delay: float = 1.0,
        circuit_breaker_config: Optional[CircuitBreakerConfig] = None
    ):
        """
        Initialize webhook dispatcher.

        Args:
            timeout: Request timeout in seconds
            max_retries: Maximum retry attempts
            retry_delay: Initial delay between retries (with exponential backoff)
            circuit_breaker_config: Optional circuit breaker configuration
        """
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.circuit_breaker_config = circuit_breaker_config or CircuitBreakerConfig()

    def _calculate_delay(self, attempt: int) -> float:
        """
        Calculate delay with exponential backoff and jitter.

        Args:
            attempt: Current attempt number (0-indexed)

        Returns:
            Delay in seconds with jitter
        """
        # Exponential backoff: base_delay * (2 ^ attempt)
        delay = self.retry_delay * (2 ** attempt)

        # Cap at 30 seconds max
        delay = min(delay, 30.0)

        # Add jitter (±25% of delay) to prevent thundering herd
        jitter = delay * 0.25
        delay += random.uniform(-jitter, jitter)

        return max(0, delay)

    def _generate_signature(self, payload: str, secret: str) -> str:
        """
        Generate HMAC-SHA256 signature for payload.

        Args:
            payload: JSON string to sign
            secret: Shared secret key

        Returns:
            Hex-encoded HMAC signature
        """
        return hmac.new(
            secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
        ).hexdigest()

    async def send_webhook(
        self,
        url: str,
        payload: Dict[str, Any],
        secret: Optional[str] = None,
        event_type: str = "data_update",
    ) -> bool:
        """
        Send webhook with retry logic and circuit breaker.

        Args:
            url: Webhook URL to POST to
            payload: Data to send
            secret: Optional secret for HMAC signature
            event_type: Type of event being sent

        Returns:
            True if successful, False otherwise
        """
        # Get circuit breaker for this URL
        breaker = get_circuit_breaker(f"webhook:{url}", self.circuit_breaker_config)
        
        # Check if circuit breaker allows execution
        try:
            with breaker:
                return await self._send_webhook_internal(url, payload, secret, event_type)
        except CircuitBreakerOpen:
            print(f"[WebhookDispatcher] ⚠ Circuit breaker OPEN for {url}, skipping")
            return False
    
    async def _send_webhook_internal(
        self,
        url: str,
        payload: Dict[str, Any],
        secret: Optional[str] = None,
        event_type: str = "data_update",
    ) -> bool:
        """Internal webhook sending logic with exponential backoff and jitter"""
        payload_str = json.dumps(payload)
        headers = {
            "Content-Type": "application/json",
            "X-Contex-Event": event_type,
            "User-Agent": "Contex-Webhook/0.2.0",
        }

        # Add signature if secret provided
        if secret:
            signature = self._generate_signature(payload_str, secret)
            headers["X-Contex-Signature"] = f"sha256={signature}"

        last_exception = None

        # Retry logic with exponential backoff and jitter
        for attempt in range(self.max_retries):
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.post(
                        url, content=payload_str, headers=headers, timeout=self.timeout
                    )

                    # Success on 2xx status codes
                    if 200 <= response.status_code < 300:
                        if attempt > 0:
                            logger.info("Webhook delivered after retry",
                                       url=url,
                                       event_type=event_type,
                                       attempt=attempt + 1,
                                       max_attempts=self.max_retries)
                        else:
                            logger.debug("Webhook delivered", url=url, event_type=event_type)
                        return True

                    # Log non-2xx responses
                    logger.warning("Webhook returned non-2xx status",
                                 url=url,
                                 status_code=response.status_code,
                                 event_type=event_type,
                                 attempt=attempt + 1)

                    # Don't retry on 4xx errors (client errors)
                    if 400 <= response.status_code < 500:
                        logger.error("Webhook client error, not retrying",
                                   url=url,
                                   status_code=response.status_code,
                                   event_type=event_type)
                        return False

            except httpx.TimeoutException as e:
                last_exception = e
                logger.warning("Webhook timeout",
                             url=url,
                             event_type=event_type,
                             attempt=attempt + 1,
                             max_attempts=self.max_retries,
                             timeout=self.timeout)

            except httpx.RequestError as e:
                last_exception = e
                logger.warning("Webhook request error",
                             url=url,
                             event_type=event_type,
                             attempt=attempt + 1,
                             error=str(e),
                             error_type=type(e).__name__)

            except Exception as e:
                last_exception = e
                logger.warning("Webhook unexpected error",
                             url=url,
                             event_type=event_type,
                             attempt=attempt + 1,
                             error=str(e),
                             error_type=type(e).__name__)

            # Exponential backoff with jitter before retry
            if attempt < self.max_retries - 1:
                delay = self._calculate_delay(attempt)

                # Record retry metrics
                _import_metrics()
                if _webhook_retries_total:
                    _webhook_retries_total.inc()
                if _webhook_retry_delay_seconds:
                    _webhook_retry_delay_seconds.observe(delay)

                logger.info("Retrying webhook",
                          url=url,
                          event_type=event_type,
                          attempt=attempt + 1,
                          next_attempt=attempt + 2,
                          delay=f"{delay:.2f}s")
                await asyncio.sleep(delay)

        logger.error("Webhook delivery failed after all retries",
                   url=url,
                   event_type=event_type,
                   max_attempts=self.max_retries,
                   last_error=str(last_exception) if last_exception else "unknown")
        return False

    async def send_initial_context(
        self,
        url: str,
        agent_id: str,
        context: Dict[str, Any],
        secret: Optional[str] = None,
    ) -> bool:
        """
        Send initial context to agent after registration.

        Args:
            url: Webhook URL
            agent_id: Agent identifier
            context: Initial matched context
            secret: Optional HMAC secret

        Returns:
            True if successful
        """
        payload = {"type": "initial_context", "agent_id": agent_id, "context": context}

        return await self.send_webhook(
            url=url, payload=payload, secret=secret, event_type="initial_context"
        )

    async def send_data_update(
        self,
        url: str,
        agent_id: str,
        sequence: str,
        data_key: str,
        data: Dict[str, Any],
        secret: Optional[str] = None,
    ) -> bool:
        """
        Send data update notification to agent.

        Args:
            url: Webhook URL
            agent_id: Agent identifier
            sequence: Event sequence number
            data_key: Key of updated data
            data: Updated data
            secret: Optional HMAC secret

        Returns:
            True if successful
        """
        payload = {
            "type": "data_update",
            "agent_id": agent_id,
            "sequence": sequence,
            "data_key": data_key,
            "data": data,
        }

        return await self.send_webhook(
            url=url, payload=payload, secret=secret, event_type="data_update"
        )

    async def send_event(
        self,
        url: str,
        agent_id: str,
        sequence: str,
        event_type: str,
        event_data: Dict[str, Any],
        secret: Optional[str] = None,
    ) -> bool:
        """
        Send event notification to agent.

        Args:
            url: Webhook URL
            agent_id: Agent identifier
            sequence: Event sequence number
            event_type: Type of event
            event_data: Event data
            secret: Optional HMAC secret

        Returns:
            True if successful
        """
        payload = {
            "type": "event",
            "agent_id": agent_id,
            "sequence": sequence,
            "event_type": event_type,
            "data": event_data,
        }

        return await self.send_webhook(
            url=url, payload=payload, secret=secret, event_type="event"
        )


# Helper function to verify webhook signatures (for agent implementations)
def verify_webhook_signature(payload: str, signature_header: str, secret: str) -> bool:
    """
    Verify HMAC signature from webhook request.

    This is a utility function for agents to verify webhooks from Contex.

    Args:
        payload: Raw request body (JSON string)
        signature_header: Value of X-Contex-Signature header
        secret: Shared secret key

    Returns:
        True if signature is valid

    Example:
        ```python
        @app.post("/webhook")
        async def handle_webhook(request: Request):
            body = await request.body()
            signature = request.headers.get("X-Contex-Signature")

            if not verify_webhook_signature(body.decode(), signature, SECRET):
                raise HTTPException(401, "Invalid signature")

            # Process webhook...
        ```
    """
    if not signature_header or not signature_header.startswith("sha256="):
        return False

    expected_signature = signature_header.split("sha256=")[1]

    computed_signature = hmac.new(
        secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
    ).hexdigest()

    return hmac.compare_digest(expected_signature, computed_signature)
