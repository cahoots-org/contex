import main


def test_mcp_mounted_at_slash_mcp():
    paths = [getattr(r, "path", "") for r in main.app.routes]
    assert any(p == "/mcp" or p.startswith("/mcp") for p in paths), f"/mcp not mounted; routes={paths}"


def test_build_mcp_server_is_wired_in_main():
    # the factory + bridge are imported and used by main
    import inspect
    src = inspect.getsource(main)
    assert "build_mcp_server" in src and "run_bridge" in src and "session_manager" in src
