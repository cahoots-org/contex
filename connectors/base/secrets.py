"""Secret-ingestion guard: skip items that are, or contain, secrets.

On by default. A connector can set ``allow_secrets: true`` to ingest secrets
intentionally — there are real use cases for storing credentials as context, so
this is a switch, not a wall. When protecting (the default), an item is skipped
if its key matches a secret *filename* pattern or its payload matches a secret
*content* pattern. Skips are logged by the runner with the matched pattern's
name — never the secret value.
"""
from __future__ import annotations

import json
import re
from collections.abc import Sequence
from fnmatch import fnmatch

# Files that are secrets by nature — never useful KB content.
DEFAULT_SECRET_FILE_PATTERNS: tuple[str, ...] = (
    ".env", ".env.*", "*.pem", "*.key", "*.p8", "*.p12", "*.pfx", "*.pkcs12",
    "*.keystore", "*.jks", "*.ovpn", "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519",
    ".npmrc", ".pypirc", ".netrc", ".htpasswd", "credentials", "*credentials*.json",
    "secrets.yml", "secrets.yaml",
)

# High-signal, low-false-positive content markers.
DEFAULT_SECRET_CONTENT_PATTERNS: tuple[str, ...] = (
    r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----",
    r"\bAKIA[0-9A-Z]{16}\b",                       # AWS access key id
    r"\bgh[pousr]_[A-Za-z0-9]{36,}\b",             # GitHub tokens
    r"\bxox[baprs]-[0-9A-Za-z-]{10,}\b",           # Slack tokens
    r"\bAIza[0-9A-Za-z_\-]{35}\b",                 # Google API key
    r"\bsk-[A-Za-z0-9]{20,}\b",                    # OpenAI-style secret key
    r"https://hooks\.slack\.com/services/[A-Za-z0-9/]+",
)


class SecretScanner:
    """Decides whether a ChangeEvent looks like a secret and must be skipped."""

    def __init__(
        self,
        file_patterns: Sequence[str] = DEFAULT_SECRET_FILE_PATTERNS,
        content_patterns: Sequence[str] = DEFAULT_SECRET_CONTENT_PATTERNS,
        scan_content: bool = True,
    ) -> None:
        self.file_patterns = tuple(file_patterns)
        self.scan_content = scan_content
        self._content_res = [re.compile(p) for p in content_patterns]

    def reason(self, key: str, payload: object) -> str | None:
        """A short reason the item is a secret (names the pattern, not the value), else None.

        ``key`` is the item key — for file connectors a repo path like
        ``owner/repo:path``; the trailing filename is what filename patterns match.
        ``payload`` is the item content (str for files, dict for issues/rows).
        """
        name = key.rsplit("/", 1)[-1].rsplit(":", 1)[-1]
        for pat in self.file_patterns:
            if fnmatch(name, pat) or fnmatch(key, pat):
                return f"secret-file pattern {pat!r}"
        if self.scan_content:
            text = payload if isinstance(payload, str) else json.dumps(payload, default=str)
            for rx in self._content_res:
                if rx.search(text):
                    return f"secret-content pattern {rx.pattern!r}"
        return None
