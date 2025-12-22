"""Contex v0.2.0 - Semantic Context Routing Platform"""

import os
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from src.core import ContextEngine
from src.core.auth import APIKeyMiddleware
from src.core.logging import setup_logging, get_logger
from src.core.graceful_shutdown import shutdown_cleanup
from src.core.tracing import initialize_tracing
from src.core.database import init_database
from src.core.pubsub import create_redis_connection
from src.core.sentry_integration import init_sentry, flush as sentry_flush

# Environment variables
REDIS_MODE = os.getenv("REDIS_MODE", "standalone")
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
                redis_mode=REDIS_MODE,
                similarity_threshold=SIMILARITY_THRESHOLD,
                max_matches=MAX_MATCHES,
                max_context_size=MAX_CONTEXT_SIZE,
                log_level=LOG_LEVEL,
                log_json=LOG_JSON)

    # Initialize Sentry for error tracking (if configured)
    sentry_enabled = init_sentry(
        release="contex@0.2.0",
        enable_tracing=os.getenv("SENTRY_ENABLE_TRACING", "true").lower() == "true"
    )
    if sentry_enabled:
        logger.info("Sentry error tracking enabled")

    # Connect to PostgreSQL
    try:
        db = await init_database()
        logger.info("PostgreSQL connection established successfully")
    except Exception as e:
        logger.error("Failed to connect to PostgreSQL", error=str(e))
        raise

    # Connect to Redis for pub/sub (supports both standalone and Sentinel modes)
    try:
        redis = await create_redis_connection()
        logger.info("Redis connection established successfully (pub/sub)", mode=REDIS_MODE)
    except Exception as e:
        logger.error("Failed to connect to Redis", error=str(e), mode=REDIS_MODE)
        raise

    # Bootstrap admin account if needed
    from src.core.auth import list_api_keys, create_api_key
    from src.core.rbac import assign_role, Role
    try:
        existing_keys = await list_api_keys(db)
        if not existing_keys:
            # No API keys exist - bootstrap admin
            bootstrap_key = os.getenv("BOOTSTRAP_ADMIN_KEY")
            bootstrap_name = os.getenv("BOOTSTRAP_ADMIN_NAME", "root")

            if bootstrap_key:
                # Use provided bootstrap key
                logger.info("Bootstrapping admin account with provided key", name=bootstrap_name)
                import hashlib
                import secrets
                from src.core.db_models import APIKey as APIKeyModel
                from datetime import datetime, timezone

                key_id = secrets.token_hex(8)
                key_hash = hashlib.sha256(bootstrap_key.encode()).hexdigest()

                # Store the key in PostgreSQL
                async with db.session() as session:
                    api_key_record = APIKeyModel(
                        key_id=key_id,
                        key_hash=key_hash,
                        name=bootstrap_name,
                        prefix=bootstrap_key[:7] if len(bootstrap_key) >= 7 else bootstrap_key,
                        scopes=[],
                        created_at=datetime.now(timezone.utc),
                    )
                    session.add(api_key_record)

                # Assign admin role
                await assign_role(db, key_id, Role.ADMIN, projects=[])
                logger.warning("Bootstrap admin created with provided key", key_id=key_id, name=bootstrap_name)
            else:
                # Auto-generate admin key
                raw_key, api_key = await create_api_key(db, bootstrap_name)
                # Assign admin role
                await assign_role(db, api_key.key_id, Role.ADMIN, projects=[])
                logger.warning("=" * 60)
                logger.warning("BOOTSTRAP ADMIN KEY (SAVE THIS - ONE TIME DISPLAY):")
                logger.warning(f"   API Key: {raw_key}")
                logger.warning(f"   Key ID: {api_key.key_id}")
                logger.warning(f"   Name: {api_key.name}")
                logger.warning("=" * 60)
                print("\n" + "=" * 60)
                print("BOOTSTRAP ADMIN KEY (SAVE THIS - ONE TIME DISPLAY):")
                print(f"   API Key: {raw_key}")
                print(f"   Key ID: {api_key.key_id}")
                print(f"   Name: {api_key.name}")
                print("=" * 60 + "\n")
        else:
            logger.info("API keys already exist, skipping bootstrap", count=len(existing_keys))
    except Exception as e:
        logger.error("Failed to bootstrap admin account", error=str(e))
        # Don't fail startup, but log the error

    # Initialize Context Engine
    try:
        context_engine = ContextEngine(
            db=db,
            redis=redis,
            similarity_threshold=SIMILARITY_THRESHOLD,
            max_matches=MAX_MATCHES,
            max_context_size=MAX_CONTEXT_SIZE
        )
        logger.info("Context engine initialized")
    except Exception as e:
        logger.error("Failed to initialize context engine", error=str(e))
        raise

    # Initialize pgvector index
    try:
        await context_engine.semantic_matcher.initialize_index()
        logger.info("pgvector index initialized")
    except Exception as e:
        logger.warning("pgvector index initialization failed", error=str(e))

    # Initialize health checker
    from src.core.health import HealthChecker
    health_checker = HealthChecker(db, redis, context_engine)
    logger.info("Health checker initialized")

    # Initialize audit logging
    from src.core.audit import init_audit_logger
    audit_retention_days = int(os.getenv("AUDIT_RETENTION_DAYS", "90"))
    audit_logger = init_audit_logger(db, retention_days=audit_retention_days)
    app.state.audit_logger = audit_logger
    logger.info("Audit logging initialized", retention_days=audit_retention_days)

    # Data versioning removed - now built on event sourcing (see /api/v1/versions endpoint)
    app.state.version_manager = None
    logger.info("Data versioning via event sourcing")

    # Initialize webhooks
    webhooks_enabled = os.getenv("WEBHOOKS_ENABLED", "true").lower() == "true"
    if webhooks_enabled:
        from src.core.webhooks import init_webhook_manager
        webhook_timeout = int(os.getenv("WEBHOOK_TIMEOUT", "30"))
        webhook_retries = int(os.getenv("WEBHOOK_MAX_RETRIES", "3"))
        webhook_manager = init_webhook_manager(
            db,
            default_timeout=webhook_timeout,
            max_retries=webhook_retries
        )
        app.state.webhook_manager = webhook_manager
        logger.info("Webhooks initialized", timeout=webhook_timeout, max_retries=webhook_retries)
    else:
        app.state.webhook_manager = None
        logger.info("Webhooks disabled")

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
    print("Web UI: http://localhost:8001/")
    print("API Docs: http://localhost:8001/api/docs")
    print("Health: http://localhost:8001/api/health")
    print("Metrics: http://localhost:8001/api/metrics")

    auth_enabled = os.getenv("AUTH_ENABLED", "false").lower() == "true"
    if auth_enabled:
        print("Security: API Key Auth + RBAC + Rate Limiting ENABLED")
    else:
        print("Security: Authentication DISABLED (set AUTH_ENABLED=true for production)")
    print()

    # Store in app state
    app.state.db = db
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
from src.core.tenant_middleware import TenantMiddleware, TenantQuotaMiddleware, MULTI_TENANT_ENABLED

# Tracing middleware (adds trace IDs to responses)
app.add_middleware(TracingMiddleware)
logger.info("Tracing middleware enabled")

# Authentication & Authorization (opt-in via AUTH_ENABLED)
AUTH_ENABLED = os.getenv("AUTH_ENABLED", "false").lower() == "true"
if AUTH_ENABLED:
    # Rate limiting (checks limits)
    app.add_middleware(RateLimitMiddleware)
    logger.info("Rate limit middleware enabled")

    # RBAC (checks permissions after auth)
    app.add_middleware(RBACMiddleware)
    logger.info("RBAC middleware enabled")

    # Authentication (validates API keys)
    app.add_middleware(APIKeyMiddleware)
    logger.info("Authentication middleware enabled")
else:
    logger.warning("Authentication is DISABLED - all endpoints are publicly accessible")

# Tenant middleware (identifies tenant, enforces quotas)
if MULTI_TENANT_ENABLED:
    app.add_middleware(TenantQuotaMiddleware)
    app.add_middleware(TenantMiddleware)
    logger.info("Multi-tenant middleware enabled")

# Mount static files
static_dir = Path(__file__).parent / "src" / "web" / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


# Mount API routes
from src.api import router as api_router
from src.api.tenant_routes import router as tenant_router
from src.api.audit_routes import router as audit_router
from src.api.service_account_routes import router as service_account_router
from src.api.webhook_routes import router as webhook_router
from src.api.version_routes import router as version_router

# Mount v1 API (primary)
app.include_router(api_router, prefix="/api/v1", tags=["API v1"])

# Mount tenant management API
app.include_router(tenant_router)

# Mount audit API
app.include_router(audit_router)

# Mount Service Account API
app.include_router(service_account_router)

# Mount Webhook API
app.include_router(webhook_router)

# Mount Versioning API (built on event sourcing)
app.include_router(version_router)

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
