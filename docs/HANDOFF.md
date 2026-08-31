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
web/            index.html + style.css + seven ES modules (no bundler)
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

### Head-to-head: two coaches, one board

**Ownership is STATE, not convention** — `match.controllers` maps a side to
"human" or "agent", set at `bb_game_new(you=…)` / `POST /game/new {"you": …}` and
folded from `match_started` like everything else. Empty means unclaimed, which is
the practice board and stays permissive: one person moving both teams on purpose.

**Both surfaces reach the same engine**, so each declares WHO IT IS — the routers
pass `by="human"`, the tools pass `by="agent"` — and `game.refuse_if_not_yours`
decides. Neither surface polices itself, because neither can: an agent that
respected a rule only in its own tool code would stop respecting it the moment
somebody called the route.

**⚠️ A FULL-AI SEAT GETS A FRESH CONVERSATION PER TURN, AND IT HAS TO.**
A seat's context grows by roughly 40k tokens per turn: one `bb_game_state` is
~2.6k, `bb_game_legal` is asked per player, and every `bb_game_act` returns its
events. MEASURED on a live match — an away seat rebuilt **165,000 tokens in FOUR
turns** after being purged (63 messages, 659,977 chars).

It degraded exactly as that predicts: burning a whole 600s fire timeout on three
model calls without moving a single player, then not responding to a nudge at all.
Purging bought precisely one turn before it recurred. The earlier per-MATCH token
(#85) does nothing for this — it stops a new match inheriting an old one, not a
single match drowning itself over sixteen turns.

A turn is SELF-CONTAINED: the board is the truth and the engine is authoritative,
which is the invariant the whole plugin rests on. A coach who needs the history has
`bb_game_log`. So the nudge keys the session on half/turn and the context is
bounded by construction rather than by hoping the match ends before the window
does.

**HEAD-TO-HEAD IS DELIBERATELY UNTOUCHED** — there the session is the PERSON's
chat, and splitting it per turn would scatter their game across sixteen threads.
Only the seats minted by `_ai_sessions` are split. There is a restraint test.

~~**STILL OPEN:** the board payload itself is ~10.5k chars for 22 players.~~ Done —
the wire drops player fields sitting at their default, which is most of a
once-per-turn flag set. It touched both views and the suite, which is why it was
its own change rather than a rider on the context fix.

**⚠️ THE NUDGE JOB ID IS UNIQUE PER ATTEMPT, AND THAT MATTERS TWICE OVER.**
`sdk.run_in_session` is idempotent-REPLACE: re-using an id cancels whatever that id
has pending — INCLUDING A TURN THAT IS CURRENTLY RUNNING.

* A CONSTANT id was the first version of this bug: a nudge for turn 3 cancelled
  turn 2 and the game stopped dead with nobody to act.
* Per-HANDOVER fixed that and introduced a subtler one. Re-nudging the same
  handover is exactly what `bb_game_nudge` / `POST /game/nudge` are FOR, and what
  any watchdog does when a board looks stuck — and it killed the turn it was trying
  to rescue. A seat mid-blitz reported "my previous turn was interrupted
  mid-action" three times over, and the abandoned A2A stream produced an
  `httpx.ReadTimeout` that the scheduler logged as a failed fire and RETRIED,
  starting the same turn again.

Per-ATTEMPT now. A re-nudge queues instead of cancelling, and a duplicate turn is
safe because the seat check refuses anything that is not that side's move.

**OPERATIONAL COROLLARY: do not re-nudge on a timer.** At 11-a-side a turn takes
2-5 minutes; a watchdog firing every 5 minutes will land on a live turn. Three
watchers running at once (one left behind per deploy) produced three nudges in 16
seconds and froze the board for half an hour. Report a stall; let a person decide.

**The nudge.** `engine/handover.py` works out who the match is waiting on and
whether that CHANGED; `__init__.announce` publishes `bloodbowl.turn_ready`; and
`register()` subscribes and calls `sdk.run_in_session` to run an agent turn from it.

**NOT `sdk.react_on`**, which is the obvious tool and the wrong one: it binds ONE
session at registration, and the session varies per match. The whole point is that
the opponent's turn arrives in the chat you are playing in.

**FULL AI is a THIRD mode, not a variant of the head-to-head** — `you="neither"`
claims both seats for the agent and the game plays itself to full time. It needed
almost nothing new, because `handover.owed` never cared which side it was
answering about: it reports whoever is owed and who controls them, so two agent
seats alternate through the same nudge that always existed.

**⚠️ AND THE OWNERSHIP CHECK WAS A NO-OP FOR IT.** `refuse_if_not_yours` compared
CONTROLLER KINDS — and in a full-AI match both sides are "agent", so `mine == by`
was true for EITHER seat. Each was free to move the other's team, and one did: the
home seat played a Skaven turn, moved their Gutter Runner onto the ball, ended the
turn, and then spent the rest of the game insisting it was not its move.

A seat now names its SIDE, not its kind. `_seat_of(match, state)` resolves it from
the session the tool is running in, matched against `Match.session_ids` — the only
thing that distinguishes two seats that are otherwise identical. It falls back to
"agent", which is correct for a head-to-head where there is one agent seat and the
kind identifies it fine.

The test states the defect rather than only the fix: two assertions show that
`by="agent"` is admitted on BOTH teams' turns, which is what the tools used to
pass. They pass on the unfixed engine — that IS the bug.

**⚠️ AND THAT CONVERSATION MUST BE FRESH PER MATCH.** The seat ids were fixed
strings, so every full-AI match ever played reused `bloodbowl:home` and
`bloodbowl:away`. A seat therefore inherited every earlier match's transcript —
and after two games were abandoned it held three consecutive turns concluding
"No match in progress — game already concluded". It then repeated that for a LIVE
match **without calling `bb_game_state` at all**, and the board sat frozen for
nine minutes while nudges fired into it. Purging the two sessions unstuck it
instantly.

A model trusts its own recent output over its instructions — the same force that
made it ignore `path` while its transcript was full of single-square calls. The
fix is not to argue with it in the prompt (#82 already tried: "THE BOARD IS THE
TRUTH") but to stop handing it somebody else's conversation. `_ai_sessions` now
mints a token per match.

**The one thing it did need is a conversation PER SEAT** (`Match.session_ids`,
`session_for(side)`). One `session_id` is right while the only agent seat is the
opponent's; it collapses the moment both seats are agents, because they would
share a context and each would read the plan it had just made for the other team.
Two chats, and all either seat gets is the board — which is the engine-is-the-
authority invariant doing the work it was built for. Per-side first, match-wide as
the fallback, so every existing caller is untouched (there is a restraint test
pinning the head-to-head and the practice board unchanged).

`session_bound` grew an optional `side`: with one it moves a single seat, without
one it rebinds the whole match as before.

**The nudge's closing line is conditional on `opponent`**, which `owed()` now
reports. "Tell your opponent what you did" is right against a person and actively
harmful when the other seat is a separate conversation that will never read it —
it invites precisely the narrating-to-yourself the human version warns against.
In a full-AI match the note is addressed to the SPECTATOR, who is the only
audience a self-playing game has.

**Which chat is `match.session_id`**, recorded by `bb_game_new` from the tool's
`InjectedState`. `current_session_id()` reads EMPTY in a tool body — the tracing
contextvar does not survive the hop — and graph state is the reliable carrier. The
import is GUARDED: `langgraph` is a host dependency, this plugin's suite and its
harness register with no host, and losing the binding is exactly what "no host"
should cost. A board-started match has no chat at all and falls back to the
Activity thread; `bb_game_here` is how a person moves it into their own.

Three more notes:

* `handover.changed` is what stops it firing on every action of the agent's own
  turn. The moment the ball passes over is the news; the state of it having passed
  is not.
* An unanswered Kick-off question OUTRANKS whose turn it is — it blocks the game
  including the ball landing, and nothing about the clock looks wrong while it does.
* The whole thing is guarded: no host SDK, no nudge, and every test and the browser
  harness run with no host at all.

**The agent is PACED and this is deliberate** — `engine/pace.py`, config
`bloodbowl.agent_pace_s`, default 2s. It is a real wall-clock wait on the agent's
tool path only. It is not a rate limit (it waits, it never refuses — a refusal
would just teach the agent to retry), it is not in the engine (the rules know
nothing about it), and it is off in tests. **The conftest zeroes it in TWO places**:
the autouse fixture and `_Registry.config`, because `register()` re-reads it and
would otherwise turn it back on for any test that registers the plugin. Ten
seconds appeared in the suite the first time; that is what it looks like.

**THE AGENT'S REAL BUDGET IS TOOL CALLS, AND A MOVE COSTS ONE PER SQUARE.** This
is not a rules problem and it does not show up in any test — it killed a live turn
mid-activation. The host runs an agent turn as one graph invocation with a step
budget (protoAgent: LangGraph `recursion_limit`, 200), and a middleware stack
multiplies each model call into several steps — so the budget in practice was about
25 tool calls per turn. One Blood Bowl turn is eleven activations of up to a dozen
squares each. A Grail Knight walking eight squares spent ten calls on its own; the
turn died three players in, with a Blitz declared and never thrown.

`game.walk` is the fix: `bb_game_act(action="move", path=[[8,15],[8,16],…])` walks
the whole run in one call. **It collapses the round trip, not the rules** — one
engine Move per square, every Dodge and Rush rolled and logged in turn, and the
ROUTE stays the coach's decision, because which squares a run crosses is the
tactical choice and an engine picking them would be inventing play. It stops where
the plan stops applying (refusal · Turnover · down or off the pitch · pushed
somewhere other than the square asked for) and reports `steps_taken` /
`steps_requested` / `halted`; `ok` means EVERY square was walked.

Two things about it worth keeping:

* **Saving and pacing live in the tool, not in `walk`** — `after_step` is the seam.
  The engine stays free of both, and a caller that saves per square is what keeps a
  run watchable: the board polls the saved match, so a run persisted only at the end
  arrives as a jump cut and you cannot see which step cost the Dodge.
* **The test that matters is the control**: three squares walked as one call and as
  three calls land in the same square having rolled the same dice. A collapsing
  optimisation that quietly changed the game would otherwise look like a feature.

The view keeps its own client-side walk (`web/js/game.js:walkPath`) because it has
to render between steps; the halt conditions are deliberately the same list.

> **⚠️ THE THIRD MATCH FOUND THE STEP THAT WAS MISSING FROM THE LIST.** With the
> carrier rule and the race fixed, a seat kept its ball carrier upright for two
> entire turns — and moved it three rows, sideways. It never fell over and it was
> never going to score either.
>
> The procedure had score, protect-the-carrier, blitz, block, cage and screen, and
> **no step that moved the ball**. Every other step feels more urgent, which is
> exactly why advancing has to be numbered rather than assumed. It is step 4 now.
>
> `routes` gained `downfield` for it: the furthest square toward the line being
> attacked at each of two safety bars. `to_end_zone` only exists when the End Zone
> is REACHABLE, so from fifteen rows out a coach asking "can I score?" got nothing
> back and fell to picking a square out of two hundred sorted by safety — which is
> sorted by the wrong thing for that question. A drive has about six usable turns
> and the pitch is 26 rows: one row a turn never scores.

> **⚠️ AND THE SECOND MATCH FOUND A RACE, WHICH THE COACH DIAGNOSED ITSELF.**
> A tool call loads the match, applies an action and saves it back. A model
> batches independent tool calls IN PARALLEL — normal, and usually desirable —
> and two of them racing both read the same state, each apply their action, and
> the second save silently discards the first. **The action does not fail; it
> vanishes.**
>
> The seat worked it out and still lost the turn to it: *"I intended to build a
> cage but the cage moves (h03, h06) did not persist due to the parallel-call
> race."* It then read the board 22 times and tried to end its turn 11 times,
> arguing with a board that disagreed with what it had just done. That is where
> most of a 99-call turn went.
>
> `store.match_write()` serialises the WHOLE round trip, because the unit that
> must be atomic is load→apply→save and not the write: locking only `save_match`
> still lets two callers read the same state and clobber each other. The test
> drives the real tool from two threads and **fails three times out of three
> without the lock** — a test that called `game.act` directly would pass against
> the broken version, which is the trap here.

### The coaching skill (`skills/coaching-a-turn/`)

> **⚠️ THE FIRST MEASURED MATCH FOUND THE HOLE IN THIS SKILL, ON TURN ONE.** A seat
> picked the loose ball up (correct), then **declared a Blitz with the same
> player**, rolled Player Down, and ended its own turn with its first activation.
>
> The skill was loaded — `coaching-a-turn`, "Score if you can", "top to bottom"
> and the thresholds were all in that seat's context — and every other step was
> followed. It lost the turn because the procedure **never said the carrier does
> not hit anybody**. That is so basic it did not get written down, which is the
> whole reason to run the thing rather than reason about it.
>
> It is now step 2, phrased as a rule that outranks the rest rather than a
> threshold to weigh, and `legal_moves` marks every block offered to the ball
> carrier with `carrying_the_ball` and a warning. **The engine still does not
> refuse** — hitting while carrying is legal and at the end of a half can be right
> — it makes the price visible, like `bb_pitch_review` reports instead of vetoing.



**The plugin shipped 38 tools and no skill.** The engine taught the agent what was
LEGAL and nothing taught it what was GOOD, and it played accordingly: one recorded
turn spent 47 tool calls on 7 actions, and a full sixteen-turn AI-vs-AI match
finished 0-0.

That result is not a curiosity. Blood Bowl's turn branching factor is ~10^50
(chess ~30, Go ~300) and **a random agent scored zero points in 350,000 matches** —
0-0 is what near-random play looks like. The research is unusually clear about the
remedy: in Bot Bowl I and II, machine-learning entries could beat a random opponent
and never a scripted one; GrodBot, scripted with heavy domain knowledge, won the
first competition; and the eventual ML winner got there by imitating a scripted
bot first and keeping scripted rules on top. **Encoded domain knowledge is what
plays this game.**

So the skill is an ORDERED PROCEDURE, executed top to bottom, not a pile of
advice. The agent's SOUL already had good principles — turn economy, sequencing,
position over heroics — and principles without a sequence is exactly what produced
the 0-0. The order is the skill: a safe action taken early can make a risky one
later unnecessary, which is why you sequence rather than survey.

The thresholds (score ≥0.70, pickup >0.33, cage and blocks >0.94) come from
GrodBot, which was written for an older edition, and the skill says so: they are a
default to argue with from the score and the clock, not scripture. Two down with
three turns left, a 50% score attempt is right; level with eight turns, it is a
blunder.

A test pins that the skill names only tools that exist — a procedure citing a dead
tool reads as authoritative and sends the coach after something that will never
answer — and that it stays an ordered, numbered list with its thresholds intact.

### Telling a coach where they can go (`game.routes`, `dice.chance`)

`bb_game_legal` answers for the eight squares beside a player. A run is three or
four steps and **its risk is the product of them** — and that multiplication is
the single easiest thing in this game to get wrong by eye. The SOUL of the agent
built on this plugin told it so ("three separate 2+ rolls is not three safe
rolls, it is 58%") and then gave it nothing that could compute it, so it did the
arithmetic in its head, badly, 21 odds-calls at a time.

`game.routes` searches every reachable square and reports the chance of ARRIVING
ON YOUR FEET, counting each Dodge and Rush with the engine's own modifiers. It
names the two destinations that decide most turns: the safest route to the End
Zone you are attacking, and the safest route to a loose ball WITH the pick-up
rolled in — walking to a ball you then fumble is how a drive ends.

**`dice.chance` lives beside `roll_target` on purpose.** The odds a coach is
shown and the roll they describe have to be one rule; if the natural-1/natural-6
clause ever moves, both move together. It counts faces rather than doing algebra
so the two stay obviously identical — which is also why "needs 7+" is 1/6 rather
than zero, and "needs 1+" is 5/6 rather than certain. Those two are exactly where
eyeballed odds go wrong.

**Best-first on probability, so the SAFEST route wins, not the shortest** — and
that turns out to find real Blood Bowl. Stepping straight from a Marked square
into the square next door is a Dodge at -1 if an opponent Marks it; dodging out
to open ground first and walking back in needs no second Dodge at all. 67% in two
steps beating 50% in one. Nobody taught it that; it falls out of searching on
odds. There is a test.

> **⚠️ THE DRIFT GUARD IS CONTAINMENT, NOT EQUALITY.** `routes` uses the same rule
> helpers as `move.validate` but its own loop, so a test pins that every square
> the engine allows is reachable. It must NOT demand that one-step routes equal
> `legal_moves` — the safest route to a neighbouring square is often longer, and
> asserting otherwise asserts the search is dumber than it is. That is exactly how
> this test failed the first time it ran.

`situation()` rides `bb_game_state` for the same reason: direction, possession and
distance-to-score are the three facts every decision hangs off, `touchdown_row`
has always known the first, and an agent was recorded mid-turn arguing with
itself about which way it was attacking.

### Die faces in the log (`web/js/dice.js`)

`/game/log` sends every roll TWICE — `rolls` is the sentence the engine wrote
("Dodge: needed 3+, rolled 2 — FAILED") and `dice` is the same roll structured.
That is not redundancy. The sentence is what the agent quotes and what survives a
copy-paste into an argument; the structure is what a view can paint. Deriving
either from the other means a second `describe()` to drift from the real one, and
the drift would land in the exact place a coach looks to see WHY.

**The view still reaches no verdict.** `passed` arrives from the engine with the
Skills that modify it already applied, so the face renders it rather than
recomputing `total >= target` — which would disagree the moment a Skill mattered.
A test greps `dice.js` for exactly that comparison. For the same reason a Block
face is NOT coloured by whether it is "good": which face a coach wants depends on
who chooses and on their Skills, and that is a ruling.

**The glyphs are drawn, not imported, and that is a licence decision as much as a
design one.** The FFB/FUMBBL artwork the obvious clients use cannot be
redistributed without christerk's explicit permission (jervis-ffb has it and says
so in its LICENSE; we do not). Drawing them also keeps them on `currentColor`, so
they follow the console theme — a raster die would sit in a dark panel as one
fixed bright square, which is the same class of bug as the odds tag that rendered
`--pl-color-fg` on `--pl-color-fg` and vanished.

**They are shaped to be TOLD APART at 16px, not to be self-explanatory** — the
line above already names the faces in words. The first cut drew little figures
lying down and they resolved into identical grey smudges, so direction carries it
instead: DOWN (a chevron) means somebody hits the floor, ALONG (an arrow) means
somebody is moved. Both Down doubles the chevron, Stumble gives the arrow a
dotted tail. Three harness checks pin it: that faces reach the screen at all, that
a Block die carries a glyph rather than being an empty box, and that its ink
differs from its own background.

> `const` after first use is a temporal dead zone, and in a module that means the
> BOARD DOES NOT RENDER — `BLOCK`'s initialiser calls the glyph builders, so the
> shared stroke string has to be declared above it. The suite stayed green (it
> reads the file as text); the harness caught it as `.cell` never appearing.

### The 3D board (`web3d/` → `web/3d/`)

A SECOND declared view, React + React Three Fiber, built with Vite. The 2D board is
untouched: both call `/game/legal` and neither computes a rule, which is what
stops two renderers becoming two silently diverging rules engines (there is a test
per view pinning it).

Four decisions worth not re-litigating:

* **The page is served by a REAL route** (`/plugins/bloodbowl/view3d`). It was
  first declared straight at the built file inside the static tree, to dodge the
  process restart a new route costs. That serves fine by hand and is the WRONG
  SHAPE: the host validates a view path against the paths its ROUTERS serve
  (`graph/plugins/loader.py::_served_paths`) by **exact string match**, and a
  parameterised route is stored literally as `/plugins/bloodbowl/static/{path:path}`
  — so no concrete file beneath it can ever match. The host warned on every boot
  ("no registered router serves it — it will render a blank/404 iframe") and was
  right about the shape even though `curl` got a 200. **Rule 1 of the plugin-view
  guide is not advisory.** A test now pins every declared view to a literal route.
* **The page therefore cannot use relative asset URLs**: it is served from
  `…/view3d` while its bundle lives under `…/static/3d/assets/`. It addresses the
  bundle absolutely off the base it already derives, so one HTML is correct at any
  route, on the host window and through the `/agents/<slug>` proxy alike. That is
  why the bundle filename is FIXED rather than content-hashed — the page names it
  at runtime, so a hash would need a build-time rewrite of the HTML.
* **The built output is COMMITTED.** The plugin installs from a git URL onto hosts
  with no Node, exactly as the console ships a prebuilt dist. `web3d/node_modules`
  is ignored; `web/3d` is not.
* **Labels are canvas textures, never a font file.** drei's `<Text>` resolves an
  unset `font` to a Google-hosted default — this plugin declares `network: []` and
  runs sandboxed, so it would fail SILENTLY: the scene still renders, just with no
  way to tell which player is which. A test pins that `Text` is not imported.
* **The kit import degrades.** `_ds/plugin-kit.js` is served by the CONSOLE, so it
  is absent in the plugin's own harness; a view that throws on import shows an
  empty canvas with no clue why. It falls back to a plain same-origin fetch.

### Voxel players (`web3d/src/voxelPlayer.js`, `teamPalette.js`, `VoxelPawn.jsx`)

The DEFAULT pawn, not a placeholder: an uploaded mesh replaces it, nothing requires
one. Architecture lifted from MechArena's `visual-engine/src/voxel` — sparse unit
voxels tagged with a MATERIAL SLOT, composed per ARCHETYPE, palette resolved
separately, all derivation in zero-import modules.

**The archetypes are the roster's own taxonomy.** Every positional carries Keywords
under `role`, and Lineman / Big Guy / Blitzer / Thrower / Blocker / Runner / Catcher
cover all 159. Bulk comes from ST. Nothing is invented — a test reads the keyword
table out of the JS and checks it against `rosters.json`, rather than
reimplementing the rules in Python where the two copies could drift apart.

**`role` had to be added to the wire.** It already drove Hatred, Animosity and
Bloodlust server-side but was never serialised, and a view cannot tell a Big Guy
from a Blocker by position NAME — an "Ogre Blocker" is `Big Guy, Blocker, Ogre`,
so keyword ORDER decides it and Big Guy must win.

**⚠️ three.js `Color.setStyle` ONLY PARSES THE LEGACY COMMA SYNTAX.**
`hsl(14 72% 52%)` — CSS Color 4, what every browser and stylesheet accepts —
silently yields WHITE. No throw, no warning: a `Color` constructed white simply
stays white. Every player rendered in the material's default with a perfectly
correct scene graph: 512 instances, `instanceColor` allocated, all of it [1,1,1].
Counting instances could not see it and neither could a console listener; only
reading the instance buffer back did. Use `hsl(h, s%, l%)`.

**The palette is tuned for the TOP-DOWN read**, because that is the angle a board
is mostly looked at. A near-white accent (l=0.82) on helmet and shoulders put the
team colour underneath the parts nobody sees, and the board read as two rows of
pale slabs.

`window.__bbVoxel[playerId]` is a deliberate testability hook — count, whether
`instanceColor` exists, and the first instance's colour. There is no DOM per cube
and `getComputedStyle` sees nothing, so the scene's own state is the only thing a
browser harness can asserted against.

### The roster builder (`draft.py`, view `/plugins/bloodbowl/draft`)

A Team Draft List: who is on the team, costed and checked. NOT a board — placing it
is a separate step, so one squad can be set up in as many shapes as a coach wants.

**Every global limit in `draft.py` is QUOTED from the rulebook**, looked up in the
agent's own S3 knowledge base rather than recalled, because this is precisely what
recall gets subtly wrong. Budget "usually 1,000,000 gold pieces"; "at least 11 …
when it is first drafted"; "never have more than 16"; "a maximum of 8 Team
Re-rolls"; "a maximum of 6 Assistant Coaches … 10,000 gold pieces" (and the same
sentence again for Cheerleaders); one Apothecary at 50,000, availability per team;
Dedicated Fans start at 1 and improve "up to a maximum of 3 … at the cost of 5,000
gold pieces per improvement". The TEAM-specific numbers are not in the module at
all — Hiring Fees, quantity limits and the Team Re-roll cost live in
`data/rosters.json`, scraped per team.

**The budget is an INPUT with a stated default**, like the Range Ruler and
`bb_game_new(rerolls=…)`: the rules say *usually* 1,000,000 for a rookie team, and
Exhibition and Matched Play name their own.

**An illegal roster SAVES; only placing refuses.** A team is over budget and short
of players for nearly all of the time it is being drafted, so a builder that
refused work-in-progress would be unusable. `problems()` returns EVERY broken rule
rather than the first — a coach mid-draft is usually breaking several, and fixing
them one refusal at a time is miserable. The board is shared state, so placement
is where the line is drawn.

**Placing into a named SHAPE is where a draft becomes a scenario.** The shipped
presets are legal setups by construction (`tests/test_presets.py` checks each
against the board's own `review()`), so placing exactly their squares inherits
that instead of re-deriving it, and `presets.apply_to` owns the mirroring so the
route does not re-implement the geometry.

**`draft.assign` is a STATED DEFAULT, not a recommendation.** Preset squares are
labelled by role in the SHAPE ("LOS", "screen", "back", "safety") rather than by
Blood Bowl position, because a shape has to transfer between teams — so filling
one is a coaching decision, and the engine does not make those. But filling it in
DRAFT ORDER would stand a ST1 Gnoblar on the Line of Scrimmage with three idle
Ogres behind him, which is worse than having an opinion. The line takes the
highest Strength (AV breaks a tie), deep squares the highest MA, the rest fill in
order. Anyone can be moved afterwards; this only decides where they start.

**ELEVEN TAKE THE FIELD, NOT THE DRAFT LIST.** A Team Draft List holds up to 16;
the board's own rule is "11 players on the pitch — the limit is 11". Placing was
capped at `MAX_PLAYERS` (16) and happily put an illegal side out. Both placement
paths now cap at `pitch.MAX_PLAYERS_ON_PITCH`, and the reply carries the board's
own `review()` verdict rather than claiming legality here.

**`squad()` is ordered by HIRING FEE, dearest first** — another stated default.
Taking the draft list as written meant dict order, which fielded eleven 15,000gp
Gnoblars and left all three 140,000gp Ogres in the reserves box: legal, and the
wrong eleven. A coach fields their best.

**A shape is topped up to eleven, BEHIND the line.** "Kick-off receive" is ten
squares and "Line of Scrimmage only" is three. `draft.fill_squares` imports the
board's limits rather than restating them (`MAX_PER_WIDE_ZONE`, `half_of`) and
fills Centre Field first. It never fills ON the Line of Scrimmage: the shape owns
the front row, and topping up there puts whoever is left over — typically the
cheapest player on the list — exactly where the hitting happens. Legal, bad
coaching.

**⚠️ MIRROR FIRST, THEN TAKE THE TARGET SIDE.** `presets.apply_to(side=…)` filters
on the preset's OWN stored side, and every shipped shape stores HOME rows — so
asking it for "away" matched nothing, the shape came back empty, and the top-up
quietly fielded eleven players with NOBODY on the Line of Scrimmage. It looked
fine until the board reviewed it. Pass `side=""` with `mirror=(side == "away")`
and filter the RESULT. Found by a sweep over every shipped setup × both sides —
the single-case test passed throughout.

**The view scores nothing itself.** Cost and legality come back from the server on
every change, from the same functions that gate placement — a builder that scored
itself would be a second rulebook, which is the failure this plugin exists to
avoid.

### Player icons (`sprites.py`, `web/js/sprites.js`, `web/sprites/`)

The board drew coloured tiles with initials. It now draws the FFB icons — the
ones the FUMBBL client uses, vendored with christerk's permission (Credits, in
the README). 154 sheets, 1.5 MB, 157 of the 159 positionals covered.

**THE POINT IS THAT THEY ARE REPLACEABLE.** These are a good default, not the
destination — the plan is our own art — so the whole module is built around
swapping them out without a code change. Resolution, first hit wins:

1. **`sprites.json` in a pack** says so outright: `{"Orc": {"Orc Lineman":
   {"file": "...", "columns": 1}}}`.
2. **A file named in OUR words** — `orc__orc-lineman.png`. No table, no code.
   Custom art should not have to learn FFB's naming to replace it.
3. **The FFB-derived match** (below), which is what the shipped pack uses.
4. **Nothing**, and nothing is fine — the tile draws.

`bloodbowl.sprite_dir` points at an operator's own pack, searched before the
shipped one. **It only needs to hold what it replaces**: resolution falls through
per positional, so redrawing one Troll does not mean redrawing the other 152.
The static route resolves `/static/sprites/<file>` through the packs, so a
directory outside the repo serves at the same URL.

**A MISSING ICON IS ORDINARY, AND THAT IS THE WHOLE DIFFERENCE FROM
`fumbbl.py`.** Importing a team wrong gives a coach the wrong STATS, so that
module refuses to guess and names what it could not map. An icon is decoration:
the worst case is the tile that was already there, so this one is allowed to be
generous — near matches are taken and two positionals may share a sheet. Same
contract as the 3D mesh library: it can only ever upgrade the board.

**How the FFB match works, and why the middle pass is the good one.** Names
first. Then the ROLE — FFB names many files after the job (`highelf_thrower`) and
`data/rosters.json` already records the job in `role` ("Thrower, Elf"), so
Phoenix Warrior resolves *because our own data says it is a Thrower*, not because
somebody remembered which S3 name replaced which BB2020 one. That was the
alternative, and it is exactly the confident recall this plugin exists to route
around. Then a short alias table for genuine renames, every entry carrying its
reason. Two Bretonnian positionals have no FFB icon at all and keep their tile.

> **⚠️ THE CELL IS NOT 28px.** It is `width / columns`, and it runs 20 to 42
> across the shipped sheets because a Troll is drawn bigger than a Skink.
> Assuming the commonest (28, in 54 of 154) slices every Big Guy in half. Four
> columns — 0-1 red kit, 2-3 blue — and `height / cell` variant rows, so eleven
> Linemen are not eleven identical figures. All of it derived per file and sent
> with the catalogue, so **the view measures nothing**; custom art declaring
> `"columns": 1` is a single flat image and no code changes.

> **⚠️ `lineman` is not a substring of `linemen`** — the vowel moves — so an
> irregular plural silently cost three Bretonnian positionals their icon. The
> role pass yields both forms now.

The catalogue rides `/meta` rather than taking a route of its own, because a NEW
ROUTE needs a process restart on a live host and `/meta` is already fetched at
boot. Adding `.png` to the static route's suffix allowlist is a behaviour change
and reloads fine.

### The playing surface (`sprites.field`, `web/sprites/pitch_intro.png`)

The board drew its own green. It now lays FFB's pitch under the grid — grass, mud,
the square dots, the wide-zone and End Zone lines. It is stretched to the grid
rather than tiled: the image is 782x452 for a 26x15 board, ~30.1px a square either
way, so it is a TRUE playing surface and its painted lines land on our squares.
A test pins that ratio against the engine's own geometry, so a pitch drawn for a
different board is caught rather than silently stretched.

Replaced the same way as the players: `pitch.png` in a pack wins over the shipped
`pitch_intro.png`. It rides `/meta` and the same `/static/sprites/` path.

**⚠️ THIS IS THE ONLY PITCH WE MAY SHIP.** FFB fetches a team's real pitch from
fumbbl.com per team and per weather (`IconCache.getPitch` → `getIconByUrl`), and
those are **FUMBBL USERS' OWN UPLOADS** — not christerk's to license and not ours
to vendor, whatever permission we have for the client's own art. The one here is
bundled in the FFB repo itself and is covered. Do not go collecting the others.

**THE IMAGE REPLACES DECORATION, NEVER INFORMATION.** With a pitch on, the zone
tints, the wide-zone shading and four of the five overlay lines stand down —
they are painted on the photograph already. What does not stand down is anything
the engine reported: legal and needs-a-roll marks, blockable/blitzable rings, the
selection outline, the drag target, the crosshair, the ruler highlights. The LOS
line stays too, in the accent, because it is the one line a coach reads mid-turn.

> **⚠️ AND THE MARKS HAD TO BE MADE LOUDER, WHICH IS THE PART THAT NEARLY GOT
> MISSED.** Clearing the cell backgrounds is right, but the legal-square fill is a
> GREEN WASH and the pitch it now sits on is GREEN GRASS. This file already warns
> about that exact pairing from the other direction ("a thin green outline on a
> dark green pitch was invisible") and it came back anyway. Over a pitch the marks
> get a dark scrim and a heavier ring.
>
> **It passed every check that existed**, because the harness COUNTED legal
> squares and counting cannot see whether they are visible — the same lesson as
> the odds tag that rendered fg-on-fg. There is now a check that measures the
> painted difference between a legal square and a plain one, normalised because
> the computed value may be `rgb(0-255)` or `oklch(0-1)` and a threshold in one
> unit is silently unreachable in the other.

### Importing a team from FUMBBL (`fumbbl.py`)

FUMBBL is where Blood Bowl is actually played online. `bb_roster_import_fumbbl`
turns the JSON from `fumbbl.com/api/team/get/<id>` into a Team Draft List, so a
coach's real side can be set up here without retyping it.

**IT MAKES NO NETWORK CALL, AND THAT IS THE DESIGN.** The manifest declares
`network: []` and the README says so; fetching would spend that claim on a
convenience the coach can supply with a copy-paste, and the fetch was never the
hard part. Their API *is* reachable anonymously (the whole `fumbbl.com` HTML site
sits behind Anubis anti-scraping, the JSON endpoints do not) — reachable is not
the same as ours to use, and FUMBBL deployed Anubis specifically to stop
automated traffic. Ask before building anything that talks to them.

**⚠️ THE TWO CATALOGUES DISAGREE ABOUT NAMES IN BOTH DIRECTIONS AT ONCE.** Every
one of these is real, off four actual teams, and no two obey the same rule:

| FUMBBL | ours (S3) | what moved |
|---|---|---|
| `Underworld Troll` | `Troll*` | their prefix, not ours |
| `Blitzer` (Dwarf) | `Dwarf Blitzer` | our prefix, not theirs |
| `Norse Raider Lineman` | `Norse Raider` | "Lineman" added |
| `Underworld Snotlings` | `Snotling Lineman` | "Lineman" dropped, AND plural |
| `Dwarf Blocker Lineman` | `Dwarf Lineman` | a word inserted mid-name |

So there is no strip rule and no append rule — each would break two other lines.
What works is four passes of normalise-and-compare, each needing exactly ONE
winner: exact · team name differs · wording differs (filler dropped) · ours
contained in theirs. A pass that finds several has found an AMBIGUITY, and it
stops there rather than falling through to a looser pass that would only pick
more confidently from a set already known to be unseparable.

**Matching is scoped to the identified TEAM, which is what makes the loose passes
safe.** Dwarf's `Troll Slayer` and Underworld's `Troll*` are one token apart; a
global name index would have to choose between them and a team-scoped one never
sees the other roster at all. The trailing `*` is the source page's Big Guy
marker — the same fact is already in `role`, so it is decoration in the name.

**AN UNMATCHED POSITION IS NAMED, NOT GUESSED, AND NOT SILENTLY DROPPED.** A wrong
positional means wrong STATS, quietly, in the one place a coach is trusting a
table instead of their memory — the exact failure this plugin exists to prevent.
The list comes back short by exactly those players, `draft.problems()` then says
"fewer than 11" in the rulebook's own terms, and a note names them. Same for
players FUMBBL marks with a non-zero `status`: they are counted and reported BY
CODE rather than explained, because what those codes mean is not documented here
and a confident gloss ("journeyman") that turned out to be "missing next game"
would be the same failure in miniature. A real Wood Elf team had two of status 6.

**An imported team legitimately breaks the DRAFTING rules**, and saying so is the
difference between a useful warning and a wrong one: Dedicated Fans cap at 3 *when
drafting* and grow past it in a league, so `problems` flags a five-fan team that
is entirely legal where it came from. The note says which limits are draft-time.
The edition gap is stated the same way — FUMBBL `ruleset: 4` is BB2020, we play
S3, and players are matched by NAME with S3 stats and costs applied.

The fixtures are four real teams reduced to the fields the importer reads; coach
and player names are other people's and a naming rule needs none of them.

**The paste box is in the roster builder** (`/plugins/bloodbowl/draft`), and it
shows the mapping table with a `how` per row, the notes, and — in its own bordered
block, in the error colour — the players that did not map. That block is the
POINT of the feature rather than a footnote: a coach who misses it plays a squad
that is quietly short. There is a harness check that it is on screen and that its
ink differs from its own background, because present-in-the-DOM is not visible.

> **⚠️ AND ADDING THAT COVERAGE FOUND A DEAD VIEW.** `draft.html` and
> `models.html` both opened with a bare top-level `await import` of
> `_ds/plugin-kit.js`. The kit is served by the CONSOLE, so it is absent in this
> plugin's own harness and on any host without `_ds` — and **a top-level await
> that throws means the module NEVER RUNS**. Not a degraded page: no handlers
> bound, no teams listed, static markup and nothing else, with no clue why. It is
> also why neither view had a single browser check — the harness cannot drive a
> page that never wakes up, so the gap hid the bug that caused it. Both now fall
> back to a plain same-origin fetch like `web/js/api.js` and the 3D board always
> did, and a test asserts every view that imports the kit has a fallback.

### The model library (`models.py`, view `/plugins/bloodbowl/models`)

One mesh per positional, organised by team; the 3D board loads it and **falls back to
the primitive pawn when there is none**, which is the ordinary case. That fallback is
the contract: the library can only ever upgrade the board, never break it.

* **No user input becomes a path.** A request names slugs; those are matched against
  the SHIPPED ROSTER and the canonical entry supplies the filename. An unknown slug is
  a 404 — there is no arrangement of dots and slashes that resolves to something the
  roster does not already contain. Tested with three traversal shapes.
* **Uploads are RAW BODY, not multipart.** `UploadFile` needs `python-multipart`, and
  FastAPI raises for it at ROUTER-BUILD time rather than per request — one missing
  wheel took down every route on the data router, the board included, in a form the
  test suite reported as 29 unrelated failures. This plugin ships no runtime pip deps
  and a single file needs no form parser.
* **`Request` is imported at MODULE level in api.py and must stay there.**
  `from __future__ import annotations` makes annotations strings, and FastAPI resolves
  them with `get_type_hints` against the module's globals — a `Request` imported inside
  a router-builder is invisible to it and the parameter is silently demoted to a QUERY
  field (422 "Field required"). Every other route here annotates with builtins, which
  is why this only bit once.
* **Replacing removes the other container first**, so a positional cannot hold both a
  `.glb` and a `.gltf` with the winner decided by sort order.
* The meshes are served from the GATED prefix, so the 3D view fetches them through the
  kit and parses the bytes itself — drei's `useGLTF` takes a bare URL and carries no
  bearer, so it cannot reach them. One fetch per positional per session, cloned per
  pawn: the cached scene is one object, and placing it twice moves the first.

**AND THE END ZONES ARE LABELLED BY WHO SCORES THERE.** The 2D board says "HOME END
ZONE", which is true and answers the wrong question — you score in the OPPOSITION's
End Zone. It cost a live false bug report: a home carrier standing in row 1 under a
"HOME" sign reads as an unscored touchdown, and the engine was right all along
(`touchdown_row("home") == 26`). Both boards now say SCORES HERE, and a test asserts they agree — the 3D one was
fixed at the time and the 2D one kept the old sign for a release, which is how
the two ends of the same fact drift apart.

**SHIPPING `path` DID NOT MAKE THE AGENT USE IT, AND THAT IS THE REAL LESSON.** The
first live turn after deploying it crashed exactly as before: 25 model calls, one
square per call, `path` untouched. The parameter was deployed, registered and
visible — asked in a clean session the agent quoted the new documentation back
**verbatim** and then went on moving one square at a time. It was copying its own
transcript, which by then held over a hundred single-square calls. **A model
copies its own recent behaviour over instructions it read once.**

Two things fixed it, and the second is the durable one:

* Clearing the session transcript. Same board, same turn, same schema — with the
  examples gone it reached for `path` unprompted on the first try: 6 model calls,
  runs of four squares, turn completed. Proof of the cause, but a RESET, not a
  cure: the thread refills with single-square calls over a long game.
* **`_step_hint` in `__init__.py` — the nudge lives in the REPLY.** A docstring is
  read once at the top of a long context; a reply lands at the moment of the
  decision, every time, and is the only thing that competes with the transcript.
  It fires on the SECOND single-square call for a player in a turn (the first is
  an ordinary ask — a step into a tackle zone, a shuffle — and lecturing on it
  teaches nothing), at most once per player per turn (`first_mentions` precedent:
  honest, not loud), only when there is Move Allowance left worth batching, and
  never when `path` was used — which also CLEARS the count, so a coach who takes
  the advice is not held to the calls they made before switching.

**The counter is deliberately not in the log.** How many tool calls a coach spent
is a fact about the conversation, not about the match, and the log is for facts a
replay must reproduce. It is process-local module state, on the same line
`engine/pace.py` sits on; a restart forgets it, which is the right cost for a hint.
The hint rides its own `hint` field rather than `log`, for the same reason.

**A test tried to cheat here and the design caught it**: setting `ma_used` directly
and saving does nothing, because `from_dict` folds the log. The budget guard is
tested through `_step_hint` directly — driving it through the tool would need
Rushes, whose dice can floor the player, and a floored player is suppressed by a
different clause, so the test would have passed without exercising the guard at all.

## 4. Working on it

```bash
.venv/bin/python -m pytest tests/ -q      # 686 tests
.venv/bin/python -m ruff check . && .venv/bin/python -m ruff format --check .
.venv/bin/python harness.py --check       # 148 browser checks + screenshots
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
Skills modelled — **108 of 108**. `bb_get_skill` returns the rulebook's text for all
of them, which was always a separate job from applying them: quoting is what a
coach asks for, applying is what the board does.

**NONE carries a `partial=`.** All eighteen were closed, and the pattern in them is
worth more than the number: most were not gaps. The KEYWORDS were in
`data/rosters.json` all along under `role`; two partials were stale (written when a
Skill they depended on was unmodelled); two were misreadings of what the rules
permit; five were coach's choices, which stopped being partial the moment there
was a command field to make them with; and the rest were simply work.

**Before writing off a partial, check whether the reason it was written is still
true.** Three fixtures in this suite have now hit assertions predicting their own
obsolescence — `_unmodelled_pair`, the unapplied-Kick-off-Event guard, and the
partly-modelled one. Each was rewritten to drive its MECHANISM with something
synthetic, because a fork will still need it.

**"Modelled" is not binary, and pretending it is flatters.** A Skill with two
clauses of which one is applied would report as modelled and quietly do half its
job — which *sounds settled*, and is worse than saying nothing. So
`skill_hook(..., partial="what is left out")` records the gap, `describe_skill`
returns it, and `skills.partly_modelled_on_pitch` is the standing companion to
`unmodelled_on_pitch`. Both ride with `bb_game_state`, and both are empty today —
Juggernaut, Stand Firm and Sidestep were the last three and each was closed by
giving the coach a field to make the choice with. The MECHANISM stays: a fork
adding a Skill it only half-applies should say so rather than report it modelled.

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

1. ~~The remaining skills.~~ ~~The 18 `partial=` clauses.~~ **All 108 are
   modelled and none is half-applied** — `grep partial= engine/skills.py` returns
   nothing and `bb_list_skills(partly=True)` returns nobody. Team keywords and the
   Thrall Lineman, the two that were called out here as the data-shaped ones, both
   turned out to be in `data/rosters.json` under `role` already.
2. ~~A match started from a preset has statless players.~~ Fixed — `state.flesh_out`
   gives every statless token the team's LINEMAN (cheapest positional, the only
   0-16 on every roster) at match start and says in the log that it did. The label
   is kept. Two tests, one of them through HTTP.

### Known simplifications, all deliberate and all stated in the code

- ~~One of the eleven Kick-off Events is reported rather than applied.~~ All
  eleven are applied. **Get the Ref** was the last, and it needed only the one
  Inducement an exhibition match can hold — a BRIBE, which the event itself hands
  out free. Four of the eleven work by ASKING (`Match.pending` + `bb_game_choose`),
  one of which — **Charge!** — is a free turn rather than a question.
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
- **Fan Factor is rolled**, not defaulted: "a D3 [for Fair-weather Fans] plus your
  Dedicated Fans Characteristic", and a drafted team "automatically" has 1 of
  those. It used to default to ZERO, which is not a neutral default — Pitch
  Invasion adds Fan Factor to a D6.
- The PRE-GAME SEQUENCE runs all five steps (`engine/pregame.py`). Two of them,
  Take On Journeymen and Inducements, are reported as League-only IN THE
  RULEBOOK'S OWN WORDS ("this step is only used in League Play") rather than as
  gaps. Cheerleaders and Assistant Coaches remain inputs that say what they used.
- Inducements beyond the free Bribe are League play, and a League needs a Treasury,
  a Team Value and a Draft List — none of which an exhibition match has.
- The Throw-in direction is thrown straight back in from the edge crossed; the real
  Throw-in Template is a diagram.
- The version is `0.7.0`, tagged `v0.7.0`. It ships `enabled: false` and has had
  no release beyond the tag. **Bump it with the work, not after** — six feature
  PRs landed on 0.6.0 and the plugin deployed to a live agent reported the same
  version as the one it replaced, which makes "is this agent current?"
  unanswerable from the outside.
