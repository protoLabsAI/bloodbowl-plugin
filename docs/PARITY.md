# Parity with the S3 core rules

Every section of `core_rules/the_game_of_blood_bowl`, and whether this engine
plays it. The goal is the whole table reading **yes**.

A test (`test_the_parity_table_lists_every_rules_section`) fails if the source
grows a section this file does not mention, so the list cannot quietly fall behind
the rulebook. It cannot check the *verdicts* — those are a judgement, and the way
to keep them honest is that every `yes` should have a test with the rule quoted in
it.

Three verdicts, and the middle one is the important one:

- **yes** — modelled, with the passage quoted beside the code that decides it.
- **partly** — some of it. The gap is named, here and in the engine, because a
  half-applied rule reads as a fully applied one and that is worse than silence.
- **no** — not modelled, and *reported* wherever it would have mattered.

| Section | | Notes |
|---|---|---|
| A GAME OF TWO HALVES! | yes | two halves of eight turns each |
| SETTING UP THE GAME | yes | `bb_game_setup` is strict; the practice board stays permissive on purpose |
| YOUR FIRST FEW GAMES | n/a | advice, not a rule |
| PRE-GAME SEQUENCE | yes | all five steps; the rulebook scopes two of them to League Play in its own words |
| THE FANS · FAN FACTOR | yes | rolled: D3 Fair-weather + Dedicated Fans (1 unless bought up) |
| THE WEATHER · WEATHER TABLE · WEATHER CONDITION | yes | rolled at kick-off; rides the same modifier hook as Skills |
| DETERMINE KICKING TEAM | yes | rolled off unless a side is named |
| START OF A DRIVE! · START OF DRIVE SEQUENCE | yes | a declared set-up wins; the reused one is the fallback |
| SET-UP · TOO MANY PLAYERS | yes | all four rules enforced, every violation reported at once |
| THE KICK-OFF · NOMINATE KICKING PLAYER · PLACE THE KICK | yes | |
| THE KICK DEVIATES | yes | D6 distance, D8 direction |
| THE KICK-OFF EVENT · KICK-OFF EVENT TABLE | yes | all 11 rolled, quoted and applied |
| TOUCHBACKS | yes | including a bounce that crosses back |
| A TEAM'S TURN! · ROUNDS · TURNS | yes | |
| PLAYER ACTIVATIONS | yes | `acted` begins one, `done` ends it |
| MOVE ACTION | yes | |
| SECURE THE BALL ACTION | yes | |
| BLOCK ACTION | yes | |
| BLITZ ACTION | yes | |
| PASS ACTION | yes | ranges are measured, not quoted — see §2 |
| HAND-OFF ACTION | yes | |
| THROW TEAM-MATE ACTION | yes | Quick and Short Throws only |
| FOUL ACTION | yes | |
| SPECIAL ACTION | yes | Stab, Chainsaw, Breathe Fire, Projectile Vomit, Chomp, Ball & Chain, Throw Bomb, Kick Team-mate |
| FOREGO ACTIVATION | yes | and it still faces the crowd |
| DECLARE VS PERFORM | yes | `block.declared_a_block` |
| REPLACING ACTIONS | yes | a Special Action may replace a Blitz's Block, and is not a Block |
| ACTIVATED PLAYERS | yes | |
| MOVE ACTIONS! · STANDING UP · DODGING | yes | |
| JUMPING OVER PLAYERS | yes | 2 MA, the worse of the two squares, natural 1 falls where they stood |
| PICKING UP THE BALL · RUSHING | yes | |
| SECURE THE BALL ACTIONS! · PERFORMING A SECURE THE BALL ACTION | yes | |
| BLOCK ACTIONS! · PERFORMING A BLOCK ACTION | yes | |
| ASSISTING A BLOCK ACTION · OFFENSIVE ASSISTS · DEFENSIVE ASSISTS | yes | |
| BLOCK DICE · PLAYER DOWN · BOTH DOWN · PUSH BACK · STUMBLE | yes | |
| SELECT AND APPLY RESULT | yes | the stronger coach chooses |
| PUSHED PLAYERS · CHAIN PUSHES · PUSHED INTO THE CROWD · FOLLOW-UP | yes | |
| BLITZ ACTIONS! | yes | |
| ARMOUR AND INJURIES! · RISKING INJURY | yes | |
| INJURY ROLLS · INJURY TABLE · RESULT | yes | |
| STUNTY PLAYERS · STUNTY INJURY TABLE | yes | |
| CASUALTY ROLLS · CASUALTY TABLE | yes | rolled and reported; every result is a League consequence |
| MISS NEXT GAME · NIGGLING INJURY · LASTING INJURY | n/a | League Play, between matches |
| CHARACTERISTIC REDUCTION · HEAD INJURY · SMASHED KNEE | n/a | League Play |
| BROKEN ARM · DISLOCATED HIP · BROKEN SHOULDER | n/a | League Play |
| GETTING EVEN | n/a | League Play |
| INJURY BY THE CROWD | yes | no Armour Roll; Stunned means the Reserves Box |
| APOTHECARIES | yes | Knocked-out patch-up, and the Casualty branch's choice of two rolls is asked |
| FOUL ACTIONS! · PERFORMING A FOUL ACTION | yes | |
| BEING SENT-OFF · ARGUE THE CALL | yes | the Argue roll is made for you — see §6 |
| PASS ACTIONS! · PERFORMING A PASS ACTION | yes | |
| DECLARE TARGET SQUARE · MEASURE RANGE | yes | every stated rule applied; the ruler's GEOMETRY is calibrated from the diagram (see below) and configurable |
| TEST FOR ACCURACY · ACCURATE PASS · INACCURATE PASS · FUMBLED PASS | yes | |
| INTERCEPTIONS · RESOLVE PASS ACTION · CATCHING THE BALL | yes | |
| THROW-INS | yes | D6 across the template's three arrows, then 2D6 squares |
| CORNER THROW-INS | yes | a D3 among the three inward directions |
| HAND-OFF ACTIONS! · PERFORMING A HAND-OFF | yes | |
| THROW TEAM-MATE ACTIONS! · PERFORMING A THROW TEAM-MATE ACTION | yes | |
| SUPERB THROW · SUBPAR THROW · FUMBLED THROW · LANDING | yes | dropping them is only a turnover if they held the ball |
| LANDING IN AN OCCUPIED SQUARE | yes | the occupant is flattened even if already down |
| TOUCHDOWN! · SCORING A TOUCHDOWN | yes | |
| SCORING DURING YOUR OPPONENT'S TURN | yes | a push into the End Zone scores |
| STALLING | yes | including the hand-off exception |
| THE END OF A DRIVE! · END OF DRIVE SEQUENCE · THE DRIVE ENDS | yes | |
| DEAL WITH SECRET WEAPONS | yes | sent off as if they had Fouled, and they may Argue |
| END OF DRIVE EFFECTS · RECOVER KNOCKED-OUT PLAYERS | yes | |
| RESTARTING THE GAME | yes | the conceder receives |
| ENDING THE GAME · WINNING THE GAME | yes | |
| EXTRA TIME | yes | re-rolls are NOT replenished and carry over |
| PENALTIES | yes | five roll-offs, ties re-rolled |

## Skills and Traits

**108 of 108 modelled** — every Skill and Trait in the S3 core rules, and all 108
quotable through `bb_get_skill`. `bb_list_skills(only_unmodelled=True)` is the live
version of this count and now returns nothing.

**One `partial=` remains**, and it names a POST-GAME step: Plague Ridden's "during
the Post-game Sequence, this player may be hired in the same manner as any
Journeyman". The in-match half — "you may IMMEDIATELY add one new Lineman player
from your team's Team Roster to your Reserves Box" — is applied.

Seventeen were closed, and the reasons are worth keeping because most of them were
not gaps at all:

* **The keywords were in the data.** `data/rosters.json` carries every positional's
  Keywords under `role` — "Eagle Warrior (Lineman, Human)" — and nothing had read
  it. That closed HATRED, ANIMOSITY and BLOODLUST's bite (Thrall Lineman is a
  Keyword and the Vampire roster prints it).
* **A coach's free choice is not a missing half once there is a way to make it.**
  SIDESTEP, STAND FIRM, TRICKSTER, SAFE PAIR OF HANDS and JUGGERNAUT each take a
  field on the Block or the drop; the engine's policy is what happens when nobody
  says, which is what a default is for.
* **Two were stale.** VERY LONG LEGS' "ignores Cloud Burster" was written when
  Cloud Burster was unmodelled; it has been modelled for six PRs.
* **Two were misreadings.** Rolling a MULTIPLE BLOCK's halves separately is the
  procedure the rules offer in the same breath, and the Throw-in Template (PUNT,
  SWOOP) is the same diagram the Range Ruler is.
* **The rest were work** — Shadowing's per-Turn bound, Diving Catch's other half,
  On the Ball's mid-Pass interrupt, Insignificant against the only list the engine
  keeps, and STAR PLAYER POINTS, which are earned during a game even though they
  are spent after one. That last one is what made VIOLENT INNOVATOR mean something.

## The Range Ruler, and why it is `yes`

Every rule the book STATES about measuring range is applied: the four sections and
their modifiers, "if a square is only PARTIALLY underneath the Range Ruler, then it
cannot be the target square", and the interception corridor ("the Tomb Kings
Blitzer is underneath the Range Ruler … and so now gets to attempt to Intercept").

What the book never states is a NUMBER. The Range Ruler is a physical template —
"position the circle at the end of the Range Ruler over the centre of the square
occupied by the player performing the Pass Action" — and the two worked examples
both settle their range by pointing at a picture. There is nothing to quote, so
`engine/ruler.py` calibrates the four bands from the published diagram, says in its
own docstring that this is the one un-quoted number in the engine, and exposes the
bands as CONFIG so an operator who measures differently can say so.

The same is true of the Throw-in Template, which Swoop and Punt use.

Marking these `partly` would say the engine skips a rule. It does not. It
implements a rule whose last input is a drawing.

## What blocks the rest

Four things, and knowing which one a rule is waiting on is most of the work:

1. ~~A way to ask the coach mid-resolution.~~ Done — `Match.pending` plus
   `bb_game_choose`. The engine stops, says what it is waiting for, and refuses
   every other action until it is answered; declining is always legal. Solid
   Defence, High Kick and Quick Snap! run on it, and **Charge!** — a whole free
   turn of activations with its own stop condition — runs on the same asking plus
   a mode (`engine/charge.py`).
2. ~~A uniform random pick.~~ Done — `Dice.dn(sides)`.
3. **Roster facts a practice board never bought** — Fan Factor, team Keywords,
   Inducements. The pattern is settled: take them as input, default them, and say
   what was assumed.
4. **Whole subsystems** — Inducements and the Pre-game Sequence, both of which are
   LEAGUE play. Each is a piece of work of its own rather than a gap in something
   that exists, and neither changes a match already under way.
