"""Contex Python SDK client implementation"""

import asyncio
from typing import Any, Dict, List, Optional, AsyncIterator
import httpx
from .models import (
    DataEvent,
    AgentRegistration,
    RegistrationResponse,
    QueryRequest,
    QueryResponse,
    APIKeyResponse,
    RateLimitInfo,
)
from .exceptions import (
    AuthenticationError,
    RateLimitError,
    ValidationError,
    NotFoundError,
    ServerError,
    NetworkError,
    TimeoutError as ContexTimeoutError,
)


class ContexAsyncClient:
    """
    Async Contex client for Python.
    
    Example:
        >>> client = ContexAsyncClient(url="http://localhost:8001", api_key="ck_...")
        >>> await client.publish(project_id="my-app", data_key="config", data={"env": "prod"})
    """
    
    def __init__(
        self,
        url: str = "http://localhost:8001",
        api_key: Optional[str] = None,
        timeout: float = 30.0,
        max_retries: int = 3,
    ):
        """
        Initialize Contex client.
        
        Args:
            url: Contex server URL
            api_key: API key for authentication
            timeout: Request timeout in seconds
            max_retries: Maximum number of retries for failed requests
        """
        self.url = url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.max_retries = max_retries
        self._client: Optional[httpx.AsyncClient] = None
    
    async def __aenter__(self):
        """Async context manager entry"""
        self._client = httpx.AsyncClient(timeout=self.timeout)
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit"""
        if self._client:
            await self._client.aclose()
    
    def _get_headers(self) -> Dict[str, str]:
        """Get request headers"""
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "contex-python/0.2.0",
        }
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        return headers
    
    async def _request(
        self,
        method: str,
        path: str,
        json: Optional[Dict] = None,
        params: Optional[Dict] = None,
    ) -> Any:
        """Make HTTP request with error handling and retries"""
        url = f"{self.url}{path}"
        headers = self._get_headers()
        
        # Create client if not in context manager
        if self._client is None:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                return await self._do_request(client, method, url, headers, json, params)
        else:
            return await self._do_request(self._client, method, url, headers, json, params)
    
    async def _do_request(
        self,
        client: httpx.AsyncClient,
        method: str,
        url: str,
        headers: Dict[str, str],
        json: Optional[Dict] = None,
        params: Optional[Dict] = None,
    ) -> Any:
        """Execute HTTP request with retries"""
        last_exception = None
        
        for attempt in range(self.max_retries):
            try:
                response = await client.request(
                    method=method,
                    url=url,
                    headers=headers,
                    json=json,
                    params=params,
                )
                
                # Handle response
                if response.status_code == 200 or response.status_code == 201:
                    return response.json()
                
                elif response.status_code == 401:
                    raise AuthenticationError("Invalid or missing API key")
                
                elif response.status_code == 403:
                    raise AuthenticationError("Insufficient permissions")
                
                elif response.status_code == 404:
                    raise NotFoundError(f"Resource not found: {url}")
                
                elif response.status_code == 422:
                    error_detail = response.json().get("detail", "Validation error")
                    raise ValidationError(f"Validation error: {error_detail}")
                
                elif response.status_code == 429:
                    retry_after = response.headers.get("Retry-After")
                    raise RateLimitError(
                        "Rate limit exceeded",
                        retry_after=int(retry_after) if retry_after else None
                    )
                
                elif response.status_code >= 500:
                    error_msg = response.json().get("detail", "Server error")
                    raise ServerError(f"Server error: {error_msg}")
                
                else:
                    raise ServerError(f"Unexpected status code: {response.status_code}")
            
            except httpx.TimeoutException as e:
                last_exception = ContexTimeoutError(f"Request timed out: {e}")
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(2 ** attempt)  # Exponential backoff
                    continue
            
            except httpx.RequestError as e:
                last_exception = NetworkError(f"Network error: {e}")
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(2 ** attempt)
                    continue
        
        # All retries failed
        if last_exception:
            raise last_exception
    
    # ========================================================================
    # Data Publishing
    # ========================================================================
    
    async def publish(
        self,
        project_id: str,
        data_key: str,
        data: Any,
        data_format: str = "json",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Publish data to Contex.
        
        Args:
            project_id: Project identifier
            data_key: Unique key for this data
            data: Data payload (any JSON-serializable type)
            data_format: Data format (json, yaml, toml, text)
            metadata: Optional metadata
        
        Returns:
            Response with status and sequence number
        
        Example:
            >>> await client.publish(
            ...     project_id="my-app",
            ...     data_key="config",
            ...     data={"env": "prod", "debug": False}
            ... )
        """
        event = DataEvent(
            project_id=project_id,
            data_key=data_key,
            data=data,
            data_format=data_format,
            metadata=metadata,
        )
        return await self._request("POST", "/api/v1/data/publish", json=event.model_dump())
    
    # ========================================================================
    # Agent Management
    # ========================================================================
    
    async def register_agent(
        self,
        agent_id: str,
        project_id: str,
        data_needs: List[str],
        notification_method: str = "redis",
        webhook_url: Optional[str] = None,
        webhook_secret: Optional[str] = None,
        last_seen_sequence: str = "0",
    ) -> RegistrationResponse:
        """
        Register an agent with Contex.
        
        Args:
            agent_id: Unique agent identifier
            project_id: Project identifier
            data_needs: List of data needs in natural language
            notification_method: Notification method (redis or webhook)
            webhook_url: Webhook URL (if using webhook notifications)
            webhook_secret: Webhook secret for HMAC verification
            last_seen_sequence: Last seen sequence number
        
        Returns:
            Registration response with matched data
        
        Example:
            >>> response = await client.register_agent(
            ...     agent_id="code-reviewer",
            ...     project_id="my-app",
            ...     data_needs=["coding standards", "test requirements"]
            ... )
            >>> print(f"Matched {len(response.matched_data)} items")
        """
        registration = AgentRegistration(
            agent_id=agent_id,
            project_id=project_id,
            data_needs=data_needs,
            notification_method=notification_method,
            webhook_url=webhook_url,
            webhook_secret=webhook_secret,
            last_seen_sequence=last_seen_sequence,
        )
        result = await self._request("POST", "/api/v1/agents/register", json=registration.model_dump())
        return RegistrationResponse(**result)
    
    async def unregister_agent(self, agent_id: str) -> Dict[str, Any]:
        """
        Unregister an agent.
        
        Args:
            agent_id: Agent identifier
        
        Returns:
            Response confirming unregistration
        """
        return await self._request("DELETE", f"/api/v1/agents/{agent_id}")
    
    async def get_agent_status(self, agent_id: str) -> Dict[str, Any]:
        """
        Get agent status.
        
        Args:
            agent_id: Agent identifier
        
        Returns:
            Agent status information
        """
        return await self._request("GET", f"/api/v1/agents/{agent_id}/status")
    
    # ========================================================================
    # Querying
    # ========================================================================
    
    async def query(
        self,
        project_id: str,
        query: str,
        max_results: int = 10,
    ) -> QueryResponse:
        """
        Query for relevant data.
        
        Args:
            project_id: Project identifier
            query: Query string in natural language
            max_results: Maximum number of results
        
        Returns:
            Query response with matched data
        
        Example:
            >>> response = await client.query(
            ...     project_id="my-app",
            ...     query="authentication configuration"
            ... )
            >>> for result in response.results:
            ...     print(f"{result.data_key}: {result.similarity_score}")
        """
        request = QueryRequest(
            project_id=project_id,
            query=query,
            max_results=max_results,
        )
        result = await self._request("POST", "/api/v1/query", json=request.model_dump())
        return QueryResponse(**result)
    
    # ========================================================================
    # API Key Management
    # ========================================================================
    
    async def create_api_key(self, name: str) -> APIKeyResponse:
        """
        Create a new API key.
        
        Args:
            name: Name for the API key
        
        Returns:
            API key response with key details
        
        Note:
            The API key is only returned once. Store it securely!
        """
        result = await self._request("POST", f"/api/v1/auth/keys?name={name}")
        return APIKeyResponse(**result)
    
    async def list_api_keys(self) -> List[Dict[str, Any]]:
        """List all API keys (without the actual key values)"""
        return await self._request("GET", "/api/v1/auth/keys")
    
    async def revoke_api_key(self, key_id: str) -> Dict[str, Any]:
        """
        Revoke an API key.
        
        Args:
            key_id: Key identifier
        """
        return await self._request("DELETE", f"/api/v1/auth/keys/{key_id}")
    
    # ========================================================================
    # Health & Status
    # ========================================================================
    
    async def health(self) -> Dict[str, Any]:
        """Get comprehensive health status"""
        return await self._request("GET", "/api/v1/health")
    
    async def ready(self) -> Dict[str, Any]:
        """Get readiness status"""
        return await self._request("GET", "/api/v1/health/ready")
    
    async def rate_limit_status(self) -> RateLimitInfo:
        """Get current rate limit status"""
        result = await self._request("GET", "/api/v1/rate-limit/status")
        return RateLimitInfo(**result)


class ContexClient(ContexAsyncClient):
    """
    Synchronous Contex client (wrapper around async client).
    
    Example:
        >>> client = ContexClient(url="http://localhost:8001", api_key="ck_...")
        >>> client.publish(project_id="my-app", data_key="config", data={"env": "prod"})
    """
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._loop: Optional[asyncio.AbstractEventLoop] = None
    
    def _run_async(self, coro):
        """Run async coroutine in sync context"""
        if self._loop is None:
            self._loop = asyncio.new_event_loop()
        return self._loop.run_until_complete(coro)
    
    def publish(self, *args, **kwargs):
        """Synchronous publish"""
        return self._run_async(super().publish(*args, **kwargs))
    
    def register_agent(self, *args, **kwargs):
        """Synchronous register_agent"""
        return self._run_async(super().register_agent(*args, **kwargs))
    
    def unregister_agent(self, *args, **kwargs):
        """Synchronous unregister_agent"""
        return self._run_async(super().unregister_agent(*args, **kwargs))
    
    def query(self, *args, **kwargs):
        """Synchronous query"""
        return self._run_async(super().query(*args, **kwargs))
    
    def health(self, *args, **kwargs):
        """Synchronous health"""
        return self._run_async(super().health(*args, **kwargs))
    
    def create_api_key(self, *args, **kwargs):
        """Synchronous create_api_key"""
        return self._run_async(super().create_api_key(*args, **kwargs))
    
    def __del__(self):
        """Cleanup event loop"""
        if self._loop:
            self._loop.close()
