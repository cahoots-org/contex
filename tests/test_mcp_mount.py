from starlette.routing import Match

import main


def test_mcp_mounted_at_slash_mcp():
    paths = [getattr(r, "path", "") for r in main.app.routes]
    assert any(p == "/mcp" or p.startswith("/mcp") for p in paths), f"/mcp not mounted; routes={paths}"


def _matches(path: str) -> bool:
    scope = {"type": "http", "path": path, "method": "POST", "headers": []}
    return any(r.matches(scope)[0] == Match.FULL for r in main.app.routes)


def test_mcp_endpoint_is_exactly_slash_mcp_not_doubled():
    # The endpoint must resolve at exactly /mcp — not /mcp/mcp (the bug from
    # mounting a /mcp-path sub-app under a /mcp prefix).
    assert _matches("/mcp"), "no route matches /mcp"
    assert not _matches("/mcp/mcp"), "/mcp/mcp still resolves — path is doubled"


def test_build_mcp_server_is_wired_in_main():
    # the factory + bridge are imported and used by main
    import inspect
    src = inspect.getsource(main)
    assert "build_mcp_server" in src and "run_bridge" in src and "session_manager" in src
