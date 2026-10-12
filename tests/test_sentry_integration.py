from src.core import sentry_integration


def test_sentry_sends_neither_request_bodies_nor_locals(monkeypatch):
    calls = []
    monkeypatch.setenv("SENTRY_DSN", "https://key@sentry.example/1")
    monkeypatch.setattr(sentry_integration.sentry_sdk, "init", lambda **kwargs: calls.append(kwargs))

    assert sentry_integration.init_sentry(release="contex@test")

    assert calls[0]["include_local_variables"] is False
    assert calls[0]["max_request_body_size"] == "never"
    assert calls[0]["send_default_pii"] is False
