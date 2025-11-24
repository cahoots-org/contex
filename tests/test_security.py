"""Tests for security features"""

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import AsyncClient
from src.core.security_headers import SecurityHeadersMiddleware
from src.core.config import SecurityConfig


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
        async with AsyncClient(app=app_with_security_headers, base_url="http://test") as client:
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
        async with AsyncClient(app=app_with_security_headers, base_url="http://test") as client:
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

        async with AsyncClient(app=app, base_url="https://test") as client:
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

        async with AsyncClient(app=app, base_url="https://test") as client:
            response = await client.get("/test")

            # Even for HTTPS, HSTS should not be present when disabled
            assert "Strict-Transport-Security" not in response.headers

    @pytest.mark.asyncio
    async def test_csp_frame_ancestors_none(self, app_with_security_headers):
        """Test that CSP includes frame-ancestors 'none'"""
        async with AsyncClient(app=app_with_security_headers, base_url="http://test") as client:
            response = await client.get("/test")

            csp = response.headers.get("Content-Security-Policy", "")
            assert "frame-ancestors 'none'" in csp


class TestCORSConfiguration:
    """Test CORS configuration"""

    def test_cors_default_wildcard(self):
        """Test that default CORS is wildcard"""
        config = SecurityConfig()
        assert config.cors_origins == ["*"]
        assert config.cors_allow_credentials is True

    def test_cors_custom_origins(self):
        """Test custom CORS origins"""
        config = SecurityConfig(cors_origins=["https://example.com", "https://app.example.com"])
        assert len(config.cors_origins) == 2
        assert "https://example.com" in config.cors_origins
        assert "https://app.example.com" in config.cors_origins

    def test_cors_from_comma_separated_string(self):
        """Test parsing CORS origins from comma-separated string"""
        config = SecurityConfig(cors_origins="https://example.com, https://app.example.com")
        assert len(config.cors_origins) == 2
        assert "https://example.com" in config.cors_origins
        assert "https://app.example.com" in config.cors_origins


class TestSecurityConfigValidation:
    """Test security configuration validation"""

    def test_cors_wildcard_warning(self):
        """Test warning for CORS wildcard"""
        from src.core.config import ContexConfig

        config = ContexConfig(
            security=SecurityConfig(cors_origins=["*"])
        )

        warnings = config.validate_config()

        # Should have warning about CORS wildcard
        assert any("CORS allows all origins" in w for w in warnings)

    def test_cors_wildcard_with_credentials_warning(self):
        """Test warning for CORS wildcard with credentials"""
        from src.core.config import ContexConfig

        config = ContexConfig(
            security=SecurityConfig(
                cors_origins=["*"],
                cors_allow_credentials=True
            )
        )

        warnings = config.validate_config()

        # Should have both warnings
        assert any("CORS allows all origins" in w for w in warnings)
        assert any("CORS allows credentials with wildcard origin" in w for w in warnings)

    def test_cors_specific_origins_no_warning(self):
        """Test no warning for specific CORS origins"""
        from src.core.config import ContexConfig

        config = ContexConfig(
            security=SecurityConfig(
                cors_origins=["https://example.com"],
                cors_allow_credentials=True
            )
        )

        warnings = config.validate_config()

        # Should not have CORS warnings
        assert not any("CORS" in w for w in warnings)

    def test_api_key_salt_warning(self):
        """Test warning for missing API key salt"""
        from src.core.config import ContexConfig

        config = ContexConfig(
            security=SecurityConfig(api_key_salt=None)
        )

        warnings = config.validate_config()

        # Should have warning about API key salt
        assert any("API_KEY_SALT not set" in w for w in warnings)

    def test_api_key_salt_change_me_warning(self):
        """Test warning for default API key salt"""
        from src.core.config import ContexConfig

        config = ContexConfig(
            security=SecurityConfig(api_key_salt="CHANGE_ME_IN_PRODUCTION")
        )

        warnings = config.validate_config()

        # Should have warning about changing API key salt
        assert any("CHANGE THIS IN PRODUCTION" in w for w in warnings)
