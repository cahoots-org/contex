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


class Notes:
    def __init__(self, feed: Feed, player: str):
        self._notes: dict[str, dict] = {}
        for event in feed.replay(player):
            self(event)
        feed.sinks.append(feed.view(player, self))

    def __call__(self, event: Event) -> None:
        if event.kind == "refusal":
            status = "wary"
        elif event.kind == "whisper_status" and event.data["state"] in STATUS_OF:
            status = STATUS_OF[event.data["state"]]
        else:
            return
        note = self._notes.setdefault(event.data["villager_id"], {"status": "none", "hint": None})
        if status == "wary" or RANK.index(status) > RANK.index(note["status"]):
            note["status"] = status
        if event.data.get("hint"):
            note["hint"] = event.data["hint"]

    def get(self, vid: str) -> dict:
        return dict(self._notes.get(vid, {"status": "none", "hint": None}))

    def all(self) -> dict[str, dict]:
        return {vid: dict(note) for vid, note in self._notes.items()}
