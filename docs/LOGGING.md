# Structured Logging

Contex uses structured logging with JSON output for production-ready observability.

## Overview

Structured logging provides:
- **JSON-formatted logs** for easy parsing by log aggregators
- **Request ID tracking** for request correlation
- **Contextual fields** for rich metadata
- **Consistent log levels** (DEBUG, INFO, WARNING, ERROR, CRITICAL)
- **Automatic error tracking** with stack traces

## Quick Start

### Basic Logging

```python
from src.core.logging import get_logger

logger = get_logger(__name__)

# Simple log
logger.info("User logged in")

# Log with context
logger.info("Data published",
           project_id="proj-123",
           data_key="config",
           sequence=42)
```

### Output (JSON)

```json
{
  "timestamp": "2025-11-21T22:10:30Z",
  "level": "INFO",
  "service": "contex",
  "logger": "src.api.routes",
  "message": "Data published",
  "request_id": "550e8400-e29b-41d4-a716-446655440000",
  "project_id": "proj-123",
  "data_key": "config",
  "sequence": 42
}
```

## Configuration

### Environment Variables

```bash
# Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
LOG_LEVEL=INFO

# Output format (true = JSON, false = human-readable)
LOG_JSON=true
```

### Setup in Code

```python
from src.core.logging import setup_logging

# Production (JSON output)
setup_logging(level="INFO", json_output=True, service_name="contex")

# Development (human-readable)
setup_logging(level="DEBUG", json_output=False, service_name="contex")
```

## Usage Patterns

### 1. Basic Logging

```python
from src.core.logging import get_logger

logger = get_logger(__name__)

logger.debug("Debugging information")
logger.info("Informational message")
logger.warning("Warning message")
logger.error("Error occurred")
logger.critical("Critical failure")
```

### 2. Logging with Context

```python
logger.info("Agent registered",
           agent_id="agent-123",
           project_id="proj-456",
           needs_count=5,
           notification_method="webhook")
```

Output:
```json
{
  "timestamp": "2025-11-21T22:10:30Z",
  "level": "INFO",
  "message": "Agent registered",
  "agent_id": "agent-123",
  "project_id": "proj-456",
  "needs_count": 5,
  "notification_method": "webhook"
}
```

### 3. Binding Context

Create a logger with persistent context:

```python
# Base logger
logger = get_logger(__name__)

# Create bound logger with context
request_logger = logger.bind(
    user_id="user-123",
    session_id="sess-456"
)

# All logs from this logger include the bound context
request_logger.info("Action performed", action="login")
request_logger.info("Data accessed", resource="profile")
```

Output includes `user_id` and `session_id` in every log.

### 4. Exception Logging

```python
try:
    # Some operation
    result = risky_operation()
except Exception as e:
    logger.exception("Operation failed",
                    operation="risky_operation",
                    input_data=data)
```

Output includes full stack trace:
```json
{
  "timestamp": "2025-11-21T22:10:30Z",
  "level": "ERROR",
  "message": "Operation failed",
  "operation": "risky_operation",
  "exception": {
    "type": "ValueError",
    "message": "Invalid input",
    "traceback": "Traceback (most recent call last):\n  ..."
  },
  "location": {
    "file": "/path/to/file.py",
    "line": 42,
    "function": "risky_operation"
  }
}
```

### 5. Request ID Tracking

Request IDs are automatically added by the logging middleware:

```python
# In middleware (automatic)
from src.core.logging import set_request_id
set_request_id("550e8400-e29b-41d4-a716-446655440000")

# All subsequent logs include request_id
logger.info("Processing request")  # Includes request_id automatically
```

## Log Levels

### When to Use Each Level

| Level | Use Case | Example |
|-------|----------|---------|
| **DEBUG** | Detailed diagnostic info | `logger.debug("Cache hit", key="user:123")` |
| **INFO** | General informational events | `logger.info("Agent registered", agent_id="...")` |
| **WARNING** | Warning messages | `logger.warning("Rate limit approaching", remaining=10)` |
| **ERROR** | Error events | `logger.error("Failed to publish", error=str(e))` |
| **CRITICAL** | Critical failures | `logger.critical("Redis connection lost")` |

### Log Level Guidelines

- **DEBUG**: Development only, verbose output
- **INFO**: Production default, important events
- **WARNING**: Potential issues, degraded performance
- **ERROR**: Failures that need attention
- **CRITICAL**: System-wide failures requiring immediate action

## Log Structure

### Standard Fields

Every log includes:
- `timestamp`: ISO 8601 timestamp (UTC)
- `level`: Log level (DEBUG, INFO, etc.)
- `service`: Service name ("contex")
- `logger`: Logger name (module path)
- `message`: Log message

### Optional Fields

- `request_id`: Request correlation ID (added by middleware)
- `exception`: Exception details (type, message, traceback)
- `location`: Source location (file, line, function) for WARNING+
- Custom fields: Any additional context you provide

## HTTP Request Logging

The logging middleware automatically logs all HTTP requests:

### Incoming Request
```json
{
  "timestamp": "2025-11-21T22:10:30Z",
  "level": "INFO",
  "message": "HTTP request received",
  "request_id": "550e8400-e29b-41d4-a716-446655440000",
  "method": "POST",
  "path": "/api/data/publish",
  "client_ip": "192.168.1.100",
  "user_agent": "Mozilla/5.0..."
}
```

### Completed Request
```json
{
  "timestamp": "2025-11-21T22:10:31Z",
  "level": "INFO",
  "message": "HTTP request completed",
  "request_id": "550e8400-e29b-41d4-a716-446655440000",
  "method": "POST",
  "path": "/api/data/publish",
  "status_code": 200,
  "duration_ms": 45.23
}
```

### Failed Request
```json
{
  "timestamp": "2025-11-21T22:10:31Z",
  "level": "ERROR",
  "message": "HTTP request failed",
  "request_id": "550e8400-e29b-41d4-a716-446655440000",
  "method": "POST",
  "path": "/api/data/publish",
  "duration_ms": 12.45,
  "error": "Invalid project ID",
  "error_type": "ValueError"
}
```

## Integration with Log Aggregators

### Elasticsearch / OpenSearch

Logs are JSON-formatted and ready for Elasticsearch:

```bash
# Ship logs to Elasticsearch
docker run -d \
  --name filebeat \
  -v /var/log/contex:/logs:ro \
  docker.elastic.co/beats/filebeat:8.0.0
```

### Datadog

```bash
# Set Datadog agent to parse JSON logs
DD_LOGS_CONFIG_CONTAINER_COLLECT_ALL=true \
DD_LOGS_CONFIG_AUTO_MULTI_LINE_DETECTION=true
```

### CloudWatch

```bash
# AWS CloudWatch Logs
aws logs create-log-group --log-group-name /contex/production
aws logs put-log-events --log-group-name /contex/production \
  --log-stream-name app --log-events file://logs.json
```

### Grafana Loki

```yaml
# promtail config
clients:
  - url: http://loki:3100/loki/api/v1/push

scrape_configs:
  - job_name: contex
    static_configs:
      - targets:
          - localhost
        labels:
          job: contex
          __path__: /var/log/contex/*.log
```

## Best Practices

### 1. Use Descriptive Messages

```python
# Good
logger.info("Agent registered successfully",
           agent_id=agent_id,
           project_id=project_id,
           matched_data_count=len(matches))

# Bad
logger.info("Done")
```

### 2. Include Relevant Context

```python
# Good - includes all relevant context
logger.error("Failed to publish data",
            project_id=event.project_id,
            data_key=event.data_key,
            error=str(e),
            error_type=type(e).__name__)

# Bad - missing context
logger.error("Publish failed")
```

### 3. Use Appropriate Log Levels

```python
# Good
logger.debug("Cache lookup", key=cache_key)  # Debug only
logger.info("Data published", sequence=seq)  # Important event
logger.error("Publish failed", error=str(e))  # Actual error

# Bad
logger.info("x = 5")  # Too verbose for INFO
logger.error("User logged in")  # Not an error
```

### 4. Don't Log Sensitive Data

```python
# Good
logger.info("User authenticated", user_id=user_id)

# Bad - logs sensitive data
logger.info("User authenticated",
           user_id=user_id,
           password=password,  # Never log passwords!
           api_key=api_key)    # Never log API keys!
```

### 5. Use Bound Loggers for Request Context

```python
# Good - bind request context once
request_logger = logger.bind(
    request_id=request_id,
    user_id=user_id,
    project_id=project_id
)

request_logger.info("Processing started")
request_logger.info("Data validated")
request_logger.info("Processing completed")

# Bad - repeat context in every log
logger.info("Processing started", request_id=request_id, user_id=user_id)
logger.info("Data validated", request_id=request_id, user_id=user_id)
```

## Querying Logs

### Find All Errors for a Request

```bash
# Using jq
cat logs.json | jq 'select(.request_id == "550e8400-..." and .level == "ERROR")'
```

### Find Slow Requests

```bash
# Requests taking > 1000ms
cat logs.json | jq 'select(.duration_ms > 1000)'
```

### Count Errors by Type

```bash
# Group errors by error_type
cat logs.json | jq -r 'select(.level == "ERROR") | .error_type' | sort | uniq -c
```

### Find All Logs for a Project

```bash
# All logs for a specific project
cat logs.json | jq 'select(.project_id == "proj-123")'
```

## Troubleshooting

### Logs Not Appearing

1. **Check log level**: Ensure `LOG_LEVEL` is set appropriately
   ```bash
   export LOG_LEVEL=DEBUG
   ```

2. **Check JSON output**: Verify `LOG_JSON` setting
   ```bash
   export LOG_JSON=true
   ```

3. **Check logger name**: Ensure you're using `get_logger(__name__)`

### Request IDs Missing

- Request IDs are added by the logging middleware
- Ensure middleware is registered in `main.py`
- Check that requests go through the middleware

### Logs Too Verbose

- Increase log level to `INFO` or `WARNING`
- Silence noisy third-party loggers (already done for httpx, uvicorn)

### JSON Parsing Errors

- Ensure each log is on a single line
- Use `json_output=True` for production
- Validate JSON with `jq` or similar tools

## Migration from Print Statements

### Before
```python
print(f"Publishing data: {project_id}/{data_key}")
print(f"Error: {str(e)}")
```

### After
```python
logger.info("Publishing data",
           project_id=project_id,
           data_key=data_key)
logger.error("Failed to publish",
            project_id=project_id,
            error=str(e))
```

### Convenience Functions

For quick migration:
```python
from src.core.logging import log_info, log_error

log_info("Publishing data", project_id=project_id)
log_error("Failed to publish", error=str(e))
```

## Performance Considerations

- Logging has minimal overhead (~1-2ms per log)
- JSON serialization is fast
- Use DEBUG level sparingly in production
- Consider async logging for high-throughput systems

## Related Documentation

- [Prometheus Metrics](METRICS.md) (coming soon)
- [Health Checks](../README.md#health-checks)
- [API Documentation](http://localhost:8001/docs)

## Summary

Contex structured logging provides:

✅ **JSON-formatted logs** for easy parsing  
✅ **Request ID tracking** for correlation  
✅ **Rich contextual fields** for debugging  
✅ **Automatic HTTP logging** via middleware  
✅ **Exception tracking** with stack traces  
✅ **Production-ready** for log aggregators  

All logs are structured, searchable, and ready for production observability platforms.
