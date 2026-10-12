"""Logging setup: stdlib logging with a JSON formatter and keyword-argument fields."""

import json
import logging
import sys
from datetime import datetime, timezone

_STDLIB_KWARGS = {"exc_info", "stack_info", "stacklevel", "extra"}


class StructuredFormatter(logging.Formatter):
    """Format a record as one JSON object, including its keyword fields and traceback."""

    def __init__(self, service_name: str = "contex"):
        super().__init__()
        self.service_name = service_name

    def format(self, record: logging.LogRecord) -> str:
        log_data = {
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "level": record.levelname,
            "service": self.service_name,
            "logger": record.name,
            "message": record.getMessage(),
            **getattr(record, "extra_fields", {}),
        }
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)
        if record.levelno >= logging.WARNING:
            log_data["location"] = {
                "file": record.pathname,
                "line": record.lineno,
                "function": record.funcName,
            }
        return json.dumps(log_data, default=str)


class StructuredLogger(logging.LoggerAdapter):
    """Logger that takes structured fields as keyword arguments: logger.info("msg", key=value)."""

    def process(self, msg, kwargs):
        fields = {k: kwargs.pop(k) for k in list(kwargs) if k not in _STDLIB_KWARGS}
        kwargs["extra"] = {**kwargs.get("extra", {}), "extra_fields": fields}
        return msg, kwargs


def setup_logging(level: str = "INFO", json_output: bool = True, service_name: str = "contex") -> None:
    """Send all logs to stdout, as JSON or plain text."""
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, level.upper()))
    root_logger.handlers.clear()

    handler = logging.StreamHandler(sys.stdout)
    if json_output:
        handler.setFormatter(StructuredFormatter(service_name=service_name))
    else:
        handler.setFormatter(logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
        ))
    root_logger.addHandler(handler)

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def get_logger(name: str) -> StructuredLogger:
    """Return a StructuredLogger for the named stdlib logger."""
    return StructuredLogger(logging.getLogger(name))
