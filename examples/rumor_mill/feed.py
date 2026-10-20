"""The chat feed: prints how the player's whisper travels through the village."""

from collections.abc import Callable

from examples.rumor_mill.lineage import Lineage
from examples.rumor_mill.models import Utterance
from examples.rumor_mill.town import PLAYER_ID, Town


class Feed:
    def __init__(self, lineage: Lineage, town: Town, out: Callable[[str], None] = print):
        self.lineage = lineage
        self.town = town
        self.out = out

    def say(self, line: str) -> None:
        self.out(line)

    def on_delivery(self, utterance: Utterance, listener_ids: list[str]) -> None:
        """Print one line for a delivery batch of a player-rooted rumor."""
        if utterance.speaker == PLAYER_ID or not listener_ids:
            return
        if not self.lineage.is_player_rooted(utterance.id):
            return
        if len(listener_ids) == 1:
            listener = self.town.get(listener_ids[0]).name
            self.say(f'{utterance.speaker_name} told {listener}: "{utterance.text}"')
        else:
            self.say(
                f'{utterance.speaker_name} said at the {utterance.place} '
                f'(heard by {len(listener_ids)}): "{utterance.text}"'
            )
