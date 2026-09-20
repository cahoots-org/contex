import pytest

from connectors.base import ContexConfig, load_config, resolve_batch_size
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
        "  service_account_token: ${CONTEX_TOKEN}\n"
        "batch_size: 200\n"
    )
    config = load_config(str(path))
    assert config["contex"]["service_account_token"] == "tok123"
    assert config["batch_size"] == 200


def test_contex_config_from_dict():
    config = ContexConfig.from_dict(
        {"contex": {"url": "http://x/mcp", "project_id": "p", "service_account_token": "t"}}
    )
    assert config == ContexConfig(url="http://x/mcp", project_id="p", service_account_token="t")


def test_contex_config_empty_token_is_none():
    config = ContexConfig.from_dict(
        {"contex": {"url": "http://x/mcp", "project_id": "p", "service_account_token": ""}}
    )
    assert config.service_account_token is None


@pytest.mark.parametrize("missing", ["url", "project_id"])
def test_contex_config_requires_url_and_project(missing):
    contex = {"url": "http://x/mcp", "project_id": "p"}
    del contex[missing]
    with pytest.raises(ValueError):
        ContexConfig.from_dict({"contex": contex})


def test_resolve_batch_size_default_and_override():
    assert resolve_batch_size({}) == DEFAULT_BATCH_SIZE
    assert resolve_batch_size({"batch_size": 50}) == 50
