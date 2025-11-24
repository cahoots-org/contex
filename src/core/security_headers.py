"""Security headers middleware for Contex"""

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """
    Middleware to add security headers to all responses.

    Headers added:
    - X-Content-Type-Options: nosniff (prevents MIME type sniffing)
    - X-Frame-Options: DENY (prevents clickjacking)
    - X-XSS-Protection: 1; mode=block (enables XSS protection)
    - Strict-Transport-Security: enables HSTS for HTTPS
    - Content-Security-Policy: restricts resource loading
    """

    def __init__(self, app, enable_hsts: bool = True):
        super().__init__(app)
        self.enable_hsts = enable_hsts

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)

        # Prevent MIME type sniffing
        response.headers["X-Content-Type-Options"] = "nosniff"

        # Prevent clickjacking
        response.headers["X-Frame-Options"] = "DENY"

        # Enable XSS protection (legacy but still useful for older browsers)
        response.headers["X-XSS-Protection"] = "1; mode=block"

        # Strict Transport Security (HSTS) - only for HTTPS
        if self.enable_hsts and request.url.scheme == "https":
            # max-age=31536000 (1 year), includeSubDomains
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

        # Content Security Policy - relaxed for sandbox UI, strict for API
        # Alpine.js requires unsafe-eval for dynamic expressions
        if request.url.path.startswith("/sandbox") or request.url.path.startswith("/static"):
            # Relaxed CSP for sandbox UI
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; "
                "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://unpkg.com; "  # unsafe-eval for Alpine.js
                "style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data:; "
                "font-src 'self'; "
                "connect-src 'self'; "
                "frame-ancestors 'none'"
            )
        else:
            # Strict CSP for API endpoints
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; "
                "script-src 'self'; "  # No inline scripts for API
                "style-src 'self'; "
                "img-src 'self' data:; "
                "font-src 'self'; "
                "connect-src 'self'; "
                "frame-ancestors 'none'"
            )

        # Referrer Policy - don't leak full URLs to external sites
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

        # Permissions Policy - disable potentially dangerous features
        response.headers["Permissions-Policy"] = (
            "geolocation=(), "
            "microphone=(), "
            "camera=(), "
            "payment=(), "
            "usb=()"
        )

        return response
