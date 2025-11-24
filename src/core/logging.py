"""Structured logging configuration for Contex"""

import logging
import sys
import json
from typing import Any, Dict, Optional
from datetime import datetime, timezone
import contextvars

# Context variable for request ID
request_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    'request_id', default=None
)


class StructuredFormatter(logging.Formatter):
    """JSON formatter for structured logging"""
    
    def __init__(self, service_name: str = "contex"):
        super().__init__()
        self.service_name = service_name
    
    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON"""
        # Base log structure
        log_data = {
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "level": record.levelname,
            "service": self.service_name,
            "logger": record.name,
            "message": record.getMessage(),
        }
        
        # Add request ID if available
        request_id = request_id_var.get()
        if request_id:
            log_data["request_id"] = request_id
        
        # Add extra fields from record
        if hasattr(record, 'extra_fields'):
            log_data.update(record.extra_fields)
        
        # Add exception info if present
        if record.exc_info:
            log_data["exception"] = {
                "type": record.exc_info[0].__name__,
                "message": str(record.exc_info[1]),
                "traceback": self.formatException(record.exc_info)
            }
        
        # Add file location for debug/error levels
        if record.levelno >= logging.WARNING:
            log_data["location"] = {
                "file": record.pathname,
                "line": record.lineno,
                "function": record.funcName
            }
        
        return json.dumps(log_data)


class StructuredLogger:
    """Wrapper for structured logging with context"""
    
    def __init__(self, name: str):
        self.logger = logging.getLogger(name)
        self._context: Dict[str, Any] = {}
    
    def bind(self, **kwargs) -> 'StructuredLogger':
        """Create a new logger with additional context"""
        new_logger = StructuredLogger(self.logger.name)
        new_logger.logger = self.logger
        new_logger._context = {**self._context, **kwargs}
        return new_logger
    
    def _log(self, level: int, message: str, **kwargs):
        """Internal logging method"""
        # Merge context with kwargs
        extra_fields = {**self._context, **kwargs}
        
        # Create a log record with extra fields
        extra = {'extra_fields': extra_fields}
        self.logger.log(level, message, extra=extra)
    
    def debug(self, message: str, **kwargs):
        """Log debug message"""
        self._log(logging.DEBUG, message, **kwargs)
    
    def info(self, message: str, **kwargs):
        """Log info message"""
        self._log(logging.INFO, message, **kwargs)
    
    def warning(self, message: str, **kwargs):
        """Log warning message"""
        self._log(logging.WARNING, message, **kwargs)
    
    def error(self, message: str, **kwargs):
        """Log error message"""
        self._log(logging.ERROR, message, **kwargs)
    
    def critical(self, message: str, **kwargs):
        """Log critical message"""
        self._log(logging.CRITICAL, message, **kwargs)
    
    def exception(self, message: str, **kwargs):
        """Log exception with traceback"""
        extra_fields = {**self._context, **kwargs}
        extra = {'extra_fields': extra_fields}
        self.logger.exception(message, extra=extra)


def setup_logging(
    level: str = "INFO",
    json_output: bool = True,
    service_name: str = "contex"
) -> None:
    """
    Configure structured logging for the application.
    
    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        json_output: If True, output JSON. If False, use human-readable format
        service_name: Name of the service for log identification
    """
    # Get root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, level.upper()))
    
    # Remove existing handlers
    root_logger.handlers.clear()
    
    # Create console handler
    handler = logging.StreamHandler(sys.stdout)
    
    # Set formatter based on output type
    if json_output:
        formatter = StructuredFormatter(service_name=service_name)
    else:
        # Human-readable format for development
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
    
    handler.setFormatter(formatter)
    root_logger.addHandler(handler)
    
    # Silence noisy third-party loggers
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def get_logger(name: str) -> StructuredLogger:
    """
    Get a structured logger instance.
    
    Args:
        name: Logger name (typically __name__)
    
    Returns:
        StructuredLogger instance
    """
    return StructuredLogger(name)


def set_request_id(request_id: str) -> None:
    """Set request ID for current context"""
    request_id_var.set(request_id)


def get_request_id() -> Optional[str]:
    """Get request ID from current context"""
    return request_id_var.get()


def clear_request_id() -> None:
    """Clear request ID from current context"""
    request_id_var.set(None)


# Convenience function for migration from print statements
def log_info(message: str, **kwargs):
    """Quick info log (for migration from print statements)"""
    logger = get_logger("contex")
    logger.info(message, **kwargs)


def log_error(message: str, **kwargs):
    """Quick error log"""
    logger = get_logger("contex")
    logger.error(message, **kwargs)


def log_warning(message: str, **kwargs):
    """Quick warning log"""
    logger = get_logger("contex")
    logger.warning(message, **kwargs)


def log_debug(message: str, **kwargs):
    """Quick debug log"""
    logger = get_logger("contex")
    logger.debug(message, **kwargs)
