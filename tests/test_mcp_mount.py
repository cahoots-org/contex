import inspect

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
    src = inspect.getsource(main)
    assert "build_mcp_server" in src and "run_bridge" in src and "session_manager" in src


def test_mcp_session_idle_timeout_is_tightened():
    # The MCP streamable-HTTP transport is unauthenticated; a shorter idle timeout
    # limits how long orphaned sessions from an initialize loop can accumulate.
    assert main._mcp_server.session_manager.session_idle_timeout == 600


def test_mcp_allows_only_localhost_by_default(monkeypatch):
    monkeypatch.delenv("CONTEX_ALLOWED_HOSTS", raising=False)
    settings = main.mcp_transport_security()
    assert settings.enable_dns_rebinding_protection
    assert settings.allowed_hosts == ["127.0.0.1:*", "localhost:*", "[::1]:*"]


def test_mcp_allowed_hosts_extend_localhost(monkeypatch):
    monkeypatch.setenv("CONTEX_ALLOWED_HOSTS", "contex.example.com, contex.internal:*")
    settings = main.mcp_transport_security()
    assert settings.enable_dns_rebinding_protection
    assert "localhost:*" in settings.allowed_hosts
    assert "contex.example.com" in settings.allowed_hosts
    assert "contex.internal:*" in settings.allowed_hosts
    assert "https://contex.example.com" in settings.allowed_origins


def test_allowed_host_without_port_matches_any_port(monkeypatch):
    monkeypatch.setenv("CONTEX_ALLOWED_HOSTS", "contex.example.com,10.0.0.5:8001")
    allowed = main.AllowedHostMiddleware(None, main.mcp_transport_security().allowed_hosts)._allowed
    assert allowed("contex.example.com:8001")
    assert allowed("contex.example.com")
    assert allowed("10.0.0.5:8001")
    assert not allowed("10.0.0.5:9000")


def test_mcp_allowed_hosts_wildcard_disables_protection(monkeypatch):
    monkeypatch.setenv("CONTEX_ALLOWED_HOSTS", "*")
    assert not main.mcp_transport_security().enable_dns_rebinding_protection
