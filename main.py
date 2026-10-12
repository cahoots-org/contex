"""Contex - Semantic Context Routing Platform"""

import asyncio
import os
import re
import secrets
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse, RedirectResponse, Response
from mcp.server.transport_security import TransportSecuritySettings
from prometheus_client import CONTENT_TYPE_LATEST
from src.core.authz import auth_enabled
from src.core.metrics import get_metrics
from src.core.bm25_index import Bm25Settings, ensure_bm25_index
from src.core.protected_mode import check_protected_mode
from src.core.hardened_config import check_hardened_config
from src.core.upload_limits import BodyLimitMiddleware, get_max_upload_size
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import Headers
from src.core import ContextEngine
from src.core.auth import bootstrap_admin
from src.core.logging import setup_logging, get_logger
from src.core.error_handlers import register_exception_handlers
from src.core.graceful_shutdown import shutdown_cleanup
from src.core.tracing import initialize_tracing
from src.core.version import VERSION
from src.core.database import init_database
from src.core.sentry_integration import init_sentry
from src.core.mcp_adapter import build_mcp_server
from src.core.mcp_bridge import run_bridge
from src.core.reconcile_sweep import run_sweep
from src.core.notifier import Notifier, listen_connect_kwargs
from src.core.metrics_middleware import MetricsMiddleware
from src.core.security_headers import SecurityHeadersMiddleware
from src.core.tracing_middleware import TracingMiddleware
from src.web import router as web_router
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
import uvicorn

# Environment variables
SIMILARITY_THRESHOLD = float(os.getenv("SIMILARITY_THRESHOLD", "0.5"))
MAX_MATCHES = int(os.getenv("MAX_MATCHES", "10"))
EMBED_MODEL = os.getenv("EMBED_MODEL", "thenlper/gte-base")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_JSON = os.getenv("LOG_JSON", "true").lower() == "true"

# Setup structured logging
setup_logging(level=LOG_LEVEL, json_output=LOG_JSON, service_name="contex")
logger = get_logger(__name__)


def run_startup_checks() -> None:
    """Boot-time fail-closed gates; reads env/config only, so it is testable without a database."""
    # Refuse to bind a non-loopback address when auth is off, unless the
    # operator explicitly opts out via CONTEX_PROTECTED_MODE=false.
    check_protected_mode(
        os.getenv("CONTEX_HOST", "127.0.0.1"),
        auth_on=auth_enabled(),
        protected=os.getenv("CONTEX_PROTECTED_MODE", "true").lower() == "true",
    )
    logger.info("Protected mode check passed")

    check_hardened_config()
    logger.info("Hardened-config preflight passed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for startup and shutdown"""
    run_startup_checks()
    logger.info("Contex starting", version=VERSION)
    logger.info("Configuration loaded",
                similarity_threshold=SIMILARITY_THRESHOLD,
                max_matches=MAX_MATCHES,
                log_level=LOG_LEVEL,
                log_json=LOG_JSON,
                auth_enabled=auth_enabled())

    # Initialize Sentry for error tracking (if configured)
    if init_sentry(release=f"contex@{VERSION}"):
        logger.info("Sentry error tracking enabled")

    # Connect to PostgreSQL
    try:
        db = await init_database()
        logger.info("PostgreSQL connection established successfully")
    except Exception as e:
        logger.error("Failed to connect to PostgreSQL", error=str(e))
        raise

    # Bring the schema up to head via alembic. This is the single, canonical schema
    # path: the migration chain owns the schema (pgvector column, HNSW
    # index, alembic_version row). The alembic command API is sync and drives its
    # own event loop, so migrate_to_head offloads it to a worker thread.
    #
    # NOTE: this runs migrations at boot. With multiple app replicas starting
    # concurrently, they may race on `alembic upgrade head`; alembic takes a lock
    # so this is generally safe, but a dedicated pre-deploy migration step (a
    # release-phase job / init container) is the more robust pattern at scale.
    # Called out as a known consideration; not solved here.
    try:
        await db.migrate_to_head()
        logger.info("Database schema migrated to head")
        if await ensure_bm25_index(db, Bm25Settings.from_env()):
            logger.info("Rebuilt the BM25 index with new settings")
    except Exception as e:
        logger.error("Failed to migrate database schema", error=str(e))
        raise

    # LISTEN connection for subscription-update notifications.
    notifier = Notifier(listen_connect_kwargs(db.engine.url))
    try:
        await notifier.start()
        logger.info("Notification listener connected")
    except Exception as e:
        logger.error("Failed to open the notification LISTEN connection", error=str(e))
        raise
    app.state.notifier = notifier

    if auth_enabled():
        await bootstrap_admin(db)

    # Initialize Context Engine
    try:
        context_engine = ContextEngine(
            db=db,
            similarity_threshold=SIMILARITY_THRESHOLD,
            max_matches=MAX_MATCHES,
            embed_model=EMBED_MODEL,
            notifier=notifier,
        )
        logger.info("Context engine initialized")
    except Exception as e:
        logger.error("Failed to initialize context engine", error=str(e))
        raise

    await context_engine.semantic_matcher.initialize_index()

    logger.info("Contex is ready!")

    # Store in app state
    app.state.db = db
    app.state.context_engine = context_engine

    # Wire MCP server: store references and enter the session manager context.
    # The MCP server and bus were built at module level with a lazy engine accessor;
    # now that app.state.context_engine is set, the handlers will resolve it correctly.
    app.state.mcp_server = _mcp_server
    app.state.mcp_bus = _mcp_bus
    try:
        async with _mcp_server.session_manager.run():
            bridge_task = asyncio.create_task(
                run_bridge(notifier, _mcp_bus, context_engine.subscriptions.all_ids)
            )
            # With reconcile disabled (0), the sweep still runs to purge expired subscriptions.
            sweep_seconds = float(os.getenv("RECONCILE_SWEEP_SECONDS", "300"))
            sweep_task = asyncio.create_task(run_sweep(
                db, context_engine.subscriptions, sweep_seconds if sweep_seconds > 0 else 300,
                reconcile=sweep_seconds > 0,
            ))
            try:
                yield
            finally:
                sweep_task.cancel()
                try:
                    await sweep_task
                except asyncio.CancelledError:
                    pass
                bridge_task.cancel()
                try:
                    await bridge_task
                except asyncio.CancelledError:
                    pass
    finally:
        # Shutdown — unconditional even if session_manager teardown raises
        await notifier.stop()
        await shutdown_cleanup(app.state)


# Global instances
app = FastAPI(
    title="Contex",
    description="Semantic context routing for AI agents",
    version=VERSION,
    lifespan=lifespan,
    # No REST API remains, so the auto-generated OpenAPI/Swagger/ReDoc pages
    # would only advertise the internal /health, /metrics and /sandbox routes.
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

# Global sanitizing handler for uncaught exceptions: logs the full error
# server-side and returns a generic 500 so internals never leak. Registered
# for Exception only, so FastAPI still routes HTTPException (4xx/5xx) to its
# own handler and their intentional detail is preserved.
register_exception_handlers(app)


# Build the MCP server at module level with a lazy engine accessor so the
# /mcp route exists in app.routes at import time (the test asserts this).
# The engine is resolved at handler call time via app.state.context_engine,
# which is populated during lifespan startup before any requests are served.
_mcp_server, _mcp_bus = build_mcp_server(
    lambda: app.state.context_engine,
    db_accessor=lambda: app.state.db,
)
# Mount the MCP streamable-HTTP app under /mcp. The sub-app serves at its own root
# ("/"), so the endpoint is /mcp — passing streamable_http_path="/mcp" here would
# compose with the mount prefix to /mcp/mcp. The session-manager lifecycle is run in lifespan.
# The transport is unauthenticated, so the idle timeout is tightened from the SDK
# default of 1800s to 600s to limit accumulation of orphaned sessions.
# max_request_body_size: raise the SDK's 4 MiB cap (it 413s before parsing) to our
# upload limit, so CONTEX_MAX_UPLOAD_SIZE is the single real ceiling.
_LOCAL_HOSTS = ["127.0.0.1:*", "localhost:*", "[::1]:*"]


def mcp_transport_security() -> TransportSecuritySettings:
    """DNS-rebinding protection for the whole app, driven by CONTEX_ALLOWED_HOSTS.

    Unset: localhost only (the SDK default). Comma-separated hosts: those plus
    localhost; a host without a port matches any port. "*": protection off, for
    proxies that rewrite or vary the Host.
    """
    raw = os.getenv("CONTEX_ALLOWED_HOSTS", "").strip()
    if raw == "*":
        return TransportSecuritySettings(enable_dns_rebinding_protection=False)
    entries = [h.strip() for h in raw.split(",") if h.strip()]
    hosts = _LOCAL_HOSTS + entries + [f"{h}:*" for h in entries if not re.search(r":(\d+|\*)$", h)]
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=hosts,
        allowed_origins=[f"{scheme}://{h}" for h in hosts for scheme in ("http", "https")],
    )


# Probed by infrastructure that may not use an allowed hostname; neither
# returns data a rebinding page could use (/metrics needs its token).
_HOST_CHECK_EXEMPT = {"/health", "/metrics"}


class AllowedHostMiddleware:
    """Reject requests whose Host header isn't allowed, as the MCP transport does for /mcp."""

    def __init__(self, app, allowed_hosts: list[str]):
        self.app = app
        self.allowed_hosts = allowed_hosts

    def _allowed(self, host: str) -> bool:
        return any(
            host == allowed or (allowed.endswith(":*") and host.startswith(allowed[:-1]))
            for allowed in self.allowed_hosts
        )

    async def __call__(self, scope, receive, send):
        if (
            scope["type"] == "http"
            and scope["path"] not in _HOST_CHECK_EXEMPT
            and not self._allowed(Headers(scope=scope).get("host", ""))
        ):
            await PlainTextResponse("Invalid Host header", status_code=421)(scope, receive, send)
            return
        await self.app(scope, receive, send)


_transport_security = mcp_transport_security()
_mcp_starlette_app = _mcp_server.streamable_http_app(
    streamable_http_path="/",
    session_idle_timeout=600,
    max_request_body_size=get_max_upload_size(),
    transport_security=_transport_security,
)
app.mount("/mcp", _mcp_starlette_app)


class McpPathMiddleware:
    """Serve /mcp as /mcp/: a redirect would point clients behind a TLS proxy at http://."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"] == "/mcp":
            scope = {**scope, "path": "/mcp/"}
        await self.app(scope, receive, send)


app.add_middleware(McpPathMiddleware)

# CORS: no cross-origin access unless CORS_ORIGINS lists the origins.
CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()]
CORS_ALLOW_CREDENTIALS = os.getenv("CORS_ALLOW_CREDENTIALS", "true").lower() == "true"

# Security warning for CORS wildcard
if "*" in CORS_ORIGINS:
    logger.warning("CORS allows all origins (*) - INSECURE for production. Set CORS_ORIGINS env var.")
    if CORS_ALLOW_CREDENTIALS:
        logger.warning("CORS allows credentials with wildcard origin - SECURITY RISK!")

# Browsers reject wildcard origin + credentials; coerce to safe state.
if "*" in CORS_ORIGINS and CORS_ALLOW_CREDENTIALS:
    CORS_ALLOW_CREDENTIALS = False
    logger.warning(
        "CORS: credentials disabled because a wildcard origin is configured; "
        "set explicit CORS_ORIGINS to use credentials"
    )

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=CORS_ALLOW_CREDENTIALS,
    allow_methods=["*"],
    allow_headers=["*"],
)
logger.info("CORS configured", origins=CORS_ORIGINS, allow_credentials=CORS_ALLOW_CREDENTIALS)

# Add Metrics Middleware (first, to track all requests)
app.add_middleware(MetricsMiddleware)

# Add security headers middleware
ENABLE_HSTS = os.getenv("ENABLE_HSTS", "true").lower() == "true"
app.add_middleware(SecurityHeadersMiddleware, enable_hsts=ENABLE_HSTS)
logger.info("Security headers middleware enabled", hsts=ENABLE_HSTS)

# Global body-size limit (defense-in-depth): bounds every request body so no
# handler can buffer an unbounded payload, even with a spoofed Content-Length.
app.add_middleware(BodyLimitMiddleware)
logger.info("Body-limit middleware enabled")

# Add security middleware stack (order matters - executed in reverse)
# Tracing middleware (adds trace IDs to responses)
app.add_middleware(TracingMiddleware)
logger.info("Tracing middleware enabled")

# Outermost, so a disallowed Host is turned away before any other work.
if _transport_security.enable_dns_rebinding_protection:
    app.add_middleware(AllowedHostMiddleware, allowed_hosts=_transport_security.allowed_hosts)

# Mount static files
static_dir = Path(__file__).parent / "src" / "web" / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
app.mount("/design", StaticFiles(directory=str(Path(__file__).parent / "design")), name="design")


# Mount Web UI routes
app.include_router(web_router, prefix="/sandbox", tags=["Web UI"])

# Root-level health endpoint (for Docker health checks)
@app.get("/health")
async def root_health():
    """Liveness probe for Docker and platform health checks.

    Deliberately returns nothing beyond a liveness signal: this endpoint is
    unauthenticated, so it must not disclose versions, component status, or any
    other internal infrastructure detail (issue #79).
    """
    return {"status": "healthy"}


# Prometheus scrape endpoint. Gated by METRICS_TOKEN so it is never
# unauthenticated (issue #78): disabled entirely when the env var is unset,
# and otherwise requires `Authorization: Bearer <METRICS_TOKEN>`. Both the
# unconfigured and the wrong-token cases return 404 so the endpoint's
# existence is not disclosed.
METRICS_TOKEN = os.getenv("METRICS_TOKEN")


@app.get("/metrics")
async def metrics(request: Request):
    if not METRICS_TOKEN:
        raise HTTPException(status_code=404)
    provided = request.headers.get("authorization", "")
    if not secrets.compare_digest(provided, f"Bearer {METRICS_TOKEN}"):
        raise HTTPException(status_code=404)
    return Response(content=get_metrics(), media_type=CONTENT_TYPE_LATEST)


# Root redirect to sandbox
@app.get("/")
async def root():
    """Redirect to the query sandbox, which is served only with auth off."""
    if auth_enabled():
        raise HTTPException(status_code=404)
    return RedirectResponse(url="/sandbox")


# Initialize tracing at module level so the TracerProvider is set globally
# BEFORE FastAPI instrumentation picks it up
try:
    initialize_tracing()
    logger.info("Tracing: TracerProvider initialized")
except Exception as e:
    logger.warning("Tracing: Failed to initialize TracerProvider", error=str(e))

# Instrument FastAPI with OpenTelemetry at module level (must happen after
# TracerProvider is set, and after all middleware/routes are configured)
FastAPIInstrumentor.instrument_app(app, excluded_urls="health,metrics")


if __name__ == "__main__":
    host = os.getenv("CONTEX_HOST", "127.0.0.1")
    port = int(os.getenv("CONTEX_PORT", "8001"))
    # Open MCP streams never close on their own; cap the wait so shutdown
    # cleanup runs inside Docker's 10s stop grace period.
    uvicorn.run(app, host=host, port=port, timeout_graceful_shutdown=5, log_config=None)
