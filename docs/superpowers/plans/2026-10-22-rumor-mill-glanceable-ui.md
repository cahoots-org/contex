# Rumor Mill Glanceable Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the Rumor Mill page so that each part of the screen has one job. Your rumors go in a column of marked rows that open in place. The feed becomes short: loud lines, speech and quiet one-liners. Conversations go in a dock of faces above the whisper box. On a phone the three columns become three tabs.

**Architecture:** Nearly all of the work is in `static/index.html`. Rumor cards leave the feed and become rows in a new left column, keyed by the same whisper uid. A rumor's mark is its index in creation order, taken modulo six. The feed keeps only lines; retellings, scenes and village talk fold into one quiet line each. A `dock` map keyed by villager id replaces `talks`. It is built from the events the page already gets: `whisper` (with `conversation`), `whisper_status`, `reply`, `refusal`, `time` and `gameover`. The server gains one field. A missed whisper's status says whether the villager's next whisper continues the exchange (`continues`), because the page cannot see the player's near-miss thread.

**Tech Stack:** Vanilla JS and CSS in one HTML file, FastAPI + Starlette TestClient, pytest / pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-10-22-rumor-mill-glanceable-ui-design.md`. The visual target is in the mockups `.superpowers/brainstorm/40543-1792689318/content/glanceable.html` (option A) and `conversations.html` (the dock).

## Global Constraints

- All work goes on branch `feat/rumor-mill-tiles` (PR #477), with no new branches or PRs. Each task is one commit. After each commit, `python -m pytest examples/rumor_mill -q` and `ruff check examples/rumor_mill` must pass when run from `/Users/robmiller/Projects/contex`, and the page must work. Commit subjects are imperative and the body says why. Each message ends with:
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_01YP7uhbQm8avkDcwdQ9b87E`.
- Everything stays per player. The page shows only events, tallies and state the server already scopes to this player. No new endpoint is added, and nothing names, counts or reveals another player.
- Text from villagers, players and the model goes in through `textContent` or text nodes only. `innerHTML` never appears in the file.
- Keyboard: every control is a native `<button>` or sits in the feed's `ROVE`. Every toggle carries `aria-expanded`. Escape closes the innermost open thing in this order: villager card, chat, trophy case, open rumor.
- Colour is never the only cue. Each rumor mark carries its glyph, each dock badge carries `…`, `↩`, `–` or `◷`, and a stopped rumor says "stopped".
- Motion: nothing new animates outside `.fresh`. `prefers-reduced-motion` stills it, as it does today.
- Light and dark: every new colour is a `:root` token, redefined in the existing `@media (prefers-color-scheme: dark)` block.
- At 390px wide there is no horizontal page scroll.
- Rumor marks are six pairs, assigned in this order and wrapping round: amber ◆, teal ●, violet ▲, rose ■, green ★, sky ✚.
- Removed code goes. Old card tallies, the per-card frontier toggle, the goal-link line, `movedBy`, thread tracing and pinning, the feed's duplicate heat line, `talks`, and the drawer leave no dead CSS, functions, ids or test assertions behind.
- Code style: imports at the top only, no meta or history comments, concise comments and docstrings, no clever code. Never use the word "Substrate".
- Browser verification of each page task is a separate step that the controller assigns. It uses the stub server `PYTHONPATH=. python /private/tmp/claude-501/-Users-robmiller-Projects-contex/dc5af79c-d8f0-4711-83c2-3ccea8efac2d/scratchpad/stub_players.py` on port 8791. That file is scratch: implementers may add debug routes to it, and it is never committed.

## Review Focus

1. **Reloading during a conversation.** After a reload, the dock must be rebuilt from the replay with the same faces and badges it showed live. A near-miss thread that expired at a part-of-day change must not come back, and an old answered whisper's `moved_on` must not remove a thread that is still open. (Task 2: `test_a_miss_says_whether_the_next_whisper_continues_it`, plus browser step 2b.)
2. **Marks stay put.** A rumor's mark must be the same before and after a reload. It must still be right with seven or more rumors, where the marks wrap. A reply whisper never gets its own mark or row. (Task 1: `test_the_page_marks_each_rumor`, plus browser step 1b.)
3. **A conversation ends while you are typing in it.** The face leaves the dock and the chat closes. The box flips back to "Whisper to" and the typed text stays. (Browser step 2c.)
4. **A burst of retellings.** A rumor told onward twenty times adds one quiet line to the feed, not twenty, and the line opens to show the words. A run of goal progress lines for one goal updates the last line instead of piling up. (Task 4: `test_the_feed_has_three_kinds_of_line`, plus browser step 4b.)
5. **Narrow screens and long text.** At 390px, a long goal claim, a long villager name or many dock faces must not push the page sideways. (Task 6: browser step 6b.)

---

### Task 1: Rumor marks and the rumors column

**Files:**
- Modify: `examples/rumor_mill/static/index.html`
- Test: `examples/rumor_mill/tests/test_web.py`

**Interfaces:**
- Produces (used by every later task):
  - The CSS tokens `--r0` to `--r5` and `--on-mark`, in light and dark. Starting values: light `#a87412 #1d8577 #7656c0 #b8406a #3b8236 #2c78b0` with `--on-mark: #fff`; dark `#e0b04a #4fb6a8 #a58be0 #e07a9a #7fcf8a #6fb3e6` with `--on-mark: #1a2226`. Adjust them until each has at least 3:1 contrast against `--panel`.
  - `const MARKS = ["◆", "●", "▲", "■", "★", "✚"];`
  - `const markOf = new Map()`, from whisper uid to an index. `newCard` sets `markOf.set(uid, markOf.size)` when the uid has no entry yet. `reset()` clears it.
  - `rumorMark(uid) -> HTMLSpanElement`, which is `<span class="rmark" data-r="{index % 6}" aria-hidden="true">{glyph}</span>`. A uid with no mark (`null` included) gives `<span class="rmark none" aria-hidden="true"></span>`, a blank spacer of the same width.
  - Markup: `<aside class="rumors" id="rumors-panel" aria-labelledby="rumors-heading"><h2 id="rumors-heading">Your rumors</h2><ol class="rumor-list" id="rumor-list"></ol></aside>`. It is the first column of `.app`. The grid becomes `grid-template-columns: 18rem minmax(0, 1fr) 20rem`, with rows for the header, `#strip` (spanning all columns), the three columns, and `div.bottom` (spanning all columns). `.composer` moves into `div.bottom`, which Task 2 fills further.
  - `const rumorRows = new Map()`, from whisper uid to `li.rumor-item`. `newCard` appends the row to `#rumor-list`. Rows are never pruned.
  - `setRow(uid)`. The row is `rumorMark(uid)`, then `b.heard` (`t?.heard ?? 1`), then `span.visually-hidden` " heard", then `span.rbar[role=progressbar]` holding `i` at the value's width, then `span.goal-word`. The row gets class `stopped` when `t.stopped`. It is called from `setTally`, `renderGoals` and `renderWorld`. The row's `title` is the whisper text.
  - `rumorGoal(t) -> { word: string, value: number, said: string }`. `link = t?.goals[0]`. `needed` is `world.needed` when `link.world`, and otherwise the `needed` of the entry in `goals` whose `text === link.text`. `value = needed ? Math.min(100, Math.round(100 * link.convinced / needed)) : 0`. `said` is `` `${link.convinced} of ${needed} convinced` `` when there is a `needed`, and `` `${link.convinced} convinced` `` otherwise. It is the progressbar's `ariaValueText`. `word` is `"stopped"` when `t.stopped`, otherwise `link.claim || link.text`, and `"no goal yet"` when there is no link. The bar fills in the rumor's colour (`.rbar i { background: var(--rc) }`, with `--rc` set from `data-r`).
  - In the feed (until Task 4 removes cards), the whisper meta line of each card starts with `rumorMark(uid)`.
- `.goal-word` truncates with `text-overflow: ellipsis`. `.rumor-item.stopped` has `opacity: .55`.

- [ ] **Step 1: Write the failing test** `test_web.py::test_the_page_marks_each_rumor`. It checks that `re.search(r"const MARKS = \[(.*?)\];", html).group(1)` holds exactly `"◆", "●", "▲", "■", "★", "✚"` in that order. It also checks that `'id="rumor-list"'`, `"--r5"` and `"rumorMark("` are in `html`, and that `"innerHTML" not in html`.
- [ ] **Step 2: Run** `python -m pytest examples/rumor_mill/tests/test_web.py -q -k marks`. Expected: FAIL.
- [ ] **Step 3: Implement** the interfaces above.
- [ ] **Step 4: Run** the full suite and ruff. Expected: PASS.
- [ ] **Step 5: Commit** `Give each rumor a mark and a row of its own`.
- [ ] **Step 6 (controller): Browser verification**, light and dark, at 1440px.
  - (a) Whisper three times. Three rows appear with ◆ ● ▲. Use `/debug/spread` and `/debug/link?convinced=3`: the newest row's number and bar grow. Use `/debug/stop`: that row dims and says "stopped".
  - (b) Whisper seven times and reload. The marks are identical before and after, and the seventh is ◆ again.

---

### Task 2: The conversation dock

**Files:**
- Modify: `examples/rumor_mill/player.py` (`_whisper_to` and `_hint`), `examples/rumor_mill/feed.py` (the `on_whisper_status` docstring), `examples/rumor_mill/static/index.html`
- Test: `examples/rumor_mill/tests/test_player.py`, `examples/rumor_mill/tests/test_web.py`

**Interfaces:**
- Consumes: `rumorMark(uid)`, `markOf`, `cardOf`, and `div.bottom` from Task 1.
- Produces (server):
  - A `brushed_off` or `near_miss` status from `Player._whisper_to` carries `continues: bool`, on both the first emission and the hint emission. `continues = not (thread and thread["whispers"] + 1 >= THREAD_WHISPERS)`, which is the condition `leaning` already uses (rewrite `leaning` as `bool(near_misses) and continues`). `_hint` takes a new parameter `continues: bool` and passes it through. The `on_whisper_status` docstring adds: "a miss also carries continues, whether the villager's next whisper continues the exchange."
- Produces (page):
  - `const DOCK = { near_miss: ["…", "leaning in"], answered: ["↩", "answered"], brushed_off: ["–", "brushed you off"], busy: ["◷", "busy"] };`
  - `const dock = new Map()`, from villager id to `{ name, rumor, state, thread, whispers: Set<string>, lines: Array<{ you: boolean, text: string }> }`, plus `let chatWith = null` (a villager id).
  - `dockStatus(e, fresh)`, called from `showStatus`. Apply the rules in this order:
    1. `near_miss` or `brushed_off`. If `e.continues === false`, call `dockEnd(e.villager_id)`. If `e.continues === true`, open or keep the entry, set `state = e.state` and `thread = true`, and reset `whispers` to `{e.whisper_id}`. Whenever `e.hint` is present, push `{ you: false, text: e.hint }` onto an existing entry.
    2. `answered` opens or keeps the entry, sets `state = "answered"` and `thread = false`, and adds the whisper id to `whispers`.
    3. `busy` sets `state = "busy"` and `thread = false`, but only on an existing entry.
    4. `heard` sets `thread = false` on an existing entry. The badge stays.
    5. `kept`, `took_the_bait`, `warned`, `moved_on` and `not_carried` call `dockEnd` only when `e.whisper_id` is in that entry's `whispers`.

    A new entry takes `rumor = cardOf.get(e.whisper_id)` and has `lines = [{ you: true, text: spoken.get(e.whisper_id)?.text }]`. A fresh `answered` or `near_miss` fills `#target` with the name when the whisper form does not hold focus, as `startTalk` does today.
  - `dockWhisper(e)`. A `whisper` event with `e.conversation` whose `e.target_id` is in `dock` adds `e.uid` to `whispers` and pushes `{ you: true, text: e.text }`. Every reply whisper also sets `spoken` (speaker `"you"`) so that ArrowUp recall still finds it, and sets `cardOf`. It gets no row and no feed node.
  - `dockReply(e)`. A `reply` pushes `{ you: false, text: e.text }` onto `dock.get(e.villager_id)` when that entry exists. Until Task 4, the feed line is `li.note` with `b` name, " answers you: ", and `span.brush` holding the quote.
  - `dockEnd(villagerId)` deletes the entry. When the chat was open on that villager, it closes the chat. It then calls `renderDock()` and `setComposer()`. `refusal` calls it. A `time` event calls `closeThreads()`, which ends every entry with `thread === true`. `showGameOver` clears `dock` and closes the chat.
  - `renderDock()`. Each entry becomes `li > button.dock-face[data-id][aria-expanded][aria-controls=chat]`, holding `faceFor(id, occupation)` (where `occupation` is the one in `villagers` for that id, or `""` when the villager is not listed), `span.dock-badge[data-s=state]` (the glyph, `aria-hidden`), and `small` reading `` `${name} · ${DOCK[state][1]}` ``. `#dock` is empty, with no height, when nobody is talking to you.
  - `openChat(villagerId | null)` toggles the chat. Opening sets `chatWith`, sets `#target` to the name, calls `setComposer()`, and focuses `#text`. `renderChat()` fills `#chat-about` with `rumorMark(entry.rumor)` followed by `` `About “${opening(entry.rumor)}”` ``, and fills `#chat-lines` with `li.msg.you` and `li.msg.them` (the villager's lines in the rumor font, quoted), scrolled to the bottom.
  - `opening(uid) -> string` gives the first 8 words of the rumor's whisper, plus `…` when there were more. `movedBy` uses it until Task 4 removes `movedBy`.
  - `setComposer()` reads `dock` instead of `talks`. The labels are unchanged: "Talking with", "Reply" and "Say something back".
  - Markup inside `div.bottom`, above `.composer`: `<ul class="dock" id="dock" aria-label="Talking with you"></ul><section class="chat" id="chat" aria-label="Conversation" hidden><p class="chat-about" id="chat-about"></p><ol class="chat-lines" id="chat-lines"></ol></section>`. The chat has a `max-height` of `12rem` and scrolls inside itself.
- Removes: `talks`, `startTalk`, `endTalk`, `mine`, `theirs`, the card's `.talk` list, the conversation loop in `restoredCard`, the "ended with the game" loops in `showGameOver` and `loadState`, the `.talk`, `.reply` and `.mine` CSS, and `.reply` from `ROVE`.

- [ ] **Step 1: Write the failing tests**
  - `test_player.py::test_a_miss_says_whether_the_next_whisper_continues_it`. It uses `near_missing_player("Wheel's groaning.", "Girl wants the city.")`, then `await miss(player)`, then sets `player.engine.contex = StubContex({NEEDS[1]: landing(0.495, "utt:w-2")})`, then `await miss(player)`. It asserts `[s.get("continues") for s in statuses(player) if s["state"] != "checking"] == [True, True, False, False]`.
  - `test_web.py::test_the_page_has_the_conversation_dock`. It checks that `'id="dock"'`, `'id="chat"'` and `'aria-controls", "chat"'` (or `ariaControls: "chat"`) are in `html`. It checks that the keys of `const DOCK = {...}` are exactly `{"near_miss", "answered", "brushed_off", "busy"}`, and that `"continues"` and `"Talking with"` are in `html`. It checks that `"const talks"`, `"startTalk"` and `"innerHTML"` are not in `html`. It also checks that `".reply"` is not in the `ROVE` string.
- [ ] **Step 2: Run** `python -m pytest examples/rumor_mill/tests/test_player.py examples/rumor_mill/tests/test_web.py -q -k "continues or dock"`. Expected: FAIL.
- [ ] **Step 3: Implement** the server field, then the page interfaces above.
- [ ] **Step 4: Run** the full suite and ruff. Expected: PASS.
- [ ] **Step 5: Commit** `Move conversations into a dock above the whisper box`.
- [ ] **Step 6 (controller): Browser verification.** Extend the stub with:
  - `/debug/reply?text=...`: your newest whisper's target answers, as an `answered` status plus a `reply` event.
  - `continues=0|1` on `/debug/status`, with `near_miss` allowed.
  - `/debug/phase`: emits the next `time` event.

  Then check:
  - (a) Leaning in, answering and brushing off each show a face with `…`, `✓` and `–`. Clicking a face opens the chat, tagged with the rumor's mark, and "Talking with {name}" appears. Typing and Reply add a line.
  - (b) Reload with an open answered conversation and a near-miss thread. The dock is the same. Then call `/debug/phase`: the near-miss face leaves and the answered face stays.
  - (c) Open a chat, type without sending, then send `moved_on` for its whisper. The face leaves, the chat closes, the label reads "Whisper to", and the text stays.
  - (d) Use `/debug/runout`: the dock empties.
  - (e) Tab and Enter reach every face, and Escape closes the chat.

---

### Task 3: Opening a rumor in place and ringing its villagers

**Files:**
- Modify: `examples/rumor_mill/static/index.html`
- Test: `examples/rumor_mill/tests/test_web.py`

**Interfaces:**
- Consumes: `rumorRows`, `setRow`, `rumorGoal`, `rumorMark` and `markOf` (Task 1), and `opening` (Task 2).
- Produces:
  - Row markup: `li.rumor-item > button.rumor-row[data-card][aria-expanded][aria-controls="rumor-detail-{index}"]` holding Task 1's row contents, followed by `div.rumor-detail#rumor-detail-{index}[hidden]`. The rows are native buttons in tab order.
  - `selectRumor(uid | null)`. Only one rumor is open at a time, and the open rumor is the selected one. The function sets `selectedRumor`, flips `aria-expanded` and `hidden`, calls `setDetail(uid)` and then `refreshVillagers()`, sets `#people`'s `--sel` to `var(--r{n})`, and puts the rumor's glyph in `#legend-mark`. Clicking an open row closes it, and Escape closes it last in the chain.
  - `setDetail(uid)` fills the open detail, in this order:
    1. `p.story`: `story(t)` when `t.heard > 1`, otherwise `chip(statuses.get(uid))`, otherwise "Waiting to hear back."
    2. `p.goal-line`: `goalLine(link)`, which is `` `${link.world ? "World goal" : "Goal"}: ${link.claim || link.text} · ${link.convinced} convinced` ``. It is left out when there is no link.
    3. `ul.frontier`: `whoHasIt(t.frontier)` when the rumor is spreading and not stopped. This list has no toggle.
    4. `ul.talking`: `talkingNow(t.frontier)`, with one `li` per tone and place, reading `` `${sceneWords(tone, place)} · ${plural(n, "person")}` `` (with "people" for the plural).
    5. `p.whispered`: "You whispered to {target} at the {place}", the quoted whisper, and `reuseButton(text, target)`.

    `setTally` refreshes the row, and the detail when it is open.
  - The countdown interval scans `document.querySelectorAll("[data-until]")`. It uses `busyText` inside `.frontier` and `waitText` elsewhere.
  - Village rings: `.touched > .face canvas` and `.legend .touched` use `outline-color: var(--sel, var(--ink))`. The legend reads `<span class="touched"></span><span id="legend-mark"></span>touched by the rumor you opened`.
- Removes, from the feed card: `.summary`, `.story` (the card's), `.goal-link`, `goalLink`, `.frontier`, `.frontier-more`, `toggleFrontier`, `countFrontier`, and the whisper's chip. Also removes `pinned`, `trace`, `togglePin`, `threadItem`, the feed's `mouseover` and `mouseleave` handlers, the Enter-to-pin branch, the Escape-to-unpin branch, the `.tracing` and `.lit` CSS, and `.frontier-more` from `ROVE`.

- [ ] **Step 1: Write the failing test** `test_web.py::test_a_rumor_opens_in_place`. It checks that `'"rumor-row"'` (or `rumor-row`), `"selectRumor("`, `'id="legend-mark"'`, `"--sel"`, `"Spreading: "`, `"Who has it"` or the grouped `whoHasIt(` call, and `"Reuse"` are in `html`. It checks that `"frontier-more"`, `"togglePin"`, `"goal-link"` and `"innerHTML"` are not in `html`. Update `test_the_page_renders_moments_and_goal_links` so that it no longer expects `frontier-more` in `ROVE` or the text `Who has it`.
- [ ] **Step 2: Run** `python -m pytest examples/rumor_mill/tests/test_web.py -q -k "opens_in_place or moments"`. Expected: FAIL.
- [ ] **Step 3: Implement** the interfaces above.
- [ ] **Step 4: Run** the full suite and ruff. Expected: PASS.
- [ ] **Step 5: Commit** `Open a rumor in place and ring the villagers it touched`.
- [ ] **Step 6 (controller): Browser verification.**
  - (a) Use `/debug/spread` and `/debug/link`, then click a row. It opens in place with its story, goal, who has it, and "An argument at the well · 1 person". The village faces it touched get rings in its colour, and the legend shows its glyph.
  - (b) Opening another row closes the first. Enter toggles a row. Escape closes it. Nothing else on the page moves.
  - (c) A busy entry counts down inside the open detail.

---

### Task 4: A short feed of loud lines, speech and quiet one-liners

**Files:**
- Modify: `examples/rumor_mill/static/index.html`
- Test: `examples/rumor_mill/tests/test_web.py`

**Interfaces:**
- Consumes: `rumorMark` and `cardOf` (Task 1), `dockReply` and `dockStatus` (Task 2), and `selectRumor` (Task 3).
- Produces:
  - `line(tone, uid, ...children) -> HTMLLIElement`, which is `li.line.{tone}` with `rumorMark(uid)` first. `tone` is `"loud"`, `"speech"` or `"quiet"`.
  - The fold, which replaces `talkBlock` and `countTalk`: `fold(dataset) -> li.fold` holding `p.fold-head` (a `span` and a `button.linkish.fold-more[aria-expanded]`), then `ol.fold-lines`. `countFold(block)` rebuilds the head from `block.dataset.rumor` and `foldWords(block)`, and hides the toggle when there are no lines. The toggle reads `` `Show ${n}` `` or `"Hide"`. A fold is closed by default, except that a listen fold opens open. The CSS rule is `.fold:not(.open) .fold-lines { display: none }`. The village talk "Talk" button is renamed `.fold-talk`.
  - `foldWords(block)` builds the head text:
    - A listen fold reads `` `You listen at the ${place} · ${plural(n, "line")}` ``.
    - A scene fold reads `` `${sceneWords(tone, place)} · ${people} ${people === 1 ? "person" : "people"}` ``, followed by `` ` · ${ended}` `` once it has ended.
    - Village talk reads `` `Village talk at the ${place} · ${plural(n, "line")}` ``.
    - A retelling fold shows the newest hop's `hopWords(e)` (for example "Hob told Marta at the tavern", built from the same branches as `hopMeta` but without the "after hearing" clause), followed by `` ` · ${plural(n, "retelling")}` `` when `n > 1`.
  - `retold(e, fresh)`. A `hop` joins the scene fold for `e.scene` when that fold exists. Otherwise it joins the last feed item when that item is a `.fold[data-rumor=e.root]` without a scene. Otherwise it starts a new retelling fold. Each fold line is the hop's meta followed by `quoted(e.text, parent.text, e.root)`, so the change marks survive.
  - The tone of each event kind:

    | Tone | Event kinds | How it shows |
    |---|---|---|
    | Loud | `moment` (mark plus the ✓ or ! tone glyph and its hidden "Good news: " or "Trouble: "), `warning`, `refusal`, `trophy` (keeps `.trophy-open`), a met personal or world `goal` | Bold, full text |
    | Speech | `reply` (`` `${name}: “${text}”` ``, marked by `cardOf.get(e.conversation)`), a `near_miss` or `brushed_off` status with a hint (`` `${name} leans in: “…”` `` or `` `${name} brushes you off: “…”` ``, marked by the whisper's rumor), `asked`, and `overheard` with `reason === "goal"` and no scene | One quoted line |
    | Quiet | `drift` (`DRIFT`, with the mark), goal progress (`` `${text}: now ${goalProgress(e)}` ``, with the mark; when the last feed item is a progress line with the same `data-goal`, it is replaced instead of appended), `deal`, `announcement`, `system`, and every fold head | Dim one-liners |

  - Also: `heat` now only sets `#heat` and returns null. A `whisper` returns null, because it is a row (Task 1) or a dock line (Task 2). `gameover` keeps its panel.
  - `newCard(uid, target, place, text)` builds only the row. `restoredCard(c)` becomes `newCard(c.uid, c.target_name, c.place, c.text)`, with no hops.
  - `const ROVE = ".fold-more, .fold-talk, .trophy-open";`. `threadItems` is renamed `roveItems`. The feed keydown handles arrows, Home and End among `ROVE` only, and buttons handle Enter themselves.
- Removes: card `li.rumor` in the feed, `.ev`, `.hop` and `.whisper` items, `.rails`, `.thread`, `.scenes`, `.scene` and their toggles (`toggleThread`, `countThread`, `toggleScenes`, `countScenes`, `toggleScene`, `cardScene`, `villageScene`), `addTo`, `shown`, `movedBy`, the card branch of `prune`, `talkBlock`, `countTalk`, the `.vt*` CSS, and the heat feed line.

- [ ] **Step 1: Write the failing test** `test_web.py::test_the_feed_has_three_kinds_of_line`.
  - `ROVE` must parse to exactly `{".fold-more", ".fold-talk", ".trophy-open"}`.
  - `'"loud"'`, `'"speech"'` and `'"quiet"'` must all be in `html`, as must `"retold("`.
  - `"moved by"`, `"rails"`, `"toggleThread"`, `"cardScene"` and `"innerHTML"` must all be absent.
  - Update `test_the_page_renders_moments_and_goal_links`. It keeps `case "moment"`, `"Good news: "`, `"Trouble: "` and `"Spreading: "`, and drops `moved by`.
  - Keep `test_the_page_has_words_for_every_frontier_state` as it is.
- [ ] **Step 2: Run** `python -m pytest examples/rumor_mill/tests/test_web.py -q -k "three_kinds or moments"`. Expected: FAIL.
- [ ] **Step 3: Implement** the interfaces above.
- [ ] **Step 4: Run** the full suite and ruff. Expected: PASS.
- [ ] **Step 5: Commit** `Keep the feed short: loud lines, speech, and quiet one-liners`.
- [ ] **Step 6 (controller): Browser verification.** Extend the stub with `/debug/burst?n=20`, which makes 20 one-to-one hops on your newest card. Then check:
  - (a) A whisper adds no feed line. `/debug/spread` gives quiet marked lines, and the argument at the well is one quiet scene line. `/debug/moment` is loud. A reply and a lean-in line are speech with the mark.
  - (b) `/debug/burst?n=20` gives one quiet line, "… · 20 retellings". Show opens the words with the change marks. Five `/debug/goal` calls in a row leave one progress line.
  - (c) Arrow keys move between the feed's buttons, and screen-reader text still says "Good news: " and "Trouble: ".

---

### Task 5: Goals as cards and a tidy header

**Files:**
- Modify: `examples/rumor_mill/static/index.html`
- Test: `examples/rumor_mill/tests/test_web.py`

**Interfaces:**
- Consumes: `setRow` from Task 1, which both render functions keep calling.
- Produces:
  - `#goals` holds `li.goal-card` items, each with the goal text, the bar and the progress words, on `var(--panel)` with a border and radius. `.met` stays. The world goal becomes `<div class="goal-card world" id="world">` beside the list, holding "World goal", its text and a bar. `renderWorld()` fills that card.
  - The header's status cluster is renamed `div.hud`. The composer's `#status` gets class `say-status`. This ends the clash between the two `.status` CSS rules. The header still holds the title, mood, trophies, the wheel and clock, your place, your cost and the connection state.
  - On the phone, `#strip-toggle` keeps its "Goals: …" summary.
- Removes: the `.world` paragraph styling and both `.status` rules.

- [ ] **Step 1: Write the failing test** `test_web.py::test_goals_are_cards_and_the_header_has_one_status`. It checks that `'id="world"'`, `'class="goal-card world"'`, `"goal-card"` and `'class="hud"'` are in `html`. It checks that `html.count('class="status"') == 0` and `".status {" not in html`. It checks that `test_page_has_the_trophy_case_and_world_goal` still passes unchanged.
- [ ] **Step 2: Run** `python -m pytest examples/rumor_mill/tests/test_web.py -q -k "goals_are_cards"`. Expected: FAIL.
- [ ] **Step 3: Implement** the interfaces above.
- [ ] **Step 4: Run** the full suite and ruff. Expected: PASS.
- [ ] **Step 5: Commit** `Show goals as cards under a header with one status line`.
- [ ] **Step 6 (controller): Browser verification.** Use `/debug/goal?percent=40`, `/debug/world?percent=40&believers=8&needed=12` and `/debug/deal`. The cards update, the world card shows "8 of 12 believe it", and the rumor rows' bars follow. Check light and dark at 1440px and 1100px.

---

### Task 6: Three tabs on a phone

**Files:**
- Modify: `examples/rumor_mill/static/index.html`
- Test: `examples/rumor_mill/tests/test_web.py`

**Interfaces:**
- Consumes: `#rumors-panel` from Task 1, `main`, `#town`, and `div.bottom` from Task 2.
- Produces:
  - `<nav class="tabs" role="tablist" aria-label="Views">` holds three buttons. Each is `role="tab"` with `aria-selected`, and only the selected one is in the tab order:

    | Button | Label | `aria-controls` |
    |---|---|---|
    | `#tab-rumors` | Rumors | `rumors-panel` |
    | `#tab-hear` | Hear | `hear-panel` (the `id` given to `main`) |
    | `#tab-village` | Village | `town` |

    Each panel has `role="tabpanel"` and `aria-labelledby` pointing at its tab. The tablist is `display: none` above 760px.
  - `setTab(name)` sets `.app.dataset.tab`, `aria-selected` and `tabIndex`. Left and Right move between tabs and wrap, and Home and End jump to the ends. The default is `"hear"`. At `max-width: 760px`, the grid is one column and only the panel whose name matches `data-tab` shows. `div.bottom` (the dock, chat and box) always shows. `.dock` scrolls sideways inside itself (`overflow-x: auto`), and `.chat` gets a `max-height` of `30dvh`. On a phone the villager card stays the bottom sheet inside `#town`.
- Removes: `#drawer-toggle`, `setDrawer` and its calls in `talkAbout` and `whisperTo`, the fixed `aside` drawer CSS, and the "Back to the feed" text.

- [ ] **Step 1: Write the failing test** `test_web.py::test_a_phone_gets_three_tabs`. It checks that `'role="tablist"'` is in `html`, and that each pair `('id="tab-rumors"', 'aria-controls="rumors-panel"')`, `('id="tab-hear"', 'aria-controls="hear-panel"')` and `('id="tab-village"', 'aria-controls="town"')` is in `html`. It checks that `"drawer-toggle"`, `"setDrawer"` and `"innerHTML"` are not in `html`.
- [ ] **Step 2: Run** `python -m pytest examples/rumor_mill/tests/test_web.py -q -k three_tabs`. Expected: FAIL.
- [ ] **Step 3: Implement** the interfaces above.
- [ ] **Step 4: Run** the full suite and ruff. Expected: PASS.
- [ ] **Step 5: Commit** `Turn the three columns into tabs on a phone`.
- [ ] **Step 6 (controller): Browser verification.**
  - (a) At 390x844, the tabs switch panels, and the dock and box stay at the bottom on every tab. Open a rumor on Rumors, then switch to Village: the rings show. Tapping a dock face opens the chat.
  - (b) At 390px, with a long goal claim (`/debug/deal` until one is long), `/debug/crowd?place=square&n=13`, and four dock faces, `document.documentElement.scrollWidth <= 390`.
  - (c) The tabs work with arrows, Home and End. Check light and dark, and check reduced motion: no animation outside `.fresh`.

---

## Self-review

- **Spec coverage:**

  | Spec item | Task |
  |---|---|
  | Rumor marks (six pairs, in order, stable across reloads, replies belong to the opening rumor) | 1, and `cardOf` in 2 |
  | Rumor rows (mark, big number, bar, goal word, dimmed when stopped) | 1 |
  | Opening in place (story, goal with convinced, who has it, conversations now, rings in the rumor's colour) | 3 |
  | The feed's loud, speech and quiet lines, the mark on every rumor line, and no whispers in the feed | 4 |
  | Village actions unchanged | No task changes them |
  | The dock (badges, chat tagged with the mark, "Talking with", leaving when the conversation ends, empty when nobody talks) | 2 |
  | Header content unchanged, goals as cards | 5 |
  | Mobile tabs, with the dock and box staying put | 6 |
  | Server addition (the smallest field) | 2 (`continues`) |
  | Folding bursts of quiet lines | 4 |

- **Names are consistent across tasks:** `rumorMark`, `markOf`, `rumorRows`, `setRow`, `rumorGoal`, `opening`, `dock`, `dockStatus`, `dockEnd`, `closeThreads`, `selectRumor`, `setDetail`, `line`, `fold`, `countFold`, `retold`, `setTab`. Every later task uses the names an earlier task defines.
- **Proportion:** no function bodies are given. Only the rules the code cannot decide on its own are spelled out: the dock transitions, the bar's value, and which tone each event kind takes.
