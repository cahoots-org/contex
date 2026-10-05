"""Tests for security features"""

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from httpx import AsyncClient
from src.core.security_headers import SecurityHeadersMiddleware


def _build_cors_app(origins, allow_credentials):
    """Build a minimal FastAPI app applying the same CORS coercion logic as main.py."""
    app = FastAPI()
    coerced_credentials = allow_credentials
    if "*" in origins and coerced_credentials:
        coerced_credentials = False
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=coerced_credentials,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    return app


class TestSecurityHeaders:
    """Test security headers middleware"""

    @pytest_asyncio.fixture
    async def app_with_security_headers(self):
        """Create test app with security headers"""
        app = FastAPI()
        app.add_middleware(SecurityHeadersMiddleware, enable_hsts=True)

        @app.get("/test")
        async def test_endpoint():
            return {"status": "ok"}

        return app

    @pytest.mark.asyncio
    async def test_security_headers_present(self, app_with_security_headers):
        """Test that security headers are added to responses"""
        async with AsyncClient(transport=httpx.ASGITransport(app=app_with_security_headers), base_url="http://test") as client:
            response = await client.get("/test")

            assert response.status_code == 200

            # Check security headers
            assert "X-Content-Type-Options" in response.headers
            assert response.headers["X-Content-Type-Options"] == "nosniff"

            assert "X-Frame-Options" in response.headers
            assert response.headers["X-Frame-Options"] == "DENY"

            assert "X-XSS-Protection" in response.headers
            assert response.headers["X-XSS-Protection"] == "1; mode=block"

            assert "Content-Security-Policy" in response.headers
            assert "default-src 'self'" in response.headers["Content-Security-Policy"]

            assert "Referrer-Policy" in response.headers
            assert response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"

            assert "Permissions-Policy" in response.headers

    @pytest.mark.asyncio
    async def test_hsts_only_on_https(self, app_with_security_headers):
        """Test that HSTS is only added for HTTPS requests"""
        async with AsyncClient(transport=httpx.ASGITransport(app=app_with_security_headers), base_url="http://test") as client:
            response = await client.get("/test")

            # HTTP request should not have HSTS header
            assert "Strict-Transport-Security" not in response.headers

    @pytest.mark.asyncio
    async def test_hsts_on_https(self):
        """Test that HSTS is added for HTTPS requests"""
        app = FastAPI()
        app.add_middleware(SecurityHeadersMiddleware, enable_hsts=True)

        @app.get("/test")
        async def test_endpoint():
            return {"status": "ok"}

        async with AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test") as client:
            response = await client.get("/test")

            # HTTPS request should have HSTS header
            assert "Strict-Transport-Security" in response.headers
            assert "max-age=31536000" in response.headers["Strict-Transport-Security"]
            assert "includeSubDomains" in response.headers["Strict-Transport-Security"]

    @pytest.mark.asyncio
    async def test_hsts_disabled(self):
        """Test that HSTS can be disabled"""
        app = FastAPI()
        app.add_middleware(SecurityHeadersMiddleware, enable_hsts=False)

        @app.get("/test")
        async def test_endpoint():
            return {"status": "ok"}

        async with AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test") as client:
            response = await client.get("/test")

            # Even for HTTPS, HSTS should not be present when disabled
            assert "Strict-Transport-Security" not in response.headers

    @pytest.mark.asyncio
    async def test_csp_frame_ancestors_none(self, app_with_security_headers):
        """Test that CSP includes frame-ancestors 'none'"""
        async with AsyncClient(transport=httpx.ASGITransport(app=app_with_security_headers), base_url="http://test") as client:
            response = await client.get("/test")

            csp = response.headers.get("Content-Security-Policy", "")
            assert "frame-ancestors 'none'" in csp


class TestCORSMiddlewareCoercion:
    """Test that wildcard origin disables credentials in CORSMiddleware construction."""

    def _cors_kwargs(self, app):
        cors_mw = next(mw for mw in app.user_middleware if mw.cls is CORSMiddleware)
        return cors_mw.kwargs

    def test_wildcard_origin_disables_credentials(self):
        """Wildcard origins + credentials=True must produce allow_credentials=False."""
        app = _build_cors_app(["*"], allow_credentials=True)
        assert self._cors_kwargs(app)["allow_credentials"] is False

    def test_explicit_origins_preserve_credentials(self):
        """Explicit (non-wildcard) origins with credentials=True must pass through unchanged."""
        app = _build_cors_app(["https://app.example.com"], allow_credentials=True)
        assert self._cors_kwargs(app)["allow_credentials"] is True

    def test_wildcard_origin_credentials_already_false_unchanged(self):
        """Wildcard origins with credentials=False must remain False (no-op coercion)."""
        app = _build_cors_app(["*"], allow_credentials=False)
        assert self._cors_kwargs(app)["allow_credentials"] is False
