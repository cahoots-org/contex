from pathlib import Path


def test_readme_quickstart_references_real_endpoints():
    readme = Path("README.md").read_text()
    # Producer + consumer endpoints the quickstart is built on:
    assert "/api/v1/data/publish" in readme
    assert "/sandbox/subscribe" in readme
    # Supporting visual still referenced:
    assert "docs/assets/demo.gif" in readme


def test_capture_doc_exists():
    assert Path("docs/demo/capture.md").exists()
