# Rumor Mill

A gossip game you play in the terminal or a browser. A village of 100 villagers runs on Contex. You are the
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
| `contex_create_subscription` | one per villager, on their mind project, with their 3 to 5 needs |
| `max_age_seconds` | forgetting: memories older than `memory_days` drop out of the subscription |
| `subscriptions/listen` | one stream per villager; a push is the only thing that wakes them |
| reading the subscription resource | the villager's memories each turn, and lease renewal for quiet villagers |
| `contex_query` | the judge reads minds and group records to score goals |
| semantic matching | a whisper wakes a villager only if it is within `threshold` of one of their needs, and a rumor still reaches people after it is retold in other words |
| scale | 100 projects, 100 subscriptions, and 100 listen streams at once |

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

The first run asks Haiku for the village in batches of 10 and writes it to
`rumor_mill_seed.json`; later runs reuse that file. Delete it to get a new
village; if its size differs from `RUMOR_MILL_POPULATION` the game exits and
says so. Each run starts with empty minds unless you set `RUMOR_MILL_SESSION`.

On the machine this was tuned on, a 100-villager game took 5.5 seconds to
start from the seed file, and the game process used 125 MB of memory and
6 seconds of CPU over a 400-second session. The Contex server held 100 listen
connections and used 4 to 5 CPU cores for a few seconds at each 30-second
sweep; the override uses 60 seconds.

## Commands

In the terminal:

```
<name>: <text>            whisper to a villager, e.g. Marta: the miller waters his flour
whisper <name>: <text>    the same
villagers                 everyone in town, with occupation and place
inspect <name>            personality, grudge, and relationships
goals                     your goals and how close each one is
usage                     tokens used so far and their estimated cost
help                      these commands
quit
```

A name can be part of a full name if it is unambiguous. A whisper that
touches nothing the villager cares about does nothing. A villager only checks
what they heard once their cooldown is over, so 20 seconds after that the feed
says they shrug it off. `inspect` shows enough to guess what someone cares
about, but not their needs.

A shrug is a hint. The villager brushes you off in their own voice and grumbles
about something they care about, a different concern each time until they run
out and start over, so a miss tells you where to aim next. If the line can't be
written, the feed falls back to the plain shrug.

The feed shows each hop of a rumor you started, who told whom and in what
words, and a line when a goal's percent changes. Goals are checked once a game
day and are met after they hold at 100% for three checks in a row.

## Suspicion

Each villager keeps their own suspicion of the newcomer. It goes up when:

- you whisper to them: every whisper tells them it came from you;
- they hear the newcomer named or hinted at in a rumor, or hear a rumor that is
  hurtful to someone they know;
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

The page has the feed, a whisper box with a villager picker, your goals, and
the villagers grouped by where they are. Click a villager to see their card and
whisper to them. Each hop of your rumor is indented once per retelling, says
whom the speaker heard it from, and highlights the words that differ from what
they heard. When most of it was reworded, it underlines the words that
survived instead. Hover or click a hop to light up its path back to your whisper;
Escape clears it. Reloading the page
brings back the last 500 feed events.

The server listens on 127.0.0.1 only. It answers only requests addressed to
`127.0.0.1` or `localhost` on its own port, and refuses whispers and WebSocket
connections from other origins.

## What it costs

The game keeps a running count of the tokens it sends to Haiku and Jev and
multiplies them by per-million-token prices. `usage` prints it in the
terminal, the browser page shows it at the bottom of the sidebar, and both
print it on quit. The figures are estimates: the prices are list prices you can
change, and the providers' bills are the real numbers.

Where the tokens go:

- Setup: Haiku writes the village once per seed file, and writes each newcomer
  who arrives after someone leaves.
- Each time a villager wakes: one Jev request to decide what they do, plus a
  short Haiku call (at most 80 tokens out) when they speak, and a slightly
  longer one when they found a group.
- Each shrug: one short Haiku call (at most 80 tokens out) for the brush-off,
  and a second one if the first repeated the villager's concern word for word.
- The judge: one Jev request per villager whose memories match a belief goal,
  once a game day. Verdicts are cached until the villager's matching memories
  change.

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
| `RUMOR_MILL_POPULATION` | `100` | village size; newcomers arrive to keep it there |
| `RUMOR_MILL_DAY_SECONDS` | `120` | length of a game day |
| `RUMOR_MILL_MEMORY_DAYS` | `2` | how many days a villager remembers what they heard |
| `RUMOR_MILL_THRESHOLD` | `0.815` | cosine similarity to a need that wakes a villager |
| `RUMOR_MILL_TOP_K` | `5` | memories kept per need |
| `RUMOR_MILL_COOLDOWN_SECONDS` | `30` | least time between a villager's turns |
| `RUMOR_MILL_SHRUG_SECONDS` | `20` | extra seconds after the cooldown before a whisper that woke no one is reported |
| `RUMOR_MILL_ARRIVAL_DELAY_SECONDS` | `60` | wait after a departure before newcomers arrive |
| `RUMOR_MILL_JUDGE_EVERY_DAYS` | `1` | days between goal checks |
| `RUMOR_MILL_JUDGE_THRESHOLD` | `0.75` | similarity a memory needs to a goal's evidence to be judged |
| `RUMOR_MILL_JUDGE_CONCURRENCY` | `10` | goal-check calls in flight |
| `RUMOR_MILL_TURN_CONCURRENCY` | `20` | villager turns in flight |
| `RUMOR_MILL_RENEW_SECONDS` | `1200` | read quiet villagers' subscriptions this often so they don't expire |
| `RUMOR_MILL_HAIKU_MODEL` | `claude-haiku-4-5-20251001` | model for speech and village generation |
| `RUMOR_MILL_SEED_PATH` | `rumor_mill_seed.json` | where the village is cached |
| `RUMOR_MILL_WEB_PORT` | `8765` | port for `--web` |

`THRESHOLD` matters most. The embedding model scores most pairs of sentences
between 0.74 and 0.86. In two generated 25-villager towns, whispers written to
touch a villager's needs scored a median of 0.834 against their closest need and
unrelated ones 0.794. At 0.815, 21 of 25 on-topic whispers landed and unrelated
ones such as "the tide tables were misprinted" landed on nobody. Lower it and
villagers react to more of what they hear; raise it and the village goes quiet.

## Tests

```bash
pip install -r requirements-dev.txt
pytest examples/rumor_mill
RUMOR_MILL_CONTEX_URL=http://localhost:8001/mcp pytest examples/rumor_mill
```

The second run adds the tests that need a live Contex.
