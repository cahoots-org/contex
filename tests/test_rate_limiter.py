"""Tests for rate limiting with PostgreSQL"""

import httpx
import pytest
import pytest_asyncio
import time
from fastapi import FastAPI
from httpx import AsyncClient
from src.core.rate_limiter import RateLimiter, RateLimitMiddleware, RateLimitConfig


class TestRateLimiter:
    """Test RateLimiter functionality with PostgreSQL"""

    @pytest_asyncio.fixture
    async def limiter(self, db):
        """Create a RateLimiter instance"""
        return RateLimiter(db)

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
    async def app_with_rate_limit(self, db):
        """Create a test app with rate limiting"""
        app = FastAPI()
        app.state.db = db  # Make DB available via app state

        # Add rate limit middleware
        app.add_middleware(RateLimitMiddleware)

        @app.get("/api/v1/publish")
        async def publish():
            return {"status": "ok"}

        @app.get("/api/v1/query")
        async def query():
            return {"status": "ok"}

        @app.get("/health")
        async def health():
            return {"status": "healthy"}

        return app

    @pytest.mark.asyncio
    async def test_middleware_allows_within_limit(self, app_with_rate_limit):
        """Test that middleware allows requests within limit"""
        async with AsyncClient(transport=httpx.ASGITransport(app=app_with_rate_limit), base_url="http://test") as client:
            # Make several requests (should all succeed)
            for i in range(5):
                response = await client.get(
                    "/api/v1/query",
                    headers={"X-API-Key": "test-key"}
                )
                assert response.status_code == 200
                assert "X-RateLimit-Limit" in response.headers
                assert "X-RateLimit-Remaining" in response.headers
                assert "X-RateLimit-Reset" in response.headers

    @pytest.mark.asyncio
    async def test_middleware_blocks_over_limit(self, db):
        """Test that middleware blocks requests over limit"""
        # Test the limiter directly to verify rate limit blocking
        limiter = RateLimiter(db)
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
        async with AsyncClient(transport=httpx.ASGITransport(app=app_with_rate_limit), base_url="http://test") as client:
            # Make many health check requests (should never be rate limited)
            for i in range(100):
                response = await client.get("/health")
                assert response.status_code == 200
                # Health checks should not have rate limit headers
                assert "X-RateLimit-Limit" not in response.headers

    @pytest.mark.asyncio
    async def test_middleware_different_endpoints_independent(self, db):
        """Test that different endpoints have independent rate limits"""
        # Test the limiter directly for different endpoints
        limiter = RateLimiter(db)

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


class TestRateLimitConfig:
    """Test env-configurable rate limit configuration"""

    def test_defaults(self, monkeypatch):
        """Config falls back to sane defaults when no env vars are set"""
        for var in (
            "RATE_LIMIT_PUBLISH",
            "RATE_LIMIT_REGISTER",
            "RATE_LIMIT_QUERY",
            "RATE_LIMIT_ADMIN",
            "RATE_LIMIT_DEFAULT",
            "RATE_LIMIT_WINDOW",
        ):
            monkeypatch.delenv(var, raising=False)

        config = RateLimitConfig.from_env()
        assert config.query == 200
        assert config.publish == 100
        assert config.register == 50
        assert config.admin == 20
        assert config.default == 60
        assert config.window == 60

    def test_env_override(self, monkeypatch):
        """Env vars override the defaults"""
        monkeypatch.setenv("RATE_LIMIT_QUERY", "7")
        monkeypatch.setenv("RATE_LIMIT_WINDOW", "3")

        config = RateLimitConfig.from_env()
        assert config.query == 7
        assert config.window == 3


class TestRateLimitMiddlewareEnforcement:
    """Test wired-up middleware enforcement, IP keying, and env overrides"""

    def _build_app(self, db):
        app = FastAPI()
        app.state.db = db
        app.add_middleware(RateLimitMiddleware)

        @app.get("/api/v1/query")
        async def query():
            return {"status": "ok"}

        @app.get("/health")
        async def health():
            return {"status": "healthy"}

        @app.get("/docs")
        async def docs():
            return {"status": "docs"}

        return app

    @pytest.mark.asyncio
    async def test_returns_429_when_limit_exceeded(self, db, monkeypatch):
        """The request past the configured limit gets a 429 with headers"""
        monkeypatch.setenv("RATE_LIMIT_QUERY", "3")
        monkeypatch.setenv("RATE_LIMIT_WINDOW", "60")
        app = self._build_app(db)

        async with AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            for _ in range(3):
                response = await client.get("/api/v1/query", headers={"X-API-Key": "k"})
                assert response.status_code == 200
                assert "X-RateLimit-Limit" in response.headers
                assert response.headers["X-RateLimit-Limit"] == "3"

            blocked = await client.get("/api/v1/query", headers={"X-API-Key": "k"})
            assert blocked.status_code == 429
            assert blocked.headers["X-RateLimit-Limit"] == "3"
            assert blocked.headers["X-RateLimit-Remaining"] == "0"
            assert "X-RateLimit-Reset" in blocked.headers
            assert "Retry-After" in blocked.headers

    @pytest.mark.asyncio
    async def test_keyed_by_ip_when_no_api_key(self, db, monkeypatch):
        """Without an API key, distinct client IPs get independent buckets"""
        monkeypatch.setenv("RATE_LIMIT_QUERY", "2")
        app = self._build_app(db)

        transport_a = httpx.ASGITransport(app=app, client=("10.0.0.1", 1234))
        transport_b = httpx.ASGITransport(app=app, client=("10.0.0.2", 1234))

        async with AsyncClient(transport=transport_a, base_url="http://test") as client_a:
            for _ in range(2):
                assert (await client_a.get("/api/v1/query")).status_code == 200
            assert (await client_a.get("/api/v1/query")).status_code == 429

        async with AsyncClient(transport=transport_b, base_url="http://test") as client_b:
            # Different IP -> independent bucket, still allowed
            assert (await client_b.get("/api/v1/query")).status_code == 200

    @pytest.mark.asyncio
    async def test_health_is_exempt(self, db, monkeypatch):
        """/health is never throttled and carries no rate limit headers"""
        monkeypatch.setenv("RATE_LIMIT_DEFAULT", "1")
        app = self._build_app(db)

        async with AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            for _ in range(5):
                response = await client.get("/health")
                assert response.status_code == 200
                assert "X-RateLimit-Limit" not in response.headers

    @pytest.mark.asyncio
    async def test_docs_gets_ip_limit(self, db, monkeypatch):
        """/docs is rate limited via the IP-based default bucket"""
        monkeypatch.setenv("RATE_LIMIT_DEFAULT", "2")
        app = self._build_app(db)

        transport = httpx.ASGITransport(app=app, client=("10.0.0.9", 4321))
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            for _ in range(2):
                response = await client.get("/docs")
                assert response.status_code == 200
                assert "X-RateLimit-Limit" in response.headers
            assert (await client.get("/docs")).status_code == 429