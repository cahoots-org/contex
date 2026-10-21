# Rumor Mill: Following Your Rumor, Overhearing, and Talking — Design

**Status:** proposed
**Author:** design brainstorm, 2026-10-21
**Builds on:** PR #477 (the combined Rumor Mill)

## Intent

A playtest with one whisper ("I heard there was some strange goings on at the
tavern. I think it may be haunted.") produced about a hundred feed lines in a few
minutes. Most of them were the tavern's fifteen people insulting each other about
drinking, leather, and herbs. The haunting was gone after two retellings, the
player could not tell their rumor from the noise, and the village ran the newcomer
out of town though only one villager had ever heard from them.

The chatter itself is good: it is the village coming alive. The player has to be
able to tell it apart from their rumors, hear some of it without drowning, get
caught only for what villagers could actually know, and talk with a villager who is
interested.

## Root causes (from the playtest transcript and the code)

1. **Suspicion follows lineage, not knowledge.** `Engine._turn` builds
   `player_rooted` from every new item whose lineage root is a whisper
   (`engine.py` around the TurnView construction) and asks Jev to rate suspicion for
   all of them. The rating instructions also count rumors that are "sensational or
   hurtful to someone they know". So every insult that descended from the whisper
   raised suspicion of the newcomer in every listener, though none could know the
   source.
2. **The feed follows lineage, not content.** `Feed.on_delivery` prints any
   utterance whose lineage root is a whisper. Replies to replies about other topics
   still descend from the whisper.
3. **Nothing makes a story wear out.** A `speak` reaches everyone present; with the
   0.02 margin most of them wake; each reply reaches everyone again. The cooldown only
   paces it.

## Goals

- Suspicion rises only from what a villager could know.
- The player's rumors read as their own threads; village talk is visibly different,
  filtered, and available on demand.
- Stories fade after a few retellings unless they keep touching real worries.
- A villager interested in what the player says answers, and the player can carry on
  a short conversation.
- Villagers remember what they have said, so the player can bring it up.

## Non-goals

- A map, or moving the player around by command beyond where they last whispered.
- Changes to Contex (`src/`).
- Relationships, families, homes (the village-life slices stay future work).

## Anti-goals

- A model call to decide what the player gets to see. Chatter filtering is code.
- Hiding the village. Chatter stays reachable.
- Conversations that never end or cost unbounded tokens.

## 1. Suspicion follows knowledge

- A villager's suspicion rises when (a) the newcomer whispered to them (unchanged),
  (b) a line they hear names or implies the newcomer, or (c) they are warned (unchanged).
- (b) replaces the lineage rule. The suspicion Score question is asked only when a
  new item mentions the newcomer. *(My call.)* "Mentions" is decided in code: the
  utterance carries a `names_newcomer` flag set when it is written. The writer sets it
  when the speaker's source line came from the newcomer directly (a whisper) or itself
  carried the flag and the speaker chose a faithful or blame-shifting retelling; Haiku
  is told whether the speaker knows the newcomer was the source and to say so when the
  style is faithful. Every other retelling drops the source.
- The "sensational or hurtful to someone they know" clause is removed from the rating
  instructions.
- The judge, warnings, refusals and the town level are unchanged.

## 2. Stories wear out

- Every utterance carries a `depth`: 0 for a whisper, the source's depth + 1 for a
  retelling or reply, 0 for a line with no source.
- A villager's turn on new items only happens if at least one new item is "fresh
  enough": its similarity to the matched need clears that need's threshold plus
  `fatigue_per_hop × depth` (Config, starting 0.01). Items that fail are remembered but
  do not wake the villager. *(My call.)* This makes a story fade after a few hops unless
  it touches a worry strongly, and needs no model call.
- Overheard room talk (a `speak` to everyone present) needs `overheard_margin`
  (Config, starting 0.01) more than a line told to the villager directly.
- Tuned in the last task so a whisper still spreads a few hops and the tavern settles
  within a minute or two instead of running on.

## 3. The feed: your rumors, and what you overhear

### Your rumors (option A, approved)
- Each whisper starts a **rumor card**: the whisper's text as its title, the status
  chip, and a tally ("heard by N, still told by M" where M counts listeners of hops that
  are still on topic in the last game day).
- A hop belongs on the card while it is **on topic**: its similarity to the original
  whisper text is at least `on_topic` (Config, starting value tuned in the last task;
  calibrated like a need, using the whisper as the text). Computed in code with one
  Contex query per hop against a per-session "rumors" project holding the whisper
  texts, no model call.
- When a thread leaves the topic, the card shows one line: "The story turned to other
  things after this." Off-topic descendants do not appear on the card.
- Warnings, refusals, the mood line, and goal changes always show at full strength,
  outside the cards.

### What you overhear
- **The player has a place:** where they last whispered. It shows in the goals strip
  ("You're at the tavern"). At the start it is the square.
- From utterances spoken at the player's place that are not on a rumor card, the
  player overhears:
  - **inflammatory** lines: the speaker's chosen style was `exaggerated`, `mock`, or
    `shift_blame`, or the action was `confront` or `deny`;
  - **goal-relevant** lines: similarity to any dealt goal's evidence clears that goal's
    threshold (the calibrated baselines the judge already uses);
  - **one in eight** of the rest, at random (`overhear_rate`, Config).
- Overheard lines are grouped between rumor cards into a muted **village talk** block
  per place: a count and the two newest lines, expandable to the rest of the block.
  Each line shows the speaker and the line, with an action "Talk to <name> about this".

## 4. Villagers remember what they said

- Each villager gets a second Contex project, `rumor-mill:<session>:said:<vid>`, that
  holds every line they speak. It has no subscriptions, so it never wakes them.
- When a turn's new items include a line from the newcomer, the engine queries the
  villager's said project with that line (top 3, kept when similarity clears a fixed
  floor `said_floor`, tuned) and adds the matches to
  the Jev state as "things I said recently that this is about". Haiku sees them too
  when writing a reply.
- Forgetting matches the mind's window (`max_age_seconds`).

## 5. Talking with a villager

- **Start:** a whisper that lands (direct check hit) on a villager who is interested.
  Their turn offers a new action, `answer`, addressed to the newcomer, alongside the
  existing ones. Jev chooses. If chosen, Haiku writes a short reply in the villager's
  voice that responds to what the newcomer said (and to their own earlier line if the
  whisper was about it).
- **The conversation:** the reply appears on that whisper's rumor card as a
  conversation (the newcomer's lines and the villager's replies, alternating). The
  composer shows "Talking with Petrine" with the To box filled; the player's next message
  to her is a reply.
- **Each reply** from the player goes through the same path as a whisper (published to
  her mind, checked against her needs and her own recent lines). If it lands, her next
  turn can `answer` again; if it misses, she ends the conversation with a brush-off line.
- **Limits:** at most `talk_turns` replies from the villager per conversation
  (Config, starting 4); the villager's cooldown still applies between replies, and the
  chip shows the countdown; suspicion rises with every message as it does for whispers.
  Leaving town or a refusal ends the conversation.
- **Starting from overheard talk:** "Talk to <name> about this" fills the To box and
  quotes the overheard line in the message box; sending it is an ordinary whisper, which
  her said-memory then connects to her own line.
- Villagers still cannot initiate a conversation with the newcomer.

## Decisions after a builder read *(my calls)*

- **Where match facts live.** `depth`, `names_newcomer`, audience size (one listener or
  the room), the speaker's action and style live in memory keyed by utterance id (next to
  `Lineage`), never in the published payload, so scores do not change.
- **Per-need clearance.** `Mind.poll` returns, for each new match, its best clearance
  (similarity minus that need's threshold) across the needs it matched; a direct hit
  carries its clearance over the matching need. Fatigue and the overheard margin compare
  against clearance.
- **Stale items are memories, not news.** On a turn with at least one fresh item, items
  that fail the fatigue rule go to `recent`, not `new`, so Jev cannot pick a worn-out line
  as the thing to retell. A turn with no fresh item does not happen.
- **Warnings** wake their target as now (addressed, depth 0). A retelling of a warning
  is an ordinary line at depth 1, subject to fatigue.
- **`names_newcomer`** applies to every speech action with a source (retell, speak, deny,
  confront, preach). Only a `faithful` retelling of a line that named the newcomer (or of
  the whisper itself) keeps it; every other style, including `shift_blame`, drops it, and
  the writer gets the source text without the "The newcomer whispered to you" framing and
  an instruction not to mention who started it.
- **"Took the bait" requires the line to be on topic,** like the card. A relay that wandered
  off counts as kept.
- **On topic** is a margin over the whisper's own baseline (`on_topic_margin`, tuned),
  calibrated in memory per whisper and never written to the baselines cache. The check is a
  query against a per-session `rumors` project holding each whisper's words only (no
  framing), filtered to that whisper's key with `since` at the whisper's publish time, and
  the hop's text is compared without framing too. It runs in `deliver` before the feed event,
  only for hops whose lineage root is a whisper.
- **Goal relevance for overhearing** is one query per utterance spoken at the player's place
  that is not on a card, against a per-session `goals` project holding the dealt goals'
  evidence, using the goals' calibrated thresholds. Listed under Cost.
- **Conversation threading.** A player's reply is a new whisper (its own lineage root, its
  own status chip, depth 0) tagged with the conversation's first whisper id, so it renders on
  that card in order. New chip states: `answered` (the villager replied; not final, ranks
  above listening and below kept in both `notes.py` and the page) and `moved_on` (a reply
  landed but the villager chose something else, or the reply cap was reached; final). A miss
  ends it with the brush-off as now.
- **`answer` has its own path.** Offered only when the turn's new items include a newcomer
  line that landed and the conversation is under `talk_turns`. Its target is the newcomer. It
  writes the line, records it in lineage and in the villager's said project, and emits a
  `reply` feed event. It is not delivered to any mind.
- **Reloads.** `/api/state` carries the rumor cards (whisper ids, their on-topic hop ids, the
  conversation lines, tallies), so cards survive past the 500-event replay window.

## Cost

- No new model calls for filtering, fatigue, or topic.
- One extra Contex query per player-rooted hop (topic), per newcomer line (said-memory),
  and per line spoken at the player's place that is not on a card (goal relevance).
- Conversations: one Jev request and one Haiku line per villager reply, capped per
  conversation.

## Left to the builder

- Exact copy, layout of cards and talk blocks, within the existing page design.
- The `names_newcomer` prompt wording.
- Tuning `fatigue_per_hop`, `overheard_margin`, `on_topic`, `overhear_rate`,
  `said_floor`, `talk_turns` in the last task, with the playtest whisper as the case:
  the haunting rumor spreads a few hops, the tavern settles, one whisper does not get
  the player run out, and a conversation with an interested villager runs a few turns.
