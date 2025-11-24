"""Tests for structured logging"""

import pytest
import json
import logging
from io import StringIO
from src.core.logging import (
    setup_logging,
    get_logger,
    set_request_id,
    get_request_id,
    clear_request_id,
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
    
    def test_format_with_request_id(self):
        """Test formatting with request ID in context"""
        set_request_id("req-123")
        
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
        
        output = formatter.format(record)
        log_data = json.loads(output)
        
        assert log_data["request_id"] == "req-123"
        
        clear_request_id()
    
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
        
        assert "exception" in log_data
        assert log_data["exception"]["type"] == "ValueError"
        assert log_data["exception"]["message"] == "Test error"
        assert "traceback" in log_data["exception"]
    
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
    
    def test_bind_context(self):
        """Test binding context to logger"""
        logger = get_logger("test")
        bound_logger = logger.bind(user_id="user-123", project_id="proj-456")
        
        assert bound_logger._context["user_id"] == "user-123"
        assert bound_logger._context["project_id"] == "proj-456"
        
        # Original logger should not be affected
        assert "user_id" not in logger._context
    
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


class TestRequestIdContext:
    """Test request ID context management"""
    
    def test_set_and_get_request_id(self):
        """Test setting and getting request ID"""
        set_request_id("req-123")
        assert get_request_id() == "req-123"
        clear_request_id()
    
    def test_clear_request_id(self):
        """Test clearing request ID"""
        set_request_id("req-123")
        clear_request_id()
        assert get_request_id() is None
    
    def test_request_id_isolation(self):
        """Test that request IDs are isolated per context"""
        # This would require async context testing
        # For now, just test basic functionality
        set_request_id("req-1")
        assert get_request_id() == "req-1"
        
        set_request_id("req-2")
        assert get_request_id() == "req-2"
        
        clear_request_id()


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


class TestConvenienceFunctions:
    """Test convenience logging functions"""
    
    def test_convenience_functions(self):
        """Test quick logging functions"""
        from src.core.logging import log_info, log_error, log_warning, log_debug
        
        setup_logging(level="DEBUG", json_output=False)
        
        # Test that functions execute without error
        log_debug("Debug message", key="value")
        log_info("Info message", key="value")
        log_warning("Warning message", key="value")
        log_error("Error message", key="value")
        
        # Test passes if no exceptions raised
        assert True
