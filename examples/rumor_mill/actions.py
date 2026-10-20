"""The villager action set, with the descriptions Jev sees."""

IGNORE = "ignore"
RETELL = "retell"
DENY = "deny"
CONFRONT = "confront"
SPEAK = "speak"
MOVE = "move"
FOUND_GROUP = "found_group"
JOIN_GROUP = "join_group"
LEAVE_GROUP = "leave_group"
PREACH = "preach"
LEAVE_TOWN = "leave_town"

ACTIONS = {
    IGNORE: "Do nothing; carry on with the day.",
    RETELL: "Pass on something just heard to one specific person nearby.",
    DENY: "Tell one specific person nearby that something being said is false.",
    CONFRONT: "Challenge one specific person nearby about what they said or did.",
    SPEAK: "Say something of your own to the people nearby, unprompted by any one item.",
    MOVE: "Walk to a different place in town.",
    FOUND_GROUP: "Start a new group around a belief or cause.",
    JOIN_GROUP: "Join a group that has members nearby.",
    LEAVE_GROUP: "Quit a group you belong to.",
    PREACH: "Speak up for one of your groups' beliefs to the people nearby.",
    LEAVE_TOWN: "Leave town for good.",
}

# Option details where two actions are easy to confuse.
ACTION_NOT_FOR = {
    RETELL: "Not for making something up of your own (speak) or disputing it (deny).",
    SPEAK: "Not for passing on a specific piece of news to a specific person (retell).",
    DENY: "Not for personal accusations (confront).",
    CONFRONT: "Not for correcting a rumor in general (deny).",
}

STYLES = {
    "faithful": "Repeat it as it was told, as accurately as you remember.",
    "exaggerated": "Make it bigger and more dramatic than it was told.",
    "shift_blame": "Change who is responsible for it.",
    "add_detail": "Embellish it with an invented but plausible detail.",
    "soften": "Play it down and make it sound less serious.",
    "mock": "Retell it with scorn or ridicule.",
}

TARGET_KIND = {
    RETELL: "villager",
    DENY: "villager",
    CONFRONT: "villager",
    MOVE: "place",
    JOIN_GROUP: "group",
    LEAVE_GROUP: "group",
    PREACH: "group",
}

# Speech actions, which carry a style.
STYLED = (RETELL, DENY, CONFRONT, SPEAK, PREACH)

NEEDS_SOMEONE = (RETELL, DENY, CONFRONT, SPEAK)


def available_actions(view) -> list[str]:
    """Actions this villager can take right now."""
    out = []
    for a in ACTIONS:
        if a in NEEDS_SOMEONE and not view.present:
            continue
        if a == JOIN_GROUP and not view.visible_groups:
            continue
        if a in (LEAVE_GROUP, PREACH) and not view.member_groups:
            continue
        out.append(a)
    return out
