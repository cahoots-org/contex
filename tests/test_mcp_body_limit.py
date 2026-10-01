"""The MCP transport body limit must track CONTEX_MAX_UPLOAD_SIZE, not the SDK's 4 MiB default."""
from mcp.server import MCPServer
from mcp.server.subscriptions import InMemorySubscriptionBus

from src.core import upload_limits


def _session_manager_limit(**kwargs):
    server = MCPServer(name="t", version="0", subscriptions=InMemorySubscriptionBus())
    server.streamable_http_app(streamable_http_path="/", **kwargs)
    return server.session_manager.max_request_body_size


def test_sdk_default_is_4mib():
    # The limit the fix must override; left unset it strands publishes at 4 MiB.
    assert _session_manager_limit() == 4 * 1024 * 1024


def test_limit_tracks_upload_cap(monkeypatch):
    monkeypatch.setenv("CONTEX_MAX_UPLOAD_SIZE", "200000000")
    assert _session_manager_limit(
        max_request_body_size=upload_limits.get_max_upload_size()
    ) == 200000000
