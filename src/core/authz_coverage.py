"""Boot-time (and CI) assertion that every route declares an authz decision."""
from __future__ import annotations

from fastapi.routing import APIRoute
from starlette.routing import Mount, Route, WebSocketRoute

from src.core.authz import require, public

ALLOWED_MOUNTS = {"/static"}
PUBLIC_FRAMEWORK_PATHS = {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}
# Bare Starlette routes that enforce their own auth. The MCP streamable endpoint
# is grafted onto the app as a Route (not a Mount) and self-authenticates via the
# SDK TokenVerifier (per-tool default-deny).
SELF_AUTH_ROUTES = {"/mcp"}


def _dependant_is_marked(dependant) -> bool:
    """True if any dependency in the resolved tree is a require() or the public marker."""
    stack = list(getattr(dependant, "dependencies", []))
    while stack:
        dep = stack.pop()
        call = getattr(dep, "call", None)
        if isinstance(call, require) or call is public:
            return True
        stack.extend(getattr(dep, "dependencies", []))
    return False


def find_uncovered_routes(app) -> list[str]:
    uncovered: list[str] = []
    for route in app.routes:
        if isinstance(route, Mount):
            if route.path not in ALLOWED_MOUNTS:
                uncovered.append(f"MOUNT {route.path} (not in ALLOWED_MOUNTS)")
            continue
        if isinstance(route, APIRoute):
            if route.path in PUBLIC_FRAMEWORK_PATHS:
                continue
            if not _dependant_is_marked(route.dependant):
                methods = ",".join(sorted(route.methods or []))
                uncovered.append(f"{methods} {route.path}")
            continue
        if isinstance(route, WebSocketRoute):
            # No HTTP dependant; require explicit allowlisting when one is added.
            uncovered.append(f"WEBSOCKET {route.path} (websocket routes need explicit review)")
            continue
        # Bare Starlette Route (e.g. framework health, MCP) — allow only if in the
        # public set or the self-authenticating set.
        if (
            isinstance(route, Route)
            and route.path not in PUBLIC_FRAMEWORK_PATHS
            and route.path not in SELF_AUTH_ROUTES
        ):
            # FastAPI mounts most things as APIRoute; a bare Route is unusual → flag it.
            uncovered.append(f"ROUTE {route.path} (unexpected bare route)")
    return uncovered


def assert_authz_coverage(app) -> None:
    uncovered = find_uncovered_routes(app)
    if uncovered:
        raise RuntimeError(
            "FAIL-CLOSED: routes without a require()/public decision or allowlist entry:\n"
            + "\n".join(sorted(uncovered))
        )
