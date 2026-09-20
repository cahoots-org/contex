"""Integration tests using moto to mock S3.

Seeded with several objects covering text, binary, oversized, and JSON types.
Asserts that only eligible objects are yielded, and that re-running upserts
(idempotent key — no duplication) rather than inserting duplicates.

Skipped gracefully when moto is not installed.
"""
from __future__ import annotations

import json

import pytest

try:
    import boto3
    from moto import mock_aws

    MOTO_AVAILABLE = True
except ImportError:
    MOTO_AVAILABLE = False

pytestmark = pytest.mark.skipif(not MOTO_AVAILABLE, reason="moto not installed")

BUCKET = "test-bucket"
# us-east-1 is the default region; bucket creation there requires no
# LocationConstraint, which moto also enforces.
REGION = "us-east-1"


def _make_config(
    bucket: str = BUCKET,
    prefix: str = "",
    max_object_bytes: int = 5 * 1024 * 1024,
    keys: dict | None = None,
    content_types: list[str] | None = None,
) -> dict:
    cfg: dict = {
        "contex": {"url": "http://localhost:8001/mcp", "project_id": "test"},
        "source": {"bucket": bucket, "prefix": prefix, "region": REGION},
        "max_object_bytes": max_object_bytes,
    }
    if keys:
        cfg["keys"] = keys
    if content_types:
        cfg["content_types"] = content_types
    return cfg


def _seed_bucket(s3_client) -> None:
    # us-east-1 is the default; do not pass CreateBucketConfiguration
    s3_client.create_bucket(Bucket=BUCKET)
    # Eligible: plain text
    s3_client.put_object(Bucket=BUCKET, Key="docs/readme.md", Body=b"# Hello world")
    # Eligible: JSON -> parsed dict
    s3_client.put_object(
        Bucket=BUCKET, Key="config/settings.json", Body=json.dumps({"env": "prod"}).encode()
    )
    # Eligible: CSV
    s3_client.put_object(Bucket=BUCKET, Key="data/export.csv", Body=b"a,b\n1,2")
    # Skip: binary extension
    s3_client.put_object(Bucket=BUCKET, Key="images/logo.png", Body=b"\x89PNG\r\n")
    # Skip: oversized (exceeds a tiny cap)
    s3_client.put_object(Bucket=BUCKET, Key="large/file.txt", Body=b"x" * 200)
    # Skip: excluded by glob
    s3_client.put_object(Bucket=BUCKET, Key="archive/old.md", Body=b"old content")


@mock_aws
def test_eligible_objects_yielded():
    from connectors.s3.reader import read_objects

    s3 = boto3.client("s3", region_name=REGION)
    _seed_bucket(s3)

    config = _make_config(
        max_object_bytes=100,  # small cap so large/file.txt (200 bytes) is skipped
        keys={"exclude": ["archive/*"]},
    )
    events = list(read_objects(config))
    keys = {e.key for e in events}

    assert "docs/readme.md" in keys
    assert "config/settings.json" in keys
    assert "data/export.csv" in keys


@mock_aws
def test_binary_objects_skipped():
    from connectors.s3.reader import read_objects

    s3 = boto3.client("s3", region_name=REGION)
    _seed_bucket(s3)

    config = _make_config(max_object_bytes=100, keys={"exclude": ["archive/*"]})
    events = list(read_objects(config))
    keys = {e.key for e in events}

    assert "images/logo.png" not in keys


@mock_aws
def test_oversized_objects_skipped():
    from connectors.s3.reader import read_objects

    s3 = boto3.client("s3", region_name=REGION)
    _seed_bucket(s3)

    config = _make_config(max_object_bytes=100, keys={"exclude": ["archive/*"]})
    events = list(read_objects(config))
    keys = {e.key for e in events}

    assert "large/file.txt" not in keys


@mock_aws
def test_excluded_objects_skipped():
    from connectors.s3.reader import read_objects

    s3 = boto3.client("s3", region_name=REGION)
    _seed_bucket(s3)

    config = _make_config(max_object_bytes=100, keys={"exclude": ["archive/*"]})
    events = list(read_objects(config))
    keys = {e.key for e in events}

    assert "archive/old.md" not in keys


@mock_aws
def test_json_object_mapped_correctly():
    from connectors.s3.reader import read_objects

    s3 = boto3.client("s3", region_name=REGION)
    _seed_bucket(s3)

    config = _make_config(max_object_bytes=100, keys={"exclude": ["archive/*"]})
    events = {e.key: e for e in read_objects(config)}

    json_event = events["config/settings.json"]
    assert json_event.data_format == "json"
    assert json_event.payload == {"env": "prod"}


@mock_aws
def test_text_object_mapped_correctly():
    from connectors.s3.reader import read_objects

    s3 = boto3.client("s3", region_name=REGION)
    _seed_bucket(s3)

    config = _make_config(max_object_bytes=100, keys={"exclude": ["archive/*"]})
    events = {e.key: e for e in read_objects(config)}

    md_event = events["docs/readme.md"]
    assert md_event.data_format == "text"
    assert md_event.payload == "# Hello world"


@mock_aws
def test_rerun_yields_same_keys_upsert_semantics():
    """Re-running produces the same keys — callers deduplicate via key upsert."""
    from connectors.s3.reader import read_objects

    s3 = boto3.client("s3", region_name=REGION)
    _seed_bucket(s3)

    config = _make_config(max_object_bytes=100, keys={"exclude": ["archive/*"]})

    first_run_keys = {e.key for e in read_objects(config)}
    second_run_keys = {e.key for e in read_objects(config)}

    assert first_run_keys == second_run_keys


@mock_aws
def test_prefix_filters_objects():
    from connectors.s3.reader import read_objects

    s3 = boto3.client("s3", region_name=REGION)
    _seed_bucket(s3)

    config = _make_config(prefix="docs/", max_object_bytes=1024)
    events = list(read_objects(config))
    keys = {e.key for e in events}

    assert keys == {"docs/readme.md"}


@mock_aws
def test_content_types_override():
    from connectors.s3.reader import read_objects

    s3 = boto3.client("s3", region_name=REGION)
    _seed_bucket(s3)

    # Only allow .json — md and csv should be skipped
    config = _make_config(
        max_object_bytes=1024,
        content_types=[".json"],
        keys={"exclude": ["archive/*"]},
    )
    events = list(read_objects(config))
    keys = {e.key for e in events}

    assert "config/settings.json" in keys
    assert "docs/readme.md" not in keys
    assert "data/export.csv" not in keys
