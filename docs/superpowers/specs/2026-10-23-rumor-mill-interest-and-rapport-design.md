# Rumor Mill: Interest and Rapport — Design

**Status:** approved (Rob, 2026-10-23: "Interest should be tied to the rumor/thread. Otherwise, yes. That looks good.")

## Intent

Rob: "I'd rather her leaned-in-ness be a float value that increases when I say something she's interested in and decreases when I say something that she isn't. If it hits a certain threshold in the negative direction, she should end the conversation with a line that matches her temperament. If it tilts in the positive direction, then she's more likely to become a believer or take an action." Then: "I like using the lean in lines to indicate how you're doing. A meter is too easy (also screen cluttery)." And: "The NPCs should also have a relationship with the players."

## Two numbers

**Interest** belongs to one villager, one player and one rumor (its root card). It runs from −1 to +1.
- It starts at half the villager's rapport with the player.
- Returning to a rumor with the same villager resumes its interest.

**Rapport** belongs to one villager and one player, for the length of the player's run, like suspicion. It runs from −1 to +1 and starts at 0.
- Rapport is separate from suspicion: rapport is whether she likes talking to you, suspicion is whether she thinks you started the rumors. Neither changes the other. *(My call; revisit after the playtest.)*

## How a whisper moves them

The per-need check already measures each whisper against the villager's needs (`check` → clearance per need).

| Outcome | Interest change |
|---|---|
| Hit (clearance ≥ 0), or a story she already heard | +0.4 |
| Near miss (within `near_miss_band` below the bar) | +0.1 to +0.25, closer is more |
| Brush-off (nothing within the band) | −0.35 |

- Interest is clamped to [−1, 1].
- Each whisper also moves rapport by a tenth of the interest change.

## Effects

- **Easier to land.** When interest is positive, each of her needs' bars drops by `interest × near_miss_band` for that player's whisper. At full interest, a whisper that would have been a near miss lands instead.
- **She runs out of patience.** When interest falls to −0.6 or below:
  - she ends the conversation with one Haiku line in her temper, apologetic if kindly and curt if sour;
  - the player's exchange with her ends, and her dock face leaves;
  - rapport drops by a further 0.2;
  - suspicion rises by half a whisper bump, with the reason "lost patience with you".
- **She leans in.** The lean-in and brush-off lines are told how her interest stands, so their tone carries it: eager, curious, lukewarm, or wearing thin. There is no meter and no number on the page.
- **Rapport colours how she treats you.** The first line she says in a rumor (lean-in, brush-off or reply) is told her rapport with you, so she greets you warmly ("Ah, you again, what news?") or coolly ("What is it now?").
- **She acts and believes more readily.**
  - When she takes a turn on a line rooted in the player's whisper, Jev's state says how taken she was with it (eager, interested, indifferent, or put off), drawn from that rumor's interest.
  - The judge's belief question about her sees the same words.

## What goes away

- `THREAD_WHISPERS`: the two-whisper cap on a lean-in thread. A thread now lasts while her interest is above the ending line and the player keeps talking, under the one-minute hold.
- `ANNOYING_MISSES`: "two misses in a row annoys her". `annoyed` becomes: suspicion at or above `warn_threshold`, or interest in the open rumor at or below −0.3.

## Left to the builder

- Prompt wording for the tones.
- Word bands for interest and rapport.
- Where the numbers live (an engine-owned store, like suspicion).
- Config names for the values above.
