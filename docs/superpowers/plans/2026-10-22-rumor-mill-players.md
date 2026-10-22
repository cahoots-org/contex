# Rumor Mill Players Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Many players share one Rumor Mill world in one process. Each has their own whispers, cards, notes, suspicion, personal goals and trophies, and everyone shares one world goal. Solo terminal and `--web` play run as a one-player world on the same code.

**Architecture:** A player id (`p-` + random) becomes the root of every whisper's lineage, so every event, card, conversation, suspicion score and overheard line can be routed by it. A thin `Players` registry replaces the engine's single `player_place`. `Player` owns its own game end. `Judge` checks every claim in play once per interval, deals hands and draws the world goal. The web app maps a session cookie to a player and filters the feed per socket.

**Tech Stack:** Python 3.12 asyncio, FastAPI + Starlette TestClient, pytest-asyncio, vanilla JS in `static/index.html`.

**Spec:** `docs/superpowers/specs/2026-10-21-rumor-mill-multiplayer-design.md`. This plan covers only its Order steps 1 and 2. Moderation, persistence and hosting are later plans.

## Global Constraints

- All work stays on branch `feat/rumor-mill-tiles` (PR #477). No new branches or PRs. Every task is one commit that leaves `python -m pytest examples/rumor_mill -q` and `ruff check examples/rumor_mill` green, run from `/Users/robmiller/Projects/contex`.
- There is no separate single-player path. Terminal play has one implicit player, made at start. `--web` makes a player for each browser session.
- Player ids are `"p-" + secrets.token_hex(8)`. A session cookie is the whole identity, and clearing it starts over.
- In the village every player is "a newcomer". Whispers are framed `A newcomer whispered to you`. Every whisper's speaker id is `NEWCOMER = "newcomer"`, so villagers cannot tell newcomers apart.
- Players never see each other. No player id, name, count or presence appears in any page, API response, event or terminal line. A villager's recall (`recall_heard`) keeps leaving out every newcomer's whispers.
- Never leak internal numbers (needs, scores, margins, suspicion values) to players. Goals show only "N of M believe it" or whole percents, as today.
- Village memory spans `memory_moves` (20) moves **per active player**. A player is active if they whispered in the last game day (`day_seconds`, 120 s). With one player, nothing changes.
- Personal goals: 3 per player, dealt from the whole `DECK`. The deal skips claims the village holds now and the current world goal's claim. State goals and `religion` are never the world goal.
- World goal bar: `WORLD_FRACTION = 2 / 3`. A goal is met when the village holds the claim at its bar, whoever spread it.
- When a player's three goals are all met, they get a trophy and three new goals. Suspicion and notes carry over. When the world goal is met, every player who whispered while it was in play gets a world trophy, and a new world goal is drawn. The game is never "won".
- A player who is run out has lost, and nothing more reaches them. After `RUN_OUT_LOCKOUT_SECONDS = 600` real seconds, a reload starts them over as a new newcomer with nothing carried over.
- Code style: imports at the top only, no meta or history comments, concise docstrings, no clever code. Never use the word "Substrate". New modules only for a single clear responsibility (here, only `players.py`).
- UI: set text with `textContent` only. Buttons are native and keyboard reachable, and new feed buttons join the feed's roving focus (`ROVE`). Toggles carry `aria-expanded`. Respect `prefers-reduced-motion`. Browser verification is a separate step the controller assigns.

## Review Focus

1. **Two tabs, one cookie.** Both sockets get that player's events, and neither gets the other player's. (Test in Task 8: `test_two_tabs_of_one_player_both_get_its_events`.)
2. **Stale or forged cookie.** An unknown player id gets a fresh player on `GET /`, a reload message from the API, and a 1008 close on the websocket, never a 500. (Task 8: `test_an_unknown_cookie_asks_for_a_reload`.)
3. **Two players whisper the same villager.** Conversations, threads and refusals stay separate. One player's refusal does not close the other's conversation. (Task 3: `test_a_refusal_ends_only_that_players_conversation`.)
4. **A player is run out mid-play.** Later statuses, brush-offs and hops for their whispers are dropped. Other players still get everything. (Task 4: `test_nothing_reaches_a_player_after_their_game_is_over`.)
5. **The deck runs short.** If the skip list leaves fewer than 3 goals, the deal ignores the skip list. If every world candidate is held, the world draw uses all candidates. (Task 6: `test_deal_ignores_the_skip_list_when_it_leaves_too_few`, `test_world_goal_falls_back_when_every_claim_is_held`.)

---

### Task 1: Player ids on whispers, lineage and the feed

Pure threading. Behaviour stays identical for one player.

**Files:**
- Create: `examples/rumor_mill/players.py`
- Modify: `examples/rumor_mill/town.py` (`PLAYER_ID` → `NEWCOMER`), `lineage.py`, `player.py`, `feed.py`, `rumors.py`, `overhear.py`, `scenes.py`, `__main__.py`
- Test: `tests/helpers.py`, `tests/test_lineage.py`, `tests/test_feed.py`, `tests/test_player.py`, plus a mechanical `player=True` → `player=PID` update in `test_effects.py`, `test_engine.py`, `test_feed.py`, `test_lineage.py`

**Interfaces:**
- Produces:
  - `town.NEWCOMER = "newcomer"`, the speaker id of every whisper. `PLAYER_ID` is removed. The dead `vid == PLAYER_ID` check in `Scenes.addressed` goes, since players are never in `town.present`.
  - `players.new_player_id() -> str`, which returns `"p-" + secrets.token_hex(8)`.
  - `Lineage.record(uid: str, parent: str | None, player: str | None = None) -> None`. `player` is the id of the player whose whisper `uid` is.
  - `Lineage.player_of(uid: str | None) -> str | None`: the player whose whisper `uid`'s lineage roots in. `is_player_rooted` becomes `player_of(uid) is not None`. `is_whisper` keeps its meaning.
  - `Player(engine, town, lineage, ids, feed, config, writer=None, game=None, *, player_id: str)`, with a `.id` attribute. Whispers record `lineage.record(uid, None, player=self.id)`.
  - `Event.player: str | None = None` is a new last field: who sees the event, with `None` meaning everyone in the village. `Event.to_json()` never includes it.
  - `Feed.emit(kind, line="", /, player: str | None = None, **data)` and `Feed.say(line, player: str | None = None)`.
  - `Feed.sees(player: str, event: Event) -> bool`, `Feed.replay(player: str) -> list[Event]` (the events in `recent` that player sees), and `Feed.view(player: str, sink: Sink) -> Sink` (a sink that passes only what that player sees).
  - Feed methods tag the player from lineage: `on_whisper_status` (`player_of(whisper_id)`), `on_reply` (`player_of(conversation)`), `on_drift` (`player_of(whisper_id)`), and `on_delivery`'s whisper and hop events (`player_of(u.id)`). `on_refusal(villager_id, name, line=None, player=None)` and `on_place(place, player=None)` take it from `Player`. Everything else stays `None` until its task.
  - `tests/helpers.py`: `PID = "p-1"`. `make_player` passes `player_id=PID`.

- [ ] **Step 1: Write the failing tests**

```python
# test_lineage.py
def test_player_of_follows_the_root_to_its_player():
    ln = Lineage()
    ln.record("w-1", None, player="p-a")
    ln.record("u-1", "w-1")
    ln.record("w-2", None, player="p-b")
    ln.record("u-9", None)
    assert (ln.player_of("u-1"), ln.player_of("w-2")) == ("p-a", "p-b")
    assert ln.player_of("u-9") is None and ln.player_of(None) is None
    assert ln.is_whisper("w-2") and not ln.is_whisper("u-1")

# test_feed.py
def test_events_go_to_the_player_whose_whisper_they_follow():
    town, lineage = Town(), Lineage()
    town.add(Villager("v1", "Bert", "baker", "calm", "none", [], {}))
    lineage.record("w-1", None, player="p-a")
    lineage.record("w-2", None, player="p-b")
    lineage.record("u-2", "w-2")
    feed = Feed(lineage, town, out=None)
    feed.on_whisper_status("w-1", "v1", "Bert", "checking")
    feed.on_delivery(Utterance("u-2", "v1", "Bert", "tavern", "news", "w-2"), ["v1"])
    feed.on_time(1, "afternoon")
    assert [e.kind for e in feed.replay("p-a")] == ["whisper_status", "time"]
    assert [e.kind for e in feed.replay("p-b")] == ["hop", "time"]
    assert all("player" not in e.to_json() for e in feed.recent)

def test_a_view_passes_only_what_its_player_sees():
    # feed.sinks.append(feed.view("p-a", seen.append)); emit one event per player and one village event
    # assert seen holds p-a's event and the village event only

# test_player.py
async def test_a_whisper_is_recorded_under_its_player():
    player, _, _, engine, lineage = make_player("Marta")
    assert await player.whisper_to("v1", "the well is cursed") is None
    assert lineage.player_of("w-1") == PID
    assert engine.delivered[0][0].speaker == NEWCOMER
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest examples/rumor_mill/tests/test_lineage.py examples/rumor_mill/tests/test_feed.py examples/rumor_mill/tests/test_player.py -q`
Expected: FAIL. `player_of`, `replay` and `NEWCOMER` are not defined yet.

- [ ] **Step 3: Implement the interfaces above**

Lineage keeps `_player: dict[str, str]` (whisper uid → player id). `__main__.play` makes the player with `player_id=new_player_id()`. In terminal mode it builds `Feed(..., out=None)` and appends `feed.view(player.id, terminal(print))`. Change every remaining `player=True` in the tests to `player=PID`.

- [ ] **Step 4: Run the suite and lint**

Run: `python -m pytest examples/rumor_mill -q && ruff check examples/rumor_mill`
Expected: all pass, no lint errors.

- [ ] **Step 5: Commit**

```bash
git add examples/rumor_mill
git commit -m "Root every whisper in a player id and tag feed events with who sees them"
```

---

### Task 2: A players registry for places, overhearing and scenes

**Files:**
- Modify: `examples/rumor_mill/players.py`, `engine.py`, `feed.py`, `scenes.py`, `__main__.py`
- Test: `tests/helpers.py`, `tests/test_engine.py` (replace every `engine.player_place = lambda: ...`)

**Interfaces:**
- Consumes: `Lineage.player_of` and `Feed.replay` from Task 1.
- Produces:
  - `class Players` in `players.py`, a registry of the players in one world. It has `add(player) -> None`, `get(player_id: str) -> Player | None`, `__iter__`, and `at(place: str) -> list[Player]` (players at `place` whose game is not over).
  - `Player.over() -> bool`, the public form of today's `_over`.
  - `Engine.players: Players` defaults to `Players()`. `__main__` assigns it after construction. `Engine.player_place` is removed.
  - `Engine.deliver` overhears per player. For a village-talk scene line, each player in `players.at(scene.place)` gets `feed.on_overheard(u, "scene", scene.id, player=p.id)`. Otherwise, each `p` in `players.at(u.place)` gets `overhear.keep(u, p.place, hop)`, where `hop = on_topic and lineage.player_of(u.id) == p.id`. Another player's rumor is ordinary village talk to everyone else.
  - `_enter_scene` starts village-talk scenes only `if self.players.at(place)`.
  - `Scene.seen_by: set[str]` (default empty). `Engine._show_scene(scene) -> None` replaces the three direct `feed.on_scene` calls. A rumor scene goes to `player_of(scene.rumor)`. A village-talk scene goes to every player at `scene.place` and everyone already in `seen_by`, and adds them to `seen_by`.
  - `Feed.on_overheard(utterance, reason, scene=None, player=None)` and `Feed.on_scene(scene, player=None)`.
  - `tests/helpers.py`: `stub_player(pid=PID, place="square") -> SimpleNamespace`, with `id`, `place` and `over=lambda: False`.

- [ ] **Step 1: Write the failing tests** (in `test_engine.py`, using `overhearing_engine`, `talk`, `scene_events` and `overheard`)

```python
async def test_each_player_overhears_only_talk_at_their_own_place(...):
    # players p-a at tavern, p-b at well; deliver a SPEAK line at the tavern with an overhear that always keeps
    assert [e.player for e in overheard(feed)] == ["p-a"]

async def test_a_rumor_hop_is_village_talk_to_another_player(...):
    # w-1 rooted in p-a, hop u-1 at the tavern; p-a and p-b both at the tavern; record keep(u, place, hop) calls
    assert sorted(hops) == [False, True]

async def test_a_village_talk_scene_shows_only_to_players_at_its_place(talkers):
    # p-a at the tavern where the scene starts, p-b at the well
    assert scene_events(feed) and all(e.player == "p-a" for e in scene_events(feed))

async def test_a_scene_end_reaches_a_player_who_walked_away(talkers):
    # p-a sees the scene, then moves to the well; the ended event still has player "p-a"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest examples/rumor_mill/tests/test_engine.py -q -k "overhears_only or village_talk or walked_away or rumor_hop"`
Expected: FAIL. `Engine.players` does not exist yet.

- [ ] **Step 3: Implement the interfaces above**, then wire `players = Players(); players.add(player); engine.players = players` in `__main__.play`.

- [ ] **Step 4: Run the suite and lint**

Run: `python -m pytest examples/rumor_mill -q && ruff check examples/rumor_mill`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git commit -am "Route overhearing and village-talk scenes to the players standing there"
```

---

### Task 3: Per-player conversations, rumor cards and notes

**Files:**
- Modify: `examples/rumor_mill/engine.py`, `player.py`, `rumors.py`, `notes.py`, `web.py`
- Test: `tests/helpers.py` (`FakeEngine.end_conversation(vid, player=None)`), `tests/test_engine.py`, `tests/test_player.py`, `tests/test_rumors.py`, `tests/test_notes.py`

**Interfaces:**
- Consumes: `Lineage.player_of`, `Feed.view` and `Feed.replay` from Task 1.
- Produces:
  - `Engine.conversations: dict[tuple[str, str], dict]`, keyed by `(villager id, player id)`.
  - `Engine.end_conversation(vid: str, player: str | None = None) -> None`. With `None` it closes the villager's conversation with every player (used by `remove_villager`).
  - In `_turn`, `answerable` keeps each whisper whose own player's conversation with this villager is under `talk_turns`. `_answered` and `_report` key by `player_of(wid)`.
  - `Player.whisper_to` reads and ends `(target.id, self.id)` only.
  - `Card.player: str`, `Rumors.add(whisper_uid, text, published_at, target_id, target_name, conversation=None, *, player: str)`, and `Rumors.cards(player: str) -> list[dict]`.
  - `Notes(feed: Feed, player: str)` replays `feed.replay(player)` and subscribes through `feed.view(player, self)`.
  - `web.tallies(rumors: Rumors, player: str) -> list[dict]`. `web.py` still serves the one player passed to `create_app`, as `player.id`.

- [ ] **Step 1: Write the failing tests**

```python
# test_engine.py
async def test_one_villager_holds_a_conversation_with_each_player():
    engine, _, _ = await setup("v1")
    engine.conversations[("v1", "p-a")] = {"id": "w-1", "answered": ["w-1"]}
    engine.conversations[("v1", "p-b")] = {"id": "w-2", "answered": ["w-2"]}
    engine.end_conversation("v1", "p-b")
    assert list(engine.conversations) == [("v1", "p-a")]
    engine.end_conversation("v1")
    assert engine.conversations == {}
    await engine.stop()

async def test_answerable_leaves_out_a_player_whose_conversation_is_full(turns):
    # w-1 (p-a) with p-a's conversation at talk_turns, w-2 (p-b) fresh; both new this turn
    assert [m.data["id"] for m in turns.views[0].answerable] == ["w-2"]

# test_player.py
async def test_a_refusal_ends_only_that_players_conversation():
    # make_player at refuse_threshold for v1; conversations[("v1", PID)] and [("v1", "p-b")] open
    assert ("v1", "p-b") in engine.conversations and ("v1", PID) not in engine.conversations

# test_rumors.py
async def test_cards_list_only_the_players_own_rumors():
    # add w-1 for p-a and w-2 for p-b
    assert [c["uid"] for c in rumors.cards("p-a")] == ["w-1"]

# test_notes.py
def test_notes_hold_only_the_players_own_villagers():
    # w-1 rooted in p-a to v1 brushed_off, w-2 rooted in p-b to v2 took_the_bait
    assert list(Notes(feed, "p-a").all()) == ["v1"]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest examples/rumor_mill/tests/test_engine.py examples/rumor_mill/tests/test_player.py examples/rumor_mill/tests/test_rumors.py examples/rumor_mill/tests/test_notes.py -q`
Expected: FAIL on the new tests.

- [ ] **Step 3: Implement the interfaces above**, and update the existing `conversations["v1"]` uses in the tests to `conversations[("v1", PID)]`.

- [ ] **Step 4: Run the suite and lint**

Run: `python -m pytest examples/rumor_mill -q && ruff check examples/rumor_mill`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git commit -am "Keep conversations, rumor cards and notes per player"
```

---

### Task 4: Each player owns their game end; memory spans active players

**Files:**
- Modify: `examples/rumor_mill/game.py`, `player.py`, `feed.py`, `engine.py`, `judge.py`, `cli.py`, `web.py`, `__main__.py`
- Test: `tests/test_game.py`, `tests/test_engine.py`, `tests/test_judge.py`, `tests/test_cli.py`, `tests/test_web.py`, `tests/test_player.py`

**Interfaces:**
- Consumes: `Players` and `Player.over()` from Task 2, and `Feed.view` from Task 1.
- Produces:
  - `Game(memory_moves: int = 20, day_seconds: float = 120.0)` is now only the clock and the village's memory. It loses `feed`, `state`, `summary`, `finished`, `win`, `lose` and `_finish`. `Summary` stays in `game.py`.
  - `Game.moved(player: str) -> None`. `Game.memory_since() -> str | None` needs `memory_moves * max(1, active)` moves, where `active` is the number of distinct players with a move in the last `day_seconds`. It returns the timestamp of the move that many back, or `None` if there are fewer. Moves live in a `deque(maxlen=memory_moves * MEMORY_PLAYERS)` with `MEMORY_PLAYERS = 20` and a `# ponytail:` note naming the cap.
  - `Player.state: str` (`"playing"`), `Player.summary: Summary | None`, `Player.finished: asyncio.Event`, `Player.ended_at: float | None`, and `Player.end(summary: Summary) -> None`. `end` takes effect once: it sets these fields, calls `feed.on_game_over(summary, player=self.id)`, then `feed.close(self.id)`. `Player.over()` is `self.finished.is_set()`.
  - `Feed.close(player: str) -> None`. After it, `emit` drops events addressed to that player, and `sees(player, e)` is false for any event newer than the close. `Feed.closed` (bool) is removed.
  - `Feed.on_heat(label, player=None)` and `Feed.on_game_over(summary, player=None)`.
  - The engine no longer reads `game.finished`. Its loops run until `stop()`. `Engine.summary_source: Callable[[Player], list[tuple[str, int]]]`. The run-out loop still uses one town-wide level (until Task 5), and on run-out it says `MEETING` to each player in `self.players` and calls `p.end(Summary("lost", self.summary_source(p), self.suspicion.reasons()))`.
  - `Judge.run(goals: list[Goal], player: Player) -> None` stops once `player.over()`. When every goal has held, it calls `player.end(Summary("won", self.percents(goals), []))`. This "won" outcome disappears in Task 6.
  - `cli.until_over(session, player: Player)` waits on `player.finished`. `web.game_json(player: Player) -> dict`.

- [ ] **Step 1: Write the failing tests** (in `test_game.py`. The old tests `test_first_finish_wins_the_race`, `test_nothing_prints_after_the_game_is_over` and `test_judge_stops_once_the_game_is_over`, and `test_engine.py`'s `test_no_turns_start_after_the_game_is_over` and `test_a_turn_still_deciding_when_the_game_ends_does_nothing`, plus `test_no_one_walks_once_the_game_is_over`, are replaced or deleted.)

```python
def test_a_players_game_ends_once():
    player, *_ = make_player("Marta")
    lost = Summary("lost", GOALS, ["V1 heard it from you directly."])
    player.end(lost)
    player.end(Summary("won", GOALS, []))
    assert (player.state, player.summary, player.over()) == ("lost", lost, True)
    assert [e.data["outcome"] for e in player.feed.replay(PID) if e.kind == "gameover"] == ["lost"]

def test_nothing_reaches_a_player_after_their_game_is_over():
    # feed with w-1 rooted in "p-a" and w-2 rooted in "p-b"; seen_a via feed.view("p-a", ...)
    # end p-a's game (feed.on_game_over(..., player="p-a"); feed.close("p-a"))
    assert feed.say("late", player="p-a") is None
    feed.on_whisper_status("w-1", "v1", "Bert", "kept")
    feed.on_time(2, "morning")
    feed.on_whisper_status("w-2", "v1", "Bert", "kept")
    assert seen_a[-1].kind == "gameover" and feed.replay("p-a")[-1].kind == "gameover"
    assert [e.kind for e in feed.replay("p-b")][-2:] == ["time", "whisper_status"]

def test_memory_spans_the_moves_of_every_active_player():
    game = Game(memory_moves=2)
    game.moved("p-a"); game.moved("p-a")
    first = game.memory_since()
    assert first is not None
    game.moved("p-b")
    assert game.memory_since() is None
    game.moved("p-b")
    assert game.memory_since() == first

async def test_an_idle_player_stops_widening_memory():
    game = Game(memory_moves=1, day_seconds=0.01)
    game.moved("p-a"); game.moved("p-b")
    await asyncio.sleep(0.02)
    game.moved("p-a")
    assert game.memory_since() is not None
```

Rewrite `test_run_out_after_consecutive_checks_at_the_level` and its neighbours. `start()` adds a real `Player(engine, town, lineage, Ids(), feed, config, player_id=PID)` to `engine.players` and waits on `player.finished`. The `gameover` assertions read `feed.replay(PID)`.

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest examples/rumor_mill/tests/test_game.py -q`
Expected: FAIL. `Player.end` and `Game.moved(player)` do not exist yet.

- [ ] **Step 3: Implement the interfaces above.** `Player.whisper_to` calls `self.game.moved(self.id)`. `__main__` passes `summary_source=lambda p: judge.percents(goals)` and `judge.run(goals, player)`, runs `until_over(repl(...), player)`, and builds `Game(config.memory_moves, config.day_seconds)`.

- [ ] **Step 4: Run the suite and lint**

Run: `python -m pytest examples/rumor_mill -q && ruff check examples/rumor_mill`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git commit -am "Give each player their own game end and widen memory by active players"
```

---

### Task 5: A suspicion meter per player

**Files:**
- Modify: `examples/rumor_mill/suspicion.py`, `engine.py`, `effects.py`, `models.py`, `feed.py`, `player.py`, `rumors.py`, `writer.py`, `calibration.py`, `cli.py`, `web.py`
- Test: `tests/test_suspicion.py`, `tests/test_engine.py`, `tests/test_effects.py` (`RecordingHub.suspect`), `tests/test_feed.py`, `tests/test_game.py`, `tests/test_player.py`, `tests/test_writer.py`, `tests/test_calibration.py`, plus a mechanical `bump`/`score` signature update in `test_cli.py` and `test_web.py`

**Interfaces:**
- Consumes: `Players` (Task 2), `Player.end` (Task 4), `Lineage.player_of` and `Facts`.
- Produces:
  - `Suspicion` keyed by `(villager id, player id)`:
    - `bump(vid, player, amount, reason, uid=None) -> float`
    - `rate(vid, player, strength, reason, uid=None) -> float`
    - `score(vid, player) -> float`
    - `decay(days)` covers every player.
    - `level(player) -> float`, `label(player) -> str`, `over(threshold, player) -> list[str]`, `top_evidence(player, n=3)`, `reasons(player, n=3)`
    - `most_suspected(vid, among: Collection[str] = ()) -> str | None`: the player this villager suspects most among `among`, or among everyone when `among` is empty. `None` when they suspect no one.
    - `Evidence` gains `player: str` as its second field.
  - `Utterance.about: str | None = None`: the player a warning is about.
  - `Engine.suspect(vid, player, amount, reason, uid=None)`, with the matching `Hub.suspect`.
  - `Engine._about(m: Match) -> str | None`: `lineage.player_of(id)`, or else the recorded fact's `about`.
  - In `_turn`, `front = {self._about(m) for m in new} - {None}` and `suspect = self.suspicion.most_suspected(v.id, front)`. `TurnView.suspicion = score(v.id, suspect)`, or `0.0` when `suspect` is `None`. `decision.suspicion_strength` rates each distinct `_about(m)` over `view.names_newcomer`.
  - `act(..., about: str | None = None)`. A WARN is recorded with `about=about` and calls `hub.suspect(target, about, ...)`.
  - `Feed.on_delivery` sends a warning to `u.about`.
  - `Player.whisper_to` refuses when `score(target.id, self.id) >= refuse_threshold` and bumps under `self.id`. Its refusal event goes to `self.id`.
  - The run-out loop works per playing player. It keeps `label` and `held` per player id. `feed.on_heat(label, player=p.id)` fires on change. On run-out, `feed.say(MEETING, player=p.id)` and `p.end(Summary("lost", self.summary_source(p), self.suspicion.reasons(p.id)))`.
  - Framing: whispers and conversation lines use speaker name `"A newcomer"` (`player.py`, `rumors.add`, `calibration._calibration_data`). `WHISPER_FRAMING = re.compile(r"^A newcomer whispered to you at the [^:]*: ")`. `Writer.warn` says "a newcomer in town". The calibration `FINGERPRINT` changes, so cached baselines are re-measured once.
  - `cli.repl` prints `HEAT_LINES[suspicion.label(player.id)]`. `/api/state` uses `suspicion.label(player.id)`.

- [ ] **Step 1: Write the failing tests**

```python
# test_suspicion.py
def test_suspicion_is_kept_per_player():
    s = Suspicion(Config(), town_with_v1)
    s.bump("v1", "p-a", 0.5, "whispered to them directly")
    assert (s.score("v1", "p-a"), s.score("v1", "p-b")) == (0.5, 0.0)
    assert s.level("p-b") == 0.0 and s.label("p-a") != s.label("p-b")
    assert [e.player for e in s.top_evidence("p-a")] == ["p-a"] and s.reasons("p-b") == []

def test_most_suspected_prefers_the_players_in_front():
    s.bump("v1", "p-a", 0.2, "r"); s.bump("v1", "p-b", 0.5, "r")
    assert s.most_suspected("v1", {"p-a"}) == "p-a"
    assert s.most_suspected("v1") == "p-b"
    assert s.most_suspected("v2") is None

# test_engine.py (Rater)
async def test_a_named_line_raises_suspicion_of_the_player_it_roots_in(...):
    assert engine.suspicion.score("v1", "p-a") > 0 and engine.suspicion.score("v1", "p-b") == 0.0

async def test_one_turn_naming_two_players_raises_each(...):
    # w-1 (p-a) and w-2 (p-b) both new in one turn; strength 1.0
    assert engine.suspicion.score("v1", "p-a") == engine.suspicion.score("v1", "p-b") == engine.config.suspicion_hear_max

async def test_the_turn_weighs_suspicion_of_the_player_in_front(turns):
    # v1 suspects p-b 0.6 and p-a 0.1; only p-a's whisper is new
    assert turns.views[0].suspicion == pytest.approx(0.1)

# test_effects.py
async def test_a_warning_is_about_the_player_in_front():
    # act(Decision(WARN, ..., target="v2"), ..., about="p-a")
    assert hub.suspects[0][:2] == ("v2", "p-a") and hub.delivered[0][0].about == "p-a"

# test_feed.py
def test_a_warning_reaches_only_the_player_it_is_about():
    # Utterance(..., verb="warned", about="p-a") delivered to v2
    assert [e.kind for e in feed.replay("p-a")] == ["warning"] and feed.replay("p-b") == []

# test_game.py
async def test_run_out_ends_only_that_players_game():
    # players p-a and p-b in engine.players; v1 suspects p-a at 0.9
    assert pa.state == "lost" and pb.state == "playing"
    assert MEETING in [e.line for e in feed.replay("p-a")] and MEETING not in [e.line for e in feed.replay("p-b")]

# test_player.py
async def test_a_villager_turns_away_only_the_player_they_suspect():
    # second Player sharing make_player's engine with player_id="p-b"; v1 suspects PID at refuse_threshold
    assert isinstance(await player.whisper_to("v1", "hi"), Refusal)
    assert await other.whisper_to("v1", "hi") is None

# test_writer.py
def test_whisper_framing_is_a_newcomer():
    assert WHISPER_FRAMING.sub("", "A newcomer whispered to you at the well: hi") == "hi"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest examples/rumor_mill/tests/test_suspicion.py examples/rumor_mill/tests/test_engine.py examples/rumor_mill/tests/test_effects.py -q`
Expected: FAIL on the new signatures.

- [ ] **Step 3: Implement the interfaces above.** In every test that builds a whisper string, change `"The newcomer whispered…"` to `"A newcomer whispered…"`, and fix `test_calibration`'s expected text.

- [ ] **Step 4: Run the suite and lint**

Run: `python -m pytest examples/rumor_mill -q && ruff check examples/rumor_mill`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git commit -am "Keep a suspicion meter per player and run out only the suspected newcomer"
```

---

### Task 6: Personal goals per player and a shared world goal

**Files:**
- Modify: `examples/rumor_mill/goals.py`, `judge.py`, `player.py`, `feed.py`, `overhear.py`, `engine.py`, `cli.py`, `web.py`, `__main__.py`
- Test: `tests/test_goals.py`, `tests/test_judge.py` (`Lines` gains `on_met(goal, player=None)`, `on_progress(..., player=None)`, `on_deal`, `on_world`), `tests/test_overhear.py`, `tests/test_cli.py`, `tests/test_game.py`

**Interfaces:**
- Consumes: `Players` and `Player.over()` (Tasks 2 and 4).
- Produces:
  - `goals.WORLD_FRACTION = 2 / 3`.
  - `goals.claim(goal: Goal) -> str | None`: a belief goal's text after `" believes "`, or `None` for anything else.
  - `goals.world_goal(goal: Goal) -> Goal`: `replace(goal, id=f"world-{goal.id}", text=f"Two thirds of the village believes {claim(goal)}", target_fraction=WORLD_FRACTION)`.
  - `goals.WORLD_CANDIDATES`: the `DECK` belief goals with `creed_question is None`.
  - `goals.deal(rng, n=3, skip: Collection[str] = ()) -> list[Goal]` samples from `DECK` minus `skip`, or from the whole `DECK` when fewer than `n` remain.
  - `Player.goals: list[Goal]` and `Player.met: set[str]`.
  - `Judge(contex, jev, town, config, feed, game=None, baselines=None, rng: random.Random | None = None)`.
    - `self.world: Goal` is drawn at construction, and `self.world_since: float = time.time()`.
    - `Judge.held() -> set[str]` returns the goal ids whose latest progress is ≥ 1.0.
    - `Judge.deal(player) -> None` sets `player.goals = deal(self.rng, skip=self.held() | {self.world.id.removeprefix("world-")})`, sets `player.met = set()`, and calls `feed.on_deal(player.goals, player=player.id)`.
    - `Judge._next_world() -> Goal` is `world_goal(rng.choice(...))`, choosing from candidates not in `held()` or, if every one is held, from all candidates.
  - The verdict cache key becomes `(goal.believer_question, vid, keys)`, so a claim shared by a personal goal and the world goal is judged once.
  - `Judge.run(players: Players) -> None` runs until cancelled. Each interval:
    - It checks every goal in play once: the unique goals across `p.goals` for players not over, plus `self.world`.
    - When a goal's whole percent changes, it calls `feed.on_progress(goal, progress, counts, player=p.id)` for each player holding it, or with `player=None` for the world goal.
    - A goal that holds for `hold_checks` checks is met. Each holder that has not met it gets `p.met.add(goal.id)` and `feed.on_met(goal, player=p.id)`. A player whose goals are now all met is dealt again.
    - When the world goal is met, it calls `feed.on_met(goal)`, then sets `self.world = self._next_world()`, resets `world_since` and calls `feed.on_world(self.world)`.
    - `Summary("won", ...)` is gone.
  - Feed changes:
    - `on_progress(goal, fraction, counts=None, player=None)` and `on_met(goal, player=None)` carry `world=player is None`. The met line for the world is `f'The village now holds: "{goal.text}"'`.
    - `on_deal(goals: list[Goal], player: str)` emits kind `"deal"`, with data `goals=[{"id", "text"}]` and line `"\n".join(f"Goal: {g.text}" ...)`.
    - `on_world(goal: Goal)` emits kind `"world_goal"` to everyone, with `goal_id`, `text`, and line `f'World goal: "{goal.text}"'`.
  - `Overhear.publish` is called once with `list(DECK)`. `Overhear.keep(utterance, player_place, hop=False, goals: Collection[str] = ())` counts "goal" only for evidence keys of the overhearing player's own goal ids. The engine passes `[g.id for g in p.goals]`.
  - `__main__` loads baselines for every `DECK` evidence, calls `judge.deal(player)` for the terminal player, runs `judge.run(players)`, and passes `summary_source=lambda p: judge.percents(p.goals)`.
  - `cli.repl(player, town, feed, judge=None, usage=None, suspicion=None)`. `goals` prints the player's goals, then `f"World goal: {judge.world.text}: {percent}%"`, then the heat line.
  - `/api/state` `goals` reads `player.goals` and `player.met`, and gains `world_goal: {id, text, percent, met: False, believers, needed}`.

- [ ] **Step 1: Write the failing tests**

```python
# test_goals.py
def test_world_goal_raises_the_bar_on_a_belief_claim():
    g = world_goal(next(g for g in DECK if g.id == "mayors-cat"))
    assert (g.id, g.target_fraction) == ("world-mayors-cat", WORLD_FRACTION)
    assert g.text == "Two thirds of the village believes the mayor's cat is a spy"
    assert all(c.kind == "belief" and c.creed_question is None for c in WORLD_CANDIDATES)

def test_deal_skips_held_claims():
    skip = {g.id for g in DECK[:10]}
    assert not {g.id for g in deal(random.Random(1), skip=skip)} & skip

def test_deal_ignores_the_skip_list_when_it_leaves_too_few():
    hand = deal(random.Random(1), skip={g.id for g in DECK[2:]})
    assert len({g.id for g in hand}) == 3

# test_judge.py
async def test_one_check_per_claim_whoever_holds_it():
    # players p-a and p-b both hold BELIEF; judge.world set to a state goal so it costs no jev call
    assert len(jev.calls) == believers  # one verdict per believing villager, not per player

async def test_a_claim_met_is_met_for_every_player_holding_it():
    assert pa.met == pb.met == {"g"} and [(g, p) for g, p in feed.met] == [("g", "p-a"), ("g", "p-b")]

async def test_all_goals_met_deals_a_new_hand_and_the_game_goes_on():
    assert pa.goals != first_hand and pa.met == set() and not pa.over()

async def test_world_goal_met_draws_a_new_one():
    assert judge.world.id != old.id and feed.worlds == [judge.world.id]

def test_world_goal_falls_back_when_every_claim_is_held():
    # judge.progress = {g.id: 1.0 for g in WORLD_CANDIDATES}
    assert judge._next_world().id.startswith("world-")

def test_deal_skips_the_world_claim_and_held_claims():
    assert judge.world.id.removeprefix("world-") not in {g.id for g in pa.goals}

# test_overhear.py
async def test_goal_reason_uses_only_the_overhearing_players_goals():
    assert await overhear.keep(line("the well is cursed"), "tavern", goals=["well-cursed"]) == "goal"
    assert await overhear.keep(line("the well is cursed"), "tavern", goals=["mayors-cat"]) is None
```

Delete `test_winning_through_the_judge_ends_the_game_once`.

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest examples/rumor_mill/tests/test_goals.py examples/rumor_mill/tests/test_judge.py examples/rumor_mill/tests/test_overhear.py -q`
Expected: FAIL. `world_goal`, `Judge.deal` and `Judge.run(players)` do not exist yet.

- [ ] **Step 3: Implement the interfaces above.**

- [ ] **Step 4: Run the suite and lint**

Run: `python -m pytest examples/rumor_mill -q && ruff check examples/rumor_mill`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git commit -am "Deal personal goals per player and keep one world goal for everyone"
```

---

### Task 7: Trophies

**Files:**
- Modify: `examples/rumor_mill/goals.py`, `judge.py`, `player.py`, `feed.py`, `cli.py`, `web.py`
- Test: `tests/test_judge.py`, `tests/test_feed.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: `Judge.run(players)`, `Judge.deal` and `goals.claim` (Task 6).
- Produces:
  - `goals.Trophy`, a frozen dataclass with fields `kind: Literal["personal", "world"]`, `goals: tuple[str, ...]` (goal texts) and `day: int`, plus `to_json() -> dict`.
  - `goals.trophy_line(trophy: Trophy, claim: str | None) -> str`:
    - personal: `f"The village now swears {claim}, and it all began with a whisper. A trophy for your case."`, or `"The village is not what it was, and it all began with a whisper. A trophy for your case."` when `claim` is `None`.
    - world: `f"The whole village now holds that {claim}. A world trophy for your case."`
  - `Player.trophies: list[Trophy]` and `Player.whispered_at: float | None`, set in `whisper_to` after a successful delivery.
  - In `Judge.run`, when a player's goals are all met, it builds `Trophy("personal", tuple(g.text for g in p.goals), game.day)` and appends it. It then calls `feed.on_trophy(t, line, player=p.id)`, with the claim of the last belief goal met, and deals again. When the world goal is met, every player in `players` with `whispered_at >= self.world_since` gets `Trophy("world", (world.text,), game.day)` and its line. This includes players since run out, whose closed feed drops the event. After that the next world goal is drawn.
  - `Feed.on_trophy(trophy: Trophy, line: str, player: str)` emits kind `"trophy"`, with data `trophy=trophy.to_json()` and `text=line`.
  - `/api/state` gains `trophies: [t.to_json() for t in player.trophies]`. `cli` gains a `trophies` command that prints `f"Day {t.day}: {'; '.join(t.goals)}"` per trophy, or `"No trophies yet."`. `HELP` lists it.

- [ ] **Step 1: Write the failing tests**

```python
# test_judge.py
async def test_meeting_every_goal_puts_a_trophy_in_the_case():
    [t] = pa.trophies
    assert (t.kind, t.goals, t.day) == ("personal", tuple(g.text for g in first_hand), game.day)
    assert feed.trophies[0][1] == "The village now swears cat is a spy, and it all began with a whisper. A trophy for your case."

async def test_world_trophies_go_to_everyone_who_whispered_under_that_world_goal():
    # p-a whispered after world_since, p-b whispered before it, p-c never
    assert [p.id for p in (pa, pb, pc) if p.trophies] == ["p-a"]
    assert pa.trophies[0].kind == "world"

async def test_a_whisper_under_the_next_world_goal_does_not_count_for_the_last():
    # world met twice; p-a whispered only under the first
    assert [t.kind for t in pa.trophies] == ["world"]

# test_feed.py
def test_a_trophy_reaches_only_its_player():
    assert [e.kind for e in feed.replay("p-a")] == ["trophy"] and feed.replay("p-b") == []
    assert feed.replay("p-a")[0].to_json()["trophy"] == {"kind": "personal", "goals": ["a", "b", "c"], "day": 3}

# test_cli.py
async def test_trophies_lists_the_case(monkeypatch):
    assert out[-1] == "Day 3: a; b; c"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest examples/rumor_mill/tests/test_judge.py examples/rumor_mill/tests/test_feed.py examples/rumor_mill/tests/test_cli.py -q`
Expected: FAIL. `Trophy` does not exist yet.

- [ ] **Step 3: Implement the interfaces above.**

- [ ] **Step 4: Run the suite and lint**

Run: `python -m pytest examples/rumor_mill -q && ruff check examples/rumor_mill`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git commit -am "Award personal and world trophies and keep them in each player's case"
```

---

### Task 8: Web sessions: one cookie, one player

**Files:**
- Modify: `examples/rumor_mill/web.py`, `players.py`, `__main__.py`
- Test: `tests/test_web.py`. The `game` fixture now builds `Players` and a `join` closure, makes `client.get("/")` once, and exposes `player`. Existing tests use `game.player.id` where signatures need it.

**Interfaces:**
- Consumes: everything per player from Tasks 1–7.
- Produces:
  - `players.RUN_OUT_LOCKOUT_SECONDS = 600`.
  - `web.COOKIE = "rumor_mill_player"` and `web.RELOAD = "Reload the page to join the village."`.
  - `create_app(*, players: Players, join: Callable[[], Player], town, feed, judge, usage, port, suspicion, game, rumors) -> FastAPI`. The `player` and `goals` parameters are gone.
  - `web.player_for(players: Players, cookie: str | None) -> Player | None`.
  - `GET /` joins a new player when the cookie is missing or unknown, or when its player lost and `time.time() - ended_at >= RUN_OUT_LOCKOUT_SECONDS`. It sets the cookie with `httponly=True, samesite="strict"`.
  - `/api/state`, `/api/villagers/{vid}` and `POST /api/whisper` with no known player return `_error(401, RELOAD)`. The websocket closes with 1008 before accepting.
  - `/api/state` is per player:
    - `boot` is `f"{boot}:{player.id}"`, so a new newcomer resets the page.
    - `place`, `goals`, `world_goal` and `trophies` are the player's own.
    - `events` is `feed.replay(player.id)`, `rumors` is `rumors.cards(player.id)` and `notes` comes from a lazily made `Notes(feed, player.id)`.
    - `heat`/`heat_line` use `suspicion.label(player.id)` and `game` uses `game_json(player)`.
    - No key names or counts other players.
  - Clients become `dict[asyncio.Queue, str]` (queue → player id). `on_event` puts an event only on the queues whose player `feed.sees`. The `villagers` and `usage` watchers still broadcast. The `rumors` tallies watcher compares and sends a snapshot per player id.
  - `__main__` uses `join() -> Player`, which makes `Player(..., player_id=new_player_id())`, then `players.add`, then `judge.deal`. Terminal mode calls `join()` once. Web mode makes no player up front.

- [ ] **Step 1: Write the failing tests**

```python
def test_first_visit_sets_a_player_cookie(game):
    game.client.cookies.clear()
    assert game.client.get("/").cookies[web.COOKIE].startswith("p-")

def test_two_sessions_play_as_two_players(game):
    a = game.client.cookies[web.COOKIE]
    game.client.post("/api/whisper", json={"villager_id": "v1", "text": "the well is cursed"})
    game.client.cookies.clear()
    b = game.client.get("/").cookies[web.COOKIE]
    state = game.client.get("/api/state")
    body = state.json()
    assert a != b and a not in state.text
    assert body["rumors"] == [] and body["notes"] == {} and not [e for e in body["events"] if e["kind"] == "whisper"]
    assert body["world_goal"]["text"] == game.judge.world.text

def test_a_players_socket_gets_none_of_another_players_events(game):
    # socket as b; whisper as a; then feed.say("ping", player=b)
    assert receive(ws)["text"] == "ping"

def test_two_tabs_of_one_player_both_get_its_events(game):
    # two sockets with a's cookie; whisper as a
    assert receive(ws1)["kind"] == receive(ws2)["kind"] == "whisper"

def test_an_unknown_cookie_asks_for_a_reload(game):
    game.client.cookies.set(web.COOKIE, "p-forged")
    response = game.client.get("/api/state")
    assert (response.status_code, response.json()) == (401, {"error": web.RELOAD})
    with pytest.raises(WebSocketDisconnect):
        with game.client.websocket_connect(WS) as ws:
            ws.receive_json()
    assert game.client.get("/").cookies[web.COOKIE] != "p-forged"

def test_a_run_out_player_starts_over_only_after_the_lockout(game):
    game.player.end(LOSS)
    assert web.COOKIE not in game.client.get("/").cookies
    game.player.ended_at -= RUN_OUT_LOCKOUT_SECONDS
    assert game.client.get("/").cookies[web.COOKIE] != game.player.id

def test_state_never_names_or_counts_other_players(game):
    # join a second player via game.join(); state as the first
    text = game.client.get("/api/state").text
    assert other.id not in text and "players" not in game.client.get("/api/state").json()
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest examples/rumor_mill/tests/test_web.py -q`
Expected: FAIL. `create_app` does not take `players`/`join` yet.

- [ ] **Step 3: Implement the interfaces above.**

- [ ] **Step 4: Run the suite and lint, then smoke-run locally**

Run: `python -m pytest examples/rumor_mill -q && ruff check examples/rumor_mill`
Expected: all pass. Two-browser verification is the controller's separate step.

- [ ] **Step 5: Commit**

```bash
git commit -am "Map each browser session to its own player in one shared world"
```

---

### Task 9: The page: world goal, new hands and the trophy case

**Files:**
- Modify: `examples/rumor_mill/static/index.html`
- Test: `tests/test_web.py` (`test_page_has_the_trophy_case_and_world_goal`)

**Interfaces:**
- Consumes: `/api/state` `world_goal` and `trophies`, and the events `deal`, `world_goal`, `trophy`, plus `goal` with `world` (Tasks 6–8).
- Produces:
  - In the strip, a `<p class="world" id="world" aria-live="polite">` showing `World goal: ${text}: ${goalProgress(g)}`. It is updated by `goal` events with `e.world`, and replaced by `world_goal` events. A world `met` goal event adds the feed note `The village now holds: ${text}`.
  - A `deal` event clears `goals`, sets the new hand, calls `renderGoals()`, and adds the note `New goals: a · b · c`. A personal `goal` event (`!e.world`) keeps today's behaviour.
  - Trophy case:
    - In `strip-side`, `<button id="trophies-toggle" aria-expanded="false" aria-controls="trophies">`. Its label is `Trophies (n)`, where `n` is the player's own count.
    - `<section class="trophies" id="trophies" aria-labelledby="trophies-title" hidden>` holds `<h2 id="trophies-title">Trophy case</h2>` and `<ol id="trophy-list">`. Each item reads `Day ${day}: ${goals.join("; ")}`, with world trophies prefixed `World: `. The empty case reads `No trophies yet.`
    - The toggle flips `hidden` and `aria-expanded`. Escape closes the case, in the existing Escape handler.
  - A `trophy` event adds a feed note with `e.text` and a `<button class="trophy-open">Open the trophy case</button>`. `.trophy-open` joins `ROVE`, and pressing it opens the case and focuses its heading (`tabindex="-1"`).
  - Any new animation is disabled under the existing `prefers-reduced-motion` block. All text is set with `textContent` or `el(...)`.
  - The game-over panel's `.restart` copy becomes `Reload the page in 10 minutes to come back as a new newcomer.`. `OUTCOMES` drops `won`.
  - `reset()` clears the trophies and world goal.

- [ ] **Step 1: Write the failing test**

```python
def test_page_has_the_trophy_case_and_world_goal(game):
    html = game.client.get("/").text
    assert 'id="trophies-toggle"' in html and 'aria-controls="trophies"' in html and 'id="world"' in html
    assert "trophy-open" in html and "innerHTML" not in html
    assert "restart the program" not in html
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest examples/rumor_mill/tests/test_web.py -q -k trophy_case`
Expected: FAIL.

- [ ] **Step 3: Implement the markup, styles and handlers above.**

- [ ] **Step 4: Run the suite and lint**

Run: `python -m pytest examples/rumor_mill -q && ruff check examples/rumor_mill`
Expected: all pass. Browser verification is a separate controller step: two sessions, keyboard-only use of the trophy case, and reduced motion.

- [ ] **Step 5: Commit**

```bash
git commit -am "Show the world goal, new hands and the trophy case on the page"
```

---

## Rulings on spec gaps

- **World bar and text.** The bar is `2/3` and the text is rewritten to "Two thirds of the village believes …". World goals come only from belief goals without a creed.
- **Nothing in front.** When nothing new in a turn roots in a player, the villager weighs the player they suspect most. This keeps solo behaviour identical and lets a wary villager still warn.
- **Who gets a world trophy.** "Whispered while the world goal was in play" means `whispered_at >= world_since`. Players since run out are included, but their closed feed never shows it.
- **No winning.** The "won" outcome and `Game.finished` are gone, and the world runs until the process stops. Terminal play ends when its player is run out or quits.
- **Scenes and presence.** Village-talk scene events go only to players at the scene's place, or to players who already saw it. A scene event elsewhere would reveal another player's presence.
- **Replay window.** Replay is one shared 500-event window filtered per player. A per-player window waits for hosting, where many players make it matter.
- **Usage.** The usage line stays (local cost). The hosting plan decides whether it leaks activity.
