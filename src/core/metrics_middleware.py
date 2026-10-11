"""Metrics middleware for automatic HTTP metrics collection"""

import time

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from src.core.metrics import (
    decrement_active_requests,
    http_request_duration_seconds,
    increment_active_requests,
    record_http_request,
)


def route_label(scope: dict) -> str:
    """The matched route's path template, a mount's path, or "other", so labels stay bounded."""
    if "route" in scope:
        names = {value: f"{{{name}}}" for name, value in scope["path_params"].items()}
        return "/".join(names.get(segment, segment) for segment in scope["path"].split("/"))
    if "app_root_path" in scope:
        return scope["root_path"]
    return "other"


class MetricsMiddleware(BaseHTTPMiddleware):
    """Middleware to collect HTTP metrics"""

    async def dispatch(self, request: Request, call_next):
        if request.url.path == "/metrics":
            return await call_next(request)

        increment_active_requests()
        start_time = time.time()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            endpoint = route_label(request.scope)
            http_request_duration_seconds.labels(
                method=request.method, endpoint=endpoint
            ).observe(time.time() - start_time)
            record_http_request(method=request.method, endpoint=endpoint, status_code=status_code)
            decrement_active_requests()
