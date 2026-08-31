---
name: coaching-a-turn
description: >-
  Use when playing a turn of Blood Bowl through the bb_game_* tools — your own
  seat in a match, a head-to-head, or a full-AI game. An ORDERED procedure for
  spending eight activations well: what to check, in what order, at what odds,
  and when to stop. Read it before the first activation of a turn, not after a
  turn has gone wrong.
tools: [bb_game_routes, bb_game_legal, bb_game_odds, bb_game_act, bb_game_end_turn, bb_game_log]
---

# Coaching a turn

A Blood Bowl turn is eleven players and one chance to sequence them. This is the
order to do it in.

**Work the list top to bottom and stop at the first thing that applies.** Do not
survey every option for every player — that is how a turn dies. A scripted bot
built exactly this way (an ordered priority list, executed sequentially) won the
first Bot Bowl AI competition, while machine-learning entries could beat a random
opponent and not a scripted one. The order is the skill. It also means a safe
action taken early can make a risky one later unnecessary, which is the whole
reason to sequence rather than to plan.

## Before you touch anybody

**The board is already in your prompt.** Every model call carries the current
position — direction, ball, every player, who has acted, and anything this engine
is not modelling. There is no state tool to call and nothing to fetch. Read what
you were given:

- `scores_in` — the row YOUR side is running at. Never work this out from
  coordinates; it is right there and getting it backwards loses games.
- `ball.loose`, `ball.ours`, `ball.to_score` — who has it and how far it must go.
- `turns_left_this_half` — the budget you are spending.

Then decide what kind of turn this is, in one line, before moving anyone:

| the position | the turn |
|---|---|
| we have the ball, ≤2 turns of ground to cover | **score it** |
| we have the ball, plenty of turns | **advance and protect** — a cage, not a sprint |
| they have the ball | **get it back** — pressure the carrier, deny the route |
| ball is loose | **pick it up**, or make sure they cannot |
| we lead, clock nearly out | **kill the clock** — scoring may be the mistake |

## The order

### 0. IF THE ENGINE IS WAITING ON AN ANSWER, ANSWER IT — FIRST, AND FAST

`situation` and the board both say when a Kick-off Event is waiting on you. **While
a question is unanswered NOTHING can happen** — not your turn, not the other
coach's, not even the ball landing. The game is stopped until you speak.

So answer it before anything else, and **answer it cheaply**. These are usually a
free bonus; the difference between the best answer and a decent one is a square or
two, and it is never worth a fraction of what stopping the game costs.

**DECLINING IS ALWAYS LEGAL AND IS A REAL ANSWER.** Every one of these says the
Coach *may*. If the right choice is not obvious within two or three tool calls,
decline and get on with the game.

*This is step zero because a match deadlocked on it.* A seat was asked a Charge!
question — up to six players get a free move — and spent its ENTIRE step budget
working out the perfect six, checking tackle zones square by square by hand. It
never answered. The board sat frozen at the kick-off, and because the clock still
read "home to act" nothing about it looked broken. A suboptimal Charge! costs a
square. An unanswered one costs the match.

### 1. Score if you can

If the carrier's `to_end_zone.chance` from `bb_game_routes` is **≥ 0.70**, take it.
Now, before anything else can go wrong. On the last turn of a half, take anything
better than nothing — there is no next turn to save the player for.

### 2. THE BALL CARRIER DOES NOT HIT ANYONE

**The player holding the ball never throws a Block, never declares a Blitz, and
never Fouls.** Not when it looks free, not when the odds are good, not when
there is nobody else in range.

Every one of those puts your own carrier on a Block die, and half the faces on
that die knock somebody over. If it is the carrier that falls, the ball comes
loose in a crowd and the turn ends — a turnover with the ball is the worst
outcome available on any given turn, and this is the cheapest way to buy one.

This is not a preference and it is not a threshold to weigh. **It is a rule, and
it outranks everything below it.** If the only Blitz available is the carrier's,
the answer is that there is no Blitz this turn.

*This is written this firmly because it has already happened.* On turn one of a
real match, a coach following the rest of this list picked the ball up, declared a
Blitz with the same player, rolled Player Down, and ended its own turn with its
first activation. Every other step was followed correctly.

### 3. Free the carrier before you move it

**Never move the ball carrier out of a Tackle Zone if you can avoid it.** If the
carrier is Marked, spend OTHER players first: blitz or block the Markers away with
somebody who is not holding the ball, then move the carrier through the gap you
just made. A carrier that dodges when it did not have to is the commonest way a
drive ends.

### 4. MOVE THE BALL DOWNFIELD

**Every turn you hold the ball and are not scoring, the carrier ends the turn
closer to the line than it started.** This is the step that wins games and it is
the one easiest to leave out, because every other step feels more urgent.

`bb_game_routes` gives `to_end_zone` — the safest route to the row you are
attacking. Take the furthest square along it that keeps `chance` at **0.94 or
better**, and walk it in ONE `bb_game_act` with `path=`. Do not shuffle a square
and reconsider; that is how a drive covers one row a turn and runs out of clock.

Some arithmetic worth carrying: a drive has about six usable turns, and a pitch is
26 rows. **A carrier that gains one row a turn never scores.** If the safe route
only gains a square or two, the answer is usually not "advance carefully" — it is
that the carrier is in the wrong place and needs a screen built first, or the ball
should go to somebody with room via a hand-off.

*This step was missing from the first version of this list, and it showed: a coach
followed everything else correctly, kept its carrier upright for two entire turns,
and moved it three rows sideways.*

### 5. Bank the certain actions

Everything that needs **no roll at all** — unmarked players walking into position,
standing up out of harm's way, marking an opponent from a free square. These cost
nothing and can only help. `bb_game_routes` gives `chance: 1.0` for exactly these.

### 6. The one Blitz, spent deliberately

You get one per team turn, and **it is thrown by somebody who is not holding the
ball** (see step 2). Use it on the highest-value thing available:

- Their ball carrier, if you can reach and knock it down.
- The Marker pinning your own carrier.
- Otherwise, keep it — an unspent Blitz is worth more than a bad one.

Check `bb_game_odds` before committing. Blocking someone stronger hands them the
dice, which turns your Blitz into a way of knocking your own player over.

### 7. Blocks that are near-certain

Throw a block only when the odds say your player stays upright — **> 0.94** — and
there is no fumble risk to the ball. A block that feels free and puts your own
player on the floor is how turns quietly die.

### 8. Cage the carrier

Four players on the four **diagonal** squares around the carrier. Diagonals, not
orthogonals: they block the squares an opponent needs to Mark from. Only move
players into the cage on routes at **> 0.94** — a cage assembled by three dodges
is not protection, it is three chances to end the turn.

### 9. Screen and mark

Bodies between the ball and the line they are running at. A player standing in the
right square denies more than a spectacular dodge achieves.

### 10. End it on purpose

`bb_game_end_turn`. Do not drift into the end of a turn having run out of ideas —
stop when the remaining actions are worse than not taking them. **An unused player
is not a wasted one** if every move it had was a coin flip.

## The numbers

Starting thresholds, and the reasoning is more use than the digits:

| decision | do it at | why |
|---|---|---|
| move carrier to the End Zone | ≥ 0.70 | scoring ends the drive; being greedy about certainty loses more |
| hand-off | ≥ 0.70, or last turn of the half | |
| pick up a loose ball | > 0.33 | a loose ball nobody picks up is a loose ball for THEM |
| move into the cage | > 0.94 | protection built on rolls is not protection |
| throw a block | > 0.94 upright, no fumble risk | |

**These come from GrodBot, a bot written for an older edition, and are a starting
point rather than scripture.** They are calibrated for a normal position at level
scores. Shift them with the state: two down with three turns left, a 50% score
attempt is correct; level with eight turns left, it is a blunder. The threshold is
a default, the score and the clock are the argument.

**Risk compounds and it compounds faster than it feels.** Three separate 2+ rolls
is not three safe rolls — it is 58%, so two turns in five end early. Do not
multiply these yourself: `bb_game_routes` returns the chance for the WHOLE route
with every Dodge and Rush counted.

## Spending tool calls

You have a real budget and it is smaller than it looks — a turn that spends its
calls asking questions never gets to eleven activations. A recorded turn once used
47 calls to take 7 actions.

- **Do not go looking for the board.** It is in front of you, refreshed on every
  model call. A seat once spent 66 tool calls re-reading a position it was already
  holding, and ran out of budget before it finished the turn. That tool is gone
  now; this is what replaced it.
- **`bb_game_routes` per player you are seriously considering**, not per square.
  It replaces walking `bb_game_legal` around the board.
- **Move with `path=`.** `bb_game_act(action="move", player=…, path=[[8,15],[8,16],…])`
  walks a whole run in ONE call, rolling every Dodge and Rush in order. Moving one
  square per call is what kills turns: read `steps_taken` against
  `steps_requested`, and `ok` means every square was walked.
- **`bb_game_odds` before a block, not before everything.** It answers about
  Blocks; for movement the answer is in `bb_game_routes`.

## What not to do

- **Do not re-derive the board.** It is in `situation`. Arguing with yourself about
  which half the ball is in is a turn you are not playing.
- **Do not narrate every option.** Decide, act, and say what you did afterwards.
- **Do not chase attrition.** Taking players off serves the scoreboard; it is not
  the point. A drive that removes nobody and scores is a good drive.
- **Do not spend the last roll first.** Order actions so the ones that cannot fail
  happen while a turnover would cost least.
