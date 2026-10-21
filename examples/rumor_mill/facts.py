"""How each line was said, kept by utterance id for the rules that read it later."""

from examples.rumor_mill.models import Utterance

# How many recent utterances are kept.
KEPT_FACTS = 5000


class Facts:
    def __init__(self) -> None:
        self._by_id: dict[str, Utterance] = {}

    def record(self, utterance: Utterance) -> None:
        self._by_id[utterance.id] = utterance
        while len(self._by_id) > KEPT_FACTS:
            del self._by_id[next(iter(self._by_id))]

    def get(self, uid: str) -> Utterance | None:
        return self._by_id.get(uid)
