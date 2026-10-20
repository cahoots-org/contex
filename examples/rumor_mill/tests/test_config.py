from examples.rumor_mill.config import Config


def test_memory_seconds():
    assert Config(day_seconds=120, memory_days=2).memory_seconds == 240


def test_from_env_overrides(monkeypatch):
    monkeypatch.delenv("RUMOR_MILL_CONTEX_URL", raising=False)
    monkeypatch.setenv("RUMOR_MILL_POPULATION", "7")
    monkeypatch.setenv("CONTEX_MCP_URL", "http://example:9/mcp")
    cfg = Config.from_env()
    assert cfg.population == 7
    assert cfg.contex_url == "http://example:9/mcp"


def test_from_env_threshold(monkeypatch):
    monkeypatch.delenv("RUMOR_MILL_CONTEX_URL", raising=False)
    monkeypatch.setenv("RUMOR_MILL_THRESHOLD", "0.4")
    assert Config.from_env().threshold == 0.4


def test_from_env_judge_threshold(monkeypatch):
    monkeypatch.setenv("RUMOR_MILL_JUDGE_THRESHOLD", "0.7")
    assert Config.from_env().judge_threshold == 0.7


def test_each_run_gets_its_own_session(monkeypatch):
    monkeypatch.delenv("RUMOR_MILL_SESSION", raising=False)
    assert Config.from_env().session != Config.from_env().session
    monkeypatch.setenv("RUMOR_MILL_SESSION", "replay")
    assert Config.from_env().session == "replay"
