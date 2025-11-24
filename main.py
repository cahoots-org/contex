"""Contex v0.2.0 - Semantic Context Routing Platform"""

import os
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from redis.asyncio import Redis

from src.core import ContextEngine
from src.core.auth import APIKeyMiddleware
from src.core.logging import setup_logging, get_logger

# Environment variables
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379")
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
                similarity_threshold=SIMILARITY_THRESHOLD,
                max_matches=MAX_MATCHES,
                max_context_size=MAX_CONTEXT_SIZE,
                log_level=LOG_LEVEL,
                log_json=LOG_JSON)

    # Connect to Redis
    try:
        redis = await Redis.from_url(REDIS_URL, decode_responses=False)
        logger.info("Connected to Redis", redis_url=REDIS_URL)
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
    
    # Add security middleware (order matters: Auth -> RBAC -> Rate Limit)
    from src.core.rbac_middleware import RBACMiddleware
    from src.core.rate_limiter import RateLimitMiddleware
    
    # Rate limiting (outermost - checks limits first)
    app.add_middleware(RateLimitMiddleware, redis=redis)
    logger.info("Rate limit middleware enabled")
    
    # RBAC (checks permissions after auth)
    app.add_middleware(RBACMiddleware, redis=redis)
    logger.info("RBAC middleware enabled")
    
    # Authentication (innermost - validates API keys)
    app.add_middleware(APIKeyMiddleware, redis=redis)
    logger.info("Authentication middleware enabled")
    
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
    if hasattr(app.state, 'redis') and app.state.redis:
        await app.state.redis.aclose()
        print("✓ Redis connection closed")


# Global instances
app = FastAPI(
    title="Contex",
    description="Semantic context routing for AI agents",
    version="0.2.0",
    lifespan=lifespan
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Add Metrics Middleware (first, to track all requests)
from src.core.metrics_middleware import MetricsMiddleware
app.add_middleware(MetricsMiddleware)

# Mount static files
static_dir = Path(__file__).parent / "src" / "web" / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


# Mount API routes
from src.api import router as api_router
app.include_router(api_router, prefix="/api", tags=["API"])

# Mount Web UI routes
from src.web import router as web_router
app.include_router(web_router, prefix="/sandbox", tags=["Web UI"])

# Root redirect to sandbox
from fastapi.responses import RedirectResponse

@app.get("/")
async def root():
    """Redirect to query sandbox"""
    return RedirectResponse(url="/sandbox")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
