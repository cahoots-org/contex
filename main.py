"""Contex v0.2.0 - Semantic Context Routing Platform"""

import os
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from redis.asyncio import Redis, ConnectionPool

from src.core import ContextEngine
from src.core.auth import APIKeyMiddleware
from src.core.logging import setup_logging, get_logger
from src.core.graceful_shutdown import shutdown_cleanup
from src.core.tracing import initialize_tracing

# Environment variables
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379")
REDIS_MAX_CONNECTIONS = int(os.getenv("REDIS_MAX_CONNECTIONS", "50"))
REDIS_SOCKET_TIMEOUT = int(os.getenv("REDIS_SOCKET_TIMEOUT", "5"))
REDIS_SOCKET_CONNECT_TIMEOUT = int(os.getenv("REDIS_SOCKET_CONNECT_TIMEOUT", "5"))
REDIS_SOCKET_KEEPALIVE = os.getenv("REDIS_SOCKET_KEEPALIVE", "true").lower() == "true"
SIMILARITY_THRESHOLD = float(os.getenv("SIMILARITY_THRESHOLD", "0.5"))
MAX_MATCHES = int(os.getenv("MAX_MATCHES", "10"))
MAX_CONTEXT_SIZE = int(os.getenv("MAX_CONTEXT_SIZE", "51200"))  # ~40% of 128k tokens
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_JSON = os.getenv("LOG_JSON", "true").lower() == "true"

# Setup structured logging
setup_logging(level=LOG_LEVEL, json_output=LOG_JSON, service_name="contex")
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for startup and shutdown"""
    logger.info("Contex starting", version="0.2.0")
    logger.info("Configuration loaded",
                redis_url=REDIS_URL,
                redis_max_connections=REDIS_MAX_CONNECTIONS,
                redis_socket_timeout=REDIS_SOCKET_TIMEOUT,
                redis_socket_keepalive=REDIS_SOCKET_KEEPALIVE,
                similarity_threshold=SIMILARITY_THRESHOLD,
                max_matches=MAX_MATCHES,
                max_context_size=MAX_CONTEXT_SIZE,
                log_level=LOG_LEVEL,
                log_json=LOG_JSON)

    # Connect to Redis with connection pooling
    try:
        # Create connection pool with optimized settings
        pool = ConnectionPool.from_url(
            REDIS_URL,
            max_connections=REDIS_MAX_CONNECTIONS,
            socket_timeout=REDIS_SOCKET_TIMEOUT,
            socket_connect_timeout=REDIS_SOCKET_CONNECT_TIMEOUT,
            socket_keepalive=REDIS_SOCKET_KEEPALIVE,
            # Note: socket_keepalive_options removed for platform compatibility
            # socket_keepalive=True is sufficient for most use cases
            retry_on_timeout=True,
            health_check_interval=30,  # Check connection health every 30 seconds
            decode_responses=False
        )

        redis = Redis(connection_pool=pool)

        # Test connection
        await redis.ping()

        logger.info("Connected to Redis with connection pooling",
                   redis_url=REDIS_URL,
                   max_connections=REDIS_MAX_CONNECTIONS,
                   socket_timeout=REDIS_SOCKET_TIMEOUT,
                   socket_keepalive=REDIS_SOCKET_KEEPALIVE)
    except Exception as e:
        logger.error("Failed to connect to Redis", error=str(e), redis_url=REDIS_URL)
        raise

    # Initialize Context Engine
    try:
        context_engine = ContextEngine(
            redis=redis,
            similarity_threshold=SIMILARITY_THRESHOLD,
            max_matches=MAX_MATCHES,
            max_context_size=MAX_CONTEXT_SIZE
        )
        logger.info("Context engine initialized")
    except Exception as e:
        logger.error("Failed to initialize context engine", error=str(e))
        raise

    # Initialize RediSearch index
    try:
        await context_engine.semantic_matcher.initialize_index()
        logger.info("RediSearch index initialized")
    except Exception as e:
        logger.warning("RediSearch index initialization failed (may already exist)", error=str(e))
    
    # Initialize health checker
    from src.core.health import HealthChecker
    health_checker = HealthChecker(redis, context_engine)
    logger.info("Health checker initialized")

    # Initialize distributed tracing
    try:
        tracing_manager = initialize_tracing(
            service_name="contex",
            service_version="0.2.0"
        )
        # Instrument FastAPI and Redis
        tracing_manager.instrument_fastapi(app)
        tracing_manager.instrument_redis()
        app.state.tracing_manager = tracing_manager
        logger.info("Distributed tracing initialized")
    except Exception as e:
        logger.warning("Failed to initialize tracing", error=str(e))
        app.state.tracing_manager = None
    
    logger.info("Contex is ready!")
    print("=" * 60)
    print()
    print("Contex is ready!")
    print("=" * 60)
    print()
    print("🌐 Web UI: http://localhost:8001/")
    print("📚 API Docs: http://localhost:8001/api/docs")
    print("❤️  Health: http://localhost:8001/api/health")
    print("📊 Metrics: http://localhost:8001/api/metrics")
    print("🔐 Security: API Key Auth + RBAC + Rate Limiting ENABLED")
    print()

    # Store in app state
    app.state.context_engine = context_engine
    app.state.redis = redis
    app.state.health_checker = health_checker

    yield

    # Shutdown
    await shutdown_cleanup(app.state)


# Global instances
app = FastAPI(
    title="Contex",
    description="Semantic context routing for AI agents",
    version="0.2.0",
    lifespan=lifespan
)

# CORS Configuration
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "*").split(",") if os.getenv("CORS_ORIGINS") else ["*"]
CORS_ALLOW_CREDENTIALS = os.getenv("CORS_ALLOW_CREDENTIALS", "true").lower() == "true"

# Security warning for CORS wildcard
if "*" in CORS_ORIGINS:
    logger.warning("CORS allows all origins (*) - INSECURE for production. Set CORS_ORIGINS env var.")
    if CORS_ALLOW_CREDENTIALS:
        logger.warning("CORS allows credentials with wildcard origin - SECURITY RISK!")

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=CORS_ALLOW_CREDENTIALS,
    allow_methods=["*"],
    allow_headers=["*"],
)
logger.info("CORS configured", origins=CORS_ORIGINS, allow_credentials=CORS_ALLOW_CREDENTIALS)

# Add Metrics Middleware (first, to track all requests)
from src.core.metrics_middleware import MetricsMiddleware
app.add_middleware(MetricsMiddleware)

# Add security headers middleware
from src.core.security_headers import SecurityHeadersMiddleware
ENABLE_HSTS = os.getenv("ENABLE_HSTS", "true").lower() == "true"
app.add_middleware(SecurityHeadersMiddleware, enable_hsts=ENABLE_HSTS)
logger.info("Security headers middleware enabled", hsts=ENABLE_HSTS)

# Add security middleware stack (order matters - executed in reverse)
from src.core.auth import APIKeyMiddleware
from src.core.rbac_middleware import RBACMiddleware
from src.core.rate_limiter import RateLimitMiddleware
from src.core.tracing_middleware import TracingMiddleware

# Tracing middleware (adds trace IDs to responses)
app.add_middleware(TracingMiddleware)
logger.info("Tracing middleware enabled")

# Rate limiting (checks limits)
app.add_middleware(RateLimitMiddleware)
logger.info("Rate limit middleware enabled")

# RBAC (checks permissions after auth)
app.add_middleware(RBACMiddleware)
logger.info("RBAC middleware enabled")

# Authentication (validates API keys)
app.add_middleware(APIKeyMiddleware)
logger.info("Authentication middleware enabled")

# Mount static files
static_dir = Path(__file__).parent / "src" / "web" / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


# Mount API routes
from src.api import router as api_router

# Mount v1 API (primary)
app.include_router(api_router, prefix="/api/v1", tags=["API v1"])

# Mount legacy /api for backward compatibility (with deprecation warning)
from fastapi import Response
from starlette.middleware.base import BaseHTTPMiddleware

class DeprecationWarningMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        # Check if request is using legacy /api path (not /api/v1)
        if request.url.path.startswith("/api/") and not request.url.path.startswith("/api/v1"):
            response = await call_next(request)
            response.headers["X-API-Deprecation"] = "This API version is deprecated. Use /api/v1 instead."
            response.headers["X-API-Version"] = "legacy"
            return response
        else:
            response = await call_next(request)
            if request.url.path.startswith("/api/v1"):
                response.headers["X-API-Version"] = "v1"
            return response

app.add_middleware(DeprecationWarningMiddleware)

# Mount legacy API for backward compatibility
app.include_router(api_router, prefix="/api", tags=["API (deprecated)"])

# Mount Web UI routes
from src.web import router as web_router
app.include_router(web_router, prefix="/sandbox", tags=["Web UI"])

# Root-level health endpoint (for Docker health checks)
@app.get("/health")
async def root_health():
    """
    Root-level health check endpoint for Docker/Kubernetes.

    This is separate from /api/health to avoid API versioning complexity
    and ensure health checks work reliably without authentication.
    """
    # Basic health check - just verify the app is responding
    return {
        "status": "healthy",
        "service": "contex",
        "version": "0.2.0"
    }

# Root redirect to sandbox
from fastapi.responses import RedirectResponse

@app.get("/")
async def root():
    """Redirect to query sandbox"""
    return RedirectResponse(url="/sandbox")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
