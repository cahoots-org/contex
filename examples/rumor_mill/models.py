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

    def to_data(self, day: int) -> dict:
        """Payload for Contex, which embeds all of it, so the text is one sentence."""
        return {
            "id": self.id,
            "parent": self.parent,
            "speaker": self.speaker,
            "place": self.place,
            "day": day,
            "text": f"{self.speaker_name} {self.verb} at the {self.place}: {self.text}",
        }


@dataclass
class Group:
    id: str
    name: str
    creed: str
    founder: str
    members: set[str] = field(default_factory=set)
