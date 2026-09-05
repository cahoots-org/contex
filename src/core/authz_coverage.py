"""Boot-time (and CI) assertion that every route declares an authz decision."""
from __future__ import annotations

from fastapi.routing import APIRoute
from starlette.routing import Mount, Route, WebSocketRoute

ALLOWED_MOUNTS = {"/mcp", "/static"}
PUBLIC_FRAMEWORK_PATHS = {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}


def _dependant_is_marked(dependant) -> bool:
    """True if any callable in the resolved dependency tree carries our marker."""
    stack = list(getattr(dependant, "dependencies", []))
    while stack:
        dep = stack.pop()
        call = getattr(dep, "call", None)
        # Contract (Task 3): require(*perms) always sets _authz_marker to a
        # TUPLE, never None. An empty tuple (require() with no args =
        # authenticated-only) is a valid, intentional decision and MUST count as
        # covered. Hence the `is not None` presence check rather than a
        # truthiness check — a truthiness check would silently turn the
        # empty-tuple case into a fail-closed false-negative. Do not weaken this
        # without also changing the marker's sentinel contract in authz.py.
        if getattr(call, "_authz_marker", None) is not None:
            return True
        if getattr(call, "_public_marker", False):
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
        # Bare Starlette Route (e.g. framework health) — allow only if in the public set.
        if isinstance(route, Route) and route.path not in PUBLIC_FRAMEWORK_PATHS:
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
