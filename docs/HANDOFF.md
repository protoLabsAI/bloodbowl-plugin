# Handoff

Everything a new team needs that isn't obvious from the code. Read this before the
first change; it is mostly a list of things that cost time to learn.

---

## 1. The one idea

**The engine adjudicates; the agent coaches.**

This plugin exists because of a verified failure in the agent it was built for: it
reproduces retrieved data exactly and then fabricates the prose around it. Five
observed instances, each while the correct passage was in hand — including
inventing a notation legend (`G = Guarded, A = All…`) directly beneath its own
correct list of the six skill categories. The trigger is *confidence, not
difficulty*: it stops checking when something feels obvious.

**The fifth is the one to read, because it happened while the agent was using this
plugin.** Asked to play a Foul, it drove the engine correctly and quoted every roll
verbatim. Then, reporting `unmodelled_skills`, it explained one of them unprompted:

> "Break Tackle (h03, Orc Blitzer) — the engine doesn't apply it, so the dodge I
> just made was a raw AG 3+ roll rather than using Break Tackle's **ST-based
> alternative**."

The engine's half was exactly right: Break Tackle *is* unmodelled and *was* named.
The gloss is wrong. Its own knowledge base says:

> BREAK TACKLE (ACTIVE): "Once per Turn, when this player attempts to Dodge, they
> may apply a **+1 modifier to the Agility Test** if they have a Strength
> characteristic of 3 or lower, a +2 modifier … if 4, or a +3 modifier … if 5 or
> higher."

A modifier to the Agility Test, not an alternative to it. So: structured output
correct, prose around it invented, source available and unread. That is the whole
thesis of this codebase in one reply — and the reason to keep pushing facts into
tool output and rulings into the engine, rather than into anything the model
narrates freely.

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
| A Blitz's Block **costs a point of Move Allowance** | the Block is free |
| A player may **keep moving after** the Blitz's Block | the activation ends |
| A Rush may be spent **on the Block itself**, not just a step | only on a step |
| A Block may only target a **Standing** player | any adjacent opponent |
| Pass, Hand-off, Secure and Foul each allow a **free Move first** | the action stands alone |
| Most Actions are **once per TEAM per turn** | once per player |
| Move and Block are explicitly **not** capped | capped like the rest |
| A Foul may only target a **Prone or Stunned** player | anyone adjacent |
| **"Declares" and "performs" are different triggers** | interchangeable wording |
| **Placed Prone risks no harm** — no Armour Roll at all | it's a knock-down |
| A Crowd push makes **no Armour Roll**, and Stunned = the Reserves Box | armour then stunned on the pitch |
| Dauntless **matches** the stronger player, never exceeds | beats them |
| **As many Team Re-rolls per turn as you like** | one per team turn |
| Unused Team Re-rolls **do not carry over** — the count RESETS both ways | they accumulate |
| A failed Loner **still costs** the Team Re-roll | you keep it |
| Foul assists modify the **Armour Roll**, not Strength | Strength, as in a Block |
| A **natural double on either roll** sends the fouler off | only on a break |
| A successful Argue the Call **still causes a Turnover** | it undoes everything |
| Rolling a 1 bans that Coach from arguing **for the game** | for that foul |

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

**Three flags, three different facts.** `acted`, `done`, and `p.action` /
`match.turn_actions` — and the first two are the ones that get conflated.

**`acted` and `done` are different facts.** `acted` means an activation has
*begun*, so no second Action may be declared — a single step of movement sets it,
which is why it can never be the thing that stops movement. `done` means the
activation is *over*, and that is what `move` checks. With only `acted` to go on,
movement went ungated and a player could throw a Block Action and then stroll
away. A Blitz's Block is the one Block that leaves `done` false, because "after
the player has performed the Block Action, they can continue their Move Action".

Which flag an Action asks about is a RULE, not a detail. Pass, Hand-off, Secure
and Foul each "may also make a free Move Action before" — so they must ask `done`.
They asked `acted`, which a single step sets, so the free Move the rules grant
made the Action itself illegal: **move-then-pass was impossible for as long as
passing had existed**, and nothing noticed because the tests always passed from a
standing start. `actions.refuse_if_spent` is now the one place that decides, and
`ONCE_PER_TURN` / `FREE_MOVE_FIRST` list which Actions are which, beside the
quoted text. Refusals there are ordered eligibility-first: "your team has already
passed this turn" is the one a coach cannot work around, so it outranks "you are
not holding the ball" when both are true.

**Compound actions are a DECLARATION plus the ordinary parts.** A Blitz is not a
`blitz(player, target, path)` command. "If at any point during this Move Action
they are adjacent to … their intended target" is a decision the coach makes step
by step — walk two, see whether the Dodge held, then choose. So `blitz` records
the declaration and rolls nothing, movement stays `move` one square at a time,
and the Block stays `block`. Foul and Throw Team-mate should follow the same
shape; reimplementing a Block inside another action gives you a second Block that
agrees with the first until it doesn't.

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
- The odds tag was `--pl-color-fg` text on `.pc.away`, whose background is
  *also* `--pl-color-fg` — so every block's "2D" was white on white, present in
  the DOM and invisible on the board. Counting elements cannot see that; the
  computed background can, and now does.

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

**A RELOAD USED TO LEAVE THE PLUGIN HALF-NEW, WHICH IS WORSE.** The host purges
the plugin's modules and re-execs every file, so anything reached through a *lazy*
import picks the new code up — `store.py` resolves `Match` inside a function body,
so state was new. But the mounted router still held the function objects
`build_game_router` had imported at build time, so the rules were old. Observed on
the live agent: a match payload carrying `turn_actions` and `argue_banned` that
nothing honoured, `move`-then-`pass` still refused, and a `foul` action the engine
had never heard of. Everything looked like it had worked.

The routers now resolve `engine.game` and `store` **per request** (a `sys.modules`
lookup), so `POST /enabled` means what it says for behaviour changes, and a test
monkeypatches the module attribute after the router is built to prove it. A NEW
ROUTE still needs a restart — that part is FastAPI's and cannot be fixed here.

Diagnostic for a half-applied reload: compare something only new *state* provides
against something only a new *action module* provides. If `/game` carries a field
the action list has never heard of, you are looking at two versions at once.

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

`data/skills.json` — all 108 Skills and Traits, by `tools_scrape_skills.py`. Three
facts on that page are **markup, not words**, and the flattened text loses all
three: a **Trait** is a trailing `*` on the heading (`STUNTY* (PASSIVE)`), an
**Elite** skill is an `<img>` inside it ("an Elite Skill will be denoted by the
symbol" — Block, Dodge, Guard and Mighty Blow, i.e. the four most common on the
rosters), and the **category** is the enclosing `<h3>`. A first pass that matched
only `NAME (ACTIVE|PASSIVE)` dropped every Trait and looked like a page that
documents only Skills. `--check` fails if any roster skill has no entry.

`tools_kb_docs.py` generates one knowledge-base document per team from that JSON,
with every stat **labelled**. Do not ingest the team pages directly: flattened,
Orc's Goblin Lineman reads `6 2 3+ 3+ 4+ 8+` — six values for five stats, the extra
being the struck errata — which is the worst possible input for this agent.

---

## 6. What's done, and what's next

**`docs/PARITY.md` is the score.** Every section of the S3 core rules with a
verdict of yes / partly / no, and a test that fails if the rulebook grows a
section the table never mentions. Read it before planning anything: it says which
of four things a missing rule is waiting on, and that is most of the work.

**Playable now:** set up (by hand, by agent, or from a preset) → kick-off → move,
block, blitz, foul, hand-off, Secure the Ball, pass → injuries → touchdowns →
drives → half-time → full time.

Actions: `move`, `block`, `blitz`, `foul`, `handoff`, `secure`, `pass`.
Skills modelled — **58 of 108**. The other 74 are reported as unmodelled *and can still be quoted* —
`bb_get_skill` returns the rulebook's text for all 108 whether or not the engine
applies them.

**"Modelled" is not binary, and pretending it is flatters.** A Skill with two
clauses of which one is applied would report as modelled and quietly do half its
job — which *sounds settled*, and is worse than saying nothing. So
`skill_hook(..., partial="what is left out")` records the gap, `describe_skill`
returns it, and `skills.partly_modelled_on_pitch` is the standing companion to
`unmodelled_on_pitch`. Both ride with `bb_game_state`. Three entries today:
Juggernaut (only the suppression clause), Stand Firm and Sidestep (the engine
takes a stated policy where the rules give a coach a choice).

**Two hooks carry every roll-modifying Skill**, and adding a third would be a
smell. `roll_modifier` changes the number, `reroll` grants a second go, and the
TEST'S NAME (`dodge` · `catch` · `pick_up` · `intercept` · `pass`) is in the
context. Skills that span several tests then read as they are written — Nerves of
Steel says "to Catch the ball, or … to Pass the ball" and is one `in
("catch", "pass")`. Two conventions worth knowing before writing the next one:

- A modifier that belongs to a THIRD player — not the roller, not the marker —
  goes in `roll_modifier` itself, beside the weather, because the hook dispatch
  walks the ROLLER's Skills and will never see it. Disturbing Presence is the
  worked example: "-1 … for each player on your team with this Skill within 3
  squares of them". Same for a Skill belonging to the player being rolled AGAINST
  — Iron Hard Skin registers a bare marker hook and `skills.from_skills` asks for
  it at the roll site.
- `marking` arrives as the PENALTY (negative) and is already folded into `value`.
  Cancelling it means `value -= marking`. Adding it doubles the exact thing the
  Skill removes — which is the bug a test caught in Nerves of Steel and Stunty.
- A modifier that belongs to the MARKER, not the roller, goes in `rules`, not a
  hook. Titchy's "will not apply a -1 … for Marking" is never applied at all, so
  there is nothing for a hook on the dodger to cancel.

**TEAM RE-ROLLS ARE NOT CAPPED PER TURN.** "A Coach may use AS MANY Team Re-rolls
as they want during their turn, though they may still never re-roll a re-roll." A
previous edition allowed one per team turn and that is what most people will tell
you. The only limits are how many were bought and that a die which is already a
re-roll cannot be re-rolled.

How many a team HAS is a drafting decision, and a practice board was never
drafted — so it is an INPUT with a stated default (`bb_game_new(rerolls=N)`,
`rerolls.DEFAULT_REROLLS` otherwise) and the number rides in every state report.
Same discipline as the Range Ruler: where the source cannot say, take it as input,
default it, and tell the user what was assumed.

**The coach PRE-COMMITS with `team_reroll=True`**, like `choice`, `follow_up` and
`push_to` before it. The engine cannot stop mid-resolution to ask, and spending a
finite resource unasked is not a decision it should make. A free Skill re-roll is
always tried first; the team's only steps in when there was none.

**Half-time resets the count BOTH WAYS** — "replenished at half-time" and "unused
Team Re-rolls do not carry over" are one assignment, up for the team that spent
them and down for the team that hoarded.

**THREE OF THE ELEVEN KICK-OFF EVENTS ARE APPLIED** — Time-out!, Brilliant
Coaching and Cheering Fans. The other eight still report "rolled, but NOT applied
by this engine", which is the point: a coach told BLITZ! who watches nothing move
would reasonably conclude the engine is broken.

The two new ones were unblocked by Team Re-rolls, and both are the same shape:
"Both Coaches roll a D6 and add [a roster number]". **A TIE gives nobody
anything** — the rule says "the Coach with the highest total", and on a tie there
is not one. The roster numbers (Assistant Coaches, Cheerleaders, Fan Factor) are
inputs defaulting to zero, like the Team Re-roll count, and each roll says what it
added.

Brilliant Coaching's re-roll is "FOR THE DRIVE AHEAD", so it is counted in
`drive_rerolls` rather than added to `rerolls`: it expires at the next kick-off,
and it is spent FIRST, which is the only reading that does not quietly throw it
away. Folding it into the bought ones makes "was the bonus or a bought one spent?"
unanswerable, which is why they are two numbers.

**FIVE TRAITS ARE ONE MECHANISM.** Bone Head, Really Stupid, Take Root, Animal
Savagery and Unchannelled Fury all say "Whenever this player is activated, after
declaring their Action they must roll a D6", and differ only in the target, the
modifier and the consequence. They register an `activation_gate` returning those
three things, and `game._run_activation_gates` rolls them — in `act`, before
`resolve`, because they gate the ACTIVATION rather than the Action. A gate in
`move` would not fire on a Block.

**`distracted` was dead state.** The field existed, three call sites READ it
(Tackle Zone, Catch, Intercept) and nothing on earth set it — these Traits are
what produce it. Its full rule adds one more clause the engine can now enforce
for all 108 skills at once: "Whilst a player is Distracted, they cannot use ACTIVE
Skills or Traits", and Active-versus-Passive is in the shipped catalogue. That is
`skills.can_use`, and every hook dispatcher goes through it. Note the duration —
"they will remain Distracted UNTIL THEY ARE NEXT ACTIVATED", so a new turn does
NOT clear it; the player's own next activation does.

**READ THE VERB: "declares" and "performs" are different triggers.** S3 gives it a
worked example, because it reads like a technicality until it costs you a Skill:
during a Blitz, "a rule that comes into play when a player DECLARES a Block Action
would not come into effect — the declared Action was a Blitz Action", while one
that says PERFORMS does. So Grab, Brawler and Multiple Block are switched off on a
Blitz; Tackle, Dauntless, Wrestle, Claws and Fend are not. `block.declared_a_block`
is the one place that decides it. Grab shipped without this and was wrong for a
release.

**There are THREE ways onto the floor and only one is free.** S3 names them:
"Placed Prone, Falls Over or Knocked Down". Placed Prone "aren't at risk of being
caused harm" — no Armour Roll, no Injury Roll — and it is the whole value of
Wrestle. `injury.place_prone` is that path; using `knock_down` for it hands out
armour rolls the rules do not allow.

**A roll cannot live in `validate`.** Dauntless is a D6, so validate REPORTS that
the roll is coming (`dauntless: true`) and resolve makes it; Horns is
deterministic, so it is in both and the odds a coach is shown are the odds they
get. Reporting odds that resolve then ignores would be worse than not reporting
them at all.

**FOUR SKILLS FIRE WHEN AN OPPONENT LEAVES YOUR TACKLE ZONE**, and they are one
mechanism seen from four angles — `engine/leaving.py`. The ORDER is the rules'
own and changes outcomes:

  1. **Tentacles**, before the roll — it stops them leaving at all, so a Dodge
     that never happens cannot be failed, re-rolled or Diving-Tackled.
  2. the Agility Test, its modifiers and its re-rolls.
  3. **Diving Tackle**, after all of that — "an Agility test has been rolled and
     any modifiers and re-rolls have been applied". That is what makes it worth a
     Skill: the coach spends it knowing whether it will matter. It costs the
     tackler their feet every time, so the engine spends it only when it turns a
     success into a failure.
  4. then either they left (**Shadowing** follows) or they Fell Over (**Arm Bar**
     adds its +1 to whichever roll needs it).

"Only one of those players may use this Skill" is PER SKILL, not across them — a
player can be Tentacled and Diving-Tackled by two different opponents in the same
step. Note also that the Diving Tackler is placed in the vacated square LAST,
after the dodger has left it; placing them first puts two players on one square
for the length of a knock-down and `match.at` answers with whichever it finds.

**A NEW ONCE-PER-TURN FLAG HAS THREE RESET SITES.** They used to name the flags by
hand (turn start, drive start, and `from_dict`), so a forgotten one would make a
Once-per-Turn Skill work once per MATCH — and nothing would say so. Adding Sure
Feet's `rush_reroll_used` found all three; they go through `ONCE_PER_TURN_FLAGS`
now, and a test asserts a new turn clears every flag in the list.

**Injury hooks run in registration order and Stunty must go first**: it REPLACES
the table (Stunty Injury Table: 2-6 Stunned · 7-8 KO · 9 Badly Hurt · 10-12
Casualty) and Thick Skull then ADJUSTS the result. Reversed, a 7 comes out
Knocked-out when the rules say Stunned. `test_stunty_and_thick_skull_together`
fails if they are ever swapped.

**THE ENGINE CAN ASK NOW, AND IT STOPS THE WORLD WHILE IT WAITS.** Three Kick-off
Events give a Coach a real choice, and the Kick-off Event is resolved *before the
ball lands* — so the engine cannot pick for them and cannot carry on. It records
the question in `Match.pending`, refuses every other action with the question
attached, and `bb_game_choose` answers it. Three things worth knowing:

- **Declining is always legal** — all three say the Coach "may" — and it is a real
  answer, not a no-op, because nothing else can happen until one is given.
- **The question travels in the STATE, not just the log.** The coach answers in a
  separate call, by which time the Match has been rebuilt from disk.
- **An illegal answer is not an answer.** The question stands and nobody moves —
  half a formation committed is a formation nobody chose.
- **The Drive stops with it.** "At this point the ball is still high up in the air
  and cannot be caught until after the Kick-off Event has been resolved" — so the
  kick does NOT land the ball and the first turn does NOT start while a question
  is open. `game._finish_kickoff` runs that tail when the answer arrives. The ball
  carries `in_air` for exactly this reason: `in_play` has always meant "on the
  board somewhere", which read as "landed" only because it used to land in the
  same call.
- **CHARGE! is the odd one out**: its answer only STARTS something. The selected
  players then take their free Actions through the ordinary `act` path — "exactly
  as if it was their team's Turn" — with `engine/charge.py` as the fence around
  it: who may act, which of the three one-off Actions are left, and the stop
  condition. A selected player hitting the floor ends the Charge and is NOT a
  Turnover; a Turnover would advance the Turn Marker and hand over a ball that has
  not even landed.

The Apothecary's CASUALTY branch rides the same rail, and is worth reading as the
non-kick-off example: "the opposing Coach makes a second Casualty Roll … and the
player's controlling Coach may select either of the two results to apply." It
rolls, spends the Apothecary (on DECLARATION — win or lose), and stops. Only a
Badly Hurt result brings the player back. Declining there means *keep the roll you
had*, because by then there is no doing-nothing left.

Whether to ASK or to state a policy is now a live judgement call, not a missing
capability. Sidestep and Stand Firm still take a policy on purpose (`partial=`
says so); Argue the Call does too. Ask when the choice can change the outcome.

### Next, roughly in order

1. **The remaining 74 skills.** `data/skills.json` holds every one with its real
   text; quote it into the docstring beside the hook and the catalogue's
   `modelled` flag flips on its own. What is left is no longer a long tail of
   one-liners — it groups:
   - **Special Actions** (Stab, Chainsaw, Breathe Fire, Projectile Vomit,
     Bombardier). Each is a new module in `actions/`, and Foul is the template:
     declared, once per turn, its own roll, its own consequence.
   - **Throw Team-mate** and its retinue (Right Stuff, Always Hungry, Bombardier,
     Bullseye) — a subsystem, not a skill.
   - **Frenzy**, which is the genuinely hard one: "they MUST perform a second
     Block Action" makes the engine take an action nobody asked for, and nothing
     in here does that yet.
   - The rest are ordinary hook registrations against machinery that exists.
2. ~~A match started from a preset has statless players.~~ Fixed — `state.flesh_out`
   gives every statless token the team's LINEMAN (cheapest positional, the only
   0-16 on every roster) at match start and says in the log that it did. The label
   is kept. Two tests, one of them through HTTP.

### Known simplifications, all deliberate and all stated in the code

- One of the eleven Kick-off Events is reported rather than applied: **Get the
  Ref**, which needs Inducements — a League feature. The other ten are applied,
  four of them by ASKING (`Match.pending` + `bb_game_choose`), one of which —
  **Charge!** — is a free turn rather than a question (`engine/charge.py`).
- A Blitz may be re-pointed at a different target until the player moves or
  blocks — declaring rolls nothing, so a mis-named target costs a coach nothing to
  correct, and the team's one Blitz is spent by the same player either way. The
  bound is what makes it safe: re-declaring after the Blitz's Block would reset
  `blocked` and buy a second one, and `acted` refuses it. Tested in both
  directions.
- Argue the Call is rolled for you rather than offered as a choice. The rules say
  a Coach "MAY attempt" it, but declining is never better: 2-5 changes nothing and
  only a 1 costs anything, and a 1 costs the same whether or not you argued this
  particular call. Now that `Match.pending` exists this COULD be asked; it is left
  alone because asking a question with one sensible answer is noise, not fidelity.
- No Inducements or Fan Factor. Cheerleaders and Assistant Coaches are inputs that
  default to zero and say so in the log; Weather is modelled.
- The Throw-in direction is thrown straight back in from the edge crossed; the real
  Throw-in Template is a diagram.
- The version is `0.5.0` and the plugin has never been released or tagged.
