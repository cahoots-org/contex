import main


def test_mcp_mounted_at_slash_mcp():
    paths = [getattr(r, "path", "") for r in main.app.routes]
    assert any(p == "/mcp" or p.startswith("/mcp") for p in paths), f"/mcp not mounted; routes={paths}"


def test_mcp_mount_prefix_is_exactly_slash_mcp():
    # The mount prefix must be /mcp, not /mcp/mcp — the sub-app is built with
    # streamable_http_path="/" so it does not re-add the prefix.
    paths = [getattr(r, "path", "") for r in main.app.routes]
    assert "/mcp" in paths, f"no /mcp mount; routes={paths}"
    assert "/mcp/mcp" not in paths, "MCP path is doubled to /mcp/mcp"


def test_build_mcp_server_is_wired_in_main():
    # the factory + bridge are imported and used by main
    import inspect
    src = inspect.getsource(main)
    assert "build_mcp_server" in src and "run_bridge" in src and "session_manager" in src
