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

_ENV_PATTERN = re.compile(r"\$\{([^}]+)\}")

DEFAULT_BATCH_SIZE = 500
# Files larger than this are binaries, fixtures, or generated data — not content
# worth embedding. Skipped at the source (no fetch).
DEFAULT_MAX_FILE_BYTES = 1_048_576  # 1 MiB
# Flush a publish batch before it would exceed this many bytes, keeping each
# request well under the server's upload cap (~50 MB) regardless of batch_size.
DEFAULT_MAX_BATCH_BYTES = 40_000_000


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


def resolve_max_file_bytes(config: dict) -> int:
    """Per-file size cap from config; files above it are skipped. Defaults when unset."""
    return int(config.get("max_file_bytes") or DEFAULT_MAX_FILE_BYTES)


def resolve_max_batch_bytes(config: dict) -> int:
    """Per-request byte cap for a publish batch, defaulting when unset."""
    return int(config.get("max_batch_bytes") or DEFAULT_MAX_BATCH_BYTES)


def resolve_secret_scanner(config: dict):
    """Build the secret-ingestion guard from config, or None when disabled.

    Protection is on by default. ``allow_secrets: true`` (secrets are wanted as
    context) returns None — no scanning. Otherwise an optional ``secrets:`` block
    tunes it: ``scan_content`` (default true) and ``file_patterns`` /
    ``content_patterns`` lists that *extend* the built-in defaults.
    """
    from .secrets import (
        DEFAULT_SECRET_CONTENT_PATTERNS,
        DEFAULT_SECRET_FILE_PATTERNS,
        SecretScanner,
    )

    if bool(config.get("allow_secrets", False)):
        return None
    sec = config.get("secrets") or {}
    return SecretScanner(
        file_patterns=(*DEFAULT_SECRET_FILE_PATTERNS, *(sec.get("file_patterns") or ())),
        content_patterns=(*DEFAULT_SECRET_CONTENT_PATTERNS, *(sec.get("content_patterns") or ())),
        scan_content=bool(sec.get("scan_content", True)),
    )


@dataclass
class ContexConfig:
    """Where a connector publishes: the MCP endpoint, project, and token."""

    url: str
    project_id: str
    service_account_token: str | None = None

    @classmethod
    def from_dict(cls, config: dict) -> "ContexConfig":
        contex = config.get("contex") or {}
        url = contex.get("url")
        project_id = contex.get("project_id")
        if not url:
            raise ValueError("contex.url is required")
        if not project_id:
            raise ValueError("contex.project_id is required")
        # An unset ${VAR} expands to "" — treat that as no token (auth off).
        token = contex.get("service_account_token") or None
        return cls(url=url, project_id=project_id, service_account_token=token)
