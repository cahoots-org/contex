# Logging

Contex logs to stdout through Python's `logging` module, one JSON object per line
by default.

| Variable | Default | Effect |
|----------|---------|--------|
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` or `CRITICAL` |
| `LOG_JSON` | `true` | `false` prints plain text lines instead (structured fields are dropped) |

## Record format

```json
{
  "timestamp": "2026-10-11T21:38:52.641450Z",
  "level": "WARNING",
  "service": "contex",
  "logger": "src.core.auth",
  "message": "Bootstrap admin key created",
  "key_id": "3165518bf971e608",
  "name": "root",
  "location": {"file": "/app/src/core/auth.py", "line": 125, "function": "bootstrap_admin"}
}
```

- Fields passed as keyword arguments (`key_id` and `name` above) appear at the top level.
- `exception` holds the formatted traceback when the record carries one.
- `location` is added at `WARNING` and above.

Uvicorn's access log is silenced below `WARNING`.

## In code

```python
from src.core.logging import get_logger

logger = get_logger(__name__)
logger.info("Subscription created", subscription_id=sub_id)

try:
    ...
except Exception:
    logger.exception("Reconcile failed", project_id=project_id)
```

`get_logger` returns a standard `logging.LoggerAdapter`, so `exc_info`,
`stack_info` and `%`-style arguments work as usual. Never log API keys, tokens
or published data.
