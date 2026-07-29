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
| SETTING UP THE GAME | partly | the practice board is permissive by design; no formal set-up phase |
| YOUR FIRST FEW GAMES | n/a | advice, not a rule |
| PRE-GAME SEQUENCE | no | a league-and-inducements sequence |
| THE FANS · FAN FACTOR | partly | an input, defaulting to zero; Pitch Invasion uses it |
| THE WEATHER · WEATHER TABLE · WEATHER CONDITION | yes | rolled at kick-off; rides the same modifier hook as Skills |
| DETERMINE KICKING TEAM | partly | `bb_game_new(kicking_to=…)` says who receives; no coin toss |
| START OF A DRIVE! · START OF DRIVE SEQUENCE | partly | the opening set-up is reused each drive |
| SET-UP · TOO MANY PLAYERS | partly | reported by `bb_pitch_review`, never enforced |
| THE KICK-OFF · NOMINATE KICKING PLAYER · PLACE THE KICK | yes | |
| THE KICK DEVIATES | yes | D6 distance, D8 direction |
| THE KICK-OFF EVENT · KICK-OFF EVENT TABLE | partly | all 11 rolled and quoted; 6 applied |
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
| SPECIAL ACTION | partly | Stab, Chainsaw, Breathe Fire, Projectile Vomit, Chomp; not Ball & Chain, Throw Bomb or Kick Team-mate |
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
| APOTHECARIES | partly | the Knocked-out patch-up; the Casualty branch's choice of two rolls is not offered |
| FOUL ACTIONS! · PERFORMING A FOUL ACTION | yes | |
| BEING SENT-OFF · ARGUE THE CALL | yes | the Argue roll is made for you — see §6 |
| PASS ACTIONS! · PERFORMING A PASS ACTION | yes | |
| DECLARE TARGET SQUARE · MEASURE RANGE | partly | the ruler is measured, not quoted |
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

**43 of 108 modelled**, and all 108 quotable through `bb_get_skill` — see §2 for
why those are two different jobs. `bb_list_skills(only_unmodelled=True)` is the
live version of this count; the number above is only ever a snapshot.

## What blocks the rest

Four things, and knowing which one a rule is waiting on is most of the work:

1. **A way to ask the coach mid-resolution.** Five Kick-off Events (Solid Defence,
   High Kick, Quick Snap, Blitz!, Get the Ref) and several Skills currently run on
   a stated engine policy where the rules give a coach a real choice. This is the
   one genuine design question left.
2. ~~A uniform random pick.~~ Done — `Dice.dn(sides)`.
3. **Roster facts a practice board never bought** — Fan Factor, Apothecaries,
   Inducements. The pattern is settled: take them as input, default them, and say
   what was assumed.
4. **Whole subsystems** — Apothecaries, Inducements, and the three remaining
   Special Actions (Ball & Chain is a movement mode, Throw Bomb is a Pass whose
   payload explodes, Kick Team-mate is a Throw Team-mate that does not consume
   the team's one). Each is a piece of work of its own, not a gap in something that
   exists.
