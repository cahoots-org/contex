"""Global exception handling that sanitizes unexpected errors.

Any exception not handled by a more specific handler is logged in full
server-side and returned to the caller as a generic 500 body, so internal
details (SQLAlchemy query text, schema names, exception messages) never
leak. FastAPI routes ``HTTPException`` to its own handler, so intentional
4xx/5xx responses keep their detail.
"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .logging import get_logger
from .tracing import get_current_trace_id

logger = get_logger(__name__)


async def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Log the real error with its trace id and return a generic 500."""
    trace_id = get_current_trace_id()
    logger.exception(
        "Unhandled exception",
        error=str(exc),
        error_type=type(exc).__name__,
        path=request.url.path,
        method=request.method,
        trace_id=trace_id,
    )

    headers = {"X-Trace-Id": trace_id} if trace_id else None
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
        headers=headers,
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Register the global sanitizing handler for uncaught exceptions."""
    app.add_exception_handler(Exception, _unhandled_exception_handler)
