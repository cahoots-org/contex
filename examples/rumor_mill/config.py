"""Rumor Mill settings, overridable through RUMOR_MILL_* environment variables."""

import os
from dataclasses import dataclass, fields


@dataclass(frozen=True)
class Config:
    contex_url: str = "http://localhost:8001/mcp"
    session: str = "default"
    day_seconds: float = 180.0
    memory_days: float = 2.0
    cooldown_seconds: float = 15.0
    shrug_seconds: float = 20.0
    renew_seconds: float = 1200.0
    population: int = 100
    arrival_delay_seconds: float = 60.0
    judge_every_days: float = 1.0
    judge_concurrency: int = 10
    judge_threshold: float = 0.6
    turn_concurrency: int = 20
    top_k: int = 5
    threshold: float | None = None
    haiku_model: str = "claude-haiku-4-5-20251001"
    seed_path: str = "rumor_mill_seed.json"

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
            cast = float if f.name == "threshold" else type(f.default)
            values[f.name] = cast(raw)
        return cls(**values)
