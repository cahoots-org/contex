"""VERSION reflects the CONTEX_VERSION build stamp, else honest 'dev'."""
import importlib
import os


def _reload_version():
    import src.core.version as v
    return importlib.reload(v)


def test_falls_back_to_dev(monkeypatch):
    monkeypatch.delenv("CONTEX_VERSION", raising=False)
    assert _reload_version().VERSION == "dev"


def test_uses_stamped_version(monkeypatch):
    monkeypatch.setenv("CONTEX_VERSION", "1.2.3")
    assert _reload_version().VERSION == "1.2.3"


if __name__ == "__main__":
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    os.environ.pop("CONTEX_VERSION", None)
    assert _reload_version().VERSION == "dev"
    os.environ["CONTEX_VERSION"] = "9.9.9"
    assert _reload_version().VERSION == "9.9.9"
    print("ok")
