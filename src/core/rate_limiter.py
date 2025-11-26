"""Rate limiting using Redis sliding window algorithm"""

import time
from typing import Optional
from redis.asyncio import Redis
from fastapi import Request, HTTPException
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware


def _record_rate_limited_event(
    api_key_id: str,
    endpoint: str,
    limit: int,
    actor_ip: str,
    tenant_id: str = None,
):
    """Record rate limit event in audit log (async fire-and-forget)"""
    import asyncio
    try:
        from src.core.audit import audit_log, AuditEventType, AuditEventSeverity

        async def _log():
            await audit_log(
                event_type=AuditEventType.SECURITY_RATE_LIMITED,
                action=f"Rate limit exceeded on {endpoint}",
                actor_id=api_key_id,
                actor_type="api_key" if api_key_id != "anonymous" else None,
                actor_ip=actor_ip,
                tenant_id=tenant_id,
                endpoint=endpoint,
                severity=AuditEventSeverity.WARNING,
                details={"limit": limit, "endpoint": endpoint},
            )

        # Create task to run async without blocking
        asyncio.create_task(_log())
    except Exception:
        pass  # Don't fail request if audit fails


class RateLimitConfig:
    """Rate limit configuration for different operations"""
    
    # Default limits (requests per minute)
    PUBLISH_DATA = 100
    REGISTER_AGENT = 50
    QUERY = 200
    ADMIN = 20
    DEFAULT = 60
    
    # Window size in seconds
    WINDOW_SIZE = 60


class RateLimiter:
    """Redis-based rate limiter using sliding window algorithm"""
    
    def __init__(self, redis: Redis):
        self.redis = redis
        
    async def check_rate_limit(
        self,
        key: str,
        limit: int,
        window: int = RateLimitConfig.WINDOW_SIZE
    ) -> tuple[bool, dict]:
        """
        Check if request is within rate limit.
        
        Args:
            key: Unique identifier for rate limit (e.g., "api_key:publish:project_id")
            limit: Maximum requests allowed in window
            window: Time window in seconds
            
        Returns:
            Tuple of (allowed, info_dict) where info_dict contains:
            - remaining: Requests remaining in window
            - reset: Unix timestamp when window resets
            - limit: The rate limit
        """
        now = time.time()
        window_start = now - window
        
        # Redis key for this rate limit
        redis_key = f"ratelimit:{key}"
        
        # Use Redis sorted set with timestamps as scores
        pipe = self.redis.pipeline()
        
        # Remove old entries outside the window
        pipe.zremrangebyscore(redis_key, 0, window_start)
        
        # Count requests in current window
        pipe.zcard(redis_key)
        
        # Add current request
        pipe.zadd(redis_key, {str(now): now})
        
        # Set expiry on the key
        pipe.expire(redis_key, window + 1)
        
        results = await pipe.execute()
        current_count = results[1]  # Count before adding current request
        
        # Calculate remaining and reset time
        remaining = max(0, limit - current_count - 1)
        reset = int(now + window)
        
        allowed = current_count < limit
        
        info = {
            "limit": limit,
            "remaining": remaining,
            "reset": reset,
            "retry_after": window if not allowed else None
        }
        
        return allowed, info


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Middleware to enforce rate limits on API endpoints"""

    def __init__(self, app):
        super().__init__(app)

        # Define rate limits per endpoint pattern
        self.endpoint_limits = {
            "/api/publish": RateLimitConfig.PUBLISH_DATA,
            "/api/register": RateLimitConfig.REGISTER_AGENT,
            "/api/query": RateLimitConfig.QUERY,
            "/auth/": RateLimitConfig.ADMIN,
            "/admin/": RateLimitConfig.ADMIN,
        }

    def get_rate_limit_for_path(self, path: str) -> int:
        """Get rate limit for a given path"""
        for pattern, limit in self.endpoint_limits.items():
            if path.startswith(pattern):
                return limit
        return RateLimitConfig.DEFAULT

    async def dispatch(self, request: Request, call_next):
        # Skip rate limiting for health checks and docs
        if request.url.path in ["/health", "/", "/docs", "/openapi.json", "/redoc"]:
            return await call_next(request)

        # Get Redis from app state
        redis = request.app.state.redis
        limiter = RateLimiter(redis)

        # Get API key from request (set by APIKeyMiddleware)
        api_key = request.headers.get("X-API-Key", "anonymous")

        # Build rate limit key
        path = request.url.path
        limit = self.get_rate_limit_for_path(path)

        # Include project_id in key if available (for project-level limits)
        project_id = request.path_params.get("project_id") or request.query_params.get("project_id")
        if project_id:
            rate_key = f"{api_key}:{path}:{project_id}"
        else:
            rate_key = f"{api_key}:{path}"

        # Check rate limit
        allowed, info = await limiter.check_rate_limit(rate_key, limit)

        if not allowed:
            # Record audit event for rate limiting
            actor_ip = request.client.host if request.client else None
            tenant_id = getattr(request.state, 'tenant_id', None)
            _record_rate_limited_event(
                api_key_id=api_key if api_key != "anonymous" else None,
                endpoint=path,
                limit=limit,
                actor_ip=actor_ip,
                tenant_id=tenant_id,
            )

            # Return 429 Too Many Requests
            return JSONResponse(
                status_code=429,
                content={
                    "error": "rate_limit_exceeded",
                    "message": f"Rate limit exceeded. Try again in {info['retry_after']} seconds.",
                    "limit": info["limit"],
                    "reset": info["reset"]
                },
                headers={
                    "X-RateLimit-Limit": str(info["limit"]),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(info["reset"]),
                    "Retry-After": str(info["retry_after"])
                }
            )

        # Add rate limit headers to response
        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(info["limit"])
        response.headers["X-RateLimit-Remaining"] = str(info["remaining"])
        response.headers["X-RateLimit-Reset"] = str(info["reset"])

        return response


async def get_rate_limit_status(redis: Redis, api_key: str) -> dict:
    """Get current rate limit status for an API key"""
    limiter = RateLimiter(redis)
    
    status = {}
    for endpoint, limit in RateLimitMiddleware(None, redis).endpoint_limits.items():
        rate_key = f"{api_key}:{endpoint}"
        allowed, info = await limiter.check_rate_limit(rate_key, limit)
        
        status[endpoint] = {
            "limit": info["limit"],
            "remaining": info["remaining"],
            "reset": info["reset"],
            "allowed": allowed
        }
    
    return status
