import inspect

import pytest
from mcp.server.auth.middleware.bearer_auth import AuthorizationContext
from starlette.testclient import TestClient

import main
from src.core.metrics import http_requests_total


def test_mcp_answers_at_both_paths_without_redirect():
    client = TestClient(main.app, base_url="http://localhost:8000", raise_server_exceptions=False,
                        follow_redirects=False)
    # Without the lifespan the transport isn't running, so reaching it errors; a 307 or 404 never got there.
    def mcp_posts():
        return sum(s.value for s in http_requests_total.collect()[0].samples
                   if s.name.endswith("_total") and s.labels["endpoint"] == "/mcp" and s.labels["method"] == "POST")

    before = mcp_posts()
    for path in ("/mcp", "/mcp/"):
        assert client.post(path).status_code not in (307, 404)
    assert mcp_posts() == before + 2


@pytest.mark.asyncio
async def test_sessions_are_capped_per_key_and_freed_when_they_end():
    manager = main._mcp_server.session_manager
    alice, bob = (AuthorizationContext(client_id=k, issuer=None, subject=None) for k in ("alice", "bob"))
    opened = [manager._admit_session(alice) for _ in range(main._MAX_SESSIONS_PER_KEY)]
    try:
        assert manager._admit_session(alice) is None
        opened.append(manager._admit_session(bob))
        assert opened[-1] is not None
        await manager._discard_session(opened[0].mcp_session_id, opened[0])
        opened[0] = manager._admit_session(alice)
        assert opened[0] is not None
    finally:
        for transport in opened:
            await manager._discard_session(transport.mcp_session_id, transport)


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
