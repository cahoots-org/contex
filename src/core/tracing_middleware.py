"""Middleware to add trace IDs to responses and integrate with logging"""

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from .tracing import get_current_trace_id, get_current_span_id


class TracingMiddleware(BaseHTTPMiddleware):
    """
    Middleware that adds trace context to HTTP responses.

    Adds X-Trace-Id and X-Span-Id headers to responses for client-side tracing correlation.
    """

    async def dispatch(self, request: Request, call_next):
        # Process request
        response = await call_next(request)

        # Add trace IDs to response headers
        trace_id = get_current_trace_id()
        span_id = get_current_span_id()

        if trace_id:
            response.headers["X-Trace-Id"] = trace_id

        if span_id:
            response.headers["X-Span-Id"] = span_id

        return response
