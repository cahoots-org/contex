from fastapi.testclient import TestClient

import main


def test_lifespan_wires_engine_and_notifier(monkeypatch):
    monkeypatch.setenv("CONTEX_PROTECTED_MODE", "false")
    with TestClient(main.app) as client:
        assert client.get("/health").status_code == 200
        assert main.app.state.context_engine.notifier is main.app.state.notifier
