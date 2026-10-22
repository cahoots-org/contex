"""Player identity."""

import secrets


def new_player_id() -> str:
    return "p-" + secrets.token_hex(8)
