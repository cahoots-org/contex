"""How each line was said, kept by utterance id for the rules that read it later."""

import time
from dataclasses import asdict

from examples.whispers.models import Utterance

# How many recent utterances are kept.
KEPT_FACTS = 5000


class Facts:
    def __init__(self) -> None:
        self._by_id: dict[str, tuple[Utterance, float]] = {}

    def record(self, utterance: Utterance) -> None:
        """Keep the utterance with the time it was said."""
        self._by_id[utterance.id] = (utterance, time.time())
        while len(self._by_id) > KEPT_FACTS:
            del self._by_id[next(iter(self._by_id))]

    def to_state(self) -> dict:
        """The kept utterances with their times, oldest first."""
        return {"kept": [[asdict(u), at] for u, at in self._by_id.values()]}

    def restore(self, state: dict) -> None:
        """Replace the kept utterances with a saved state, in order."""
        self._by_id = {u["id"]: (Utterance(**u), at) for u, at in state["kept"]}

    def get(self, uid: str) -> Utterance | None:
        """The utterance with this id, if it is still kept."""
        kept = self._by_id.get(uid)
        return kept[0] if kept else None

    def recent(self, place: str, since: float) -> list[Utterance]:
        """The lines said at place at or after since, oldest first."""
        return [u for u, at in self._by_id.values() if u.place == place and at >= since]
