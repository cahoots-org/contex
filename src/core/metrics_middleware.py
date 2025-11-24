"""Metrics middleware for automatic HTTP metrics collection"""

import time
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from src.core.metrics import (
    record_http_request,
    http_request_duration_seconds,
    increment_active_requests,
    decrement_active_requests
)


class MetricsMiddleware(BaseHTTPMiddleware):
    """Middleware to collect HTTP metrics"""
    
    async def dispatch(self, request: Request, call_next):
        # Skip metrics endpoint itself
        if request.url.path == "/metrics":
            return await call_next(request)
        
        # Increment active requests
        increment_active_requests()
        
        # Start timer
        start_time = time.time()
        
        # Normalize endpoint for metrics (remove IDs)
        endpoint = self._normalize_endpoint(request.url.path)
        
        try:
            # Process request
            response = await call_next(request)
            
            # Record metrics
            duration = time.time() - start_time
            http_request_duration_seconds.labels(
                method=request.method,
                endpoint=endpoint
            ).observe(duration)
            
            record_http_request(
                method=request.method,
                endpoint=endpoint,
                status_code=response.status_code
            )
            
            return response
            
        except Exception as e:
            # Record error
            duration = time.time() - start_time
            http_request_duration_seconds.labels(
                method=request.method,
                endpoint=endpoint
            ).observe(duration)
            
            record_http_request(
                method=request.method,
                endpoint=endpoint,
                status_code=500
            )
            
            raise
        
        finally:
            # Decrement active requests
            decrement_active_requests()
    
    def _normalize_endpoint(self, path: str) -> str:
        """
        Normalize endpoint path for metrics.
        Replace dynamic segments with placeholders.
        """
        # Remove leading/trailing slashes
        path = path.strip('/')
        
        # Common patterns to normalize
        parts = path.split('/')
        normalized_parts = []
        
        for i, part in enumerate(parts):
            # Replace UUIDs and IDs
            if self._looks_like_id(part):
                normalized_parts.append('{id}')
            else:
                normalized_parts.append(part)
        
        return '/' + '/'.join(normalized_parts) if normalized_parts else '/'
    
    def _looks_like_id(self, segment: str) -> bool:
        """Check if a path segment looks like an ID"""
        # UUID pattern
        if len(segment) == 36 and segment.count('-') == 4:
            return True
        
        # Hex ID pattern
        if len(segment) >= 16 and all(c in '0123456789abcdefABCDEF-_' for c in segment):
            return True
        
        # Numeric ID
        if segment.isdigit():
            return True
        
        # Common ID prefixes
        id_prefixes = ['proj-', 'agent-', 'user-', 'key-', 'req-', 'sess-']
        if any(segment.startswith(prefix) for prefix in id_prefixes):
            return True
        
        return False
