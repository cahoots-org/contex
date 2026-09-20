"""Allow/deny selection with glob patterns, shared by every connector.

Connectors narrow what they ingest with include/exclude glob lists over a
string identity (``schema.table``, an object key, a file path). The rule is the
same everywhere: if an include list is given, the value must match one of its
patterns; then any exclude match rejects it.
"""
from __future__ import annotations

from collections.abc import Sequence
from fnmatch import fnmatch


def matches_any(value: str, patterns: Sequence[str] | None) -> bool:
    """True if ``value`` matches at least one glob in ``patterns``."""
    return bool(patterns) and any(fnmatch(value, p) for p in patterns)


def allowed(
    value: str,
    include: Sequence[str] | None = None,
    exclude: Sequence[str] | None = None,
) -> bool:
    """Apply include/exclude glob lists to a single value.

    An empty/absent ``include`` means "everything is a candidate". A non-empty
    ``include`` keeps only matches. ``exclude`` then removes any match.
    """
    if include and not matches_any(value, include):
        return False
    if matches_any(value, exclude):
        return False
    return True
