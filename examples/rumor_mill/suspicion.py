"""How much each villager suspects each newcomer, and what the town does about it."""

from collections import defaultdict
from collections.abc import Collection
from dataclasses import dataclass

from examples.rumor_mill.config import Config
from examples.rumor_mill.models import Villager
from examples.rumor_mill.town import Town

# Occupation substring (lowercase) to how much that villager's suspicion counts.
WEIGHTS = {"mayor": 3.0, "priest": 2.0, "innkeeper": 2.0, "constable": 2.0, "reeve": 2.0,
           "tavern": 2.0, "bailiff": 2.0}
# What the player is told about a villager whose suspicion counts for more.
INFLUENCE = "folk listen to the {}"
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
    "heard the newcomer named": "{} grew wary of the rumors going around.",
    "was warned about the newcomer": "{} was warned about you.",
    "asked questions": "{} noticed you asking questions.",
    "stormed off": "{} stormed off on you.",
}


def influence(villager: Villager) -> str | None:
    """Who the folk listen to, in words, for a villager in a weighted role; None for any other."""
    job = villager.occupation.lower()
    return INFLUENCE.format(job) if any(key in job for key in WEIGHTS) else None


@dataclass
class Evidence:
    vid: str
    player: str
    amount: float
    reason: str
    uid: str | None = None


class Suspicion:
    """Each villager's suspicion of each player."""

    def __init__(self, config: Config, town: Town) -> None:
        self.config = config
        self.town = town
        self._scores: dict[tuple[str, str], float] = {}
        self._evidence: list[Evidence] = []

    def bump(self, vid: str, player: str, amount: float, reason: str, uid: str | None = None) -> float:
        """Raise the villager's suspicion of player, clamped to [0, 1]; ignores unknown villagers and amounts <= 0."""
        if vid not in self.town or amount <= 0:
            return self.score(vid, player)
        self._scores[(vid, player)] = max(0.0, min(1.0, self.score(vid, player) + amount))
        self._evidence.append(Evidence(vid, player, amount, reason, uid))
        return self._scores[(vid, player)]

    def rate(self, vid: str, player: str, strength: float, reason: str, uid: str | None = None) -> float:
        """Bump by a Jev strength in [0, 1], scaled by the most one hearing can add."""
        return self.bump(vid, player, min(1.0, max(0.0, strength)) * self.config.suspicion_hear_max, reason, uid)

    def score(self, vid: str, player: str) -> float:
        """How much the villager suspects player, in [0, 1]."""
        return self._scores.get((vid, player), 0.0) if vid in self.town else 0.0

    def decay(self, days: float) -> None:
        """Lower every villager's suspicion of every player."""
        drop = self.config.suspicion_decay_per_day * days
        self._scores = {key: max(0.0, s - drop) for key, s in self._scores.items()}

    def most_suspected(self, vid: str, among: Collection[str] = ()) -> str | None:
        """The player the villager suspects most among those given, or among everyone; None when they suspect none."""
        scores = {p: s for (v, p), s in self._scores.items() if v == vid and s > 0 and (not among or p in among)}
        if vid not in self.town or not scores:
            return None
        return max(scores, key=scores.__getitem__)

    def weight(self, villager: Villager) -> float:
        """How much the villager's suspicion counts, by their occupation."""
        job = villager.occupation.lower()
        return max((w for key, w in WEIGHTS.items() if key in job), default=1.0)

    def level(self, player: str) -> float:
        """Weighted mean suspicion of player over the living villagers."""
        villagers = self.town.villagers
        total = sum(self.weight(v) for v in villagers)
        if not total:
            return 0.0
        return sum(self.score(v.id, player) * self.weight(v) for v in villagers) / total

    def label(self, player: str) -> str:
        """How hunted player is, in words."""
        if self.config.run_out_level <= 0:
            return "hunted"
        ratio = self.level(player) / self.config.run_out_level
        return next((name for bound, name in LABELS if ratio < bound), "hunted")

    def over(self, threshold: float, player: str) -> list[str]:
        """The villagers whose suspicion of player is at or above threshold."""
        return [v.id for v in self.town.villagers if self.score(v.id, player) >= threshold]

    def top_evidence(self, player: str, n: int = 3) -> list[Evidence]:
        """The largest contributions to suspicion of player, summed per villager and reason."""
        sums: dict[tuple[str, str], float] = defaultdict(float)
        uids: dict[tuple[str, str], str | None] = {}
        for e in self._evidence:
            if e.player == player:
                sums[(e.vid, e.reason)] += e.amount
                uids.setdefault((e.vid, e.reason), e.uid)
        ranked = sorted(sums, key=sums.__getitem__, reverse=True)
        return [Evidence(vid, player, sums[(vid, r)], r, uids[(vid, r)]) for vid, r in ranked[:n]]

    def reasons(self, player: str, n: int = 3) -> list[str]:
        """Plain sentences for the top evidence against player, skipping villagers whose name is unknown."""
        lines = []
        for e in self.top_evidence(player, n):
            if name := self.town.name_of(e.vid):
                lines.append(REASON_LINES.get(e.reason, "{} grew suspicious of you.").format(name))
        return lines
