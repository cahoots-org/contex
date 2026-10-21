"""Rumor Mill settings, overridable through RUMOR_MILL_* environment variables."""

import os
import uuid
from dataclasses import dataclass, field, fields


@dataclass(frozen=True)
class Config:
    contex_url: str = "http://localhost:8001/mcp"
    # Names this run's Contex projects; a fresh one per run starts every mind empty.
    session: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    day_seconds: float = 120.0
    # How long a subscription keeps talk for a villager to react to.
    memory_days: float = 2.0
    # How many of the newcomer's moves the village remembers a line for when judging belief.
    memory_moves: int = 20
    cooldown_seconds: float = 30.0
    renew_seconds: float = 1200.0
    population: int = 50
    arrival_delay_seconds: float = 60.0
    judge_every_days: float = 1.0
    judge_concurrency: int = 10
    turn_concurrency: int = 20
    top_k: int = 5
    # How far before a whisper's publish its direct check looks, absorbing clock skew with Contex's database.
    check_lookback_seconds: float = 5.0
    # How far above a need's ordinary-talk similarity a whisper must score to match it.
    match_margin: float = 0.015
    # How far above a whisper's ordinary-talk similarity a retelling must score to stay on its rumor card.
    on_topic_margin: float = 0.035
    # How similar a villager's earlier line must be to what the newcomer says to be remembered.
    said_floor: float = 0.8
    # How much more clearance each retelling hop needs before it wakes a listener.
    fatigue_per_hop: float = 0.0015
    # How much more clearance overheard room talk needs than a line told to the listener.
    overheard_margin: float = 0.005
    # The share of ordinary talk at the player's place that the player overhears.
    overhear_rate: float = 0.125
    # The share of lively talk at the player's place that the player overhears.
    lively_rate: float = 0.4
    # How far news must clear a need for a villager who walks off to share it wherever their nature lets them.
    share_clearance: float = 0.02
    # How many times a villager answers the newcomer in one conversation.
    talk_turns: int = 3
    # Where need baselines are cached; empty means the seed path plus .baselines.json.
    calibration_path: str = ""
    haiku_model: str = "claude-haiku-4-5-20251001"
    seed_path: str = "rumor_mill_seed.json"
    web_port: int = 8765
    # Estimated list prices in dollars per million tokens.
    haiku_input_per_mtok: float = 1.0
    haiku_output_per_mtok: float = 5.0
    jev_input_per_mtok: float = 0.04
    jev_output_per_mtok: float = 0.0
    suspicion_whisper_bump: float = 0.12
    suspicion_hear_max: float = 0.25
    suspicion_decay_per_day: float = 0.05
    warn_threshold: float = 0.3
    refuse_threshold: float = 0.7
    # Weighted town suspicion at which villagers start moving to run the newcomer out.
    run_out_level: float = 0.08
    run_out_checks: int = 3
    suspicion_check_days: float = 0.25
    # How many scenes run at once.
    max_scenes: int = 3
    # How long a villager in a scene waits between turns.
    scene_cooldown_seconds: float = 5.0
    # The share of a scene's momentum that becomes the chance a bystander is drawn in.
    join_rate: float = 0.3
    # How many lines a scene runs before it ends.
    scene_max_lines: int = 12
    # How far above a need's ordinary-talk similarity a line must score to count as a hint.
    hint_margin: float = 0.0
    # How close under a threshold a line must fall to count as a near miss.
    near_miss_band: float = 0.01

    @property
    def memory_seconds(self) -> int:
        return int(self.day_seconds * self.memory_days)

    @classmethod
    def from_env(cls) -> "Config":
        values = {}
        for f in fields(cls):
            raw = os.environ.get(f"RUMOR_MILL_{f.name.upper()}")
            if f.name == "contex_url" and raw is None:
                raw = os.environ.get("CONTEX_MCP_URL")
            if raw is None:
                continue
            values[f.name] = f.type(raw)
        return cls(**values)
