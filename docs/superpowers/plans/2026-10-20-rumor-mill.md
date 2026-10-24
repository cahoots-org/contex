# Rumor Mill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `examples/rumor_mill/`, a terminal gossip game where about 100 LLM villagers hold private Contex minds, wake on push, and forget on a sliding window, while the player whispers rumors to complete goals.

**Architecture:** Each villager's mind is a Contex project with one subscription. A per-villager worker wakes on push, diffs the bundle for new items, asks Jev to decide (one speculative Choice request), has Haiku write the words, and publishes utterances into listeners' minds. A judge scores goals by querying minds and asking Jev. The player talks to the engine through a chat feed.

**Tech Stack:** Python 3.12, `mcp>=2,<3`, `typesafe-sdk` (Jev), `anthropic` (Haiku), pytest + pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-10-19-rumor-mill-design.md`. Read it first; this plan only records decisions the spec leaves open.

**Branch:** `feat/rumor-mill`, cut from `docs/rumor-mill-design` (the spec PR is unmerged). Rebase onto `main` once Rob merges that. Do not merge anything; Rob merges.

## Global Constraints

- Everything lives under `examples/rumor_mill/`; **no changes under `src/`**. The only edits outside it are `.github/workflows/ci.yml`, `examples/README.md`.
- Python 3.12; imports at the top of each module; docstrings short; comments say what code does.
- Contex over MCP only, `mcp>=2,<3`, following `examples/mcp_watch.py`.
- Haiku model id `claude-haiku-4-5-20251001`, used only to write speech, group names/creeds, and characters. Jev (`typesafe-sdk`, key in `TYPESAFE_API_KEY`) makes every decision and scores goals. Anthropic key in `ANTHROPIC_API_KEY`.
- Every Choice has at most 255 options.
- Every mind subscription passes `max_age_seconds`. No catch-all "what people told me" need.
- Villagers wake only on push, never on a timer. Speakers' own words are never published into their own mind.
- Each commit passes CI (`ruff check .` and the tests). One commit per task, imperative subject, body says why.
- Absolute imports: `from examples.rumor_mill.town import Town`. Tests live in `examples/rumor_mill/tests/` (with `__init__.py`), async tests use `@pytest.mark.asyncio`.

## Review Focus

Inputs the spec implies but no obvious task covers. Each has a test in the task named.

1. Whisper to a name that matches nobody, matches several, or belongs to someone who left: a clear feed message, no crash (Task 7).
2. Empty or over-long whisper (`MAX_WHISPER_CHARS = 500`): rejected with a message, never published (Task 7).
3. A Jev, Haiku, or Contex error mid-turn: that turn is dropped and logged, the worker survives (Task 6).
4. A mind subscription that expired or was purged: re-created transparently, the listener keeps working (Task 2).
5. A villager alone in a place, or no groups visible: Choices with zero options are never sent, only `ignore` and the actions that still make sense (Task 3).

## File Structure

```
examples/rumor_mill/
  __init__.py  __main__.py  requirements.txt  README.md  compose.sweep.yaml
  config.py       Config, env overrides, derived timings
  models.py       Villager, Utterance, Group
  town.py         Town: villagers, places, groups, presence, departures
  lineage.py      Lineage (utterance -> root), Ids
  contex_client.py ContexClient, Match, Bundle, new_matches, SubscriptionGone
  mind.py         Mind: one villager's subscription, poll/diff, resubscribe
  actions.py      action/style constants, availability rules
  decide.py       TurnView, Decision, decide() via Jev
  writer.py       Writer: Haiku speech and group descriptions
  town_gen.py     villager generation, relationship wiring, seed cache, newcomers
  effects.py      act(): turn a Decision into deliveries and Town changes
  engine.py       Engine: workers, cooldown, renewal, day clock, Hub
  feed.py         Feed: player-visible lines
  player.py       Player.whisper, shrug timers
  cli.py          parse_command, repl
  population.py   Replenisher
  goals.py        Goal, DECK, deal()
  judge.py        Judge
  tests/          one test module per module above
```

---

### Task 1: Scaffold, config, town model, lineage

**Files:**
- Create: `examples/rumor_mill/{__init__,config,models,town,lineage}.py`, `examples/rumor_mill/requirements.txt`, `examples/rumor_mill/tests/{__init__,test_config,test_town,test_lineage}.py`
- Modify: `.github/workflows/ci.yml` (connectors job: install `examples/rumor_mill/requirements.txt`, add `pytest examples/rumor_mill` to the "Run connector tests" step)

**Interfaces:**
- Produces:
  - `Config` (frozen dataclass) fields: `contex_url="http://localhost:8001/mcp"`, `session="default"`, `day_seconds=180.0`, `memory_days=2.0`, `cooldown_seconds=15.0`, `shrug_seconds=20.0`, `renew_seconds=1200.0`, `population=100`, `arrival_delay_seconds=60.0`, `judge_every_days=1.0`, `judge_concurrency=10`, `turn_concurrency=20`, `top_k=5`, `threshold: float | None = None`, `haiku_model`, `seed_path="rumor_mill_seed.json"`; `Config.from_env()` reads `RUMOR_MILL_<FIELD>` / `CONTEX_MCP_URL`; property `memory_seconds -> int`.
  - `Villager(id, name, occupation, personality, grudge, needs: list[str], relationships: dict[str, str])`; `Utterance(id, speaker, speaker_name, place, text, parent: str | None)` with `to_data(day: int) -> dict` (keys `id, parent, speaker, place, day`, and `text` as one natural-language sentence such as `"Bert said at the tavern: ..."`, since Contex embeds the whole payload); `Group(id, name, creed, founder, members: set[str])`.
  - `PLACES = ("square", "tavern", "market", "well", "mill", "chapel", "gate")`, `START_PLACE = "square"`, `GATE = "gate"`, `PLAYER_ID = "newcomer"`.
  - `Town` methods: `add(v, place=START_PLACE)`, `remove(vid) -> Villager` (drops group memberships, appends to `departed`), `get(vid)`, `place_of(vid)`, `move(vid, place)` (ValueError on unknown place), `present(place, exclude=None) -> list[Villager]`, `find(name) -> list[Villager]` (case-insensitive; exact match wins over substring), `alive_count`, `found_group(founder_id, name, creed) -> Group` (id `g-<n>`, founder is a member), `join(vid, gid)`, `leave(vid, gid)`, `member_groups(vid)`, `visible_groups(vid)` (groups the villager is not in that have a member in the villager's place), `departed: list[Villager]`.
  - `Lineage.record(uid, parent, player=False)`, `Lineage.root(uid) -> str`, `Lineage.is_player_rooted(uid) -> bool`; `Ids.next(prefix) -> str` (`"u-1"`, `"w-1"`, monotonic per prefix).

- [ ] **Step 1: Write the failing tests**
  - `test_config.py::test_memory_seconds` (`Config(day_seconds=120, memory_days=2).memory_seconds == 240`), `::test_from_env_overrides` (`RUMOR_MILL_POPULATION=7` -> 7; `CONTEX_MCP_URL` honored).
  - `test_town.py`: `test_present_excludes_given_id`, `test_find_exact_beats_substring` (names "Bert" and "Bertha": `find("bert")` returns only Bert), `test_remove_records_departure_and_drops_membership`, `test_visible_groups_needs_member_in_same_place`, `test_found_group_makes_founder_member`, `test_move_rejects_unknown_place`.
  - `test_lineage.py::test_root_follows_parents` (w-1 <- u-1 <- u-2: `root("u-2") == "w-1"`), `::test_player_rooted_only_when_root_is_player`, `::test_ids_are_monotonic_per_prefix`.
- [ ] **Step 2: Run** `pytest examples/rumor_mill -v` — Expected: FAIL, modules missing.
- [ ] **Step 3: Implement** the modules above. `requirements.txt`: `mcp>=2,<3`, `typesafe-sdk`, `anthropic`.
- [ ] **Step 4: Run** `pytest examples/rumor_mill -v && ruff check .` — Expected: PASS. Confirm the CI edit by reading the workflow diff.
- [ ] **Step 5: Commit** `Add the Rumor Mill town model and config`

---

### Task 2: Contex client and villager minds

**Files:**
- Create: `examples/rumor_mill/{contex_client,mind}.py`, `examples/rumor_mill/compose.sweep.yaml`, `tests/{test_contex_client,test_mind,test_contex_integration}.py`
- Modify: `.github/workflows/ci.yml` (docker job: after "Boot the image", set up Python 3.12 as the connectors job does, install `examples/rumor_mill/requirements.txt` and pytest/pytest-asyncio, run `RUMOR_MILL_CONTEX_URL=http://localhost:8001/mcp pytest examples/rumor_mill/tests/test_contex_integration.py -v`)

**Interfaces:**
- Consumes: `Villager`, `Config` (Task 1).
- Produces:
  - `Match(data_key: str, data: dict)` (frozen); `Bundle = dict[str, list[Match]]`; `SubscriptionGone(Exception)`.
  - `ContexClient(url)`: `async with`; `publish(project_id, data_key, data) -> None`; `subscribe(project_id, needs, *, max_age_seconds, top_k, threshold=None) -> str` (resource URI); `read(uri) -> Bundle` (raises `SubscriptionGone` on the "Subscription expired" resource error); `query(project_id, query, top_k=3) -> list[Match]`; `listen(uri) -> AsyncIterator[None]` (own MCP session, one yield per push, returns when the stream closes). Calls share one session guarded by a semaphore.
  - `new_matches(seen: set[str], bundle: Bundle) -> list[Match]` (unseen `data_key`s, deduplicated across needs, bundle order).
  - `mind_project(session, villager_id) -> str` = `rumor-mill:<session>:mind:<id>`; `group_project(session, gid)` = `rumor-mill:<session>:group:<gid>`.
  - `Mind(contex, villager, config)`: `await open()`; `await hear(key, data)`; `await poll() -> list[Match]` (reads; on `SubscriptionGone` re-subscribes with the same arguments and re-reads; returns matches whose keys were not in the previous poll; then sets `seen` to the current bundle's keys, so forgotten items count as new if they return); `pushes() -> AsyncIterator[None]` (re-opens the listen stream after a 1s pause if it closes, and after a resubscribe).
  - `compose.sweep.yaml`: compose override setting `RECONCILE_SWEEP_SECONDS=30` on the `contex` service (used as `docker compose -f docker-compose.yml -f examples/rumor_mill/compose.sweep.yaml up`).

- [ ] **Step 1: Write the failing tests**
  - `test_contex_client.py::test_new_matches_dedupes_across_needs_and_skips_seen`.
  - `test_mind.py` with a stub client: `test_poll_returns_only_new_matches`, `test_poll_resubscribes_after_subscription_gone` (stub `read` raises `SubscriptionGone` once; `subscribe` called twice with equal arguments; poll still returns the matches), `test_forgotten_then_returned_item_is_new_again`, `test_subscribe_passes_memory_window` (`max_age_seconds == config.memory_seconds`).
  - `test_contex_integration.py` (skipped unless `RUMOR_MILL_CONTEX_URL` is set): `test_publish_subscribe_read_matches_by_meaning` (need "anything about my bakery", publish "the oven at the baker's shop is cold" under another key; the match appears), `test_push_arrives_on_publish` (`listen` yields within 30s after a second publish), `test_mind_projects_are_isolated` (an item published to mind A never appears in B's bundle).
- [ ] **Step 2: Run** `pytest examples/rumor_mill -v` — Expected: unit tests FAIL (modules missing); integration skipped.
- [ ] **Step 3: Implement** the modules. Use `Client` and `agent.listen(resource_subscriptions=[uri])` as in `examples/mcp_watch.py`; parse tool results with `json.loads(result.content[0].text)`.
- [ ] **Step 4: Run** unit tests, then the integration tests against a local server (`RUMOR_MILL_CONTEX_URL=http://127.0.0.1:8021/mcp pytest examples/rumor_mill/tests/test_contex_integration.py -v`; see the local test DB setup in `CONTRIBUTING.md`) and `ruff check .` — Expected: PASS.
- [ ] **Step 5: Commit** `Add the Contex client and per-villager minds`

---

### Task 3: Actions and Jev decisions

**Files:**
- Create: `examples/rumor_mill/{actions,decide}.py`, `tests/test_decide.py`

**Interfaces:**
- Consumes: `Villager`, `Group`, `Town` (Task 1); `Match` (Task 2).
- Produces:
  - `actions.py`: constants `IGNORE, RETELL, DENY, CONFRONT, SPEAK, MOVE, FOUND_GROUP, JOIN_GROUP, LEAVE_GROUP, PREACH, LEAVE_TOWN`; `ACTIONS: dict[str, str]` (one-line descriptions); `STYLES: dict[str, str]` (`faithful, exaggerated, shift_blame, add_detail, soften, mock`); `TARGET_KIND: dict[str, str]` mapping `RETELL/DENY/CONFRONT -> "villager"`, `MOVE -> "place"`, `JOIN_GROUP/LEAVE_GROUP/PREACH -> "group"`; `available_actions(view) -> list[str]`.
  - `decide.py`: `TurnView(villager, place, present: list[Villager], member_groups: list[Group], visible_groups: list[Group], new: list[Match], recent: list[Match])`; `Decision(action, source: Match | None, target: str | None, style: str | None)` (frozen); `async decide(jev, view) -> Decision`, where `jev` is anything with `async system_one(state, questions)` (the real `AsyncTypeSafeClient`).
- Rules (my calls, from the spec): one `system_one` request with every applicable question. Questions: `action` Choice over `available_actions`; `source` Choice keyed by `data_key` only when `len(view.new) > 1` (one new item is the source; none means `source=None`); `villager_target` over `view.present` (cap 255); `place_target` over `PLACES` minus current; `group_target` over member groups for LEAVE/PREACH and visible groups for JOIN (ask both as separate questions); `style` Choice. Code consumes only the answers the chosen action needs. `available_actions`: `RETELL/DENY/CONFRONT/SPEAK` need someone else present; `JOIN_GROUP` needs `visible_groups`; `LEAVE_GROUP/PREACH` need `member_groups`; `IGNORE, MOVE, FOUND_GROUP, LEAVE_TOWN` always.
- State sent to Jev: villager profile (name, occupation, personality, grudge, relationship text), current place, names of those present, new memories as text, up to 10 recent memories, group names.

- [ ] **Step 1: Write the failing tests** (stub `jev` returning `SimpleNamespace(choices={id: SimpleNamespace(choice=...)})` and recording the questions it was sent):
  - `test_alone_in_place_excludes_social_actions_and_target_question` (`present=[]`: `retell/deny/confront/speak` not in the `action` criteria; no `villager_target` question) — Review Focus 5.
  - `test_no_groups_omits_group_questions_and_actions`.
  - `test_single_new_item_skips_source_question_and_is_the_source`.
  - `test_target_options_capped_at_255` (300 present villagers: `len(criteria) == 255`).
  - `test_ignore_returns_empty_decision` (`Decision(IGNORE, None, None, None)`).
  - `test_retell_maps_chosen_target_and_style`.
- [ ] **Step 2: Run** `pytest examples/rumor_mill/tests/test_decide.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement** both modules. Option descriptions are what the model sees; give each action and style one clear sentence, and add `not_for` notes only where two options are easy to confuse (retell vs speak, deny vs confront).
- [ ] **Step 4: Run** the tests and `ruff check .` — Expected: PASS.
- [ ] **Step 5: Commit** `Add the villager action set and Jev decisions`

---

### Task 4: Haiku writer and town generation

**Files:**
- Create: `examples/rumor_mill/{writer,town_gen}.py`, `tests/{test_writer,test_town_gen}.py`

**Interfaces:**
- Consumes: `Villager`, `Group`, `Config`; `Decision`.
- Produces:
  - `Writer(client, model)` where `client` is an `anthropic.AsyncAnthropic`: `async complete(system, user, max_tokens=400) -> str`; `async say(villager, decision, source_text: str | None, audience: str) -> str` (one to three sentences in the villager's voice, applying `decision.style`); `async describe_group(villager, inspiration: list[str]) -> tuple[str, str]` (name, creed).
  - `town_gen.py`: `async generate_villagers(writer, count, rng) -> list[Villager]` (batches of 10; Haiku returns JSON: name, occupation, personality, grudge, 3-5 plain-English needs written in the villager's voice about their own concerns; ids `v001`...); `wire_relationships(villagers, rng) -> None` (2-4 per villager from `spouse, rival, friend, debtor/creditor`, reciprocal, never exceeding 4); `async load_or_generate(path, writer, count, rng) -> list[Villager]` (JSON seed cache); `async generate_newcomer(writer, existing, rng) -> Villager` (fresh id, relationships to one or two existing villagers, no reciprocal edit).
  - JSON from Haiku may arrive fenced or with prose around it; parse by extracting the first JSON array or object.

- [ ] **Step 1: Write the failing tests** (stub client returning canned text):
  - `test_say_includes_style_and_audience_in_prompt`.
  - `test_describe_group_parses_name_and_creed`.
  - `test_generate_villagers_parses_fenced_json_and_assigns_ids`.
  - `test_wire_relationships_bounds_and_reciprocity` (30 villagers, seeded rng: every count is 2-4, every referenced id exists, every edge has its reverse).
  - `test_load_or_generate_reuses_seed_file` (second call does not invoke the writer).
  - `test_newcomer_relates_to_one_or_two_existing`.
  - `test_every_villager_has_three_to_five_needs`.
- [ ] **Step 2: Run** `pytest examples/rumor_mill/tests/test_writer.py examples/rumor_mill/tests/test_town_gen.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement** both modules. Prompt for needs must ask for interests specific to the villager (their trade, family, fears, hobbies) so whispers match some villagers and not others.
- [ ] **Step 4: Run** the tests and `ruff check .` — Expected: PASS.
- [ ] **Step 5: Commit** `Add the Haiku writer and village generation`

---

### Task 5: Effects

**Files:**
- Create: `examples/rumor_mill/effects.py`, `tests/test_effects.py`

**Interfaces:**
- Consumes: `Decision`, `Town`, `Utterance`, `Lineage`, `Ids`, `Writer`.
- Produces:
  - `Hub` (Protocol): `async deliver(utterance: Utterance, listener_ids: list[str]) -> None`; `async publish_group(group: Group) -> None`.
  - `async act(decision, villager, *, town, writer, hub, lineage, ids, day: int) -> None`.
- Behavior per action (the utterance `parent` is `decision.source.data["id"]` when there is a source; every utterance is recorded in `lineage` before delivery):
  - `IGNORE`: nothing. `MOVE`: `town.move`, no delivery.
  - `RETELL/DENY/CONFRONT`: Haiku text, delivered to `[target]` only if the target is still in the speaker's place.
  - `SPEAK`: delivered to everyone else present.
  - `FOUND_GROUP`: `describe_group` from the new items, `town.found_group`, `hub.publish_group`, announcement delivered to those present.
  - `JOIN_GROUP`: `town.join`, announcement to those present. `LEAVE_GROUP`: `town.leave`, no delivery.
  - `PREACH`: delivered to all other members of the target group, wherever they are.
  - `LEAVE_TOWN`: `town.remove`, departure line delivered to everyone in `square`.

- [ ] **Step 1: Write the failing tests** (stub writer, recording hub, real `Town`):
  - `test_retell_delivers_to_target_with_parent_lineage` (parent equals the source's id; `lineage.root` of the new utterance equals the source's root).
  - `test_retell_to_target_who_moved_away_delivers_nothing`.
  - `test_speak_reaches_everyone_present_but_speaker`.
  - `test_speaker_never_receives_own_utterance`.
  - `test_found_group_adds_group_and_publishes_record`.
  - `test_preach_reaches_members_in_other_places`.
  - `test_leave_town_removes_villager_and_announces_in_square`.
- [ ] **Step 2: Run** `pytest examples/rumor_mill/tests/test_effects.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement** `act` and `Hub`.
- [ ] **Step 4: Run** the tests and `ruff check .` — Expected: PASS.
- [ ] **Step 5: Commit** `Turn villager decisions into town changes and speech`

---

### Task 6: Engine

**Files:**
- Create: `examples/rumor_mill/engine.py`, `tests/test_engine.py`

**Interfaces:**
- Consumes: everything above. `Mind` is injected through a factory so tests can substitute a fake: `mind_factory(villager) -> Mind`.
- Produces `Engine(config, town, contex, jev, writer, lineage, ids, feed, mind_factory=...)` implementing `Hub`, plus:
  - `async add_villager(v, place=START_PLACE)`: `town.add`, `mind.open()`, start the worker.
  - `async remove_villager(vid)`: cancel the worker, drop the mind (the subscription is left to expire; MCP cannot delete it).
  - `async run()` / `async stop()`: day clock (`day` property = `int(elapsed / day_seconds)`), lease renewal (`mind.poll` result discarded every `renew_seconds` for idle minds), clean cancellation of every task and listen session on stop.
  - `on_heard: Callable[[str, list[Match]], None] | None` called with `(villager_id, new_matches)` after every poll that returned matches.
  - `deliver(utterance, listener_ids)`: skips ids no longer in town, `mind.hear(f"utt:{utterance.id}", utterance.to_data(day))`, then `feed.on_delivery(utterance, listener_ids)`.
  - `publish_group(group)`: publishes `{id, name, creed, founder}` to `group_project(...)` under key `group:<id>`.
- Worker loop per villager (my calls): a push only sets an event; the loop waits for the event, waits out the remaining cooldown (`config.cooldown_seconds` since its last turn), then `poll()`. No new matches means no turn. Otherwise build a `TurnView`, take a `turn_concurrency` semaphore, `decide`, `act`. After `act`, if the villager left town the worker ends. Any exception from `decide`/`act`/`poll` is logged with `logging` and the loop continues.

- [ ] **Step 1: Write the failing tests** (fake mind driven by an `asyncio.Queue`, stub `decide`/`act` injected, `cooldown_seconds=0.05`):
  - `test_push_with_no_new_matches_makes_no_turn`.
  - `test_pushes_during_cooldown_coalesce_into_one_turn`.
  - `test_turn_exception_is_logged_and_worker_survives` (first `decide` raises, second push still produces a turn) — Review Focus 3.
  - `test_departed_villager_worker_ends`.
  - `test_deliver_skips_departed_listeners_and_notifies_feed`.
  - `test_on_heard_called_with_new_matches`.
  - `test_stop_leaves_no_pending_tasks`.
- [ ] **Step 2: Run** `pytest examples/rumor_mill/tests/test_engine.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement** `Engine`. `decide` and `act` are module-level imports in `engine.py` that tests patch.
- [ ] **Step 4: Run** the tests and `ruff check .` — Expected: PASS.
- [ ] **Step 5: Commit** `Add the engine that wakes villagers on push`

---

### Task 7: Feed, whispers, and the REPL (playable)

**Files:**
- Create: `examples/rumor_mill/{feed,player,cli,__main__}.py`, `tests/{test_feed,test_player,test_cli}.py`

**Interfaces:**
- Consumes: `Engine`, `Town`, `Lineage`, `Ids`, `Config`, `generate_villagers`/`load_or_generate`, `Writer`.
- Produces:
  - `Feed(lineage, town, out: Callable[[str], None] = print)`: `say(line)`; `on_delivery(utterance, listener_ids)` prints only when `lineage.is_player_rooted(utterance.id)`. One line per delivery batch: a single listener reads `Bert told Marta: "..."`; several read `Bert said at the tavern (heard by 4): "..."`. The whisper itself is never echoed as a hop.
  - `MAX_WHISPER_CHARS = 500`. `Player(engine, town, lineage, ids, feed, config)`: `async whisper(name, text) -> None`: validates (empty, over-long, no match, several matches listing the candidates, departed villager `X has left town`), records a `w-<n>` utterance as `player=True` in lineage, delivers it to the target (speaker `PLAYER_ID`, `speaker_name="The newcomer"`, text rendered as `The newcomer whispered to you: ...`), and starts a `shrug_seconds` timer. `note_heard(villager_id, matches)` cancels the timer when a match's `data["id"]` is that whisper; if the timer fires, the feed says `<Name> shrugs it off.`. Wire `engine.on_heard = player.note_heard`.
  - `cli.py`: `parse_command(line) -> Command` (pure). Forms: `<name>: <text>` and `whisper <name>: <text>`, `villagers`, `inspect <name>`, `goals` (prints "no goals yet" until Task 9), `quit`; anything else yields an `unknown` command. `async repl(...)` reads stdin through `asyncio.to_thread`, so feed lines print while the player types. `inspect` shows occupation, personality, grudge, and relationships by name, never needs.
  - `__main__.py`: build `Config`, clients, `Writer`, villagers (`load_or_generate`), engine, player; add all villagers spread over `PLACES` randomly (excluding `gate`); run the REPL; on `quit` or Ctrl-C call `engine.stop()` and exit without a traceback.

- [ ] **Step 1: Write the failing tests**:
  - `test_feed.py::test_only_player_rooted_deliveries_print`, `::test_single_and_group_line_formats`.
  - `test_player.py`: `test_unknown_name_says_so_and_publishes_nothing`, `test_ambiguous_name_lists_candidates`, `test_departed_villager_message`, `test_empty_and_overlong_whisper_rejected` (Review Focus 1 and 2), `test_whisper_is_delivered_and_rooted_in_lineage`, `test_unheard_whisper_triggers_shrug_line` (`shrug_seconds=0.05`), `test_heard_whisper_cancels_shrug`.
  - `test_cli.py::test_parse_command_forms` (all forms above, including a name containing a colon-free space such as `Old Bert: hello`).
- [ ] **Step 2: Run** `pytest examples/rumor_mill/tests -v` — Expected: new tests FAIL.
- [ ] **Step 3: Implement** the modules.
- [ ] **Step 4: Run** the tests and `ruff check .` — Expected: PASS. Then play a short session against a local Contex with real keys and confirm: a whisper to a villager whose interests match wakes them and a hop line appears; one that does not match prints the shrug line; `quit` exits cleanly.
- [ ] **Step 5: Commit** `Add the chat feed, whispers, and REPL`

---

### Task 8: Population

**Files:**
- Create: `examples/rumor_mill/population.py`, `tests/test_population.py`
- Modify: `examples/rumor_mill/engine.py` (expose `departures: asyncio.Queue[str]`, fed whenever a villager leaves town), `examples/rumor_mill/__main__.py` (start the replenisher)

**Interfaces:**
- Consumes: `Engine`, `Writer`, `generate_newcomer`, `GATE`, `Config.population`, `Config.arrival_delay_seconds`.
- Produces `Replenisher(engine, town, writer, config, rng)`: `async run()` waits on `engine.departures`; after `arrival_delay_seconds`, while `town.alive_count < config.population`, generates a newcomer, `engine.add_villager(newcomer, GATE)`, and delivers an arrival line (`<Name>, a <occupation>, has arrived at the gate.`) to everyone in the square.

- [ ] **Step 1: Write the failing tests** (stub engine/writer, `arrival_delay_seconds=0.05`):
  - `test_departure_triggers_one_arrival_at_gate`.
  - `test_count_returns_to_target_after_several_departures`.
  - `test_no_arrival_when_population_already_full`.
  - `test_arrival_line_reaches_the_square`.
- [ ] **Step 2: Run** `pytest examples/rumor_mill/tests/test_population.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement** `Replenisher`; put departures on the queue from `Engine.remove_villager`.
- [ ] **Step 4: Run** `pytest examples/rumor_mill -v && ruff check .` — Expected: PASS.
- [ ] **Step 5: Commit** `Replenish the village when villagers leave`

---

### Task 9: Goals and the judge

**Files:**
- Create: `examples/rumor_mill/{goals,judge}.py`, `tests/{test_goals,test_judge}.py`
- Modify: `examples/rumor_mill/feed.py` (`on_progress(goal, fraction)`), `examples/rumor_mill/cli.py` (`goals` command prints each goal with its percent), `examples/rumor_mill/__main__.py` (deal three goals, start the judge)

**Interfaces:**
- Consumes: `ContexClient.query`, `mind_project`, `group_project`, `Town`, Jev, `Config.judge_every_days`, `Config.judge_concurrency`.
- Produces:
  - `Goal(id, text, kind: "belief" | "state", evidence: tuple[str, ...] = (), believer_question: str = "", target_fraction: float = 0.0, creed_question: str | None = None, min_group_members: int = 0, state_check: Callable[[Town], float] | None = None, hold_checks: int = 3)`.
  - `DECK: tuple[Goal, ...]` of about 20 hand-written goals. Each is measurable town-wide (`belief`) or from `Town` state (`state`, e.g. `departed` families by last name); no goals about a specific pair. Include the religion goal (belief fraction 0.25, creed question, `min_group_members=8`), the mayor's cat, and "three families leave town".
  - `deal(rng, n=3) -> list[Goal]`.
  - `Judge(contex, jev, town, config, feed)`: `async check(goal) -> float` (progress in [0,1]); `async run(goals)` loops every `judge_every_days * day_seconds`, prints a feed line whenever a goal's whole-percent progress changes, marks a goal met after `hold_checks` consecutive checks at progress 1.0, and returns when all are met.
- Belief check (spec): for each living villager, `contex.query(mind_project(...), goal.evidence[0], top_k=3)`; villagers with no matches are non-believers without a Jev call; otherwise Jev Noul with the villager's profile and those memories plus `believer_question`; a believer has probability above 0.5. Progress is `min(1, believers / alive / target_fraction)`. If `creed_question` is set, a group counts only if `contex.query(group_project(...), evidence[0], top_k=1)` returns its record, Jev Noul on that record says yes, and it has at least `min_group_members` members; with no qualifying group, progress is capped below 1.0.

- [ ] **Step 1: Write the failing tests** (stub contex and jev):
  - `test_goals.py::test_deck_is_well_formed` (unique ids; at least 15 goals; at least 5 `state` goals; belief goals have evidence and a question), `::test_deal_returns_n_distinct_goals`.
  - `test_judge.py`: `test_villagers_without_matches_cost_no_jev_calls`, `test_progress_is_believer_fraction_over_target`, `test_goal_met_only_after_hold_checks_consecutive_checks` (a dip resets the count), `test_creed_goal_capped_without_qualifying_group`, `test_state_goal_uses_town_departures` (three departed villagers with distinct last names -> 1.0), `test_progress_line_only_when_percent_changes`.
- [ ] **Step 2: Run** `pytest examples/rumor_mill/tests/test_goals.py examples/rumor_mill/tests/test_judge.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement** the modules and the feed/CLI/main wiring.
- [ ] **Step 4: Run** `pytest examples/rumor_mill -v && ruff check .` — Expected: PASS.
- [ ] **Step 5: Commit** `Add goals and the judge`

---

### Task 10: README and an end-to-end session

**Files:**
- Create: `examples/rumor_mill/README.md`
- Modify: `examples/README.md` (add a Rumor Mill section in the same style as its siblings)

**Interfaces:** none.

- [ ] **Step 1: Write the README**: what the game is in two sentences, how it uses Contex (the table from the spec's "Contex surface exercised"), setup (`docker compose -f docker-compose.yml -f examples/rumor_mill/compose.sweep.yaml up`, `pip install -r examples/rumor_mill/requirements.txt`, `ANTHROPIC_API_KEY`, `TYPESAFE_API_KEY`, `python -m examples.rumor_mill`), commands, the tuning knobs from `Config` with their env names. Follow the repo's writing style: plain, no filler, no marketing phrasing.
- [ ] **Step 2: Play a full session** against a local Contex with real keys and record what happened in the PR description: at least one whisper that lands and one that shrugs; a rumor that hops three or more times and visibly changes wording; a group founded; a villager leaving and a newcomer arriving; a goal's percent moving. Tune `needs` prompts, `threshold`, `cooldown_seconds`, and `memory_days` until sessions are not dead, and commit those tuning changes into the owning modules.
- [ ] **Step 3: Run** `pytest examples/rumor_mill -v && ruff check .` — Expected: PASS.
- [ ] **Step 4: Commit** `Document the Rumor Mill example`
- [ ] **Step 5: Push the branch and open the PR** against `main`, linking the spec. Do not merge.

---

## Self-Review

- **Spec coverage:** minds as projects, one subscription each, no speaker self-publish, diff-on-push turns (Tasks 2, 5, 6); places, groups, presence rules (1, 5); Jev decisions with the 255 cap and speculative fan-out (3); Haiku speech and generation (4); whispers only landing on interests, shrug feed line, lineage-based hop lines, command set, inspect hides needs (7); goals as hand-written judge specs, consecutive-check hold, no player-typed goals (9); departures and arrivals at the gate (8); sweep override (2); docs (10). The spec's "one self-check for lineage tracking and one for goal threshold logic" are `test_root_follows_parents` and `test_goal_met_only_after_hold_checks_consecutive_checks`.
- **Type consistency:** `Match`, `Decision`, `TurnView`, `Hub`, `Utterance`, `Lineage` names and signatures are identical wherever they recur; `PLAYER_ID`/`GATE`/`START_PLACE` come from Task 1.
- **Proportion:** the plan records interfaces, rules, and test names; function bodies are left to the implementer.
