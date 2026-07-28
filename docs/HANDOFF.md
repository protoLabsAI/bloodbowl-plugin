# Handoff

Everything a new team needs that isn't obvious from the code. Read this before the
first change; it is mostly a list of things that cost time to learn.

---

## 1. The one idea

**The engine adjudicates; the agent coaches.**

This plugin exists because of a verified failure in the agent it was built for: it
reproduces retrieved data exactly and then fabricates the prose around it. Four
observed instances, each while the correct passage was in hand — including
inventing a notation legend (`G = Guarded, A = All…`) directly beneath its own
correct list of the six skill categories. The trigger is *confidence, not
difficulty*: it stops checking when something feels obvious.

Two consequences run through the whole design:

1. **Data is structured, not prose.** A hover card reading a parsed table cell
   cannot drift the way a paraphrase can.
2. **Rulings belong to the engine.** A wrong stat is bad; a wrong *ruling* changes
   the game. The agent decides *what to do* and says why; the engine decides
   whether that was legal and what the dice said.

That is why `validate()` is side-effect-free and exposed as `bb_game_legal`, and
why the log carries every roll. A coach quoting *"Dodge needed 3+, rolled 2 —
FAILED"* is reading the engine's own arithmetic, not reconstructing it.

---

## 2. How rules get into this codebase

**Look them up. Never recall them.** The agent's own knowledge base holds the S3
core rules, scraped from <https://bloodbowlbase.ru/bb2025/>. Query it, quote the
passage in the code beside the branch it decides, and pin it with a test.

This is not ceremony. Every milestone turned up rules that differ from what an
older edition — or a language model — would say:

| Looked up | What recall would have said |
|---|---|
| It is **Rushing**, 2+, twice per activation | "Going For It" |
| The **Rush roll comes before the Dodge** | either order |
| Dodge is modified by who Marks the square **moved into** | the square being left |
| A failed Dodge still **moves** the player | leaves them where they were |
| Die faces are **PLAYER DOWN / BOTH DOWN / PUSH BACK / STUMBLE / POW** | the previous edition's names |
| **Three dice on *over* double** — strictly | "double" |
| A **push onto the ball is not a pick-up** | it is |
| A push into the End Zone **scores** | it doesn't |
| S3 has a **Secure the Ball** action | doesn't exist |
| Kick-off 11 is **Dodgy Snack** | Officious Ref |
| S3 has a fourth status, **Distracted** | three statuses |

**One trap when reading the KB:** chunk boundaries can put a heading above the
wrong body. A search hit labelled `BLOCK (ACTIVE)` actually contained *Multiple
Block*'s text. Trust the quoted block; distrust the label and the prose around it.

**What to do when the source doesn't say.** It happens. The Range Ruler is a
physical template and the rules define passing by laying it on the table — there
is no official table of squares. The answer is not to guess quietly:

- Isolate the number in one file that says it is not from the rulebook
  (`engine/ruler.py` is the only such file in the engine).
- Derive it from evidence and **write the derivation down**.
- Cross-check against an independent source. The ruler's band limits reproduce a
  separately-reported table of maximum reaches on all eight boundaries.
- Make it configurable, so anyone with the real object can correct it.
- **Report the caveat to the user** (`bb_pass_ranges`).

**Unmodelled things are reported, never ignored.** `unmodelled_skills()` tells a
coach that an Orc Blitzer's `Block` and `Break Tackle` were not applied. Ten of
the eleven Kick-off Events say *"rolled, but NOT applied by this engine"*. Silence
would be the same failure the plugin exists to prevent — a coach told **BLITZ!**
who watches nothing move would reasonably conclude the engine is broken.

But honest is not the same as loud, and at scale the two pull apart: the same two
Skills on the same Troll, on every step of every activation, is one fact several
hundred times, and a warning that always fires is one nobody reads. So the gap is
reported in two registers — `skills.first_mentions` names each Skill in the log
the first time it is actually relevant and then never again, while
`skills.unmodelled_on_pitch` is the standing summary, recomputed from the board
whenever anyone asks and therefore never stale. **The ledger behind the first is
the LOG**, because a match is reloaded from disk between tool calls: a ledger held
on the object re-announces everything on every call *and looks like it works*.
There is a test that fails for exactly that implementation.

---

## 3. Architecture

```
pitch.py        geometry, roster lookup, the permissive practice board (Scenario)
presets.py      named setups; shipped shapes + operator-saved
store.py        board, match and preset persistence (atomic writes)
api.py          three routers: public view + static, gated data, gated game
engine/
  dice.py       Roll, Seeded/Scripted/Replay dice, roll_target, roll_2d6
  events.py     the log — facts, each carrying the dice that produced it
  state.py      Match, PlayerState, Ball, Clock, and fold()
  rules.py      shared board maths: adjacency, Tackle Zones, assists, push arc
  ball.py       bounce, catch, pick up, drop, touchdown
  injury.py     knock down → armour → injury → Stunned/KO/Casualty
  kickoff.py    the kick, the Kick-off Event table, touchbacks
  ruler.py      THE measured constants (see §2)
  skills.py     skills as named hooks
  game.py       the orchestrator: act(), end_turn(), start_drive(), legal_moves()
  actions/      one module per action: validate() + resolve()
web/            index.html + style.css + six ES modules (no bundler)
harness.py      drives the view in real Chromium
```

### Invariants — break these and things go wrong quietly

**State is derived. `apply()` is the only mutation**, and it takes a recorded
Event. `fold(events)` therefore rebuilds any position exactly, with no dice and no
rules consulted — which is why a match saved under old rules re-watches as it was
played. `Match.from_dict` folds the log rather than trusting the saved board, so
the two can never disagree.

> Setting `match.ball.carrier` directly and saving *looks* fine and then silently
> vanishes. To set up a drill, emit events.

`Match.from_dict` seeds each player from the cached row **before** folding, which
makes this invariant easy to break without noticing: a field the fold does not
restore is quietly supplied by the cache on the one path anyone exercises. Block,
Hand-off, Secure and Pass all assigned `p.acted` directly and emitted a note
saying so — a note nothing read — so `fold()` alone left players free to act
twice while `from_dict` looked perfect. If you add state to `PlayerState`, add the
event that restores it and test it through `fold()`, not `from_dict`.

**Determinism and replay are two mechanisms.** The seed is for *regeneration*; the
log is for *re-watching*. A seed alone cannot survive a rules change — add one
skill that rolls an extra die and every later draw from the shared stream shifts.
`ReplayDice` is for a third thing: re-executing a saved match's dice through
today's rules and diffing the events, which turns a corpus of saved matches into a
regression suite.

**`resolve()` applies its own events and returns what it applied.** A Chain Push
cannot place the next player until the previous one has moved. `Recorder` has two
verbs: `extend` applies, `absorb` records what a helper already applied. Using the
wrong one moves the ball twice and scores twice.

**Strict in play, permissive in setup.** The practice board reports and never
blocks — an illegal position is a legitimate thing to want while working a shape
out. A Match refuses, with a reason.

**The view computes no rules.** It calls `/game/legal` and paints the answer. A
test asserts `game.js` contains no dodge modifier of its own: two implementations
agree right up until they don't, and then the board lies.

**Geometry is never written down twice.** The board is 26×15 today, but every
grid, ratio, ruler and overlay line derives from `/meta` via `--cols`/`--rows`. A
test decomments the assets and fails on a bare `26` or `15` anywhere in `web/js`.

---

## 4. Working on it

```bash
.venv/bin/python -m pytest tests/ -q      # 235 tests
.venv/bin/python -m ruff check . && .venv/bin/python -m ruff format --check .
.venv/bin/python harness.py --check       # 44 browser checks + screenshots
```

- **Use `.venv/bin/python`.** The system `python3` is 3.9 and produces ~11 bogus
  failures (`zip() takes no keyword arguments`).
- **CI** runs lint + tests on the org's Namespace runner, ~20s. Playwright is
  deliberately *not* a dev dependency — the harness is a local tool.
- **First test run after writing new files takes 20–90s** on macOS (file scanning,
  high system time). Re-runs are ~2s. Batch your writes.

### The harness earns its keep

Server-side tests prove endpoints answer and tools don't throw. They say nothing
about whether the board is *legible*. Every one of these was found by looking:

- `[hidden]` loses to any `display` rule of ours, so the entire setup toolbar
  stayed on screen in play mode.
- Legal squares drawn as a thin green outline on a dark green pitch were invisible
  — which defeats the whole point of asking the engine.
- Both End Zones tinted with the accent, so both ends read as home territory.
- Badge type scaled off `vw` resolved to ~7px in a rail panel.

Two rules for the harness itself, both learned the hard way:

1. **Never let harness setup depend on dice.** The block section ran after a
   dice-driven move; a failed Dodge is a turnover leaving nobody able to block, so
   it failed ~1 run in 3 and read as a broken feature. It starts from a fixed seed.
2. **Capture response bodies, not just status codes.** A refused action returns
   `200` with `ok:false`. That one probe ended ~40 minutes of guesswork when the
   HTTP route was silently dropping a Block's `target`.

### Deploying to a live agent

```bash
rsync -a --delete --exclude .git --exclude .venv --exclude __pycache__ \
      --exclude team_html --exclude shots --exclude state \
      ~/dev/bloodbowl-plugin/ "<box>/workspaces/<Name>-<hash>/plugins/bloodbowl/"
```

Copy, never symlink. Then `POST /api/plugins/bloodbowl/enabled {"enabled":true}`.

> **That reload does not pick up NEW ROUTES.** FastAPI cannot swap a mounted
> router in place, so the first mount wins. `restart_recommended: false` is correct
> for *enabling* and misleading after you rsync over a running plugin — a new route
> 404s until the process restarts. Diagnostic: FastAPI's unmatched-route body is
> `{"detail":"Not Found"}` (capitalised); your own guard's 404 says whatever you
> wrote.

**ONE ROUTER PER PREFIX.** The host mounts plugin routers keyed on
`(plugin_id, prefix)` and skips any already mounted, so a second router for the
same prefix has *every* route discarded. This plugin shipped two on
`/api/plugins/bloodbowl` for weeks; the entire match API 404'd on a real host
while the board's routes worked, and nothing was logged. Both the harness and the
test-client fixture had mounted every router blindly, which made them more
forgiving than production — the one thing a test harness must never be. They now
mimic the host's dedup, and a test asserts the plugin never hands over two.

Static assets need `public_paths` in the manifest. The host auto-exempts a declared
*view* path from the auth gate but not its siblings — so the page returns 200 while
the stylesheet and modules 401, giving an unstyled dead board. The harness cannot
catch this: it mounts routers with no auth middleware.

---

## 5. Where the data came from

`data/rosters.json` — 30 teams, 159 positionals, 63 star players, scraped by
`tools_scrape_rosters.py` from the `/bb2025/teams/` and `/starplayers/` tables.
Parse the `<table>` cells, never the flattened page text.

Scrape traps, each of which produced wrong-not-missing data:

- **Errata are marked in the HTML.** The superseded value is wrapped in `<del>`
  with the correction beside it. Stripping tags without dropping the struck
  *content* fuses them — `PA "3+ 4+"` — and where the column holds letters it is
  invisible: three teams kept a Mutation access the errata removed. **Cross-check
  any scrape against `core_rules/latest_faq`, which lists every errata in plain
  text.** Additions are applied inline with no markup; only changes get `<del>`.
- Paired Star Players use a **different table layout** — the pair is priced once in
  a `<p><strong>`, and each member's table is headed plainly `MA | ST | …`.
  Assuming a leading cost cell reads "MA" as the price and shifts every stat left.
- Some pages head the column `Skills`, others `Skills & Traits`. An exact match
  silently dropped 42 of 159 skill lists.
- Team URLs are capitalised; quantities use U+2011 non-breaking hyphens; skills are
  bullet-separated.

`tools_kb_docs.py` generates one knowledge-base document per team from that JSON,
with every stat **labelled**. Do not ingest the team pages directly: flattened,
Orc's Goblin Lineman reads `6 2 3+ 3+ 4+ 8+` — six values for five stats, the extra
being the struck errata — which is the worst possible input for this agent.

---

## 6. What's done, and what's next

**Playable now:** set up (by hand, by agent, or from a preset) → kick-off → move,
block, hand-off, Secure the Ball, pass → injuries → touchdowns → drives →
half-time → full time.

Actions: `move`, `block`, `handoff`, `secure`, `pass`.
Skills modelled: Block, Dodge, Guard, Jump Up, Mighty Blow, Prehensile Tail,
Thick Skull. Everything else is reported as unmodelled.

### Next, roughly in order

1. **Blitz** (a Move plus a Block in one activation) — the most-used action in real
   Blood Bowl, fully specified in the text, and it composes two things that exist.
2. **Foul**, and the Argue the Call / sending-off rules that go with it.
3. **The remaining ~100 skills** as hook registrations. The registry is built for
   this: a new skill is one decorated function, not an edit to an action.
4. **Kick-off events that need a choice** (High Kick, Solid Defence, Quick Snap,
   Blitz!) — these need a way for the engine to *ask* the coach mid-resolution,
   which does not exist yet and is a genuine design question.
5. **Team Re-rolls**, which several kick-off events and much of real play depend on.
6. **A setup phase**, so each drive can be set up afresh rather than reusing the
   opening positions.

### Known simplifications, all deliberate and all stated in the code

- The drive setup is captured once, at the first kick-off, and reused.
- The Casualty Roll (D16 table) is not made; a Casualty just leaves the pitch.
- No Weather, Inducements, Cheerleaders, Assistant Coaches or Fan Factor.
- The Throw-in direction is thrown straight back in from the edge crossed; the real
  Throw-in Template is a diagram.
- The version is `0.5.0` and the plugin has never been released or tagged.
