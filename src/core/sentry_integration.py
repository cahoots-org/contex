"""Optional Sentry error tracking, enabled by setting SENTRY_DSN."""

import os

import sentry_sdk
from sentry_sdk.integrations.logging import LoggingIntegration


def init_sentry(release: str) -> bool:
    """Initialize Sentry if SENTRY_DSN is set; return whether it was."""
    dsn = os.getenv("SENTRY_DSN")
    if not dsn:
        return False
    tracing = os.getenv("SENTRY_ENABLE_TRACING", "true").lower() == "true"
    sentry_sdk.init(
        dsn=dsn,
        environment=os.getenv("SENTRY_ENVIRONMENT", "development"),
        release=os.getenv("SENTRY_RELEASE", release),
        sample_rate=float(os.getenv("SENTRY_SAMPLE_RATE", "1.0")),
        traces_sample_rate=float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.1")) if tracing else 0.0,
        profiles_sample_rate=float(os.getenv("SENTRY_PROFILES_SAMPLE_RATE", "0.1")) if tracing else 0.0,
        # Published content and credentials pass through request bodies and locals.
        send_default_pii=False,
        include_local_variables=False,
        max_request_body_size="never",
        # Errors only; log records would otherwise ride along as breadcrumbs.
        integrations=[LoggingIntegration(level=None)],
    )
    return True
