import pytest

from connectors.base import (
    ContexConfig,
    load_config,
    resolve_batch_size,
    resolve_flush_interval,
    resolve_prune,
    resolve_secret_scanner,
)
from connectors.base.config import DEFAULT_BATCH_SIZE, _expand_env


def test_expand_env_substitutes_and_recurses(monkeypatch):
    monkeypatch.setenv("CONTEX_TOKEN", "secret")
    data = {"contex": {"token": "${CONTEX_TOKEN}", "nested": ["${CONTEX_TOKEN}", "plain"]}}
    assert _expand_env(data) == {
        "contex": {"token": "secret", "nested": ["secret", "plain"]}
    }


def test_expand_env_unset_becomes_empty(monkeypatch):
    monkeypatch.delenv("MISSING_VAR", raising=False)
    assert _expand_env("${MISSING_VAR}") == ""


def test_load_config_reads_and_expands(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTEX_TOKEN", "tok123")
    path = tmp_path / "connector.yaml"
    path.write_text(
        "contex:\n"
        "  url: http://localhost:8001/mcp\n"
        "  project_id: my-app\n"
        "  api_key: ${CONTEX_TOKEN}\n"
        "batch_size: 200\n"
    )
    config = load_config(str(path))
    assert config["contex"]["api_key"] == "tok123"
    assert config["batch_size"] == 200


def test_contex_config_from_dict():
    config = ContexConfig.from_dict(
        {"contex": {"url": "http://x/mcp", "project_id": "p", "api_key": "t"}}
    )
    assert config == ContexConfig(url="http://x/mcp", project_id="p", api_key="t")


def test_contex_config_empty_token_is_none():
    config = ContexConfig.from_dict(
        {"contex": {"url": "http://x/mcp", "project_id": "p", "api_key": ""}}
    )
    assert config.api_key is None


@pytest.mark.parametrize("missing", ["url", "project_id"])
def test_contex_config_requires_url_and_project(missing):
    contex = {"url": "http://x/mcp", "project_id": "p"}
    del contex[missing]
    with pytest.raises(ValueError):
        ContexConfig.from_dict({"contex": contex})


def test_resolve_batch_size_default_and_override():
    assert resolve_batch_size({}) == DEFAULT_BATCH_SIZE
    assert resolve_batch_size({"batch_size": 50}) == 50


def test_env_expanded_false_is_false():
    assert resolve_prune({"prune": "false"}) is False
    assert resolve_prune({"prune": ""}) is True
    assert resolve_secret_scanner({"allow_secrets": "false"}) is not None
    with pytest.raises(ValueError):
        resolve_prune({"prune": "maybe"})


def test_flush_interval_defaults_when_unset():
    assert resolve_flush_interval({}) == 1.0
    assert resolve_flush_interval({"flush_interval": ""}) == 1.0


def test_flush_interval_reads_seconds():
    assert resolve_flush_interval({"flush_interval": "0.25"}) == 0.25


def test_flush_interval_must_be_positive():
    with pytest.raises(ValueError, match="flush_interval"):
        resolve_flush_interval({"flush_interval": 0})
