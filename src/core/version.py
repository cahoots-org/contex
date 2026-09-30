"""Single source of truth for the running build's version.

Stamped into the image at build time via the ``CONTEX_VERSION`` env (Dockerfile
ARG/ENV, set by the release workflow from the release version). Falls back to
``"dev"`` for local runs and unstamped images, so an unbumped literal can never
masquerade as a real version — an honest "dev" beats a stale "0.3.0".
"""
import os

VERSION = os.getenv("CONTEX_VERSION", "dev")
