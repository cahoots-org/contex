# Rumor Mill: A Hosted, Shared World — Design

**Status:** proposed
**Author:** design conversation, 2026-10-21
**Builds on:** PR #479 (scenes, brush-off threads, remembered stories, daily round)

## Intent

Rob: "Why not make this multiplayer and host it? Just let everybody play in the same world."
Many players whisper into one living village. Nobody can tell who started a rumor, so the village
fills with stories from strangers you never see, and you can't be sure which ones are yours to
build on and which are someone else's. One shared world goal gives everyone a reason to pull the
same way now and then.

## Rob's decisions

- **Players never see each other.** "More than one person can start a rumor." No player sees
  another player's whispers, rumor cards, notes, suspicion, or presence.
- **Goals are individual, plus one world goal** that all players in the world work toward together.
- **Each player has their own suspicion meter.** You can only be run out of town for your own
  rumors.

## Goals

- One world holds many players at once; it keeps running when nobody is playing.
- Each player has the full single-player game: their own whispers, rumor cards, notes, goals,
  suspicion, conversations, and threads.
- The world goal and its progress are shown to everyone in the world.
- The world survives a restart.
- Cost stays bounded no matter how many players join.
- Nothing a player types reaches other players unfiltered.

## Non-goals

- Chat between players, friends, parties, leaderboards, who's-online lists.
- Accounts with profiles. A player is a browser session at first.
- A separate single-player code path: the local CLI and web game become a one-player world on the
  new code.

## Anti-goals

- Any way to learn that another specific player exists or what they did (a name, a count of
  players in your place, their rumor's card).
- One player's text showing up in other players' feeds as they wrote it, abusive or not.
- Cost that grows with the number of players instead of the size of the village.

## The world and its players

- **Identity.** Each player gets an internal id (`p-` + random) tied to a session cookie. In the
  village every player is **"a newcomer"**: whispers are framed "A newcomer whispered to you", and
  villagers who name the source say "a newcomer". Villagers can't tell newcomers apart in what they
  say; only the game's bookkeeping can. *(My call: no in-world names or descriptions for players,
  since any description would let players notice each other.)* A player may overhear villagers
  talking about "a newcomer" who isn't them; that keeps the ambiguity, not breaks it. A villager's
  recall of what they heard already leaves out every newcomer's whispers, so a villager never tells
  one player what another whispered. A cookie is the whole identity for now; clearing it is
  accepted as a way to start over. *(My call.)*
- **Per-player state.** Everything that is the player's today becomes keyed by player id: the
  player's place, rumor cards, notes, suspicion, goals, open conversations and brush-off threads,
  and the feed. `PLAYER_ID` stops being a constant: the lineage records which player a whisper came
  from, so every hop knows its root player.
- **What a player sees.** Their own whispers and the cards of rumors rooted in them; village talk
  overheard at their place under the existing rules (this can include other players' rumors, shown
  as ordinary village talk, never as anyone's card); village-wide events (time of day, arrivals and
  departures, groups founded); their own goals; the world goal.
- **Suspicion per player.** A villager's suspicion becomes per (villager, player). A line naming
  the newcomer raises suspicion of the player its lineage roots in; when one turn's lines root in
  several players, the turn's suspicion strength applies to each of them. A villager's decision to
  warn or turn away uses their suspicion of the player whose whisper or rumor is in front of them
  (the highest, if several). A warning is about that player; the run-out meeting is per player.
  Only the player it is about sees a warning or the heat line. Being run out ends that player's
  game; they may start again as a new newcomer after 10 real minutes, with nothing carried over.
  *(My call.)*
- **Villager memory.** The village's memory is shared, so it can't be measured in one player's
  moves. It spans the last `memory_moves` (20) moves **per active player** in the world (a player
  is active if they whispered in the last game day), with the single-player case unchanged.
  *(My call.)*

## Goals in a shared world

- **Personal goals** are dealt from the whole existing deck when a player joins (3 each), skipping
  claims already met in the world and the current world goal. The state goals ("8 villagers
  leave", the religion's group of 8) stay personal-only. *(My call.)*
  A personal goal is met when the village holds the claim at its threshold, **whoever spread it**:
  with anonymous players there is no fair way to tell. *(My call; it also means strangers can help
  you without knowing it.)*
- **The world goal** is one belief claim from the deck, held at a higher bar (e.g. two thirds of the
  village), shown to everyone with the same "N of M believe it" progress. When it is met, the world
  celebrates it in the feed and a new world goal is drawn. *(My call: no reset of the world.)*
- **Winning.** When a player's three personal goals are all met, a trophy is posted in that player's
  feed with a warm line ("The village now swears the mayor's cat is a spy, and it all began with a
  whisper. A trophy for your case."). Then three new goals are dealt and play goes on in the same
  world, suspicion and notes intact (Rob).
- **The trophy case.** Each player has a trophy case: a panel listing their trophies, each with the
  goals it was for and the game day it was won. When the world goal is met, every player who was
  active (whispered at least once) while that world goal was in play gets a world trophy, since no
  one's share can be told apart (Rob). The world records who whispered under each world goal.
  Trophies are stored with the player and survive restarts.
- **Judging cost** is bounded by the deck, not the players: the judge checks each claim that is in
  play (any player's goal or the world goal) once per check, and every player with that claim reads
  the same result. At most 15 claims × the village per check, with the existing verdict cache.

## Scale and cost

- **Worlds in one process.** A world is the unit: one Contex session, one village of 50–100
  villagers, one clock; worlds share nothing but the process. One server process hosts several
  worlds as objects and sends each session cookie to its player's world; a cap on worlds per process
  bounds it. *(My call: simplest on one Railway service; split into processes only if load says so.)*
- **The clock** keeps its pace (a game day is `day_seconds`, 120 s). Anything that must last in real
  time is set in real time: the run-out lockout, retention, snapshot cadence.
- **Turn budget.** A village of 50 runs about 100 villager turns a minute outside scenes (30 s
  cooldown, 20 turns at once); scenes run faster (5 s), so `max_scenes` bounds the rest. A world takes players up to a cap, `max_players` (Config, 20); the next
  player starts a new world. *(My call; tune the cap in a load test.)*
- **Scenes and overhearing.** Village-talk scenes may start at any player's place; `max_scenes`
  scales with players (one per 5 active players, at least 3). A scene on one player's rumor shows on
  that player's card; to everyone else at the place it is village talk. Overhearing's "goal" reason
  uses the overhearing player's own goals. *(My call.)*
- **Per-player limits.** At most one whisper every few seconds per player (`whisper_cooldown_seconds`,
  Config, 5), so one player can't flood the village.
- **Cost** follows villager activity: about the cost of a busy single-player village per world,
  whatever the number of players in it. A cap on worlds bounds the whole service.

## A world that survives a restart

- Game state that lives in memory today is snapshotted to Postgres every minute and on shutdown, and
  loaded on start: the town roster (including villagers who arrived after the seed and those who
  left), places, groups and members, lineage, facts, rumor cards, scenes, suspicion, notes, players
  and their goals, the clock, conversations and threads. The seed, the places cache and the baseline
  cache move into the same database, since a container's disk does not survive a redeploy.
  *(My call: snapshots, not a write per event; losing up to a minute on a crash is acceptable.)*
- **The world's Contex session is pinned**: a world keeps its session id across restarts (stored
  with the snapshot), so villagers' minds and subscriptions resume instead of starting empty.
- **Retention.** What villagers react to is already bounded (subscription windows, `Facts` keeps
  5000 lines). Storage is not: the game drops state older than a day of real time, and Contex
  needs a way to drop old items from a project (or a whole session's projects): a change under
  `src/`, as its own PR.

## Keeping players' text in check

- Every whisper goes through a moderation check before it is published: one Jev Noul set asking
  whether it is abusive, sexual, a threat, or an attempt to instruct the villagers' writer. A
  failed check returns "That won't fly in this village." and publishes nothing. *(My call: Jev,
  not a separate moderation service; the cost is one cheap request per whisper.)*
- Villagers' lines are written by Haiku from what they heard, never copied verbatim, which already
  softens what a player wrote; the writer's prompts gain one sentence telling it to drop slurs and
  instructions it finds in what was heard.

## Hosting

- A container image with the game; Contex and Postgres (ParadeDB, as today) as services. Rob's own
  Railway project is the first host. Model keys stay server-side.
- The web app stops being localhost-only: origin and host checks follow the configured public
  hostname; a session cookie identifies the player; the websocket feed is filtered per player.

## Order

1. Per-player identity and state in one process (local first: two browser sessions, one world).
2. Personal and world goals, per-player suspicion.
3. Moderation of whispers.
4. Persistence and retention (with the Contex retention PR).
5. Hosting: public web app, sessions, per-player limits, the world cap, deployment.

Each step is its own PR.

## Left to the builder

- The snapshot format and tables, the session cookie details, how a new world is started when one
  is full, the moderation question wording, the exact world-goal bar, and the load test that sets
  `max_players`.
