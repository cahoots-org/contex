# Rumor Mill

A gossip game you play in the terminal or a browser. A village of 50 villagers runs on Contex. You are the
newcomer, and all you can do is whisper. You get three goals, such as "a third
of the village believes the well is cursed", and you win by spreading rumors
until the village matches them.

Each villager's mind is a Contex project. A villager wakes only when Contex
pushes something that matches their needs. Jev (TypeSafe) decides what they do,
and Claude Haiku writes what they say. Nothing is scripted: a whisper about
backwards-flying birds can end up as a ploughman's doomsday watch society.

## How it uses Contex

| Feature | Where |
|---|---|
| `contex_publish` | every line a villager hears (one publish per listener), whispers, group records |
| `contex_create_subscription` | one per need (each villager has 3 to 5) on the villager's mind project, at that need's threshold |
| `max_age_seconds` | noticing: a subscription only carries talk from the last `memory_days`, for a villager to react to |
| `since` | forgetting: the judge counts only lines heard since your last `memory_moves` moves, so idling never costs belief |
| `subscriptions/listen` | one stream per villager over all their subscriptions; a push is what wakes them |
| reading the subscription resource | the villager's memories each turn, and lease renewal for quiet villagers |
| `contex_query` | the check right after a whisper of whether it matched the target's needs; calibrating needs; the judge reading minds and group records to score goals |
| semantic matching | a whisper wakes a villager only if it scores clearly above ordinary village talk for one of their needs, and a rumor still reaches people after it is retold in other words |
| scale | 100 projects, about 400 subscriptions, and 100 listen streams at once |

A villager never hears their own words, so speaking never wakes the speaker.
Who told whom is tracked in the game, which is how the feed shows every hop of
your rumors and only yours.

## Setup

Optionally, start Contex with a 60-second reconcile sweep so quiet minds forget
sooner. The setting applies to the whole Contex server, and each sweep costs a
CPU burst: at a 30-second sweep with 100 villagers, the server used 4 to 5 CPU
cores for a few seconds each time. Without the override, forgetting is trimmed
whenever a villager takes a turn and every 300 seconds otherwise.

```bash
docker compose -f docker-compose.yml -f examples/rumor_mill/compose.sweep.yaml up
```

Install the game's dependencies (Python 3.12) and set the two API keys, either
exported or in a `.env` file (the game reads the nearest one; exported values win):

```bash
pip install -r examples/rumor_mill/requirements.txt
export ANTHROPIC_API_KEY=...
export TYPESAFE_API_KEY=...
```

Run it from the repo root:

```bash
python -m examples.rumor_mill
```

To play in a browser instead, add `--web` and open the address it prints,
`http://127.0.0.1:8765` unless `RUMOR_MILL_WEB_PORT` says otherwise:

```bash
python -m examples.rumor_mill --web
```

Each run asks Haiku for a new village in batches of 10, every villager with a
first name of their own, and saves it as `rumor_mill_worlds/<session>.json`;
the game prints the path. Set `RUMOR_MILL_SEED_PATH` to that file to play the
same village again (the model can't reproduce a village from a seed, so the
file is the record). Most villagers are good-natured; about one in five is sour
and holds a grudge, and those are the village's skeptics, quick to doubt or mock
a rumor. If a saved village's size differs from `RUMOR_MILL_POPULATION` the game
exits and says so. Each run starts with empty minds unless you set `RUMOR_MILL_SESSION`.

On the machine this was tuned on, a 100-villager game took 5.5 seconds to
start from the seed file, and the game process used 125 MB of memory and
6 seconds of CPU over a 400-second session. The Contex server held 100 listen
connections and used 4 to 5 CPU cores for a few seconds at each 30-second
sweep; the override uses 60 seconds.

## How a whisper lands

The embedding model gives almost any two village sentences a similarity
between about 0.72 and 0.82, related or not, and each need sits at its own
level within that band. So each need gets a baseline: its mean similarity to
20 ordinary sentences (weather, chores, prices, a lost goat) that concern no
one. A line matches a need when it scores at least `RUMOR_MILL_MATCH_MARGIN`
(0.015) above that need's baseline. Each need's subscription uses that
threshold, so Contex pushes only real matches.

Right after you whisper, the game queries each of the target's needs for your
whisper, one query per need and no model call, which is how the feed can say
at once whether it landed. A whisper that lands joins the villager's next turn
even if the push is slow.

The first run computes the baselines and writes them to
`rumor_mill_baselines.json`, shared by every village; for 400 needs that took 5.4 seconds on
the machine this was tuned on. Later runs read the file. Baselines are kept by
need text and by the exact payload the calibration sentences were published
in, and are recomputed when either changes. They depend on Contex's embedding
model, so delete `rumor_mill_baselines.json` after changing it.

Contex embeds every field of what is published, including the data key, so
the game publishes only an utterance's id and its sentence. The key alone
still moves a score by up to about 0.02, so a whisper that sits right at the
margin can land under one id and miss under another.

The margin was tuned on the 100-villager seed with 20 whispers written from
villagers' cards before reading their needs, half aimed at a trade and grudge
and half unrelated, each published four times with random ids and places.
These figures and the retold-line figure below were measured while the place
was still part of the published text, and are due to be re-measured:

| Margin | Aimed whispers that land | Unrelated whispers that land |
|---|---|---|
| 0.0175 | 100% | 36% |
| 0.02 | 96% | 26% |
| 0.025 | 90% | 16% |
| 0.03 | 82% | 5% |

The default is 0.015, a little below the lowest measured margin: in play,
aimed whispers kept missing a fitting need by less than 0.005, which felt like
a hit that didn't count. Expect more unrelated whispers to land than at 0.0175.

A line someone retells ("Bert said: ...") matches a little more
easily than a whisper: at 0.02, 42% of the unrelated lines matched a need of
whoever heard them retold (measured with the place in the text). A lower margin makes the village talk more, and a
higher one makes more of your aimed whispers miss.

Run Contex with authentication off, since its rate limits would throttle
calibration and the check after each whisper, and with `SYSTEM_ONE_ENABLED`
off, since query reranking would call a model on every check. Both are off by
default.

## Commands

In the terminal:

```
<name>: <text>            whisper to a villager, e.g. Marta: the miller waters his flour
whisper <name>: <text>    the same
villagers                 everyone in town, with occupation and place
inspect <name>            personality, any grudge, and relationships
goals                     your goals, numbered, and how close each one is
swap <n>                  swap unmet goal n for a new one
usage                     tokens used so far and their estimated cost
help                      these commands
quit
```

A name can be part of a full name if it is unambiguous. A whisper that
touches nothing the villager cares about does nothing, and the feed says at
once whether it caught their ear or they shrug it off. `inspect` shows enough
to guess what someone cares about, but not their needs.

A shrug is a hint. The villager brushes you off in their own voice, kindly or
curtly as their temperament goes, and alludes to something they care about, a
different concern each time until they run out and start over, so a miss tells
you where to aim next. If the line can't be
written, the whisper just says it was brushed off.

The feed shows each hop of a rumor you started, who told whom and in what
words, and a line when a goal's percent changes.

## Goals

Each goal is a template filled from the village. Code picks who it is about,
favouring villagers someone holds a grudge against, and Jev fills the rest. A
hand mixes the kinds at random:

- Belief goals: convince one villager that someone did something, get a share
  of a circle (a group, or a place's regulars) to believe it, or get a share of
  the village to believe a claim.
- Action goals: get a villager to do something because of your rumor, such as
  confront someone, leave town, start a group against someone, or join a
  group. "Drive 2 villagers out of town" counts anyone who leaves because of
  you.

The accusations and claims come from a pool Haiku writes once per village and
saves beside the village file as `<village>.goals.json`. Belief goals are checked once a
game day and are met after they hold at 100% for three checks in a row. An
action goal is met the moment its action happens, if what moved the villager
traces back to one of your whispers.

## Suspicion

Each villager keeps their own suspicion of the newcomer. It goes up when:

- you whisper to them: every whisper tells them it came from you;
- they hear a line that names the newcomer: only a faithful retelling of your
  whisper keeps your part in it, so talk that has drifted from it never does;
- someone warns them about you.

A villager who suspects you enough may warn someone else about you. The feed
shows that a warning happened ("Bert Cole warned Marta Hale about you."), but
not what was said. A villager who suspects you strongly turns you away: your
whisper doesn't reach them, and trying again makes them more suspicious.

Suspicion fades a little each day. The village's mood is a mean of everyone's
suspicion in which the mayor, priest, innkeeper, tavern keeper, constable,
bailiff, and reeve count for more. You see it as one of four lines, under your
goals on the browser page and after `goals` in the terminal:

| Label | What it says |
|---|---|
| quiet | Folk are not talking about you. |
| murmurs | There are murmurs. |
| watched | Folk are watching you. |
| hunted | The village is hunting for you. |

The village checks its mood four times a game day. If it is at the run-out
level for three checks in a row, the village meets in the square and runs you
out of town, and you lose. The summary lists your goals with their percent and
says what gave you away, such as "Marta Hale heard it from you directly." You
win when every goal is met before that happens. Either way the game is over:
whispers are refused, and you restart the program to play again.

## The browser page

The page has the feed with your goals pinned above it, a whisper box, and the
village in the sidebar.

The village is one tile per place, each with how many villagers are there and
their faces. One tile is open at a time and lists everyone there: face, name,
trade, what you know about them, and the last line they brushed you off with.
The page remembers which tile you opened. The search box above the tiles finds
villagers by name, trade, or place: it highlights matching faces and opens the
place where the best match is. Escape clears it. The whisper box offers the
same matches as you type; arrow keys and Enter pick one.

Click a face, or press Enter on a name, to open the villager's card: occupation,
personality, grudge if they have one, relationships, and what you know. "Whisper to" fills the
whisper box with their name. Escape closes the card.

A face is generated from the villager's id and trade, so the same villager has
the same face every game. A badge on the face says what you know:

| Badge | Means |
|---|---|
| ✓ | took the bait: passed your whisper on |
| ↩ | answered you: you are talking with them |
| ○ | kept it quiet: heard you and told no one |
| – | brushed you off: your whisper missed what they care about |
| ! | wary of you: turned you away or warned others about you |
| … | listening: you whispered to them and nothing has come of it yet |

The open tile and the card say the same in words. Each whisper in the feed
shows where it stands, updated as it changes: checking, busy (with the seconds
until they hear you), heard, took the bait (and how many people they told),
kept it quiet, warned others, answered you, moved on, brushed you off (with
their line once it is written), or didn't carry (the game couldn't check it; a
delivery failure shows no chip).

Each whisper starts a rumor card: your words as its title, the chip, and a
tally, "heard by N · still told by M", where M counts the people a retelling
reached in the last game day (the target is not counted). A retelling goes on the card while it still carries what
you whispered. Each one is indented once per retelling, says whom the speaker
heard it from, and highlights the words that differ from what they heard. When
most of it was reworded, it underlines the words that survived instead. Hover
or click a hop to light up its path back to your whisper; Escape clears it.
When the village starts telling it as something else, the card says "The story
turned to other things after this." and shows no more of that thread.
Warnings, refusals, the mood line, and goal changes stay outside the cards.

Between the cards is village talk: lines you overhear where you are, which is
where you last whispered ("You're at the tavern" above the feed). You don't
hear everything, only four kinds of line: heated ones (someone mocking,
blaming, confronting, or denying), four in ten lively ones (someone telling
it with relish, worry, or a lowered voice, or exaggerating or embellishing
it), ones about one of your goals, and one in eight of the rest, picked at
random.
Heated and lively lines are marked as such. A block of village talk shows
the place, how many lines it holds, and the two newest; "Show all" opens the
rest. Each line ends with "Talk", which puts the speaker's name in the
To box and starts your message with what they said.

A villager whose interest you caught may answer you. Their reply goes on that
whisper's card, and the card shows the exchange as it goes: your lines, each
with its own chip, and theirs. While they are talking with you, the composer
says "Talking with" their name, and what you send them next is a reply. The
conversation ends when they move on, brush you off, turn you away, or the game
ends.

Arrow keys move through the feed: the hops, your lines, and the buttons in
village talk. Reloading the page brings back the last 500 feed events and
every rumor card.

The server listens on 127.0.0.1 only. It answers only requests addressed to
`127.0.0.1` or `localhost` on its own port, and refuses whispers and WebSocket
connections from other origins.

## What it costs

The game keeps a running count of the tokens it sends to Haiku and Jev and
multiplies them by per-million-token prices. `usage` prints it in the
terminal, the browser page shows it in the strip above the feed, and both
print it on quit. The figures are estimates: the prices are list prices you can
change, and the providers' bills are the real numbers.

Where the tokens go:

- Setup: Haiku writes each new village, the goal pool once beside it, and each newcomer who arrives after someone leaves.
- Each hand after the first, and each swap: two Jev requests per belief goal
  about a villager, one for the accusation and one for who it works on, and
  one per action goal that names who must act.
- Each time a villager wakes: one Jev request to decide what they do, plus a
  short Haiku call (at most 80 tokens out) when they speak, and a slightly
  longer one when they found a group.
- Each shrug: one short Haiku call (at most 80 tokens out) for the brush-off,
  and a second one if the first repeated the villager's concern word for word.
- The judge: one Jev request per villager whose memories match a belief goal,
  once a game day. Verdicts are cached until the villager's matching memories
  change.
- An answer to you: the turn's Jev request plus one short Haiku line, at
  most `TALK_TURNS` per conversation.

Following and overhearing add no model calls, only Contex queries. On the 100-villager seed with the defaults, the haunting
whisper and the talk it started cost about $0.002 in five minutes; four
whispers and a three-reply conversation, under $0.004.

| Variable | Default (dollars per million tokens) |
|---|---|
| `RUMOR_MILL_HAIKU_INPUT_PER_MTOK` | `1.0` |
| `RUMOR_MILL_HAIKU_OUTPUT_PER_MTOK` | `5.0` |
| `RUMOR_MILL_JEV_INPUT_PER_MTOK` | `0.04` |
| `RUMOR_MILL_JEV_OUTPUT_PER_MTOK` | `0.0` |

## Settings

Every field of `Config` can be set with an environment variable.

| Variable | Default | Meaning |
|---|---|---|
| `RUMOR_MILL_CONTEX_URL` (or `CONTEX_MCP_URL`) | `http://localhost:8001/mcp` | Contex MCP endpoint |
| `RUMOR_MILL_SESSION` | random per run | prefix for this run's Contex projects |
| `RUMOR_MILL_POPULATION` | `50` | village size; newcomers arrive to keep it there |
| `RUMOR_MILL_DAY_SECONDS` | `120` | length of a game day |
| `RUMOR_MILL_MEMORY_DAYS` | `2` | how many days a villager remembers what they heard |
| `RUMOR_MILL_MATCH_MARGIN` | `0.015` | how far above a need's score against ordinary village talk a whisper must score to match that need |
| `RUMOR_MILL_TOP_K` | `5` | memories kept per need |
| `RUMOR_MILL_CALIBRATION_PATH` | `rumor_mill_baselines.json` | where need baselines are cached |
| `RUMOR_MILL_ON_TOPIC_MARGIN` | `0.035` | how far above a whisper's score against ordinary village talk a retelling must score to stay on its rumor card |
| `RUMOR_MILL_FATIGUE_PER_HOP` | `0.003` | extra clearance over a need's threshold each retelling hop needs before it wakes a listener |
| `RUMOR_MILL_OVERHEARD_MARGIN` | `0.01` | extra clearance a line said to the room needs over one told to the listener |
| `RUMOR_MILL_OVERHEAR_RATE` | `0.125` | share of ordinary talk at your place that you overhear |
| `RUMOR_MILL_LIVELY_RATE` | `0.4` | share of lively talk at your place that you overhear |
| `RUMOR_MILL_SAID_FLOOR` | `0.8` | how similar a villager's earlier line must be to what you say to them for them to recall it |
| `RUMOR_MILL_TALK_TURNS` | `3` | most times a villager answers you in one conversation |
| `RUMOR_MILL_COOLDOWN_SECONDS` | `30` | least time between a villager's turns |
| `RUMOR_MILL_CHECK_LOOKBACK_SECONDS` | `5` | how far before a whisper the check that it landed looks, to absorb clock skew with Contex |
| `RUMOR_MILL_ARRIVAL_DELAY_SECONDS` | `60` | wait after a departure before newcomers arrive |
| `RUMOR_MILL_JUDGE_EVERY_DAYS` | `1` | days between goal checks |
| `RUMOR_MILL_JUDGE_CONCURRENCY` | `10` | goal-check calls in flight |
| `RUMOR_MILL_TURN_CONCURRENCY` | `20` | villager turns in flight |
| `RUMOR_MILL_RENEW_SECONDS` | `1200` | read quiet villagers' subscriptions this often so they don't expire |
| `RUMOR_MILL_HAIKU_MODEL` | `claude-haiku-4-5-20251001` | model for speech and village generation |
| `RUMOR_MILL_SEED_PATH` | empty: a new village | a saved village to play again |
| `RUMOR_MILL_WEB_PORT` | `8765` | port for `--web` |
| `RUMOR_MILL_SUSPICION_WHISPER_BUMP` | `0.12` | suspicion a whisper adds to the villager you whisper to |
| `RUMOR_MILL_SUSPICION_HEAR_MAX` | `0.25` | most suspicion one rumor about the newcomer can add |
| `RUMOR_MILL_SUSPICION_DECAY_PER_DAY` | `0.05` | suspicion each villager loses per game day |
| `RUMOR_MILL_WARN_THRESHOLD` | `0.3` | suspicion at which a villager may warn others |
| `RUMOR_MILL_REFUSE_THRESHOLD` | `0.7` | suspicion at which a villager turns you away |
| `RUMOR_MILL_RUN_OUT_LEVEL` | `0.08` | village mood at which the run-out checks start counting |
| `RUMOR_MILL_RUN_OUT_CHECKS` | `3` | checks in a row at that level before you are run out |
| `RUMOR_MILL_SUSPICION_CHECK_DAYS` | `0.25` | game days between mood checks |

`MATCH_MARGIN` matters most; see [How a whisper lands](#how-a-whisper-lands). Lower it and villagers react to more of what they hear; raise it and the village goes quiet.

Stories wear out. A line wakes a listener only if it clears one of their
needs by `FATIGUE_PER_HOP` for each time it was retold, plus
`OVERHEARD_MARGIN` when it was said to the room. Before these, one whisper
at the tavern on the 100-villager seed set its 15 villagers trading about a
hundred insults in a few minutes: a line said to the room cleared the needs
of 6 to 9 of them. With the defaults, replays of that whisper gave 1 to 4
retellings, each over within a minute, and the village stayed quiet. Raise
the fatigue for a quieter village; lower the overheard margin and room talk
runs on.

`ON_TOPIC_MARGIN` decides what stays on a rumor card. Measured as similarity
above the whisper's own baseline, 15 real retellings scored +0.039 to +0.119
and 14 lines of other tavern talk -0.008 to +0.048; at 0.035, 2 of the 14
reach the card and no retelling is lost.

A conversation costs suspicion like whispering does. Three messages to one
villager left them at about 0.73, past the 0.7 at which they turn you away,
which is why `TALK_TURNS` is 3.

The mood is a mean over the whole village, so the same whispers count for more
in a small one. In a village of 100, a player who whispered to someone
every few seconds was run out on the fourth game day, while one who picked a
dozen villagers whose worries matched stayed at quiet or murmurs. In a village
of 30, eight well-aimed whispers reached watched.

## Tests

```bash
pip install -r requirements-dev.txt
pytest examples/rumor_mill
RUMOR_MILL_CONTEX_URL=http://localhost:8001/mcp pytest examples/rumor_mill
```

The second run adds the tests that need a live Contex.
