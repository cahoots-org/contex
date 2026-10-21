"""Plain data types for villagers, utterances, and groups."""

from dataclasses import dataclass, field


@dataclass
class Villager:
    id: str
    name: str
    occupation: str
    personality: str
    grudge: str
    needs: list[str]
    relationships: dict[str, str]


@dataclass
class Utterance:
    id: str
    speaker: str
    speaker_name: str
    place: str
    text: str
    parent: str | None
    verb: str = "said"
    depth: int = 0
    names_newcomer: bool = False
    audience: str = "one"
    action: str | None = None
    style: str | None = None

    def to_data(self) -> dict:
        """Payload for Contex, which embeds every field, so it holds only what the game reads back."""
        return {"id": self.id, "text": f"{self.speaker_name} {self.verb} at the {self.place}: {self.text}"}


@dataclass
class Group:
    id: str
    name: str
    creed: str
    founder: str
    members: set[str] = field(default_factory=set)
