"""Config loading shared by every connector.

A connector reads a ``connector.yaml`` with a ``contex`` block (where to
publish) and a ``source`` block (connector-specific). ``${VAR}`` references are
expanded from the environment so secrets stay out of the file.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass

import yaml

from .secrets import DEFAULT_SECRET_CONTENT_PATTERNS, DEFAULT_SECRET_FILE_PATTERNS, SecretScanner

_ENV_PATTERN = re.compile(r"\$\{([^}]+)\}")

DEFAULT_BATCH_SIZE = 500
DEFAULT_FLUSH_INTERVAL = 1.0  # seconds a streamed event may wait for batch-mates
# Files larger than this are binaries, fixtures, or generated data — not content
# worth embedding. Skipped at the source (no fetch).
DEFAULT_MAX_FILE_BYTES = 1_048_576  # 1 MiB


def _expand_env(value):
    """Recursively replace ``${VAR}`` with the environment value ("" if unset)."""
    if isinstance(value, str):
        return _ENV_PATTERN.sub(lambda m: os.environ.get(m.group(1), ""), value)
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    return value


def load_config(path: str) -> dict:
    """Load a connector.yaml and expand ``${VAR}`` references."""
    with open(path) as handle:
        raw = yaml.safe_load(handle) or {}
    if not isinstance(raw, dict):
        raise ValueError("connector config must be a mapping")
    return _expand_env(raw)


def resolve_batch_size(config: dict) -> int:
    """The publish batch size from config, defaulting when unset."""
    return int(config.get("batch_size") or DEFAULT_BATCH_SIZE)


def resolve_flush_interval(config: dict) -> float:
    """Seconds a streaming batch waits before flushing, defaulting when unset."""
    raw = config.get("flush_interval")
    value = DEFAULT_FLUSH_INTERVAL if raw is None or raw == "" else float(raw)
    if value <= 0:
        raise ValueError(f"flush_interval must be positive, got {value}")
    return value


def resolve_max_file_bytes(config: dict) -> int:
    """Per-file size cap from config; files above it are skipped. Defaults when unset."""
    return int(config.get("max_file_bytes") or DEFAULT_MAX_FILE_BYTES)


def resolve_flag(config: dict, name: str, default: bool) -> bool:
    """A true/false setting; an empty value (an unset ``${VAR}``) means ``default``."""
    value = config.get(name)
    if value is None or value == "":
        return default
    text = str(value).strip().lower()
    if text in ("true", "yes", "on", "1"):
        return True
    if text in ("false", "no", "off", "0"):
        return False
    raise ValueError(f"{name} must be true or false, got {value!r}")


def resolve_prune(config: dict) -> bool:
    """Whether a full run deletes what the source no longer has (default on)."""
    return resolve_flag(config, "prune", True)


def resolve_secret_scanner(config: dict):
    """Build the secret-ingestion guard from config, or None when disabled.

    Protection is on by default. ``allow_secrets: true`` (secrets are wanted as
    context) returns None — no scanning. Otherwise an optional ``secrets:`` block
    tunes it:

    - ``scan_content`` (default true) — also scan file *contents*, not just names.
    - ``files`` — filename globs (e.g. ``"*.secret"``) added to the built-in
      secret-file list.
    - ``content`` — regexes added to the built-in content markers.
    """
    if resolve_flag(config, "allow_secrets", False):
        return None
    sec = config.get("secrets") or {}
    return SecretScanner(
        file_patterns=(*DEFAULT_SECRET_FILE_PATTERNS, *(sec.get("files") or ())),
        content_patterns=(*DEFAULT_SECRET_CONTENT_PATTERNS, *(sec.get("content") or ())),
        scan_content=resolve_flag(sec, "scan_content", True),
    )


@dataclass
class ContexConfig:
    """Where a connector publishes: the MCP endpoint, project, API key, and stream name."""

    url: str
    project_id: str
    api_key: str | None = None
    origin: str | None = None

    @classmethod
    def from_dict(cls, config: dict) -> "ContexConfig":
        contex = config.get("contex") or {}
        url = contex.get("url")
        project_id = contex.get("project_id")
        if not url:
            raise ValueError("contex.url is required")
        if not project_id:
            raise ValueError("contex.project_id is required")
        # An unset ${VAR} expands to "" — treat that as no key (auth off).
        return cls(
            url=url,
            project_id=project_id,
            api_key=contex.get("api_key") or None,
            origin=config.get("origin") or None,
        )
