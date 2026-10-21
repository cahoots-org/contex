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


def test_match_margin_default_and_override(monkeypatch):
    monkeypatch.delenv("RUMOR_MILL_CONTEX_URL", raising=False)
    assert Config().match_margin == 0.015
    monkeypatch.setenv("RUMOR_MILL_MATCH_MARGIN", "0.05")
    assert Config.from_env().match_margin == 0.05


def test_old_threshold_fields_are_gone():
    assert not hasattr(Config(), "threshold") and not hasattr(Config(), "judge_threshold")


def test_each_run_gets_its_own_session(monkeypatch):
    monkeypatch.delenv("RUMOR_MILL_SESSION", raising=False)
    assert Config.from_env().session != Config.from_env().session
    monkeypatch.setenv("RUMOR_MILL_SESSION", "replay")
    assert Config.from_env().session == "replay"


def test_price_overrides(monkeypatch):
    monkeypatch.setenv("RUMOR_MILL_JEV_INPUT_PER_MTOK", "0.1")
    monkeypatch.setenv("RUMOR_MILL_HAIKU_OUTPUT_PER_MTOK", "4")
    cfg = Config.from_env()
    assert (cfg.jev_input_per_mtok, cfg.haiku_output_per_mtok, cfg.haiku_input_per_mtok) == (0.1, 4.0, 1.0)


def test_suspicion_defaults_and_env_overrides(monkeypatch):
    cfg = Config()
    assert (cfg.suspicion_whisper_bump, cfg.suspicion_hear_max, cfg.suspicion_decay_per_day) == (0.12, 0.25, 0.05)
    assert (cfg.warn_threshold, cfg.refuse_threshold, cfg.run_out_level) == (0.3, 0.7, 0.08)
    assert (cfg.run_out_checks, cfg.suspicion_check_days) == (3, 0.25)
    for name, raw in {
        "SUSPICION_WHISPER_BUMP": "0.1",
        "SUSPICION_HEAR_MAX": "0.5",
        "SUSPICION_DECAY_PER_DAY": "0.01",
        "WARN_THRESHOLD": "0.3",
        "REFUSE_THRESHOLD": "0.6",
        "RUN_OUT_LEVEL": "0.2",
        "RUN_OUT_CHECKS": "5",
        "SUSPICION_CHECK_DAYS": "0.5",
    }.items():
        monkeypatch.setenv(f"RUMOR_MILL_{name}", raw)
    cfg = Config.from_env()
    assert (cfg.suspicion_whisper_bump, cfg.suspicion_hear_max, cfg.suspicion_decay_per_day) == (0.1, 0.5, 0.01)
    assert (cfg.warn_threshold, cfg.refuse_threshold, cfg.run_out_level) == (0.3, 0.6, 0.2)
    assert (cfg.run_out_checks, cfg.suspicion_check_days) == (5, 0.5)


def test_scene_defaults_and_env_overrides(monkeypatch):
    cfg = Config()
    assert (cfg.max_scenes, cfg.scene_cooldown_seconds, cfg.join_rate) == (3, 5.0, 0.3)
    assert (cfg.scene_max_lines, cfg.hint_margin, cfg.near_miss_band) == (12, 0.0, 0.01)
    monkeypatch.setenv("RUMOR_MILL_MAX_SCENES", "2")
    monkeypatch.setenv("RUMOR_MILL_JOIN_RATE", "0.5")
    cfg = Config.from_env()
    assert (cfg.max_scenes, cfg.join_rate) == (2, 0.5)
