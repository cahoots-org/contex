from pathlib import Path


def test_readme_quickstart_references_real_mcp_tools():
    # The quickstart is MCP-native: it must reference the MCP endpoint and tools
    # that actually exist in the adapter (guards against README/tool drift).
    readme = Path("README.md").read_text()
    adapter = Path("src/core/mcp_adapter.py").read_text()
    assert "/mcp" in readme
    for tool in ("contex_publish", "contex_create_subscription"):
        assert tool in readme, f"README should reference {tool}"
        assert f'name="{tool}"' in adapter, f"{tool} should be a registered MCP tool"


def test_capture_doc_exists():
    assert Path("docs/demo/capture.md").exists()
