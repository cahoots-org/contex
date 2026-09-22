"""JSON serialization helpers for the persistence layer.

Structured parsing (YAML in particular) can yield Python values that the stdlib
JSON encoder — and therefore Postgres JSON/JSONB columns — cannot serialize: a
bare ``2026-09-21`` in a YAML/text document becomes a ``datetime.date``, numeric
literals can become ``Decimal``, etc. Without handling, a single such value makes
the whole ``contex_publish_batch`` write raise ``TypeError: Object of type date
is not JSON serializable`` and the batch fails.

:func:`json_safe_default` coerces those types to serializable forms so a stray
date can't sink an entire publish.
"""
from __future__ import annotations

import datetime
from decimal import Decimal
from typing import Any


def json_safe_default(obj: Any) -> Any:
    """``default=`` callback for ``json.dumps`` covering common non-JSON types."""
    if isinstance(obj, (datetime.datetime, datetime.date, datetime.time)):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, (set, frozenset)):
        return list(obj)
    if isinstance(obj, bytes):
        return obj.decode("utf-8", "replace")
    return str(obj)
