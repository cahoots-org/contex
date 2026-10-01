"""Secret-ingestion guard: detection, config switch, and runner skip."""
from __future__ import annotations

import asyncio

from connectors.base import ChangeEvent, resolve_secret_scanner
from connectors.base.runner import run
from connectors.base.secrets import SecretScanner


# --- detection -------------------------------------------------------------

def test_flags_secret_filenames():
    s = SecretScanner()
    for key in ["acme/repo:.env", "acme/repo:deploy/id_rsa", "acme/repo:certs/server.pem"]:
        assert s.reason(key, "whatever") is not None
    assert s.reason("acme/repo:src/main.py", "x = 1") is None


def test_flags_secret_content_in_normal_file():
    s = SecretScanner()
    key = "acme/repo:config/settings.py"
    assert s.reason(key, 'AWS = "AKIAIOSFODNN7EXAMPLE"') is not None
    assert s.reason(key, "-----BEGIN RSA PRIVATE KEY-----\nMIIE...\n") is not None
    assert s.reason(key, "def add(a, b):\n    return a + b\n") is None


def test_reason_names_pattern_not_value():
    s = SecretScanner()
    why = s.reason("r:config.py", 'k = "AKIAIOSFODNN7EXAMPLE"')
    assert "AKIAIOSFODNN7EXAMPLE" not in why  # never leak the secret into logs


def test_scan_content_toggle_off_keeps_filename_check():
    s = SecretScanner(scan_content=False)
    assert s.reason("r:config.py", 'k = "AKIAIOSFODNN7EXAMPLE"') is None  # content not scanned
    assert s.reason("r:.env", "x") is not None                           # filename still blocked


# --- config switch ---------------------------------------------------------

def test_allow_secrets_disables_scanner():
    assert resolve_secret_scanner({"allow_secrets": True}) is None


def test_default_protects_and_extra_patterns_add_to_builtins():
    s = resolve_secret_scanner({"secrets": {"extra_file_patterns": ["*.secret"]}})
    assert s is not None
    assert s.reason("r:vault.secret", "x") is not None   # extra pattern
    assert s.reason("r:.env", "x") is not None            # built-in still applies


# --- runner integration ----------------------------------------------------

class _RecordingPublisher:
    def __init__(self):
        self.published_keys = []

    async def publish_batch(self, items):
        self.published_keys.extend(i["data_key"] for i in items)
        return len(items)


def test_runner_skips_secret_events_and_counts_them():
    pub = _RecordingPublisher()
    events = [
        ChangeEvent(op="upsert", key="r:src/app.py", payload="x = 1", data_format="text"),
        ChangeEvent(op="upsert", key="r:.env", payload="TOKEN=abc", data_format="text"),
        ChangeEvent(op="upsert", key="r:config.py", payload='k="AKIAIOSFODNN7EXAMPLE"', data_format="text"),
    ]
    stats = asyncio.run(run(events, pub, secret_scanner=SecretScanner()))
    assert pub.published_keys == ["r:src/app.py"]
    assert stats.skipped_secrets == 2
    assert stats.published == 1
