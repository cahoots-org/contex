# Rumor Mill Feedback and Actions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Each player can scout before whispering (Walk, Listen, Ask around), check on a rumor after it goes out (who holds it now, who it touched, which goal it serves and whom it convinced), see why it stopped, target villagers from what they have earned, and get a feed line at each turning point. All of it is per player.

**Architecture:** Cards in `Rumors` gain a per-villager frontier, built from hop records, a feed sink for the whisper's own statuses, and engine follow-ups: a Contex-only care check after each one-to-one hop, a kept-it-quiet report after a turn, and scene marks. The web layer folds in goal links and believers (traced by `Judge` to the rumor behind each believer's best memory) and sends them in the per-player `rumors` snapshot. `Notes` keep every hint and what each villager passed on, and a new `hints.py` stores each player's hint lines in their own Contex project so the page can mark villagers a draft might interest. Walk, Listen and Ask around are `Player` methods with terminal commands and JSON routes. Page work for each area is its own task.

**Tech Stack:** Python 3.12 asyncio, FastAPI + Starlette TestClient, pytest-asyncio, Contex MCP (`contex_query`/`contex_publish`), Haiku via `Writer`, vanilla JS in `static/index.html`.

**Spec:** `docs/superpowers/specs/2026-10-22-rumor-mill-feedback-and-actions-design.md`, which carries Part B (B1–B5) of `docs/superpowers/specs/2026-10-21-rumor-mill-scenes-design.md` into the shared world of `docs/superpowers/specs/2026-10-21-rumor-mill-multiplayer-design.md` (steps 1–2 built).

## Global Constraints

- All work goes on branch `feat/rumor-mill-tiles` (PR #477). No new branches or PRs. Each task is one commit that leaves `python -m pytest examples/rumor_mill -q` and `ruff check examples/rumor_mill` green, run from `/Users/robmiller/Projects/contex`. Commit subjects are imperative and the body says why. Each message ends with:
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_01YP7uhbQm8avkDcwdQ9b87E`.
- Everything is per player. Every event this plan adds is emitted with an explicit `player=` id. A per-player emit with no resolvable player is dropped, never sent with `player=None`. No event, snapshot, key or Contex project reveals or changes anything for another player.
- Cost: the B1 care check is Contex queries only (one per need per one-to-one hop listener). Listen makes no model calls. Ask around makes at most one Haiku line, charged to the asker with `usage.charging(player.id)`. Goal links and draft marks are Contex queries only.
- Never send a need, similarity, clearance, baseline, margin, suspicion score or influence weight to the terminal, the page, or a log line above DEBUG. Influence goes out as words. Believers go out as whole counts, as goals already do.
- New Config fields, overridable through `RUMOR_MILL_*`: `listen_seconds: float = 20.0` and `draft_floor: float = 0.82` (draft-to-hint similarity bar, a starting value for tuning).
- Fixed values: the Listen opening is up to `LISTEN_OPENING = 5` lines from the current part of the day (`day_seconds / 4`, 45 real seconds at defaults; a literal game hour is only 7.5 s). Ask around bumps suspicion by `suspicion_whisper_bump / 2` with reason `"asked questions"`. Moment sizes are `(5, 10)`. The hints project is `rumor-mill:{session}:hints:{player}`.
- Minimal: extend existing modules. The only new module is `hints.py`, with new tests `tests/test_hints.py` and `tests/test_facts.py`.
- Code style: imports at the top only, no meta or history comments, concise docstrings, no clever code. Never use the word "Substrate".
- UI: villager, player and model text goes in through `textContent` or text nodes only, never `innerHTML`. New feed buttons join `ROVE`. Panel and card buttons are native `<button>`s. Toggles carry `aria-expanded`. Nothing new animates outside `.fresh`, which `prefers-reduced-motion` already stills. Browser verification of each page task is a separate controller step, not part of the task.

## Review Focus

1. **A per-player line with no player.** A moment for a rumor with no player, a world progress event, or a listen or answer event must never go out with `player=None`, which means everyone. (Task 9: `test_a_moment_reaches_only_its_rumors_player`. Task 7: `test_progress_carries_the_rumor_only_for_its_player`. Tasks 2 and 4 make `player` a required keyword.)
2. **Ask around retelling another newcomer's business.** A recalled line that names a newcomer, or warns about one, may belong to another player's rumor. A line whose fact has been evicted can't be checked. Both must be left out of the answer. (Task 4: `test_lines_about_another_newcomer_are_never_told`.)
3. **A care check that lands after the listener's turn.** A late "busy" or "didn't care" must not undo "kept it quiet" or "passed it on". (Task 6: `test_a_late_check_never_undoes_an_outcome`.)
4. **A listener who never wakes or leaves town.** The rumor must still say why it stopped once a game day has passed, and not stay "heard it" forever. (Task 6: `test_no_stop_line_while_anyone_still_holds_it`.)
5. **Leaving mid-listen.** If the player walks off, whispers someone elsewhere, or loses the game while listening, the listen ends once and sends nothing after the game ends. (Task 2: `test_walking_away_stops_listening`, `test_a_listen_ending_after_game_over_sends_nothing`.)

---

### Task 1: Walk

**Files:**
- Modify: `examples/rumor_mill/player.py`, `cli.py`, `web.py`
- Test: `tests/test_player.py`, `tests/test_cli.py`, `tests/test_web.py`

**Interfaces:**
- Produces in `player.py`:
  - `Player.find(name: str) -> Villager | str`. This is the name resolution moved out of `whisper`, and it returns the same messages. `whisper(name, text)` calls it.
  - `Player._move(place: str) -> None` sets `self.place` and calls `feed.on_place(place, player=self.id)`. `_whisper_to` uses it when the target's place differs.
  - `Player.walk(place: str) -> str | None`. It returns `GAME_OVER` when the game is over. For an unknown place it returns `f"There's no {place} here. Try one of: {', '.join(PLACES)}."`. For the current place it returns `f"You're already at the {place}."`. Otherwise it calls `_move` and returns None. There is no suspicion, no `game.moved`, and no delivery. Any place in `PLACES` is allowed, including the gate.
- Produces in `cli.py`: `parse_command("go <place>") -> Command("go", text=<place lowercased, stripped>)`. A bare `go` is `unknown`. `HELP` second line: `"Other commands: go <place>, villagers, inspect <name>, goals, trophies, usage, help, quit"`.
- Produces in `web.py`:
  - `async _payload(request: Request, bad: str, too_long: str) -> dict | JSONResponse`. It reads the body under `MAX_BODY_BYTES`, returning 413 with `too_long` when over, and 400 with `bad` when the body is not a JSON object. `/api/whisper` uses it and keeps its current messages.
  - `BAD_WALK = 'Send JSON like {"place": "tavern"}.'`
  - `POST /api/walk {"place": str}`. It returns `{"ok": True}`, 400 `{"error": problem}`, or 401 `RELOAD`.

- [ ] **Step 1: Write the failing tests**
  - `test_player.py::test_walk_moves_the_player_and_tells_only_them`: `walk("mill") is None`, `player.place == "mill"`, the last event is `("place", PID)` with `place == "mill"`, `engine.delivered == []`, and every villager's `engine.suspicion.score(v, PID) == 0`.
  - `test_walk_refuses_an_unknown_place_the_same_place_and_a_finished_game`: `"There's no moon here" in walk("moon")`, `walk("square") == "You're already at the square."`, and after `player.end(...)`, `walk("mill") == GAME_OVER`.
  - `test_cli.py::test_parse_command_forms` gains `parse_command("go Tavern") == Command("go", text="tavern")` and `parse_command("go ") == Command("unknown")`.
  - `test_go_walks_and_prints_problems`: the repl is scripted with `"go moon\n"` and `"go mill\n"`. One output line contains `"There's no moon"`, and `player.place == "mill"`.
  - `test_web.py::test_walk_moves_the_player_and_reports_problems`: `post("/api/walk", json={"place": "mill"}).json() == {"ok": True}` and the state's `place == "mill"`. `{"place": "moon"}` gives 400 with `"There's no moon"`. `content=b"nope"` gives 400 `BAD_WALK`. `content=b"x" * 10000` gives 413.
- [ ] **Step 2: Run** `python -m pytest examples/rumor_mill/tests/test_player.py examples/rumor_mill/tests/test_cli.py examples/rumor_mill/tests/test_web.py -q`. Expected: FAIL (`walk` and `/api/walk` do not exist).
- [ ] **Step 3: Implement** the interfaces above.
- [ ] **Step 4: Run** the full suite and ruff. Expected: PASS.
- [ ] **Step 5: Commit** `Let the player walk to any place for free`.

---

### Task 2: Listen

**Files:**
- Create: `tests/test_facts.py`
- Modify: `config.py`, `facts.py`, `overhear.py`, `player.py`, `engine.py`, `feed.py`, `cli.py`, `web.py`, `tests/helpers.py`
- Test: `tests/test_config.py`, `tests/test_overhear.py`, `tests/test_player.py`, `tests/test_engine.py`, `tests/test_feed.py`, `tests/test_cli.py`, `tests/test_web.py`

**Interfaces:**
- Consumes: Task 1 `Player._move`.
- Produces:
  - `Config.listen_seconds: float = 20.0`.
  - `Facts.record(u)` stamps `time.time()` with each utterance (`_by_id: dict[str, tuple[Utterance, float]]`, `get` unchanged). `Facts.recent(place: str, since: float) -> list[Utterance]` returns the lines said at `place` at or after `since`, oldest first.
  - `overhear.audible(u: Utterance) -> bool` is `u.speaker != NEWCOMER and u.action in SPOKEN`. `Overhear.keep(utterance, player_place, hop=False, goals=(), listening=False) -> str | None` keeps its first filter (place, `audible`, not `hop`). After that, when `listening` it returns `"listen"`, with no draw and no goal query.
  - `player.py`: `LISTEN_OPENING = 5`, `LISTEN_LOOKBACK_DAYS = 1 / 24`, `ALREADY_LISTENING = "You're already listening."`. `self._brush_offs` is renamed `self._tasks` (every background task; `close` cancels them).
  - `Player.listen() -> str | None`. It returns `GAME_OVER` or `ALREADY_LISTENING`. Otherwise it sets the listen to end at `time.time() + listen_seconds`. It emits `feed.on_listen(place, opening, until, player=self.id)`, where `opening` is the last `LISTEN_OPENING` of `facts.recent(self.place, now - day_seconds * LISTEN_LOOKBACK_DAYS)` that are `audible` and not `lineage.player_of(u.id) == self.id`. Then it starts a task that calls `_stop_listening()` after `listen_seconds`.
  - `Player.listening() -> bool`. `Player._stop_listening() -> None` ends a running listen once, emitting `feed.on_listen_end(place, player=self.id)`. `_move` calls it before changing place.
  - `Feed.on_listen(place: str, lines: list[Utterance], until: float, *, player: str) -> None` emits kind `"listen"` with `place`, `until`, `ended=False`, and `lines=[{"speaker", "speaker_name", "text"}]`. Terminal: `f"You listen at the {place}."`, then `f'{name}: "{text}"'` per line, or `" It has been quiet here."` appended when there are none.
  - `Feed.on_listen_end(place: str, *, player: str) -> None` emits kind `"listen"`, `ended=True`, terminal line `"You stop listening."`.
  - `Feed.on_overheard` prints `f'{speaker_name}: "{text}"'` when `reason == "listen"`, and nothing otherwise.
  - `Engine.deliver` passes `listening=player.listening()` to `overhear.keep`.
  - `helpers.stub_player(pid=PID, place="square", listening=False)` gains `listening=lambda: listening`.
  - `cli`: `parse_command("listen") == Command("listen")`. `HELP` second line: `"Other commands: go <place>, listen, villagers, inspect <name>, goals, trophies, usage, help, quit"`.
  - `web`: `POST /api/listen` returns `{"ok": True}`, or 400 `{"error": problem}`.

- [ ] **Step 1: Write the failing tests**
  - `test_config.py`: `Config().listen_seconds == 20.0`.
  - `test_facts.py::test_recent_lists_lines_said_at_a_place_since_a_time`: with `facts.time.time` monkeypatched to 100 and then 200, `recent("square", 150)` holds only the line recorded at 200. A tavern line is left out. The order is oldest first.
  - `test_overhear.py::test_listening_hears_every_line_but_not_whispers_hops_or_other_places`: with `Always(0.99)`, a plain `SPEAK` line at the place gives `"listen"`. A `NEWCOMER` line, `hop=True`, a `WARN` line and another place all give None. `contex.queries == []`.
  - `test_player.py::test_listen_opens_with_the_last_five_lines_of_this_part_of_the_day`: record 7 audible square lines in `engine.facts`, one of them older than `day_seconds / 4`, plus a tavern line, a `NEWCOMER` whisper and a `WARN` line. The `"listen"` event's `lines` are the newest five audible square lines in order, with `event.player == PID`.
  - `test_one_listen_at_a_time_and_it_ends_after_listen_seconds`: with `listen_seconds=0.05`, a second `listen()` returns `ALREADY_LISTENING`. Then `wait_for` an event with `ended=True`, `listening()` is False, and `listen()` works again.
  - `test_walking_away_stops_listening`: `listen()` then `walk("mill")` give events `[..., listen ended, place]`, and `listening()` is False.
  - `test_a_listen_ending_after_game_over_sends_nothing`: `listen()`, then `end(...)`, then wait past `listen_seconds`. No `"listen"` event has `ended=True`.
  - `test_listen_makes_no_model_calls_and_raises_no_suspicion`: the writer is `StubClient()` with no texts, its `calls == []`, and `suspicion.level(PID) == 0`.
  - `test_engine.py::test_a_listening_player_hears_every_line_at_their_place`: `overhearing_engine(Overhear(..., Always(0.99)), stub_player("p-1", "tavern", listening=True), stub_player("p-2", "tavern"))`. A plain line reaches p-1 with reason `"listen"`, and p-2 gets nothing.
  - `test_feed.py::test_listen_events_reach_only_the_listener_and_print_the_lines`: the event goes to `PID`, the terminal prints `"You listen at the square."` and `'Ada: "the well is dry"'`, `on_overheard(..., "listen", player=PID)` prints a line, and `"heated"` prints none.
  - `test_cli.py`: `parse_command("listen") == Command("listen")`.
  - `test_web.py::test_listen_starts_and_a_second_is_refused`: the first post gives `{"ok": True}`. The second gives 400 `{"error": "You're already listening."}`.
- [ ] **Step 2: Run** the listed test files. Expected: FAIL.
- [ ] **Step 3: Implement** the interfaces above.
- [ ] **Step 4: Run** the full suite and ruff. Expected: PASS.
- [ ] **Step 5: Commit** `Let the player listen to everything said where they stand`.

---

### Task 3: Notes keep every hint, and hint lines go to the player's hints project

**Files:**
- Create: `examples/rumor_mill/hints.py`, `tests/test_hints.py`
- Modify: `notes.py`, `config.py`, `engine.py`, `player.py`, `web.py`, `__main__.py`, `static/index.html`, `tests/helpers.py`
- Test: `tests/test_notes.py`, `tests/test_player.py`, `tests/test_web.py`, `tests/test_config.py`

**Interfaces:**
- Produces:
  - A note is `{"status": str, "hints": list[str], "passed": list[str]}`. `hint` is gone.
  - `Notes.__call__` also handles:
    - `"whisper"` events: it remembers `uid → text`.
    - `"whisper_status"`: a non-empty `hint` is appended once, and `took_the_bait` appends the whisper's text to `passed` once.
    - `"hop"`: the root's text is appended to the speaker's `passed` once, and the status does not change.
    - `"asked"`: `data["line"]` is appended to hints, and the status does not change.
    `RANK` and the status rules are unchanged.
  - `Config.draft_floor: float = 0.82`.
  - `hints.py`:
    - `HINT_TOP_K = 20`.
    - `class Hints(contex: ContexClient, config: Config)`.
    - `project(player: str) -> str` returns `f"rumor-mill:{config.session}:hints:{player}"`.
    - `async add(player: str, villager_id: str, line: str) -> None` publishes key `f"hint:{n}"` (a per-instance counter) with data `{"id": villager_id, "text": line}`. A failure is logged at WARNING with ids only.
    - `async matching(player: str, draft: str) -> list[str]` returns `[]` with no query until that player has added a hint. Otherwise it runs `query(project(player), draft, top_k=HINT_TOP_K, threshold=config.draft_floor)` and returns distinct `data["id"]`s, best first. It returns `[]` on failure.
  - `Engine.hints = Hints(contex, config)`. `helpers.FakeEngine.hints = Hints(self.contex, Config())`.
  - `Player._hint`: after emitting a settled, non-empty line, it runs `await self.engine.hints.add(self.id, villager_id, line)`.
  - `create_app(..., hints: Hints)` (and `__main__` passes `engine.hints`). `GET /api/hints?text=<draft>` returns `{"ids": [...]}`. A draft over `MAX_WHISPER_CHARS` gives `{"ids": []}`. No player gives 401.
  - Page: `learn(e)` mirrors the server rule. It appends hints once and fills `passed` from `spoken` text on `took_the_bait` and `hop`. `learn` also runs for `hop` and `asked` events. `who()` shows the latest hint, `notes[id].hints.at(-1)`. `cardBody` "What you know" lists every hint, then `Passed on: “…”` for each passed text.

- [ ] **Step 1: Write the failing tests**
  - `test_notes.py`: the existing tests move to the new shape. For example, `test_status_never_moves_backwards` ends with `{"status": "took_the_bait", "hints": ["The well worries me."], "passed": []}`.
  - `test_every_hint_is_kept_once_in_order`.
  - `test_what_they_passed_on_comes_from_bait_and_hops`: `feed.emit("whisper", player=PID, uid="w-1", text="the well is cursed")`, then `took_the_bait` from v1 and a `"hop"` event with `speaker_id="v2"`, `root="w-1"`. Both notes have `passed == ["the well is cursed"]`.
  - `test_an_answer_from_asking_around_is_a_hint`: an `"asked"` event for v1 with `line="Tom said so at the mill."` gives hints `["Tom said so at the mill."]` and status `"none"`.
  - `test_hints.py::test_add_publishes_to_the_players_own_project`: `published[0] == (f"rumor-mill:{s}:hints:p-1", "hint:1", {"id": "v1", "text": "The vines worry me."})`.
  - `test_matching_returns_villagers_whose_hints_match_best_first_once`: `results[draft]` holds data ids v2, v1, v2, which gives `["v2", "v1"]`. The query used the player's project and `threshold == Config().draft_floor`.
  - `test_no_query_before_the_player_has_a_hint`: `matching("p-2", ...) == []` and `queries == []`.
  - `test_a_failed_query_matches_no_one`: `StubContex(error=...)` gives `[]`, and the draft text is not in `caplog.text`.
  - `test_player.py::test_a_settled_brush_off_line_goes_to_the_players_hints`: this follows the existing brush-off-line test with a `StubClient` writer. `engine.contex.published` holds `(Hints project for PID, "hint:1", {"id": target, "text": line})`.
  - `test_a_plain_shrug_adds_no_hint`.
  - `test_web.py::test_state_and_card_carry_notes_without_needs_or_numbers` is updated to the new shape.
  - `test_hints_endpoint_marks_villagers_by_earned_hints`: add a hint for the player through `game.client.portal.call`, and set the stub results for `"the vines"` to `[Match("hint:1", {"id": "v1", "text": "x"}, 0.9)]`. GET gives `{"ids": ["v1"]}`. A second owner (`as_owner`) gets `{"ids": []}` and causes no new query. The `game` fixture exposes `engine`.
  - `test_config.py`: `draft_floor == 0.82`.
- [ ] **Step 2: Run** the listed test files. Expected: FAIL.
- [ ] **Step 3: Implement** the interfaces above.
- [ ] **Step 4: Run** the full suite and ruff. Expected: PASS.
- [ ] **Step 5: Commit** `Keep every hint and what each villager passed on, and store each player's hint lines`.

---

### Task 4: Ask around

**Files:**
- Modify: `writer.py`, `player.py`, `suspicion.py`, `feed.py`, `cli.py`, `web.py`
- Test: `tests/test_writer.py`, `tests/test_player.py`, `tests/test_suspicion.py`, `tests/test_feed.py`, `tests/test_cli.py`, `tests/test_web.py`

**Interfaces:**
- Consumes: Task 1 `Player.find`, `_payload`. Task 3 `Hints.add` and Notes' `"asked"` handling.
- Produces:
  - `Writer.answer(villager: Villager, topic: str, heard: Sequence[str]) -> str`. This is one Haiku call with `max_tokens=SAY_MAX_TOKENS`, returning `first_line(...)`. The system prompt (persona, as in `reply`) says: "The newcomer asks what folk have been saying about something. Answer in one short sentence, under 30 words, in your own voice, naming who told you and where. If you heard nothing about it, say so. Never mention suspicion. No stage directions, no quotation marks, no preamble." The user message is `f"The newcomer asks about: {topic}"` followed by `"What you heard: " + " / ".join(heard)`, or `"What you heard: nothing about it."`.
  - `suspicion.REASON_LINES["asked questions"] = "{} noticed you asking questions."`
  - `Feed.on_answer(villager_id: str, name: str, topic: str, line: str, *, player: str) -> None` emits kind `"asked"`, terminal line `f'{name}, asked about {topic}: "{line}"'`, with data `villager_id, name, topic, line`.
  - `player.py`: `ASK_LINES = 3`. `Player.ask(villager_id: str, topic: str) -> str | None` runs inside `charging(self.id)`. In order:
    - Return `GAME_OVER` when the game is over.
    - When the villager is gone, return the left-town or no-id message (as `_whisper_to` does).
    - An empty topic returns `"Ask about what? Try: the mill"`.
    - A topic over `MAX_WHISPER_CHARS` returns `f"Too long: a question is at most {MAX_WHISPER_CHARS} characters."`.
    - When the villager is elsewhere, return `f"{name} is at the {place}. Walk there to ask them."`.
    - When score ≥ `refuse_threshold`, emit `on_refusal` with the `_turn_away` line and return `Refusal(f"{name} turns away from you.")`, with no bump and no recall.
    - Otherwise bump `suspicion_whisper_bump / 2` with reason `"asked questions"`. Recall with `engine.recall_heard(vid, topic, "")`. Keep up to `ASK_LINES` lines that `_tellable` passes, formatted `f"{u.speaker_name} at the {u.place}: {u.text}"`. Get the line from `_answer`. Emit `on_answer`. Finally `await engine.hints.add(...)`.
    - Nothing is delivered, published to a mind, or recorded as a fact.
  - `Player._tellable(m: Match) -> Utterance | None` returns the fact for `m.data["id"]`. It returns None when the fact is unknown, or when it names a newcomer or is `about` a player, unless `lineage.player_of(fact.id) == self.id` or `fact.about == self.id`.
  - `Player._answer(villager: Villager, topic: str, heard: list[Utterance]) -> str` calls the writer's line. Without a writer, or when the call fails (logged with ids only), it falls back to `f"{name} heard {u.speaker_name} talking about it at the {u.place}."` for the first heard line, or `f"{name} hasn't heard anything about that."`.
  - `cli`: `parse_command("ask Marta: the mill") == Command("ask", "Marta", "the mill")`, checked before the bare `name: text` whisper form. The repl resolves the name with `player.find` and prints any problem that is not a `Refusal`. `HELP` second line: `"Other commands: go <place>, listen, ask <name>: <topic>, villagers, inspect <name>, goals, trophies, usage, help, quit"`.
  - `web`: `BAD_ASK = 'Send JSON like {"villager_id": "v1", "topic": "the mill"}.'` `POST /api/ask` returns `{"ok": True}`, 409 for a `Refusal`, or 400 otherwise.

- [ ] **Step 1: Write the failing tests** (`make_player` puts villagers at the tavern; set `player.place = "tavern"` unless the test needs otherwise)
  - `test_writer.py::test_answer_names_the_topic_and_what_was_heard_in_one_line`: `StubClient("Tom told me at the mill, aye.")` returns that line. `calls[0]["max_tokens"] == SAY_MAX_TOKENS`, and the user text holds the topic and the heard line. Empty `heard` gives a user text holding `"nothing about it"`.
  - `test_player.py::test_ask_answers_from_what_the_villager_heard`: `engine.heard = [Match("utt:u-3", {"id": "u-3"}, 0.9)]` with the fact `Utterance("u-3", "v2", "Tom", "mill", "the mill is haunted", None, action=SPEAK)` and writer `StubClient("Tom told me so.")`. `ask("v1", "the mill") is None`. `engine.recalls == [("v1", "the mill", "")]`. The writer's user text holds `"Tom at the mill: the mill is haunted"`. The `"asked"` event has `player == PID`, `line == "Tom told me so."`, and `engine.delivered == []`.
  - `test_ask_needs_the_villager_at_your_place`: with the player at the square, the result is `"Marta is at the tavern. Walk there to ask them."`, with `engine.recalls == []` and score 0.
  - `test_asking_raises_half_a_whisper_bump_and_charges_the_asker`: score `== pytest.approx(config.suspicion_whisper_bump / 2)`, the evidence reason is `"asked questions"`, and with `Writer(StubClient(...), "m", usage)`, `usage.cost(PID) > 0`.
  - `test_a_villager_who_heard_nothing_says_so`: the writer's user text holds `"nothing about it"`. With no writer, the line is `"Marta hasn't heard anything about that."`.
  - `test_lines_about_another_newcomer_are_never_told`: three recalled lines. One hop names the newcomer and roots in `w-9`, recorded for `"p-2"`. One is a warning with `about="p-2"`. One has no fact. The writer's heard list is empty.
  - `test_a_wary_villager_turns_the_question_away`: at score 0.9, the result is a `Refusal`, there is a `refusal` event for PID, `engine.recalls == []`, and the score is unchanged.
  - `test_the_answer_joins_notes_and_hints`: `Notes(feed, PID).get("v1")["hints"] == [line]`, and the hints project publish is present.
  - `test_suspicion.py::test_asking_has_its_own_reason_line`: `reasons(...) == ["Marta noticed you asking questions."]`.
  - `test_feed.py::test_an_answer_reaches_only_the_asker`.
  - `test_cli.py`: the parse forms above, and `parse_command("ask : x") == Command("unknown")`.
  - `test_web.py::test_ask_reports_problems_and_answers_through_the_feed`: walk to the well, then post `{"villager_id": "v1", "topic": "the mill"}`, which gives `{"ok": True}`. The websocket gets `"asked"` with the fallback line. Asking v2 (at the mill) gives 400 `"Bert Cole is at the mill. Walk there to ask them."`. A bad body gives 400 `BAD_ASK`. At score 0.9, the result is 409.
- [ ] **Step 2: Run** the listed test files. Expected: FAIL.
- [ ] **Step 3: Implement** the interfaces above.
- [ ] **Step 4: Run** the full suite and ruff. Expected: PASS.
- [ ] **Step 5: Commit** `Let the player ask a villager what folk have been saying`.

---

### Task 5: Page: Walk, Listen and Ask around

**Files:**
- Modify: `static/index.html`
- Test: `tests/test_web.py`

**Interfaces:**
- Consumes: `/api/walk`, `/api/listen`, `/api/ask`, and the `place`, `listen` and `asked` events from Tasks 1, 2 and 4.
- Produces (page only):
  - `let here` holds the player's place, from `state.place` and `place` events.
  - `async function post(path, body)` returns `{ok, error}`.
  - The open tile gets a `Walk here` button, or a `Listen here` button at `here`. While a listen runs (until the end event, or `until` passes on reload), it reads `Listening…` and is disabled.
  - `case "listen"` builds a `.vt` block with `data-listen` and head `You listen at the {place}`. Its opening lines use the same line builder as `overheard` (with the Talk button). The end appends `You stop listening.` as `.scene-end`. Overheard events with `reason === "listen"` go into the newest unended listen block for that place.
  - The villager card gets an "Ask around" `<form class="ask">`. Its label is `Ask {name} what folk say about`, it has a text input with `maxlength="500"` and an `Ask` button, and errors go to a `role="status"` line in the card. When the villager is elsewhere, the form is replaced by `{name} is at the {place}.` and a `Walk there` button.
  - `case "asked"` renders a note with `<b>{name}</b>, asked about {topic}: <span class="brush">“{line}”</span>`.
  - `/api/walk` errors show in `#status`.

- [ ] **Step 1: Write the failing test** `test_web.py::test_page_has_the_walk_listen_and_ask_controls`. The HTML contains `"Walk here"`, `"Listen here"`, `"/api/walk"`, `"/api/listen"`, `"/api/ask"`, `'case "listen"'`, `'case "asked"'` and `'class="ask"'` or `className: "ask"`. `"innerHTML" not in html`.
- [ ] **Step 2: Run** it. Expected: FAIL.
- [ ] **Step 3: Implement** the page changes above.
- [ ] **Step 4: Run** the full suite and ruff. Expected: PASS. (Browser verification is a separate controller step.)
- [ ] **Step 5: Commit** `Add Walk, Listen and Ask around to the page`.

---

### Task 6: B1: the frontier, and why a rumor stopped

**Files:**
- Modify: `rumors.py`, `engine.py`, `web.py`
- Test: `tests/test_rumors.py`, `tests/test_engine.py`, `tests/test_web.py`

**Interfaces:**
- Produces in `rumors.py`:
  - `FRONTIER_STATES = ("heard", "busy", "answered", "uncared", "worn", "knew", "kept", "warned", "drifted", "passed")` and `OPEN_STATES = ("heard", "busy", "answered")`.
  - `TARGET_STATES = {"checking": "heard", "heard": "heard", "busy": "busy", "answered": "answered", "brushed_off": "uncared", "near_miss": "uncared", "kept": "kept", "moved_on": "kept", "warned": "warned", "took_the_bait": "passed"}`. `not_carried` removes the target's entry.
  - `Card.frontier: dict[str, dict]`, villager id to entry. An entry is internal: `name`, `state`, `at`, `until`, `told: list[str]`, `scene: dict | None`, `before: bool`, `depth: int`. Hop dicts gain `"speaker_id"` and `"ids"` (listener ids).
  - `Rumors.on_event(event: Event) -> None` is a feed sink. A `whisper_status` for a card's whisper sets the target's entry (with `until` for busy) through `reached`. `Engine.__init__` registers it when `rumors` is given.
  - `Rumors.record(...)`, for an on-topic hop: the speaker's entry becomes `passed`, and a one-to-one listener's name goes into `told`. A one-to-one listener's entry becomes `heard`, with `before=True` when they were already in `heard`. `passed` is never replaced. Room listeners get no entry. For an off-topic hop, the speaker's entry becomes `drifted` unless it is `passed`.
  - `Rumors.reached(uid: str, vid: str, name: str, state: str, **extra) -> None`. It applies only to a new entry or one whose state is in `OPEN_STATES`. `uncared` and `worn` become `knew` when `before`. `extra` holds `until` (busy) or `depth` (worn).
  - `Rumors.in_scene(uid: str, scene: Scene, names: dict[str, str]) -> None`. While the scene runs, each participant's entry gets `scene={"id", "tone": "argument"|"conversation", "place"}`, and missing participants are added as `heard`. Once `scene.ended`, those marks are cleared.
  - `STOP_LINES`:
    - `"worn": "It wore out after {tellings}."`
    - `"knew": "Everyone who heard it had heard it before."`
    - `"uncared": "It stopped with {who}: it didn't touch anything they care about."`
    - `"warned": "It stopped with {who}, who warned others about you."`
    - `"kept": "It stopped with {who}, who kept it quiet."`
    - `"drifted": "It turned into another story with {who}."`
    - `"crowd": "It stopped at the {place}: {heard} heard it and no one passed it on."`
    `who` is one name, `"A and B"`, or `f"{n} people"`. `tellings` is `"1 telling"` or `f"{n} tellings"`, where n is the largest worn `depth`.
  - The stop rule. There is no line while the frontier is empty, while any entry has a `scene`, or while any entry is in `OPEN_STATES` with `at >= now - day_seconds`. Otherwise the ends are the entries that are not `passed`:
    - With no ends, use `crowd` from the latest room hop. With no room hop either, there is no line.
    - When every end is `knew`, use `knew`.
    - Otherwise treat `knew` as `uncared` and use the most common state, breaking ties in the order `worn, uncared, warned, drifted, kept`.
  - `cards(player)` adds:
    - `"frontier"`: entries sorted by `at`, each exactly `{"id", "name", "state", "until", "told", "crowd", "scene"}`. `crowd` is `{"place", "heard", "passed"}` for the teller's latest room hop. Its `passed` counts those listener ids whose entry is `passed`. `scene` is `{"tone", "place"}`.
    - `"stopped": str | None`.
    - `"touched": dict[str, str]`, which is `"heard"` for every id in `heard`, then `"passed"` and `"scene"` from entries.
- Produces in `engine.py`:
  - `async Engine.cares(vid: str, uid: str, depth: int, since: str) -> str | None`. It runs one query per need on `mind_project(session, vid)` with `top_k=config.top_k`, `since=since` and `threshold=baselines.threshold(need)`. The best clearance is `similarity - threshold` over matches keyed `f"utt:{uid}"`. No match gives `"uncared"`, a clearance ≥ `fatigue_per_hop * depth` gives `"cares"`, and anything else gives `"worn"`. On failure it logs ids only and returns None.
  - `async Engine._follow(root: str, u: Utterance, vid: str, since: str) -> None`. On `"cares"` with `cooldown_ends(vid) > time.time()`, it calls `reached(..., "busy", until=...)`. On `"uncared"` or `"worn"`, it calls `reached(..., verdict, depth=u.depth)`. On None, nothing happens. `_carries_rumor` starts it in `self._following: set[asyncio.Task]` only for an on-topic hop with exactly one listener, when `baselines` is set. `since` is now minus `check_lookback_seconds`. `stop()` cancels them.
  - `Engine._carried(uid: str | None) -> bool` means uid is an on-topic hop shown on a player whisper's card (`root != uid`, `lineage.is_whisper(root)`, `rumors.carries(root, uid)`).
  - `_report(v, whispers, action, said, answered=None, hops: Sequence[str] = ())`. For each carried hop uid whose root is not in `told`, and when `scenes.of(v.id)` is None, it calls `rumors.reached(root, v.id, v.name, "kept")`. `_turn` passes the carried ids among `new`.
  - `_show_scene` calls `rumors.in_scene(scene.rumor, scene, names)` for a rumor scene.
- Produces in `web.py`: `tallies` also carries `frontier`, `stopped` and `touched`.

- [ ] **Step 1: Write the failing tests**
  - `test_rumors.py::test_a_hop_makes_its_teller_pass_it_on_and_its_listener_hear_it`. After `add("w-1", ..., "v1", "Marta")`, a `checking` status, and `record("w-1", hop by v1, {"v2": "Bert"}, True)`, the frontier is `[{"id": "v1", "state": "passed", "told": ["Bert"], ...}, {"id": "v2", "state": "heard", ...}]`.
  - `test_target_outcomes_follow_the_whisper_status`: parametrized over `TARGET_STATES`, plus `not_carried` leaving no entry.
  - `test_a_room_hop_is_one_summary_on_its_teller`: v1 tells v2, v3 and v4 at the tavern, then v3 tells v5. v1's `crowd == {"place": "tavern", "heard": 3, "passed": 1}`. v2 and v4 have no entry. `touched` holds v1 through v5.
  - `test_a_late_check_never_undoes_an_outcome`: `reached(kept)` then `reached(busy)` stays kept. A new hop to that listener makes it heard again. `passed` survives `reached(uncared)` and a later hop to them.
  - `test_stopped_says_why`, parametrized over each `STOP_LINES` case with exact sentences:
    - `"It stopped with Bert: it didn't touch anything they care about."`
    - `"It stopped with Bert and Ida, who kept it quiet."`
    - `"It wore out after 5 tellings."`
    - `"Everyone who heard it had heard it before."`
    - `"It stopped at the tavern: 3 heard it and no one passed it on."`
    - `"It stopped with Bert, who warned others about you."`
    - `"It turned into another story with Bert."`
  - `test_no_stop_line_while_anyone_still_holds_it`: a heard entry gives None. With `rumors.time.time` monkeypatched past `day_seconds`, the stop line appears.
  - `test_a_scene_marks_its_people_until_it_ends`: with `Scene(... heated=True, place="tavern")`, the entry has `scene == {"tone": "argument", "place": "tavern"}`, `touched` is `"scene"`, and `stopped is None`. After `ended="cooled"`, `scene is None`.
  - `test_frontier_entries_carry_no_internal_fields`: every entry's keys are `{"id", "name", "state", "until", "told", "crowd", "scene"}`.
  - `test_engine.py::test_a_listener_who_does_not_care_is_marked_with_queries_only`: with `stub_rumors(0.9)`, `StubBaselines(0.5)`, a v2 with needs `["bread", "ale"]`, and contex returning nothing for needs, deliver hop `u-1` (parent `w-1`, recorded for PID) from v1 to `[v2]`. `wait_for` v2 `uncared`. Exactly two queries hit `mind_project(session, "v2")`, and `turns.views == []`.
  - `test_a_listener_who_cares_but_is_busy_shows_when_they_hear_it`: the need query returns `Match("utt:u-1", ..., 0.9)` and `engine._last_turn["v2"] = time.monotonic()` with `cooldown_seconds=5`. The entry is `busy` with `until > time.time()`.
  - `test_a_retelling_too_tired_to_wake_is_worn`: `fatigue_per_hop=0.01`, depth 3, similarity `threshold + 0.005` gives `worn` with `depth == 3`.
  - `test_a_room_hop_runs_no_check`: two listeners give no mind-project queries.
  - `test_a_listener_whose_turn_does_not_retell_kept_it_quiet`: an IGNORE turn on the carried hop sets v2 to `kept`.
  - `test_stop_cancels_pending_checks`.
  - `test_web.py::test_card_tallies_reach_the_websocket`: the fixture adds `feed.sinks.append(rumors.on_event)`, and the expected tally becomes `{"uid": "w-1", "heard": 1, "still": 0, "frontier": [], "stopped": None, "touched": {"v1": "heard"}}`.
- [ ] **Step 2: Run** `python -m pytest examples/rumor_mill/tests/test_rumors.py examples/rumor_mill/tests/test_engine.py examples/rumor_mill/tests/test_web.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** the interfaces above.
- [ ] **Step 4: Run** the full suite and ruff. Expected: PASS.
- [ ] **Step 5: Commit** `Show who holds each rumor now and why it stopped`.

---

### Task 7: B3 and B2 data: goal links, who each rumor convinced, and who it touched

**Files:**
- Modify: `overhear.py`, `rumors.py`, `player.py`, `judge.py`, `feed.py`, `web.py`, `__main__.py`
- Test: `tests/test_overhear.py`, `tests/test_rumors.py`, `tests/test_judge.py`, `tests/test_feed.py`, `tests/test_web.py`

**Interfaces:**
- Consumes: Task 6 `cards()` `touched`.
- Produces:
  - `async Overhear.claims(text: str) -> list[str]` returns deck goal ids whose published evidence the text matches (`baselines.matches(evidence, similarity)`), best first. It runs one query on the goals project with `top_k=len(self._evidence)` and `threshold=0`. It returns `[]` when nothing is published or on failure, logging ids only.
  - `Rumors(contex, baselines, config, overhear: Overhear | None = None)`. `add(..., *, player: str, place: str = "")` stores `Card.place`. For a card that is not a reply, it stores `Card.claims = await overhear.claims(text)`. `Rumors.card(uid: str) -> Card | None` returns the card that shows uid's whisper, following `part_of`. `cards()` adds `"claims"` and `"place"`.
  - `Player._card` passes `place=utterance.place`.
  - `Judge(..., rng=None, lineage: Lineage | None = None)`. `Judge.roots: dict[str, dict[str, str | None]]` maps goal id to {believer id: the root of their best-matching memory when that root is a player whisper, else None}. `check` resets `roots[goal.id]` for a belief goal. `_believes` records a believer, using the match with the highest similarity. `_forget` also drops `roots`.
  - `Judge._moved(goal_id: str, player_id: str, before: dict[str, str | None]) -> str | None` returns, among roots with `lineage.player_of(root) == player_id`, the one with the most believers gained since `before`. It returns None when no root gained. `run` captures `before` ahead of each `check` and passes `rumor=` to each holder's `on_progress`.
  - `Feed.on_progress(goal, fraction, counts=None, player=None, rumor: str | None = None)` puts `rumor` in the data only when `player` is not None. The terminal line is unchanged.
  - `web.py`:
    - `card_view(card: dict, player: Player, judge, rumors: Rumors) -> dict` adds `"goals"`. That is, for the first claim in the player's hand, and for the world goal when `judge.world.id == f"world-{claim}"`: `{"text": goal.text, "claim": claim(goal), "world": bool, "convinced": n}`. n counts `judge.roots[goal.id]` roots whose `rumors.card(root).uid == card["uid"]`, and those believers become `"believes"` in `touched`.
    - `TALLY_KEYS = ("uid", "heard", "still", "frontier", "stopped", "touched", "goals")`.
    - `tallies(rumors, judge, players, player_id) -> list[dict]` uses `card_view`, and `/api/state` `rumors` is `[card_view(...)]`.
  - `__main__`: `Rumors(contex, baselines, config, overhear)` and `Judge(..., lineage=lineage)`.

- [ ] **Step 1: Write the failing tests**
  - `test_overhear.py::test_claims_are_the_goals_whose_evidence_the_words_match_best_first`: with `StubBaselines(0.5)`, scores of `goal:tavern-ghost` 0.95, `goal:well-cursed` 0.9 and `goal:dragon` 0.1 give `["tavern-ghost", "well-cursed"]`.
  - `test_claims_fail_quietly`.
  - `test_rumors.py::test_a_whisper_card_keeps_its_place_and_claims_and_a_reply_asks_nothing`: a stub overhear returns `["tavern-ghost"]` and counts its calls. One call is made for `w-1`, and none for the reply `w-2`. `cards()[0]["claims"] == ["tavern-ghost"]` and `["place"] == "tavern"`.
  - `test_card_follows_a_reply_to_its_conversation_card`.
  - `test_judge.py::test_believers_are_traced_to_the_rumor_behind_their_best_memory`: the judge has a `Lineage`, `u-1` is a child of `w-1` (recorded for `"p-1"`), and `u-2` is its own root. v1 matches `[u-2 at 0.8, u-1 at 0.9]`, which gives `roots[goal.id] == {"v1": "w-1"}`. A believer whose only memory is `u-2` maps to None. A non-believer is absent.
  - `test_progress_names_the_players_rumor_that_moved_it`: a scripted `check` sets `judge.roots` and returns rising progress. `Lines.on_progress` gains `rumor=None` and records it. Holder p-1 gets `"w-1"`. Holder p-2, with the same goal, gets None.
  - `test_feed.py::test_progress_carries_the_rumor_only_for_its_player`: with `player=PID`, the data has `rumor == "w-1"`. With no player, there is no `rumor` key, even when one is passed.
  - `test_web.py::test_cards_link_goals_and_count_who_each_convinced`: the card `w-1` has claims `["cat", "well"]`, and `judge.roots = {"cat": {"v1": "w-1", "v2": None}, WORLD.id: {"v2": "w-1"}}`. The state's card `goals == [{"text": GOAL.text, "claim": "the mayor's cat is a spy", "world": False, "convinced": 1}, {"text": WORLD.text, "claim": "the well is cursed", "world": True, "convinced": 1}]` and `touched == {"v1": "believes", "v2": "believes"}`. The `game` fixture's judge gains `roots={}`.
  - `test_another_players_believers_never_count_on_your_card`: the root is another player's card `w-9`, which gives `convinced == 0`.
  - `test_card_tallies_reach_the_websocket` adds `"goals": []`.
- [ ] **Step 2: Run** the listed test files. Expected: FAIL.
- [ ] **Step 3: Implement** the interfaces above.
- [ ] **Step 4: Run** the full suite and ruff. Expected: PASS.
- [ ] **Step 5: Commit** `Link each rumor to the goal it serves and count whom it convinced`.

---

### Task 8: B4 data: influence in words, and who believes your goals

**Files:**
- Modify: `suspicion.py`, `web.py`
- Test: `tests/test_suspicion.py`, `tests/test_web.py`

**Interfaces:**
- Consumes: Task 7 `Judge.roots`.
- Produces:
  - `suspicion.INFLUENCE = "folk listen to the {}"` and `suspicion.influence(villager: Villager) -> str | None`. It returns `INFLUENCE.format(occupation.lower())` when a `WEIGHTS` key is in the occupation, else None. `Suspicion.weight` is unchanged.
  - `web.villagers(town)` entries gain `"influence"`. `/api/villagers/{vid}` gains `"influence"` and `"believes": list[str]`.
  - `web.beliefs(judge, player: Player) -> dict[str, list[str]]` maps villager id to `claim(goal)` for each of the player's goals, then the world goal, that they believe (`vid in judge.roots.get(goal.id, {})`).
  - `/api/state` gains `"beliefs"`. A `watch_each("beliefs", partial(beliefs_of, judge, players), VILLAGER_POLL_SECONDS)` sends `{"kind": "beliefs", "beliefs": ...}` per player, and `sent` gains `"beliefs"`.

- [ ] **Step 1: Write the failing tests**
  - `test_suspicion.py::test_influence_is_words_for_the_weighted_roles_only`: `"Priest"` gives `"folk listen to the priest"`, `"Tavern keeper"` gives `"folk listen to the tavern keeper"`, and `"baker"` gives None.
  - `test_web.py::test_villagers_carry_influence_words_and_no_weights`: every state villager has an `influence` key, and neither `"2.0"` nor `"3.0"` is in the response text.
  - `test_beliefs_are_the_players_own_goals_only`: with `judge.roots = {"cat": {"v1": None}, "other": {"v2": "w-9"}, WORLD.id: {"v2": None}}`, the state's `beliefs == {"v1": ["the mayor's cat is a spy"], "v2": ["the well is cursed"]}`, and the v1 card's `believes == ["the mayor's cat is a spy"]`.
  - `test_beliefs_reach_the_websocket`: `VILLAGER_POLL_SECONDS` is monkeypatched to 0.05. A change to `judge.roots` gives `kind == "beliefs"` to that player only (a second owner's socket gets none).
- [ ] **Step 2: Run** the listed test files. Expected: FAIL.
- [ ] **Step 3: Implement** the interfaces above.
- [ ] **Step 4: Run** the full suite and ruff. Expected: PASS.
- [ ] **Step 5: Commit** `Say who is influential and who believes the player's goals`.

---

### Task 9: B5: moments

**Files:**
- Modify: `feed.py`, `rumors.py`, `engine.py`
- Test: `tests/test_feed.py`, `tests/test_rumors.py`, `tests/test_engine.py`

**Interfaces:**
- Consumes: Task 7 `Rumors.card`, `Card.place`. Task 8 `influence`.
- Produces:
  - `feed.MOMENTS`:
    - `"size": "{n} people have heard your rumor now."`
    - `"influence": "Your rumor reached {name}, and {influence}."`
    - `"place": "Your rumor has reached the {place}."`
    - `"scene": "Your rumor started {kind} at the {place}."`, where kind is `"an argument"` or `"a conversation"`
    - `"turned": "Your rumor turned against you: {name} says a newcomer started it."`
  - `Feed.on_moment(rumor: str, line: str, tone: str = "good") -> None` emits kind `"moment"`, terminal `line`, data `rumor, text=line, tone`, with `player=lineage.player_of(rumor)`. It returns without emitting when that is None.
  - `Rumors.first(uid: str, key: str) -> bool` is True the first time `key` is marked on the card that shows uid. It is False for an unknown uid. (`Card.marked: set[str]`.)
  - `engine.MOMENT_SIZES = (5, 10)`. `Engine._moments(root: str, u: Utterance, heard: list[str]) -> None` is called from `_carries_rumor` after an on-topic `record`. In order:
    - A `size` moment for each size ≤ `len(card.heard)`.
    - An `influence` moment for each heard villager with `influence(v)`, keyed per villager.
    - A `place` moment when `u.place != card.place`, keyed per place.
    - A bad `turned` moment when `u.names_newcomer`, once per card.
    Each is guarded by `rumors.first`.
  - `_show_scene` for a rumor scene with `len(scene.people) >= 2` emits a `scene` moment once per scene id.

- [ ] **Step 1: Write the failing tests**
  - `test_feed.py::test_a_moment_reaches_only_its_rumors_player`: `on_moment("w-1", "x")` gives kind `moment`, player `PID`, tone `good`, and the terminal prints `"x"`. `on_moment("u-unknown", "x")` returns None, and `feed.recent` is unchanged.
  - `test_rumors.py::test_first_is_true_once_per_card`: a reply's uid shares its conversation card's marks.
  - `test_engine.py::test_moments_mark_five_and_ten_hearers_once` (with `stub_rumors(0.9)`): hops that grow `heard` to 5 and then 11 give exactly `["5 people have heard your rumor now.", "10 people have heard your rumor now."]`, and a further hop adds none.
  - `test_reaching_an_influential_villager_or_a_new_place_is_a_moment`: v2 is a `"priest"`, the hop is at the tavern, and the card's place is the square. The moments are `"Your rumor reached V2, and folk listen to the priest."` and `"Your rumor has reached the tavern."`.
  - `test_a_retelling_that_names_the_newcomer_is_a_bad_moment`: tone `bad`, once.
  - `test_a_rumor_scene_is_a_moment_once_two_have_spoken`.
  - `test_an_off_topic_line_is_no_moment`: with `stub_rumors(0.1)`, there are no moments.
- [ ] **Step 2: Run** the listed test files. Expected: FAIL.
- [ ] **Step 3: Implement** the interfaces above.
- [ ] **Step 4: Run** the full suite and ruff. Expected: PASS.
- [ ] **Step 5: Commit** `Mark the moments a rumor turns, for better or worse`.

---

### Task 10: Page: cards show the frontier, why it stopped, goals and moments

**Files:**
- Modify: `static/index.html`
- Test: `tests/test_web.py`

**Interfaces:**
- Consumes: the card fields `frontier`, `stopped`, `goals` (state and `rumors` snapshot), goal events' `rumor`, and `moment` events.
- Produces (page only):
  - `const FRONTIER`, state to words:
    - `heard: "heard it"`
    - `busy: "busy, hears it in about {n}s"` (a live countdown through the existing `[data-until]` ticker, with a `.wait` twin for screen readers)
    - `answered: "answered you"`
    - `uncared: "heard it, didn't care"`
    - `worn: "heard it, but it had worn thin"`
    - `knew: "had heard it before"`
    - `kept: "kept it quiet"`
    - `warned: "warned others about you"`
    - `drifted: "told it as another story"`
    - `passed`: `"passed it on to {names}"`, or `"passed it on at the {place}: {heard} heard it, {passed} passed it on"` with a crowd
    A `scene` entry reads `in an argument at the {place}` or `in a conversation at the {place}` instead.
  - `newCard` gains `ol.frontier`, `p.stopped` and `p.goal-link`, filled by `setTallies` and `restoredCard`. The newest `FRONTIER_SHOWN = 4` entries show, behind a `Show all N` toggle `.frontier-more` (`aria-expanded`, added to `ROVE`).
  - Goal links read `For your goal: {text}` or `For the world goal: {text}`, plus ` · {n} now believe {claim}` when n > 0.
  - A personal goal event with `rumor` appends ` · moved by “{first 8 words of that card's whisper}…”` when the card is on the page.
  - `case "moment"` gives `li.note.moment` with `data-tone`. `.note.moment[data-tone="bad"]` and `.note.against` share one weight, equal to good moments.

- [ ] **Step 1: Write the failing tests**
  - `test_web.py::test_the_page_has_words_for_every_frontier_state`: a regex `const FRONTIER = \{(.*?)\};` gives keys equal to `set(rumors.FRONTIER_STATES)`.
  - `test_the_page_renders_moments_and_goal_links`: the HTML contains `'case "moment"'`, `"frontier-more"` inside the `ROVE` constant, `"For your goal"`, `"moved by"`, and no `innerHTML`.
- [ ] **Step 2: Run** them. Expected: FAIL.
- [ ] **Step 3: Implement** the page changes above.
- [ ] **Step 4: Run** the full suite and ruff. Expected: PASS. (Browser verification is a separate controller step.)
- [ ] **Step 5: Commit** `Show each rumor's frontier, its goal and its moments on the page`.

---

### Task 11: Page: the village panel shows whom a rumor touched, what you know, and whom to try

**Files:**
- Modify: `static/index.html`
- Test: `tests/test_web.py`

**Interfaces:**
- Consumes: the card `touched` (Tasks 6 and 7), notes `hints` and `passed` (Task 3), villagers' `influence`, `beliefs` (state, `beliefs` socket message), and `/api/hints` (Task 3).
- Produces (page only):
  - **B2.** Pinning a card's whisper or hop (click, or Enter, as today) sets `selectedRumor` to that card's uid. Unpinning or Escape clears it. Only one rumor is selected at a time. While a rumor is selected:
    - Faces and rows of touched villagers get `.touched` with `data-touch`, plus a word: `heard it`, `passed it on`, `believes it` or `in a scene` (a visually hidden span on faces, `, {word}` on rows).
    - Tile counts read `{k} of {n} touched`, with a matching `aria-label`.
    - `setTallies` re-renders the panel when the selected card changes.
  - **B4 rows and card.** Rows show the influence words and `believes {claim}` after the trade. The card's "What you know" adds influence, beliefs, every hint and `Passed on: “…”`.
  - **B4 sort.** `<select id="sort">` with options `By place` (name order), `By what you know` and `By influence` reorders faces and rows inside each tile. The choice is stored under `rumor-mill-sort`, with try/catch like `OPEN_KEY`.
    - By what you know: `NOTE_ORDER = ["took_the_bait", "answered", "kept", "listening", "brushed_off", "none", "wary"]`, where a non-empty `passed` ranks as `took_the_bait`, then name.
    - By influence: influential first, then name.
  - **B4 draft marks.** Input on `#text` that has at least `DRAFT_MIN = 12` characters triggers, after `DRAFT_WAIT = 400` ms, `GET /api/hints?text=` into `drafted`. A stale reply is dropped. Matching faces and rows get `.might` with the text `this might interest them`. Marks clear when the draft empties or a whisper is sent.
  - `receive()` handles `kind === "beliefs"`.

- [ ] **Step 1: Write the failing test** `test_web.py::test_the_panel_has_selection_sort_and_draft_marks`. The HTML contains `'id="sort"'`, `"By what you know"`, `"By influence"`, `"/api/hints"`, `"this might interest them"`, `"touched"`, and `'"beliefs"'`, and has no `innerHTML`.
- [ ] **Step 2: Run** it. Expected: FAIL.
- [ ] **Step 3: Implement** the page changes above.
- [ ] **Step 4: Run** the full suite and ruff. Expected: PASS. (Browser verification is a separate controller step.)
- [ ] **Step 5: Commit** `Mark whom a rumor touched and whom a draft might interest in the village panel`.
