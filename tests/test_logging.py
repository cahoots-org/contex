"""Tests for structured logging"""

import json
import logging
from datetime import datetime
from io import StringIO
from src.core.logging import (
    setup_logging,
    get_logger,
    StructuredFormatter,
    StructuredLogger
)


class TestStructuredFormatter:
    """Test JSON formatter"""
    
    def test_format_basic_log(self):
        """Test formatting a basic log message"""
        formatter = StructuredFormatter(service_name="test-service")
        record = logging.LogRecord(
            name="test.logger",
            level=logging.INFO,
            pathname="/path/to/file.py",
            lineno=42,
            msg="Test message",
            args=(),
            exc_info=None
        )
        
        output = formatter.format(record)
        log_data = json.loads(output)
        
        assert log_data["service"] == "test-service"
        assert log_data["level"] == "INFO"
        assert log_data["logger"] == "test.logger"
        assert log_data["message"] == "Test message"
        assert "timestamp" in log_data
    
    def test_format_with_extra_fields(self):
        """Test formatting with extra fields"""
        formatter = StructuredFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="/path",
            lineno=1,
            msg="Test",
            args=(),
            exc_info=None
        )
        record.extra_fields = {"user_id": "user-123", "action": "login"}
        
        output = formatter.format(record)
        log_data = json.loads(output)
        
        assert log_data["user_id"] == "user-123"
        assert log_data["action"] == "login"
    
    def test_format_with_exception(self):
        """Test formatting with exception info"""
        formatter = StructuredFormatter()
        
        try:
            raise ValueError("Test error")
        except ValueError:
            import sys
            exc_info = sys.exc_info()
        
        record = logging.LogRecord(
            name="test",
            level=logging.ERROR,
            pathname="/path",
            lineno=1,
            msg="Error occurred",
            args=(),
            exc_info=exc_info
        )
        
        output = formatter.format(record)
        log_data = json.loads(output)
        
        assert "ValueError: Test error" in log_data["exception"]
    
    def test_format_includes_location_for_warnings(self):
        """Test that location is included for warnings and errors"""
        formatter = StructuredFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.WARNING,
            pathname="/path/to/file.py",
            lineno=42,
            msg="Warning message",
            args=(),
            exc_info=None,
            func="test_function"
        )
        
        output = formatter.format(record)
        log_data = json.loads(output)
        
        assert "location" in log_data
        assert log_data["location"]["file"] == "/path/to/file.py"
        assert log_data["location"]["line"] == 42
        assert log_data["location"]["function"] == "test_function"


class TestStructuredLogger:
    """Test structured logger wrapper"""
    
    def test_get_logger(self):
        """Test getting a logger instance"""
        logger = get_logger("test.module")
        assert isinstance(logger, StructuredLogger)
        assert logger.logger.name == "test.module"
    
    def test_log_levels(self):
        """Test different log levels"""
        setup_logging(level="DEBUG", json_output=False)
        logger = get_logger("test.log_levels")
        
        # Manually check that methods exist and don't raise
        logger.debug("Debug message")
        logger.info("Info message")
        logger.warning("Warning message")
        logger.error("Error message")
        logger.critical("Critical message")
        
        # Test passes if no exceptions raised
        assert True
    
    def test_log_with_context(self, caplog):
        """Test logging with contextual fields"""
        setup_logging(level="INFO", json_output=True)
        logger = get_logger("test")
        
        # Capture log output
        stream = StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(StructuredFormatter())
        logging.getLogger("test").addHandler(handler)
        
        logger.info("User action", user_id="user-123", action="login")
        
        output = stream.getvalue()
        log_data = json.loads(output)
        
        assert log_data["message"] == "User action"
        assert log_data["user_id"] == "user-123"
        assert log_data["action"] == "login"


class TestLoggingSetup:
    """Test logging configuration"""
    
    def test_setup_json_output(self):
        """Test setup with JSON output"""
        setup_logging(level="INFO", json_output=True, service_name="test-service")
        
        root_logger = logging.getLogger()
        assert root_logger.level == logging.INFO
        assert len(root_logger.handlers) > 0
        
        # Check formatter
        handler = root_logger.handlers[0]
        assert isinstance(handler.formatter, StructuredFormatter)
    
    def test_setup_human_readable_output(self):
        """Test setup with human-readable output"""
        setup_logging(level="DEBUG", json_output=False)
        
        root_logger = logging.getLogger()
        assert root_logger.level == logging.DEBUG
        
        # Check formatter is not StructuredFormatter
        handler = root_logger.handlers[0]
        assert not isinstance(handler.formatter, StructuredFormatter)
    
    def test_setup_log_levels(self):
        """Test different log levels"""
        for level in ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]:
            setup_logging(level=level, json_output=False)
            root_logger = logging.getLogger()
            assert root_logger.level == getattr(logging, level)
    
    def test_silences_noisy_loggers(self):
        """Test that noisy third-party loggers are silenced"""
        setup_logging(level="DEBUG", json_output=False)
        
        httpx_logger = logging.getLogger("httpx")
        httpcore_logger = logging.getLogger("httpcore")
        uvicorn_logger = logging.getLogger("uvicorn.access")
        
        assert httpx_logger.level == logging.WARNING
        assert httpcore_logger.level == logging.WARNING
        assert uvicorn_logger.level == logging.WARNING



def test_logger_keeps_traceback_and_caller_location():
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(StructuredFormatter())
    logging.getLogger("test.caller").addHandler(handler)
    logger = get_logger("test.caller")

    try:
        raise ValueError("boom")
    except ValueError:
        logger.warning("failed", when=datetime(2026, 1, 1), exc_info=True)

    log_data = json.loads(stream.getvalue())
    assert "ValueError: boom" in log_data["exception"]
    assert log_data["location"]["file"] == __file__
    assert log_data["when"] == "2026-01-01 00:00:00"
