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
WARN = "warn"
ANSWER = "answer"

ACTIONS = {
    IGNORE: "Do nothing; carry on with the day.",
    RETELL: "Pass on something just heard to one specific person nearby.",
    DENY: "Tell one specific person nearby that something being said is false.",
    CONFRONT: "Challenge one specific person nearby about what they said or did.",
    SPEAK: "Tell everyone nearby: spread what you just heard, or say what is on your mind.",
    MOVE: "Walk off to a different place in town, keeping the news to yourself for now.",
    FOUND_GROUP: "Start a new group around a belief, fear, or cause you feel strongly about: a cult, a watch, a guild, a protest.",
    JOIN_GROUP: "Join a group that has members nearby and a cause you share.",
    LEAVE_GROUP: "Quit a group you belong to that you no longer believe in.",
    PREACH: "Speak up for one of your groups' beliefs to the people nearby.",
    LEAVE_TOWN: "Leave town for good: ruined, disgraced, frightened, or fed up.",
    WARN: "Warn one specific person nearby that the newcomer is behind the rumors.",
    ANSWER: "Answer the newcomer, who just spoke to you about something you care about.",
}

# Option details where two actions are easy to confuse.
ACTION_NOT_FOR = {
    RETELL: "Not for telling everyone nearby (speak) or disputing it (deny).",
    SPEAK: "Not for telling one person in particular (retell).",
    DENY: "Not for personal accusations (confront).",
    CONFRONT: "Not for correcting a rumor in general (deny).",
    WARN: "Not for passing on what you heard (retell) or arguing with someone (confront).",
}

STYLES = {
    "faithful": "Repeat it as it was told, as accurately as you remember.",
    "exaggerated": "Make it bigger and more dramatic than it was told.",
    "shift_blame": "Change who is responsible for it.",
    "add_detail": "Embellish it with an invented but plausible detail.",
    "soften": "Play it down and make it sound less serious.",
    "mock": "Retell it with scorn or ridicule.",
    "delighted": "Tell it with relish and delight.",
    "worried": "Tell it anxiously, worried for those involved.",
    "conspiratorial": "Lower your voice and share it as a juicy secret.",
}

TARGET_KIND = {
    RETELL: "villager",
    DENY: "villager",
    CONFRONT: "villager",
    MOVE: "place",
    JOIN_GROUP: "group",
    LEAVE_GROUP: "group",
    PREACH: "group",
    WARN: "villager",
}

# Speech actions, which carry a style.
STYLED = (RETELL, DENY, CONFRONT, SPEAK, PREACH, WARN)

NEEDS_SOMEONE = (RETELL, DENY, CONFRONT, SPEAK, WARN)


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
        if a == WARN and view.suspicion < view.warn_threshold:
            continue
        if a == ANSWER and not view.answerable:
            continue
        out.append(a)
    return out
