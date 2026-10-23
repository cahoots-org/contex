# Targeted Goals and Not Tipping Your Hand — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Haiku writes personal and circle goals about real villagers. How believable a claim is sets its bar. Marks and subjects punish a player who tips their hand.

**Architecture:**
- A `Goal` gains an audience, a subject and an owner.
- The judge counts only the audience. It discounts marks who suspect the owner, and it traces the claim to the subject.
- A new `GoalMaker` writes, validates and scores generated goals, falling back to the deck.
- The player's whisper path adds the mark check.
- The engine carries out the subject's suspicion bump and denial.

**Tech Stack:** Python 3.12, asyncio, Contex MCP (via `ContexClient`), TypeSafe Jev (`typesafe_sdk` Noul and Score), Anthropic Haiku (via `Writer`), FastAPI page in `static/index.html`. Tests use pytest with the stubs in `examples/rumor_mill/tests/helpers.py`.

**Spec:** `docs/superpowers/specs/2026-10-23-rumor-mill-targeted-goals-design.md`

## Global Constraints

- All work is on branch `feat/rumor-mill-tiles` (PR #477). No new PR, no worktree.
- Each task is one or more CI-green commits ending with the trailers:
  ```
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01YP7uhbQm8avkDcwdQ9b87E
  ```
- Test command: `python -m pytest examples/rumor_mill/tests -q` from the repo root, using the scratchpad venv `/private/tmp/claude-501/-Users-robmiller-Projects-contex/dc5af79c-d8f0-4711-83c2-3ccea8efac2d/scratchpad/venv-web/bin/python`. Lint: `ruff check examples/rumor_mill`.
- Style:
  - no inline imports and no meta comments;
  - concise docstrings that say what a thing is, matching the surrounding code;
  - player-facing text never leaks internals: no ids, scores or prompts.
- Personal and circle goals are generated. Village-wide and world goals come from the deck.
- Circle audience: 3 or more villagers in town. Personal bar: 1.
- Believability bands, lowest first: fractions `(1/5, 1/4, 1/3, 1/2)` with words `("A fifth", "A quarter", "A third", "Half")`. Band 0 is **Long shot**.
- Subject bump by hops from the whisper: 1 → `suspicion_whisper_bump`, 2 → half, 3 → a quarter, 4 or more → none. Applied once per (subject, player, rumor root). Reason: "traced it to you".
- Mark bump: one extra `suspicion_whisper_bump`, reason "worked on them".
- A mark whose suspicion of the owner is at or above `warn_threshold` is not counted as a believer for that owner's goals.
- Denial: once per goal, when a bump carries the subject's suspicion of the owner past `warn_threshold`.
- New config: `goal_sample_size: int = 15`, `goal_writer_timeout_seconds: float = 10.0`.
- Per-player privacy: marks, subject, void lines and denials go only to the owning player's feed and state.

## Review Focus

1. **A generated goal's evidence has no baseline.** `Judge._believes` calls `baselines.threshold(evidence)`, which raises KeyError for an unseen text. GoalMaker must `await baselines.load([evidence])` before the goal is dealt. Test: `test_hand_loads_baselines_before_dealing` (Task 3).
2. **Two players hold generated goals with clashing ids, or a deck goal and a generated goal share an id.** Generated ids are `gen-<player id>-<n>`, where n is from `Ids`. Test: `test_generated_ids_unique_across_players` (Task 3).
3. **Haiku returns junk.** That covers bad JSON, an unknown name, the mark equal to the subject, a circle under 3, or a claim that doesn't name the subject. Each bad goal is replaced from the deck and the hand is still 3 goals. Test: `test_invalid_generated_goals_fall_back_to_deck` (Task 3).
4. **A mark or subject leaves mid-hand.** The goal is replaced, the player is told, and the judge never divides by zero. Test: `test_void_goal_replaced_when_mark_leaves` (Task 2).
5. **Overhear's evidence set grows forever on a hosted server.** Generated evidence is dropped from overhear when its goal leaves play. Test: `test_forget_drops_generated_evidence` (Task 3).

---

### Task 1: Goal fields, claim text and believability bands

**Files:**
- Modify: `examples/rumor_mill/goals.py`
- Create: `examples/rumor_mill/believability.py`
- Modify: `examples/rumor_mill/__main__.py` (score the deck at startup)
- Test: `examples/rumor_mill/tests/test_goals.py`, `examples/rumor_mill/tests/test_believability.py`

**Interfaces:**
- Produces, as new `Goal` fields (all defaulted, so existing constructors keep working):
  ```
  audience: tuple[str, ...] = ()     # villager ids that count; () = the whole village
  audience_label: str = ""           # "Agnes", "the farmers"
  subject: str | None = None         # villager id the claim is about
  owner: str | None = None           # player id, for generated goals
  claim_text: str = ""               # the claim in plain words
  long_shot: bool = False
  ```
- Produces `claim(goal) -> str | None`. It returns `goal.claim_text` when set; otherwise it parses as today.
- Produces `BANDS: tuple[tuple[float, str], ...] = ((1/5, "A fifth"), (1/4, "A quarter"), (1/3, "A third"), (1/2, "Half"))`.
- Produces `rebar(goal: Goal, band: int) -> Goal`. For a village-wide belief goal it returns the goal with:
  - `target_fraction` set to the band's fraction;
  - `text` = `f"{word} of the village believes {claim}"`;
  - `long_shot` = `band == 0`.
  The creed goal and state goals come back unchanged.
- Every deck belief goal gets `claim_text` set to the words after "believes" in its text.
- Produces `class Believability` in `believability.py`:
  - `__init__(self, jev)`;
  - `async band(self, claim: str) -> int`, cached per claim. It asks one Jev `Score` how believable the claim is to an ordinary villager, on 4 levels from far-fetched to plausible, and maps the score into 4 equal bands 0–3. On failure it returns 2 and does not cache.
- `__main__.py`: after baselines load, the deck's village-wide belief goals are replaced by `rebar(goal, await believability.band(claim(goal)))`. Export a module-level `rebar_deck(believability) -> None` from goals.py that rewrites the module's `DECK` and `WORLD_CANDIDATES` tuples, so `deal` and `_next_world` see the scored deck.

- [ ] **Step 1: Write the failing tests**

```python
def test_rebar_sets_fraction_text_and_long_shot():
    g = next(g for g in DECK if g.id == "moon-omen")
    low = rebar(g, 0)
    assert low.target_fraction == 1/5 and low.long_shot
    assert low.text == "A fifth of the village believes the moon is an omen of disaster"
    high = rebar(g, 3)
    assert high.target_fraction == 1/2 and not high.long_shot and high.text.startswith("Half of the village believes")

def test_rebar_leaves_creed_and_state_goals():
    for g in DECK:
        if g.creed_question or g.kind == "state":
            assert rebar(g, 0) == g

def test_claim_prefers_claim_text():
    assert claim(Goal("x", "Convince Agnes that Hob steals geese", "belief", claim_text="Hob steals geese")) == "Hob steals geese"

def test_deck_claim_text_matches_parsed_claim():
    for g in DECK:
        if g.kind == "belief" and g.creed_question is None:
            assert g.claim_text and g.claim_text in g.text

async def test_band_maps_score_and_caches():   # test_believability.py; StubJev returning a score of 0.1 then 0.9
    b = Believability(jev)
    assert await b.band("dragons") == 0
    assert await b.band("dragons") == 0          # cached: Jev called once
    assert jev.calls == 1

async def test_band_failure_is_middling_and_uncached(): ...  # Jev raises → 2, next call asks again
```

- [ ] **Step 2: Run the tests to make sure they fail.** Run `python -m pytest examples/rumor_mill/tests/test_goals.py examples/rumor_mill/tests/test_believability.py -q`. They should fail on ImportError for `rebar` / `Believability`.
- [ ] **Step 3: Implement the fields, `claim`, `BANDS`, `rebar` and `rebar_deck` in goals.py, and `Believability` in believability.py. Wire `rebar_deck` into `__main__.py` after `baselines.load`, before `overhear.publish(list(DECK))`, reading `goals.DECK` at that point instead of the imported name.**
- [ ] **Step 4: Run the full suite and ruff. Both should pass.**
- [ ] **Step 5: Commit.** "Set each deck goal's bar from how believable its claim is"

### Task 2: The judge counts the audience, discounts wary marks, and replaces void goals

**Files:**
- Modify: `examples/rumor_mill/judge.py`, `examples/rumor_mill/goals.py` (`goal_counts` / `needed`), `examples/rumor_mill/feed.py`, `examples/rumor_mill/__main__.py`
- Test: `examples/rumor_mill/tests/test_judge.py`, `examples/rumor_mill/tests/test_feed.py`

**Interfaces:**
- Consumes the Task 1 `Goal` fields.
- `Judge.__init__` gains `engine=None`. The judge reads `engine.suspicion` and `config.warn_threshold`. In `__main__.py`, construct `Engine` before `Judge` and pass `engine=engine`. The engine's `summary_source` lambda already resolves `judge` at call time.
- Produces `audience_of(goal: Goal, town: Town) -> list[Villager]`: the villagers in town that count. That is the whole village when `goal.audience` is empty.
- `Judge.check` gathers over `audience_of(goal, town)`. A believer whose `engine.suspicion.score(vid, goal.owner) >= config.warn_threshold` is counted as a doubter, not a believer, when `goal.owner` is set.
- `needed(goal, counted)`: for a goal with exactly one audience member it returns 1. Otherwise `ceil(target_fraction * counted)` as today. `goal_counts` uses `len(audience)` in place of `villagers` when the audience is set.
- Produces `void_reason(goal: Goal, town: Town) -> str | None`. It returns:
  - `"{name} has left town"` when the subject or a personal goal's mark is gone (use the departed villager's name from `town.departed`);
  - `"too few of {audience_label} are left"` when a multi-member audience has under 3 in town;
  - otherwise None.
- In `Judge.run`, before checking goals, each playing player's goal with a `void_reason` is replaced in place. The replacement is one deck goal drawn by `deal(self.rng, 1, skip=held | ids in hand, never={world claim})`. Then `feed.on_goal_void(old, new, reason, player=p.id)` is called.
- Produces `Feed.on_goal_void(self, old: Goal, new: Goal, reason: str, player: str) -> None`. It emits a quiet `goal_void` event: `f'"{old.text}" is off: {reason}. New goal: "{new.text}"'`, with `goal_id=old.id`, `new={"id","text","believers","needed"}`.

- [ ] **Step 1: Write the failing tests**

```python
async def test_audience_goal_counts_only_its_audience():
    # town of 4; goal audience = (a, b); a and c believe → counts == (1, 1) for a one-of-two half goal (fraction 1/2)

async def test_personal_goal_needs_one():
    # audience=(a,), target_fraction 0 → counts (0, 1); a believes → progress 1.0

async def test_wary_mark_not_counted_for_owner():
    # owner "p-1"; engine stub whose suspicion.score(a, "p-1") == warn_threshold; a believes → believers 0, doubt count 1

async def test_void_goal_replaced_when_mark_leaves():
    # player holds personal goal with audience (a,); remove a from town (departed); one run iteration →
    # player.goals has 3 goals, none with id of the old, and the feed got a goal_void event for that player only

def test_void_reason_cases(): ...   # subject gone; circle down to 2; all present → None
```

- [ ] **Step 2: Run them to make sure they fail.**
- [ ] **Step 3: Implement as in Interfaces.** `_believes` already returns None for a departed villager. Keep that, and stop the gather from including villagers outside the audience.
- [ ] **Step 4: Run the full suite and ruff. Both should pass.**
- [ ] **Step 5: Commit.** "Count a goal's own audience and replace goals whose people left"

### Task 3: GoalMaker writes personal and circle goals, and dealing goes async

**Files:**
- Create: `examples/rumor_mill/goal_maker.py`
- Modify: `examples/rumor_mill/writer.py` (`Writer.goals`), `examples/rumor_mill/config.py`, `examples/rumor_mill/judge.py`, `examples/rumor_mill/overhear.py` (`drop`), `examples/rumor_mill/__main__.py`
- Test: `examples/rumor_mill/tests/test_goal_maker.py`, `examples/rumor_mill/tests/test_writer.py`, `examples/rumor_mill/tests/test_judge.py`, `examples/rumor_mill/tests/test_overhear.py`

**Interfaces:**
- Produces `Writer.goals(self, sample: list[Villager], taken: list[str]) -> list[dict]`. It makes one Haiku call and parses the result with `extract_json`, returning a list of 2 dicts:
  - personal: `{"kind":"personal","text","claim","question","mark","subject"}`;
  - circle: `{"kind":"circle","claim","question","label","circle":{"by":"occupation"|"family"|"ties","value"},"subject"}`.
  Names are villager names, not ids. `taken` holds claims already in play, which Haiku must avoid. On a parse failure it returns `[]`.
- Produces `class GoalMaker` in goal_maker.py:
  - `__init__(self, writer, believability, baselines, overhear, town, config, rng, ids)`;
  - `async hand(self, player_id: str, taken: set[str], deck_skip: set[str], never: set[str]) -> list[Goal]`. It returns 3 goals: the personal goal, the circle goal, and one deck goal (a village-wide goal or a state goal) drawn with `deal(rng, 1, skip=deck_skip, never=never)`. Each invalid or missing generated goal is replaced by another deck draw. The whole Haiku step is bounded by `config.goal_writer_timeout_seconds` through `asyncio.wait_for`. On a timeout or any exception, the hand is 3 deck goals.
- Validation, as `_validate(raw, town) -> Goal | None`. A raw goal is rejected unless all of these hold:
  - names resolve to villagers in town;
  - the mark is not the subject;
  - the circle resolves to 3 or more villagers in town:
    - `occupation`: case-insensitive equal occupation;
    - `family`: the same surname (`name.split()[-1]`);
    - `ties`: the named villager's `relationships` keys that are in town;
  - the subject is excluded from the circle;
  - the subject's first name appears in the claim;
  - the claim is not case-insensitively equal to one in `taken`.
- A valid generated goal is:
  ```
  Goal(id=f"gen-{player_id}-{ids.next('goal')}", text=..., kind="belief", evidence=(claim,),
       believer_question=question, target_fraction=frac, audience=ids, audience_label=label,
       subject=subject_id, owner=player_id, claim_text=claim, long_shot=band == 0)
  ```
  - personal: `text` is Haiku's text, `audience=(mark,)`, `target_fraction=0`, and `long_shot` comes from its band;
  - circle: `frac = BANDS[band][0]`, `text = f"Get {BANDS[band][1].lower()} of {label} to believe {claim}"`.
- Before returning, `hand` runs `await baselines.load([claims])` and `await overhear.publish(generated)` for the generated goals.
- Sample: `config.goal_sample_size` villagers in town, chosen with `rng`. Villagers with a non-empty grudge or relationships are preferred.
- Produces `Overhear.drop(self, goal_ids: Collection[str]) -> None`, which forgets those `goal:` keys from `_evidence`. The published Contex docs may stay.
- Judge:
  - `Judge.__init__` gains `maker: GoalMaker | None = None`.
  - `Judge.deal(player)` stays sync and deals from the deck. It is used for a player's first hand and when there is no maker.
  - Produces `async Judge.redeal(player) -> None`. With a maker it awaits `maker.hand(...)`. Then it sets `player.goals` and `player.met = set()` and emits `on_deal`. Without a maker it falls back to `deal`.
  - In `run`, a met hand calls `asyncio.create_task(self.redeal(p))`. Keep a reference in a set, discarded when done. The player keeps the finished hand, all met, until it lands.
  - `_forget` calls `overhear.drop` for dropped ids that start with `gen-`. The judge gets `overhear` through the maker.
- `__main__.py` builds `GoalMaker` and passes it to `Judge`. `Ids` is the existing instance.

- [ ] **Step 1: Write the failing tests**

```python
async def test_hand_has_personal_circle_and_deck_goal():
    # StubWriter.goals returns one valid personal and one valid circle raw; town has a 3-farmer occupation
    hand = await maker.hand("p-1", taken=set(), deck_skip=set(), never=set())
    assert [g.audience_label != "" for g in hand[:2]] == [True, True]
    assert hand[0].audience == (agnes.id,) and hand[0].subject == hob.id and hand[0].owner == "p-1"
    assert set(hand[1].audience) == {f1.id, f2.id, f3.id} and hob.id not in hand[1].audience
    assert hand[2].owner is None

async def test_invalid_generated_goals_fall_back_to_deck():
    # raws: unknown mark name; mark == subject; circle of 2; claim lacking subject name → 3 deck goals, distinct ids

async def test_writer_timeout_gives_deck_hand(): ...   # writer.goals sleeps past timeout → 3 deck goals

async def test_hand_loads_baselines_before_dealing():
    # StubBaselines records load() calls; generated claims are loaded before hand() returns

async def test_generated_ids_unique_across_players():
    # two hands for p-1 and p-2 from the same raws → no id shared

async def test_taken_claim_rejected(): ...

async def test_redeal_runs_in_background_and_keeps_old_hand_until_ready():
    # judge.run with a maker whose hand() waits on an Event; met hand stays in player.goals; set Event → new hand dealt, deal event emitted

def test_forget_drops_generated_evidence():   # test_judge/test_overhear
    # overhear has evidence for gen-p-1-1 and deck id; judge._forget({deck id}) → only the gen key is dropped

async def test_writer_goals_parses_and_tolerates_junk(): ...   # test_writer.py, StubClient
```

- [ ] **Step 2: Run them to make sure they fail.**
- [ ] **Step 3: Implement `Writer.goals`, `GoalMaker`, `Overhear.drop`, the judge's `redeal`, `_forget` and `run` changes, the config fields and the `__main__` wiring.** The Haiku prompt asks for one personal and one circle goal about the sampled villagers. They should be petty, specific village intrigue drawn from their grudges and relationships, as the examples in the spec are. The prompt gives the JSON shape above and lists the `taken` claims to avoid.
- [ ] **Step 4: Run the full suite and ruff. Both should pass.**
- [ ] **Step 5: Commit.** You may split this into two commits: "Write personal and circle goals about real villagers" and "Deal a finished player's next hand in the background".

### Task 4: Working on a mark costs suspicion

**Files:**
- Modify: `examples/rumor_mill/player.py`
- Test: `examples/rumor_mill/tests/test_player.py`

**Interfaces:**
- Consumes `Goal.audience`, `Goal.subject` and `Goal.claim_text`, `engine.jev`, `engine.suspicion` and `usage.charging`.
- Produces `Player._marks_of(self, villager_id: str) -> list[Goal]`: this player's unmet goals that have a subject and the villager in their audience.
- After a whisper is delivered to a target who is a mark of one or more goals, `whisper` schedules `asyncio.create_task(self._worked_on(target, text, goals))`, keeping a task reference. `_worked_on`:
  - asks one Jev `Noul` under `charging(self.id)`. The state is `{"villager": "<name>, <occupation>. <personality>", "claim": claim_text, "about": subject name, "newcomer said": text}`. The instructions: "Is the newcomer plainly trying to turn this villager against the person the claim is about?"
  - if the Noul is above 0.5, calls `engine.suspicion.bump(target.id, self.id, config.suspicion_whisper_bump, "worked on them")` once, however many goals matched;
  - logs and swallows exceptions.
- No check when the whisper didn't carry, and none for goals without a subject.

- [ ] **Step 1: Write the failing tests**

```python
async def test_blunt_whisper_to_mark_adds_suspicion():
    # player holds personal goal audience=(agnes,), subject hob; StubJev noul 0.9; whisper to agnes
    # → after wait_for, suspicion evidence for (agnes, player) includes reason "worked on them" exactly once

async def test_subtle_whisper_to_mark_adds_nothing(): ...   # noul 0.2 → no "worked on them"

async def test_whisper_to_non_mark_asks_nothing(): ...      # jev.calls unchanged for the mark check
```

- [ ] **Step 2: Run them to make sure they fail.** **Step 3: Implement.** **Step 4: Run the suite and ruff.** **Step 5: Commit.** "Make whispering straight to a mark a gamble"

### Task 5: The subject traces it to you and denies it

**Files:**
- Modify: `examples/rumor_mill/judge.py`, `examples/rumor_mill/engine.py`, `examples/rumor_mill/writer.py` (`Writer.deny`)
- Test: `examples/rumor_mill/tests/test_judge.py`, `examples/rumor_mill/tests/test_game.py`, `examples/rumor_mill/tests/test_writer.py`

**Interfaces:**
- In `Judge.check`, for a goal with a subject in town, the judge runs the same Contex query against the subject's mind (`goal.evidence[0]`, `top_k=3`, the baseline threshold, the `since` window). For each matching memory whose lineage `player_of(uid) == goal.owner`, it calls `engine.traced(goal, subject_id, uid)`. This makes no Jev call.
- Produces `Engine.traced(self, goal: Goal, subject_id: str, uid: str) -> None` (sync; it schedules the async work):
  - The hops are `lineage.depth(uid) + 1`. The whisper itself, heard by the subject, is depth 0, which is 1 hop.
  - The bump is `suspicion_whisper_bump * {1: 1, 2: 0.5, 3: 0.25}.get(hops, 0)`, applied once per `(subject_id, goal.owner, lineage.root(uid))`. The engine records this in a set.
  - The reason is "traced it to you".
  - If the subject's suspicion of the owner was below `warn_threshold` before the bump and is at or above it after, and `goal.id` is not in the engine's denied set, the engine schedules `_deny(goal, subject_id)`.
- Produces `Engine._deny(self, goal, subject_id) -> None` (async). Under `charging(goal.owner)` it gets `await writer.deny(subject, claim_text)`. It builds an `Utterance` with a new id, speaker = the subject, the subject's place, and verb "said". It records the utterance in lineage with no parent and no player. It then calls `await self.deliver(utterance, [marks in town])`. It sends `feed.on_denied(subject name, line, player=goal.owner)`, a quiet speech line. On failure it logs and returns.
- Produces `Writer.deny(self, villager: Villager, claim: str) -> str`: one line, at most 80 tokens out, in the villager's voice. It flatly denies the claim about them and names no newcomer.
- Produces `Feed.on_denied(self, name: str, line: str | None, player: str) -> None`, emitting a `denied` event.

- [ ] **Step 1: Write the failing tests**

```python
async def test_subject_hearing_owner_rumor_bumps_by_hops():
    # lineage: whisper w (player p-1) → u1 → u2; subject's mind matches u2 (3 hops) → bump == whisper_bump/4, reason "traced it to you"

async def test_trace_bump_once_per_root(): ...           # two checks, two memories of the same root → one bump

async def test_unrelated_rumor_reaching_subject_no_bump(): ...   # memory roots in p-2's whisper → no bump for p-1

async def test_crossing_warn_threshold_denies_to_marks_once():
    # suspicion just under warn; trace bump crosses → one deliver to the mark with the subject as speaker; second crossing → none

def test_writer_deny_one_line(): ...
```

- [ ] **Step 2: Run them to make sure they fail.** **Step 3: Implement.** **Step 4: Run the suite and ruff.** **Step 5: Commit.** "Let a rumor's subject trace it to you and deny it"

### Task 6: What the player sees

**Files:**
- Modify: `examples/rumor_mill/web.py` (`goal_json`), `examples/rumor_mill/feed.py` (`on_deal` adds the same fields), `examples/rumor_mill/goals.py` (`trophy_line`), `examples/rumor_mill/judge.py` (trophy call), `examples/rumor_mill/static/index.html`
- Test: `examples/rumor_mill/tests/test_web.py`, `examples/rumor_mill/tests/test_goals.py`, `examples/rumor_mill/tests/test_feed.py`

**Interfaces:**
- `goal_json` and `on_deal` entries gain `long_shot: bool`, `marks: list[str]` (audience ids, only for goals with a subject) and `subject: str | None`.
- `trophy_line(trophy, claim, unseen: str | None = None)` appends `f" And {unseen} never knew."` when `unseen` is given. When awarding a personal trophy, the judge passes the subject name of the met goals' first subject whose suspicion of the player stayed below `warn_threshold`, or None.
- `Trophy` gains `long_shot: bool = False`, which `to_json` includes. A personal trophy is a long shot when any of its goals was. A world trophy is one when the world goal was.
- Page:
  - a goal card with `long_shot` shows a small "Long shot" tag, and a long-shot trophy in the trophy case gets a distinct glyph;
  - in the village column, faces of `marks` get a small badge with the title "your mark", and the face of `subject` a badge with the title "the one it's about", while the goal is unmet;
  - `goal_void` and `denied` events render as quiet lines (`denied` as speech);
  - all text goes in with `textContent` through `el()`, as the rest of the page does.

- [ ] **Step 1: Write the failing tests**

```python
def test_goal_json_carries_marks_subject_long_shot(): ...
def test_state_hides_other_players_marks(): ...   # two players; /api/state for p-2 shows no p-1 goal fields
def test_trophy_line_never_knew():
    assert trophy_line(Trophy("personal", ("x",), 1), "Hob steals geese", unseen="Hob").endswith(" And Hob never knew.")
def test_page_renders_badges_and_long_shot(): ...  # static check in test_web like existing page tests: "your mark", "Long shot" present
```

- [ ] **Step 2: Run them to make sure they fail.** **Step 3: Implement.** **Step 4: Run the suite and ruff.** Load the page through the stub server and confirm in a browser that the badges and the tag render at desktop width and at 390px. **Step 5: Commit.** "Show marks, subjects and long shots, and credit a clean win"
