"""Webhook notification dispatcher for agent updates"""

import asyncio
import hashlib
import hmac
import json
from typing import Dict, Any, Optional
import httpx


class WebhookDispatcher:
    """
    Dispatches updates to agents via HTTP webhooks.

    Features:
    - HMAC signature verification
    - Retry logic with exponential backoff
    - Timeout handling
    - Error logging
    """

    def __init__(
        self, timeout: float = 5.0, max_retries: int = 3, retry_delay: float = 1.0
    ):
        """
        Initialize webhook dispatcher.

        Args:
            timeout: Request timeout in seconds
            max_retries: Maximum retry attempts
            retry_delay: Initial delay between retries (doubles each retry)
        """
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_delay = retry_delay

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
        Send webhook with retry logic.

        Args:
            url: Webhook URL to POST to
            payload: Data to send
            secret: Optional secret for HMAC signature
            event_type: Type of event being sent

        Returns:
            True if successful, False otherwise
        """
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

        # Retry logic
        for attempt in range(self.max_retries):
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.post(
                        url, content=payload_str, headers=headers, timeout=self.timeout
                    )

                    # Success on 2xx status codes
                    if 200 <= response.status_code < 300:
                        print(f"[WebhookDispatcher] ✓ Webhook delivered to {url}")
                        return True

                    # Log non-2xx responses
                    print(
                        f"[WebhookDispatcher] ⚠ Webhook returned {response.status_code}: {url}"
                    )

                    # Don't retry on 4xx errors (client errors)
                    if 400 <= response.status_code < 500:
                        print(f"[WebhookDispatcher] ✗ Client error, not retrying")
                        return False

            except httpx.TimeoutException:
                print(
                    f"[WebhookDispatcher] ⚠ Timeout on attempt {attempt + 1}/{self.max_retries}"
                )

            except httpx.RequestError as e:
                print(f"[WebhookDispatcher] ⚠ Request error: {e}")

            except Exception as e:
                print(f"[WebhookDispatcher] ⚠ Unexpected error: {e}")

            # Exponential backoff before retry
            if attempt < self.max_retries - 1:
                delay = self.retry_delay * (2**attempt)
                print(f"[WebhookDispatcher] Retrying in {delay}s...")
                await asyncio.sleep(delay)

        print(
            f"[WebhookDispatcher] ✗ Failed to deliver webhook after {self.max_retries} attempts"
        )
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
