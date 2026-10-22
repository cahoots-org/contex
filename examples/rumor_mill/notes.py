"""A player's notes: what each villager has shown the player, built from feed events."""

from examples.rumor_mill.feed import Event, Feed

# Lowest to highest; a note only moves up, except that wary overrides anything.
RANK = ("none", "listening", "brushed_off", "answered", "kept", "wary", "took_the_bait")
STATUS_OF = {
    "checking": "listening",
    "busy": "listening",
    "heard": "listening",
    "brushed_off": "brushed_off",
    "answered": "answered",
    "kept": "kept",
    "moved_on": "kept",
    "took_the_bait": "took_the_bait",
    "warned": "wary",
}


def _blank() -> dict:
    return {"status": "none", "hints": [], "passed": []}


class Notes:
    def __init__(self, feed: Feed, player: str):
        self._notes: dict[str, dict] = {}
        # The text of each of the player's whispers, by id.
        self._said: dict[str, str] = {}
        for event in feed.replay(player):
            self(event)
        feed.sinks.append(feed.view(player, self))

    def __call__(self, event: Event) -> None:
        data, status, hint, passed = event.data, None, None, None
        if event.kind == "whisper":
            self._said[data["uid"]] = data["text"]
            return
        if event.kind == "refusal":
            status = "wary"
        elif event.kind == "whisper_status":
            status, hint = STATUS_OF.get(data["state"]), data.get("hint")
            if data["state"] == "took_the_bait":
                passed = self._said.get(data["whisper_id"])
        elif event.kind == "hop":
            passed = self._said.get(data["root"])
        elif event.kind == "asked":
            hint = data["line"]
        if status is None and not hint and not passed:
            return
        vid = data["speaker_id"] if event.kind == "hop" else data["villager_id"]
        note = self._notes.setdefault(vid, _blank())
        if status and (status == "wary" or RANK.index(status) > RANK.index(note["status"])):
            note["status"] = status
        if hint and hint not in note["hints"]:
            note["hints"].append(hint)
        if passed and passed not in note["passed"]:
            note["passed"].append(passed)

    def get(self, vid: str) -> dict:
        """The player's note on the villager."""
        return self._copy(self._notes.get(vid, _blank()))

    def all(self) -> dict[str, dict]:
        """Every note the player has, by villager id."""
        return {vid: self._copy(note) for vid, note in self._notes.items()}

    @staticmethod
    def _copy(note: dict) -> dict:
        return {"status": note["status"], "hints": list(note["hints"]), "passed": list(note["passed"])}
