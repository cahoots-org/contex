"""S3 reader — list, filter, fetch, and map objects to ChangeEvents.

Pure functions (filter_key, is_text_extension, object_to_event) are kept
separate so they can be unit-tested without AWS credentials or a live bucket.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Iterator

import boto3

from connectors.base import ChangeEvent, allowed, document_event, document_format, resolve_document_types

logger = logging.getLogger(__name__)

DEFAULT_MAX_BYTES = 5 * 1024 * 1024  # 5 MB

DEFAULT_TEXT_EXTENSIONS = frozenset(
    [".txt", ".md", ".markdown", ".json", ".csv", ".tsv", ".yaml", ".yml", ".html", ".log", ".rst"]
)


def is_text_extension(key: str, allowed_extensions: frozenset[str]) -> bool:
    """True when the object key has a text-like file extension."""
    _, ext = os.path.splitext(key.lower())
    return ext in allowed_extensions


def filter_key(
    key: str,
    include: list[str] | None,
    exclude: list[str] | None,
    allowed_extensions: frozenset[str],
) -> bool:
    """Apply glob allow/deny and extension filter to a single object key."""
    if not allowed(key, include=include, exclude=exclude):
        return False
    return is_text_extension(key, allowed_extensions)


def object_to_event(
    key: str, body: bytes, last_modified: datetime | None = None,
    document_types: frozenset[str] = frozenset(),
) -> ChangeEvent:
    """Map a fetched S3 object to a ChangeEvent.

    Objects in ``document_types`` are sent base64 for the server to extract.
    .json objects are parsed into a dict (data_format="json").
    All other text objects are decoded as UTF-8 (data_format="text").
    """
    published_at = last_modified.isoformat() if last_modified else None
    doc_format = document_format(key, document_types)
    if doc_format is not None:
        return document_event(key, body, doc_format, source_meta={"source": "s3"}, published_at=published_at)
    _, ext = os.path.splitext(key.lower())
    if ext == ".json":
        payload = json.loads(body.decode("utf-8"))
        return ChangeEvent(
            op="upsert",
            key=key,
            payload=payload,
            source_meta={"source": "s3"},
            data_format="json",
            published_at=published_at,
        )
    text = body.decode("utf-8")
    return ChangeEvent(
        op="upsert",
        key=key,
        payload=text,
        source_meta={"source": "s3"},
        data_format="text",
        published_at=published_at,
    )


def _build_extensions(config: dict) -> frozenset[str]:
    raw = config.get("content_types")
    if not raw:
        return DEFAULT_TEXT_EXTENSIONS
    return frozenset(ext if ext.startswith(".") else f".{ext}" for ext in raw)


def _client_kwargs(source: dict) -> dict:
    """Assemble boto3 S3 client kwargs from the source config.

    ``endpoint_url`` targets S3-compatible stores (MinIO, R2, LocalStack).
    Credentials fall back to the standard AWS chain when not set explicitly.
    """
    kwargs: dict = {}
    if source.get("region"):
        kwargs["region_name"] = source["region"]
    if source.get("endpoint_url"):
        kwargs["endpoint_url"] = source["endpoint_url"]
    access_key = source.get("aws_access_key_id") or os.environ.get("AWS_ACCESS_KEY_ID")
    secret_key = source.get("aws_secret_access_key") or os.environ.get("AWS_SECRET_ACCESS_KEY")
    if access_key and secret_key:
        kwargs["aws_access_key_id"] = access_key
        kwargs["aws_secret_access_key"] = secret_key
    return kwargs


def read_objects(config: dict) -> Iterator[ChangeEvent]:
    """Yield a ChangeEvent for each eligible object in the configured bucket.

    Pagination, key filtering, content-type filtering, and size limits are all
    handled here. Skipped objects are logged at DEBUG/WARNING level.
    """
    source = config.get("source") or {}
    bucket = source["bucket"]
    prefix = source.get("prefix", "")

    client = boto3.client("s3", **_client_kwargs(source))

    include: list[str] | None = (config.get("keys") or {}).get("include") or None
    exclude: list[str] | None = (config.get("keys") or {}).get("exclude") or None
    document_types = resolve_document_types(config)
    extensions = _build_extensions(config) | document_types
    max_bytes: int = int(config.get("max_object_bytes") or DEFAULT_MAX_BYTES)

    paginator = client.get_paginator("list_objects_v2")
    pages = paginator.paginate(Bucket=bucket, Prefix=prefix)

    for page in pages:
        for obj in page.get("Contents") or []:
            key: str = obj["Key"]
            size: int = obj["Size"]

            if not filter_key(key, include, exclude, extensions):
                logger.debug("skipping %s (filtered by key/extension)", key)
                continue

            if size > max_bytes:
                logger.warning(
                    "skipping %s (%d bytes > max_object_bytes %d)", key, size, max_bytes
                )
                continue

            logger.debug("fetching %s (%d bytes)", key, size)
            resp = client.get_object(Bucket=bucket, Key=key)
            body: bytes = resp["Body"].read()

            try:
                event = object_to_event(key, body, obj.get("LastModified"), document_types)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                logger.warning("skipping %s: %s", key, exc)
                continue

            yield event
