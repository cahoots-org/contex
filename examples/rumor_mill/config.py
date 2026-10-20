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
    memory_days: float = 2.0
    cooldown_seconds: float = 30.0
    shrug_seconds: float = 20.0
    renew_seconds: float = 1200.0
    population: int = 100
    arrival_delay_seconds: float = 60.0
    judge_every_days: float = 1.0
    judge_concurrency: int = 10
    judge_threshold: float = 0.75
    turn_concurrency: int = 20
    top_k: int = 5
    # Cosine similarity a memory needs to one of a villager's needs to wake them.
    threshold: float = 0.815
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
            values[f.name] = f.type(raw)
        return cls(**values)
