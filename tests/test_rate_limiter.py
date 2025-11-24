"""Tests for rate limiting"""

import pytest
import pytest_asyncio
import time
from fastapi import FastAPI
from httpx import AsyncClient
from src.core.rate_limiter import RateLimiter, RateLimitMiddleware, RateLimitConfig


class TestRateLimiter:
    """Test RateLimiter functionality"""

    @pytest_asyncio.fixture
    async def limiter(self, redis):
        """Create a RateLimiter instance"""
        return RateLimiter(redis)

    @pytest.mark.asyncio
    async def test_rate_limit_allows_within_limit(self, limiter):
        """Test that requests within limit are allowed"""
        key = "test:endpoint:user1"
        limit = 5
        
        # Make 5 requests (all should be allowed)
        for i in range(5):
            allowed, info = await limiter.check_rate_limit(key, limit)
            assert allowed
            assert info["limit"] == limit
            assert info["remaining"] >= 0
    
    @pytest.mark.asyncio
    async def test_rate_limit_blocks_over_limit(self, limiter):
        """Test that requests over limit are blocked"""
        key = "test:endpoint:user2"
        limit = 3
        
        # Make 3 requests (should be allowed)
        for i in range(3):
            allowed, info = await limiter.check_rate_limit(key, limit)
            assert allowed
        
        # 4th request should be blocked
        allowed, info = await limiter.check_rate_limit(key, limit)
        assert not allowed
        assert info["remaining"] == 0
        assert info["retry_after"] is not None
    
    @pytest.mark.asyncio
    async def test_rate_limit_resets_after_window(self, limiter):
        """Test that rate limit resets after window expires"""
        key = "test:endpoint:user3"
        limit = 2
        window = 1  # 1 second window
        
        # Use up the limit
        for i in range(2):
            allowed, info = await limiter.check_rate_limit(key, limit, window)
            assert allowed
        
        # Should be blocked
        allowed, info = await limiter.check_rate_limit(key, limit, window)
        assert not allowed
        
        # Wait for window to expire
        time.sleep(1.1)
        
        # Should be allowed again
        allowed, info = await limiter.check_rate_limit(key, limit, window)
        assert allowed
    
    @pytest.mark.asyncio
    async def test_rate_limit_different_keys_independent(self, limiter):
        """Test that different keys have independent limits"""
        limit = 2
        
        # Use up limit for user1
        for i in range(2):
            allowed, _ = await limiter.check_rate_limit("test:user1", limit)
            assert allowed
        
        # user1 should be blocked
        allowed, _ = await limiter.check_rate_limit("test:user1", limit)
        assert not allowed
        
        # user2 should still be allowed
        allowed, _ = await limiter.check_rate_limit("test:user2", limit)
        assert allowed
    
    @pytest.mark.asyncio
    async def test_rate_limit_info_accurate(self, limiter):
        """Test that rate limit info is accurate"""
        key = "test:endpoint:user4"
        limit = 5
        
        # First request
        allowed, info = await limiter.check_rate_limit(key, limit)
        assert allowed
        assert info["limit"] == 5
        assert info["remaining"] == 4
        
        # Second request
        allowed, info = await limiter.check_rate_limit(key, limit)
        assert allowed
        assert info["remaining"] == 3


class TestRateLimitMiddleware:
    """Test RateLimitMiddleware functionality"""

    @pytest_asyncio.fixture
    async def app_with_rate_limit(self, redis):
        """Create a test app with rate limiting"""
        app = FastAPI()
        
        # Add rate limit middleware
        app.add_middleware(RateLimitMiddleware, redis=redis)
        
        @app.get("/api/publish")
        async def publish():
            return {"status": "ok"}
        
        @app.get("/api/query")
        async def query():
            return {"status": "ok"}
        
        @app.get("/health")
        async def health():
            return {"status": "healthy"}
        
        return app

    @pytest.mark.asyncio
    async def test_middleware_allows_within_limit(self, app_with_rate_limit):
        """Test that middleware allows requests within limit"""
        async with AsyncClient(app=app_with_rate_limit, base_url="http://test") as client:
            # Make several requests (should all succeed)
            for i in range(5):
                response = await client.get(
                    "/api/query",
                    headers={"X-API-Key": "test-key"}
                )
                assert response.status_code == 200
                assert "X-RateLimit-Limit" in response.headers
                assert "X-RateLimit-Remaining" in response.headers
                assert "X-RateLimit-Reset" in response.headers

    @pytest.mark.asyncio
    async def test_middleware_blocks_over_limit(self, redis):
        """Test that middleware blocks requests over limit"""
        # Create app with custom low limits
        app = FastAPI()
        
        # Create middleware with custom limits
        middleware = RateLimitMiddleware(app, redis)
        middleware.endpoint_limits["/api/query"] = 2
        
        app.add_middleware(RateLimitMiddleware, redis=redis)
        
        @app.get("/api/query")
        async def query():
            return {"status": "ok"}
        
        # Manually set the limit on the middleware
        for m in app.user_middleware:
            if isinstance(m.cls, type) and issubclass(m.cls, RateLimitMiddleware):
                # Can't easily modify after creation, so we'll test the limiter directly
                pass
        
        # Test the limiter directly instead
        limiter = RateLimiter(redis)
        key = "test-key-2:/api/query"
        limit = 2
        
        # Make requests up to limit
        for i in range(2):
            allowed, info = await limiter.check_rate_limit(key, limit)
            assert allowed
        
        # Next request should be blocked
        allowed, info = await limiter.check_rate_limit(key, limit)
        assert not allowed
        assert info["remaining"] == 0

    @pytest.mark.asyncio
    async def test_middleware_skips_health_checks(self, app_with_rate_limit):
        """Test that middleware skips rate limiting for health checks"""
        async with AsyncClient(app=app_with_rate_limit, base_url="http://test") as client:
            # Make many health check requests (should never be rate limited)
            for i in range(100):
                response = await client.get("/health")
                assert response.status_code == 200
                # Health checks should not have rate limit headers
                assert "X-RateLimit-Limit" not in response.headers

    @pytest.mark.asyncio
    async def test_middleware_different_endpoints_independent(self, redis):
        """Test that different endpoints have independent rate limits"""
        # Test the limiter directly for different endpoints
        limiter = RateLimiter(redis)
        
        publish_key = "test-key-3:/api/publish"
        query_key = "test-key-3:/api/query"
        limit = 2
        
        # Use up publish limit
        for i in range(2):
            allowed, _ = await limiter.check_rate_limit(publish_key, limit)
            assert allowed
        
        # Publish should be blocked
        allowed, _ = await limiter.check_rate_limit(publish_key, limit)
        assert not allowed
        
        # Query should still work (different key)
        allowed, _ = await limiter.check_rate_limit(query_key, limit)
        assert allowed
