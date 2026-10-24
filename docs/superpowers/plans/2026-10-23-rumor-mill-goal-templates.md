# Goal Templates Filled by Jev — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every goal, including the world goal, comes from a template that Jev fills from this village's people, circles and a per-village pool of accusations and claims. This adds action goals and removes the fixed deck and the per-hand Haiku writer.

**Architecture:** New `pool.py` loads or writes `{seed}.goals.json`, with believability scored once per entry. `GoalMaker` is rewritten around templates:
- code draws the template and the subject;
- two Jev Choice requests fill the rest;
- a uniform-random fill serves the first hand and any Jev failure.

Action goals are a new `Goal.kind="action"`. The engine reports each villager action to the judge, and the judge marks matching goals met.

**Tech Stack:** Python 3.12, asyncio, TypeSafe Jev (`typesafe_sdk` Choice), Anthropic Haiku (pool only), Contex MCP, pytest with `examples/rumor_mill/tests/helpers.py` stubs.

**Spec:** `docs/superpowers/specs/2026-10-23-rumor-mill-goal-templates-design.md`. It builds on `2026-10-23-rumor-mill-targeted-goals-design.md`.

## Global Constraints

- Branch `feat/rumor-mill-tiles` (PR #477). No new PR, no worktree, no push from tasks.
- Commits are CI-green and end with:
  ```
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01YP7uhbQm8avkDcwdQ9b87E
  ```
- Tests: `/private/tmp/claude-501/-Users-robmiller-Projects-contex/dc5af79c-d8f0-4711-83c2-3ccea8efac2d/scratchpad/venv-web/bin/python -m pytest examples/rumor_mill/tests -q`. Lint: `ruff check examples/rumor_mill`.
- Style:
  - no inline imports and no meta comments;
  - concise docstrings;
  - player-facing text never leaks internals;
  - the page uses `el()` and textContent only.
- Pool limits:
  - entries under 80 characters, with no second-person word (`\byou(rs?|rself)?\b`) and no villager first name;
  - about 40 accusations and 15 claims.
- Templates and their text are exactly as in the spec's table. The share words come from `BANDS` in goals.py.
- Circles:
  - founded groups with 3 or more members in town, labelled "the {group name}";
  - place regulars for every routine place except the square and the gate, labelled "the {place} regulars";
  - 3 or more members besides the subject.
- Action goals:
  - "Drive out" and "Exodus" are always `long_shot=True`;
  - Exodus n is 2 or 3;
  - `hold_checks=1`.
- The world goal is a village-wide pool claim at `WORLD_FRACTION`, not held by any player now.

## Review Focus

1. **A village too small or quiet for some templates:** no groups, so no circle from groups and no "Join". The hand must still come out at size 3 from the templates that can be filled. Test: `test_hand_skips_templates_that_cannot_be_filled` (Task 2).
2. **Jev fails or times out mid-fill.** That slot falls back to a uniform pick. The swap and the hand still succeed. Test: `test_jev_failure_falls_back_to_uniform_fill` (Task 2).
3. **A pool file that is corrupt or hand-edited** (bad JSON, missing bands, entries with "you" or a villager name). Bad entries are dropped and missing bands are scored, with no crash. Test: `test_pool_cleans_and_rescores_a_bad_file` (Task 1).
4. **The subject of a "Drive out" goal leaves.** That must meet the goal, not void it. Test: `test_drive_out_met_not_voided_when_subject_leaves` (Task 3).
5. **An action with no player behind it**, such as a routine walk or village talk, meets nothing. Test: `test_unattributed_action_meets_nothing` (Task 3).

---

### Task 1: The pool

**Files:** Create `examples/rumor_mill/pool.py`. Modify `writer.py` (`Writer.goal_pool`), `__main__.py`, `.gitignore` (`rumor_mill_seed.json.goals.json`). Test: `tests/test_pool.py`, `tests/test_writer.py`.

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class Entry: text: str; band: int`
  - `@dataclass(frozen=True) class Pool: accusations: tuple[Entry, ...]; claims: tuple[Entry, ...]`
  - `DEFAULT_ACCUSATIONS: tuple[str, ...]`, about 20 general ones such as "is stealing from the tithe box".
  - `DEFAULT_CLAIMS: tuple[str, ...]`: the old deck's belief claims, moved here verbatim.
  - `def clean(texts: Iterable[str], names: Collection[str]) -> list[str]` drops anything over 80 characters, anything with a second-person word, anything naming a villager first name (whole word, case-insensitive), and duplicates.
  - `async def load_pool(path: str, writer, believability, villagers: list[Villager]) -> Pool`:
    1. Read `path`. If it is missing or unreadable, ask `writer.goal_pool(villagers)` for `{"accusations": [...], "claims": [...]}`. If that fails, use the defaults.
    2. Clean the entries.
    3. Score each entry without a band, accusations as `believability.band(f"someone {text}")` and claims as `believability.band(text)`.
    4. Write `{"accusations": [{"text", "band"}], "claims": [...]}` back to `path`. A write failure is logged and ignored.
- Produces `Writer.goal_pool(self, villagers: list[Villager]) -> dict`: one Haiku call with each villager's trade, place-free temper and grudge. It asks for about 40 accusations completing "{someone} …" and about 15 village-wide claims, following the spec's rules. It returns the parsed dict, or `{}` when the reply can't be parsed.
- `__main__.py` calls `pool = await load_pool(config.seed_path + ".goals.json", writer, Believability(jev), villagers)` right after jev is created, where `rebar_deck` is called now. Leave `rebar_deck` in place for now; Task 2 removes it.

- [ ] **Step 1: Write the failing tests.**
  - `test_clean_drops_long_second_person_named_and_duplicate`
  - `test_load_pool_writes_and_reuses_file`: the first call asks the writer and scores; the second call reads the file with no writer and no Jev calls.
  - `test_pool_cleans_and_rescores_a_bad_file`
  - `test_load_pool_falls_back_to_defaults_when_writer_fails`
  - `test_writer_goal_pool_parses_and_tolerates_junk`
- [ ] **Step 2: Run them; they should fail.** **Step 3: Implement.** **Step 4: Run the full suite and ruff; both should pass.** **Step 5: Commit** "Give each village a pool of accusations and claims for its goals".

### Task 2: Claim templates, the world goal, and removing the deck

**Files:** Rewrite `goal_maker.py`. Modify `goals.py`, `judge.py`, `__main__.py`, `config.py`, `writer.py` (remove `goals`), `web.py` and `cli.py` (only where `kind_of` or the deck is referenced), and tests (`test_goal_maker.py`, `test_judge.py`, `test_goals.py`, and others that build a deck).

**Interfaces:**
- Consumes the Task 1 `Pool`/`Entry`. Uses `routine.place(vid, phase)` and the `PHASES` from game.py for regulars, and `town.groups` for groups.
- Produces `GoalMaker(jev, pool: Pool, town: Town, routine, overhear, baselines, config, rng, ids)` with:
  - `def draw(self, player_id: str, taken: set[str], size: int = 3) -> list[Goal]`: a sync fill using uniform picks. It serves a player's first hand and replaces void goals.
  - `async def hand(self, player_id: str, taken: set[str], size: int = 3) -> list[Goal]`: the same draw, refined by Jev. It runs under `charging(player_id)`. Per goal it makes two Choice requests: (1) the accusation from that subject's untaken pool entries; (2) the mark or circle (and, from Task 3, the actor). If a request fails, that slot keeps its uniform pick. Before returning, generated claims go through `baselines.load(..., persist=False)` and `overhear.publish`.
  - `def world_goal(self, taken: set[str]) -> Goal`: a pool claim at `WORLD_FRACTION`, with id `world-<n>` and text "Two thirds of the village believes {claim}".
  - Templates in this task: personal, circle and village-wide. The template for each slot is drawn uniformly from those that can be filled.
  - The subject is drawn weighted toward villagers named in another villager's grudge text by first name, or who hold a "rival" tie. Weight 3, otherwise 1. A villager already a subject in this player's hand is never drawn.
  - A claim's `claim_text` is `"{subject first name} {accusation}"`, or the pool claim. Its `believer_question` is `"Does this villager believe {claim_text}?"`.
  - Bars: a circle or village-wide goal takes `BANDS[entry.band]`. A personal goal needs 1. `long_shot = entry.band == 0`.
  - Ids: `f"gen-{player_id}-{ids.next('goal')}"`, and `owner=player_id`.
  - `taken` holds lowercased claim texts. Taken accusations for the subject are removed from the options before choosing.
- Judge:
  - Takes `maker: GoalMaker`, which is now required, and drops `deal`, `rebar_deck`, `goals.DECK` and `goals.WORLD_CANDIDATES`.
  - `Judge.deal(player)` becomes `self._hand_to(player, self.maker.draw(player.id, taken))`.
  - `redeal` uses `await maker.hand(...)`.
  - `_replace_void` uses `maker.draw(player.id, taken, 1)[0]`.
  - `swap` uses `await maker.hand(player.id, taken, 1)` (any template), with the existing guards kept.
  - `_next_world` uses `maker.world_goal(taken)`.
  - `world` is built after the maker exists.
- `goals.py` removes `DECK`, `WORLD_CANDIDATES`, `deal`, `rebar_deck`, `kind_of`, the state-goal helpers and the creed fields. `NO_QUALIFYING_GROUP_CAP` goes from judge.py if it becomes unused. It keeps `Goal`, `BANDS`, `needed`, `goal_counts`, `claim`, `audience_of`, `void_reason`, `world_goal`, `Trophy`, `trophy_line` and `goal_targets`.
- Config removes `goal_writer_timeout_seconds`. Writer removes `goals` and `GOALS_SYSTEM`.
- `__main__.py` builds `GoalMaker` from the pool, needing `routine`, and passes it to `Judge`. `Overhear` publishes the world goal and generated goals only; there is no deck publish.

- [ ] **Step 1: Write the failing tests.**
  - `test_draw_fills_personal_circle_and_village_templates`: assert the template text exactly. For example `"Convince Agnes that Hob waters down the ale"`, and `"Get half of the tavern regulars to believe Hob waters down the ale"` with band 3.
  - `test_hand_skips_templates_that_cannot_be_filled`
  - `test_jev_failure_falls_back_to_uniform_fill`
  - `test_hand_uses_jev_choices`: a stub Jev picks a specific accusation and mark, and they appear.
  - `test_subject_weighting_prefers_grudge_targets`: seeded rng, counts over 200 draws.
  - `test_taken_accusations_not_offered`
  - `test_world_goal_from_pool_not_taken`
  - `test_regulars_exclude_square_and_gate_and_subject`
  - `test_swap_draws_any_template`
  - Judge tests updated to build a maker with the default pool and no Jev.
- [ ] **Step 2: Run them; they should fail.** **Step 3: Implement.** **Step 4: Run the full suite and ruff; both should pass.** **Step 5: Commit** in two parts: "Fill claim goals from templates with Jev" and "Draw the world goal from the pool and retire the deck".

### Task 3: Action goals

**Files:** Modify `goals.py`, `goal_maker.py`, `judge.py`, `engine.py` (report actions), `overhear.py` and `player.py` (skip action goals), `static/index.html` (only if the actor badge needs it), and `README.md` (goals section). Test: `tests/test_goal_maker.py`, `tests/test_judge.py`, `tests/test_game.py`.

**Interfaces:**
- `Goal` gains:
  - `action: str | None = None`, one of "confront", "leave_town", "found_group", "join_group" or "exodus";
  - `actor: str | None = None`;
  - `target: str | None = None`, the subject id or the group id;
  - `count: int = 1`.
  - Action goals have `kind="action"`, `audience=(actor,)` when there is an actor so the badges work, and `subject` set where the template has one.
- `GoalMaker` adds the action templates from the spec's table, with these texts:
  - "Get {actor first name} to confront {subject first name}"
  - "Drive {subject first name} out of town"
  - "Get someone to start a group against {subject first name}"
  - "Get {actor first name} to join the {group name}"
  - "Drive {n} villagers out of town"

  Jev's second request picks the actor: villagers in town other than the subject, and for join, not already members. When a full name is shared, use it as `_called` does today.
- `goal_targets(goal)` marks the actor for action goals.
- Produces `Judge.record_action(self, actor: str, action: str, target: str | None, player: str | None, creed: str | None = None) -> None`. It is sync and only marks state. Each unmet action goal held by `player` that matches is set to progress 1.0; Exodus increments its own count toward n. The run loop picks the change up through `hold_checks=1`. Matching:
  - confront: the actor and the target match;
  - leave_town: the actor is the goal's subject;
  - found_group: the creed names the subject's first name as a whole word;
  - join_group: the actor and the group match;
  - exodus: any leave_town.
- The engine calls `judge.record_action` after `act(...)` in `_turn`, for CONFRONT, LEAVE_TOWN, FOUND_GROUP and JOIN_GROUP. The player is `self._player_behind(decision.source)`. `decision.target` is the confront target or group id; for found_group, pass the new group's creed. The engine gets the judge through a constructor argument or a setter, matching how `engine.players` is wired. *(Pick the existing pattern.)*
- `Judge.check` for an action goal returns its recorded progress, with no Contex query. `_trace`, overhear publish, baselines and the whisper-to-mark check all skip goals whose `claim_text` is empty.
- `void_reason`:
  - A "leave_town" goal whose subject left is not void; the run loop sees it as met.
  - An action goal whose actor left is void.
  - A goal whose group no longer exists in town is void.

- [ ] **Step 1: Write the failing tests.**
  - `test_draw_fills_action_templates`, one case per template text.
  - `test_confront_met_when_actor_confronts_subject_from_player_rumor`
  - `test_unattributed_action_meets_nothing`
  - `test_drive_out_met_not_voided_when_subject_leaves`
  - `test_found_group_met_when_creed_names_subject`
  - `test_join_group_met`
  - `test_exodus_counts_to_n`
  - `test_action_goals_skip_evidence_and_trace`
  - `test_engine_reports_actions_to_judge`
- [ ] **Step 2: Run them; they should fail.** **Step 3: Implement.** **Step 4: Run the full suite and ruff; both should pass.** Browser-check on the stub (port 8791, never 8765) that an action goal card and its actor badge render. **Step 5: Commit** "Add action goals met by what villagers do because of you".
