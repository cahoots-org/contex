"""How much each villager suspects the newcomer, and what the town does about it."""

from collections import defaultdict
from dataclasses import dataclass

from examples.rumor_mill.config import Config
from examples.rumor_mill.models import Villager
from examples.rumor_mill.town import Town

# Occupation substring (lowercase) to how much that villager's suspicion counts.
WEIGHTS = {"mayor": 3.0, "priest": 2.0, "innkeeper": 2.0, "constable": 2.0, "reeve": 2.0}
# Upper bound of each label as a fraction of the run-out level.
LABELS = ((0.25, "quiet"), (0.5, "murmurs"), (0.8, "watched"))
# What the player is told for each label.
HEAT_LINES = {
    "quiet": "Folk are not talking about you.",
    "murmurs": "There are murmurs.",
    "watched": "Folk are watching you.",
    "hunted": "The village is hunting for you.",
}
# Evidence reason to the loss-summary sentence about the villager.
REASON_LINES = {
    "whispered to them directly": "{} heard it from you directly.",
    "tried to whisper again": "{} caught you whispering to them again.",
    "heard the newcomer named": "{} heard the newcomer named.",
    "was warned about the newcomer": "{} was warned about you.",
}


@dataclass
class Evidence:
    vid: str
    amount: float
    reason: str
    uid: str | None = None


class Suspicion:
    def __init__(self, config: Config, town: Town) -> None:
        self.config = config
        self.town = town
        self._scores: dict[str, float] = {}
        self._evidence: list[Evidence] = []

    def bump(self, vid: str, amount: float, reason: str, uid: str | None = None) -> float:
        """Add suspicion for a villager, clamped to [0, 1]; unknown villagers and non-positive amounts are ignored."""
        if vid not in self.town or amount <= 0:
            return self.score(vid)
        self._scores[vid] = max(0.0, min(1.0, self.score(vid) + amount))
        self._evidence.append(Evidence(vid, amount, reason, uid))
        return self._scores[vid]

    def rate(self, vid: str, strength: float, reason: str, uid: str | None = None) -> float:
        """Bump by a Jev strength in [0, 1], scaled by the most one hearing can add."""
        return self.bump(vid, min(1.0, max(0.0, strength)) * self.config.suspicion_hear_max, reason, uid)

    def score(self, vid: str) -> float:
        return self._scores.get(vid, 0.0) if vid in self.town else 0.0

    def decay(self, days: float) -> None:
        drop = self.config.suspicion_decay_per_day * days
        self._scores = {vid: max(0.0, s - drop) for vid, s in self._scores.items()}

    def weight(self, villager: Villager) -> float:
        job = villager.occupation.lower()
        return max((w for key, w in WEIGHTS.items() if key in job), default=1.0)

    def level(self) -> float:
        """Weighted mean suspicion over the living villagers."""
        villagers = self.town.villagers
        total = sum(self.weight(v) for v in villagers)
        if not total:
            return 0.0
        return sum(self.score(v.id) * self.weight(v) for v in villagers) / total

    def label(self) -> str:
        if self.config.run_out_level <= 0:
            return "hunted"
        ratio = self.level() / self.config.run_out_level
        return next((name for bound, name in LABELS if ratio < bound), "hunted")

    def over(self, threshold: float) -> list[str]:
        return [v.id for v in self.town.villagers if self.score(v.id) >= threshold]

    def top_evidence(self, n: int = 3) -> list[Evidence]:
        """The largest contributions, summed per villager and reason."""
        sums: dict[tuple[str, str], float] = defaultdict(float)
        uids: dict[tuple[str, str], str | None] = {}
        for e in self._evidence:
            sums[(e.vid, e.reason)] += e.amount
            uids.setdefault((e.vid, e.reason), e.uid)
        ranked = sorted(sums, key=sums.__getitem__, reverse=True)
        return [Evidence(vid, sums[(vid, reason)], reason, uids[(vid, reason)]) for vid, reason in ranked[:n]]

    def reasons(self, n: int = 3) -> list[str]:
        """Plain sentences for the top evidence, skipping villagers whose name is unknown."""
        lines = []
        for e in self.top_evidence(n):
            if name := self.town.name_of(e.vid):
                lines.append(REASON_LINES.get(e.reason, "{} grew suspicious of you.").format(name))
        return lines
