# bloodbowl-plugin

A Blood Bowl pitch, rules engine and roster reference for
[protoAgent](https://github.com/protoLabsAI/protoAgent).

Set a scenario up on a real 26×15 pitch, then play it — kick-off to full time —
with the engine adjudicating and the agent coaching. Ships parsed Third Season
Edition (S3) data for 30 teams and 63 star players. No network access, no secrets,
no runtime pip dependencies.

> **New to the codebase?** Read [docs/HANDOFF.md](docs/HANDOFF.md) first. It has the
> design rules, the traps, and what to build next.

## What it does

### The board

A real 26×15 pitch — End Zones one square deep, Wide Zones four wide, a
seven-square Centre Field, the Line of Scrimmage between rows 13 and 14. Drag a
positional off the roster palette onto a square, drag players to move them, hover
anyone for a stat card. Or ask the agent: `bb_pitch_setup` places a whole formation
in one call, so *"set up an Orc defensive line against Skaven"* works from chat.

The board is deliberately **permissive** — an illegal position is a legitimate
thing to want while working a shape out. `bb_pitch_review` reports against the S3
setup limits; it never blocks.

### Presets

Named setups you can recall by name — five shipped shapes plus anything you save.
Shipped presets store *roles* rather than positionals, so a defence transfers
between teams; one saved from your own board keeps its real players and statlines.
`Mirror` flips a home shape into the away half, so a single stored defence serves
both as a defence and as the thing you practise attacking into.

### The match

Start a match from the board and play it. The engine owns the rules:

- **Movement** — MA, Tackle Zones, Dodging, Rushing, standing up, turnovers
- **Blocking** — assists, block dice, who chooses them, pushes, chain pushes,
  crowd pushes, follow-up
- **Blitzing** — one per team per turn: declare a target, run at them, hit them,
  and keep running. The Block costs a square of movement and will Rush for it.
- **Fouling** — the boot in, on a player already down; assists modify the armour
  roll, a natural double gets you Sent off, and the engine argues the call
- **Injury** — armour, the injury table, Stunned / Knocked-out / Casualty
- **The ball** — pick up, Secure the Ball, hand-off, passing with ranges and
  interceptions, bounces, catches, touchdowns
- **Team Re-rolls** — as many per turn as you have left, replenished at half-time,
  with Loner's roll and its cost when it fails
- **The weather** — rolled at kick-off, and it changes the odds: rain penalises
  every ball roll, a Blizzard forbids a Long Pass outright
- **The game** — kick-off with the full event table, all eleven results rolled,
  quoted and applied, drives, half-time, full time — and, on a draw, Extra Time
  and a Penalty Shoot-out

Every roll lands in an event log with its arithmetic shown, and that log is what
the agent narrates from.

**You play it by dragging.** Pick a player up and the engine says where they may
go and what it costs; drag across several squares and the whole run is walked a
step at a time, stopping the moment a Dodge or a Rush fails. Distance decides the
verb — drop on an opponent you are already touching to Block them, drag across the
pitch onto one to Blitz (declare, run, hit), drop on a team-mate to hand it off.
Clicking a player and then a square does the same things and always will: it is
the keyboard-reachable path, not a legacy one.

The squares beside your player carry the engine's **real** odds. The trail beyond
them is numbered and claims nothing — a move is a sequence of single squares, and
step two's Dodge cannot be costed until step one's dice have been rolled. A board
that guessed at it would be inventing the one number you are reading it for.

### Why it's built this way

The agent this was written for reproduces retrieved data exactly and then
fabricates the prose around it — verified, repeatedly. So roster data is parsed
from tables rather than recalled, and **rulings belong to the engine**: a wrong
stat is bad, a wrong ruling changes the game. `bb_game_legal` lets a coach ask what
is possible instead of working it out and being confidently wrong about one square.

Every rule is read off the S3 source and quoted in the code beside the branch it
decides. Where the source genuinely doesn't say — the Range Ruler is a physical
template — the derived number is isolated in one file that says so, cross-checked
against an independent source, made configurable, and reported to the user as
measured rather than quoted.

Thirty-four skills are modelled, and where one is applied only in part the engine
says which half it left out. A skill the engine does not model can still be **quoted**: `bb_get_skill` returns
the rulebook's own words plus whether this engine applies it. That split matters —
modelling a skill changes the game, quoting it changes what can honestly be said
about it, and the second is what stops a confident wrong explanation.

Anything not modelled is **reported, not ignored** — but reported once. The first
time an unmodelled Skill is relevant, the log says so and then stops; `bb_game_state`
carries the standing list of every unmodelled Skill on the pitch and who has it.
Ten of the eleven kick-off events say plainly that they were rolled but not applied.

## Tools

**Rosters** — `bb_list_teams` · `bb_get_roster` · `bb_team_costs` ·
`bb_list_stars` · `bb_get_star`

**Skills** — `bb_get_skill` · `bb_list_skills`

**The board** — `bb_pitch_show` · `bb_pitch_setup` · `bb_pitch_place` ·
`bb_pitch_clear` · `bb_pitch_review`

**Presets** — `bb_presets` · `bb_preset_load` · `bb_preset_save` ·
`bb_preset_delete`

## Playing the agent

Tick **vs. agent** on the board (or `bb_game_new(you="home")`) and the sides are
claimed: you play one, the agent plays the other, and **neither can move the
other's team**. The board refuses with a reason, and so do the tools — the check is
in the engine they share, because neither surface can be trusted to police itself.

When the turn comes round, the plugin publishes `bloodbowl.turn_ready` and the host
turns that into an agent turn **in the chat the match belongs to** — you end your
turn on the board and its coach plays where you are looking, without being asked.

A match you start with `bb_game_new` is bound to the chat you started it in. One
started **from the board** has no conversation behind it, so its turns land in the
Activity thread until you say `bb_game_here` — "play it here".

If the board is waiting and nothing is happening, `bb_game_nudge` (or
`POST /game/nudge`) re-sends the signal. A nudge *can* be lost — an agent restart
mid-turn, a cancelled job — and a lost one looks exactly like the agent thinking.

**Full AI** — `bb_game_new(you="neither")`, or `POST /game/new {"you": "neither"}` from the
board. Both seats are agent-played and the game runs itself to full time, each turn's end
handing over to the next. **Each side gets its own conversation**: two seats out of one
chat would be a single coach with both hands, reading the plan it just made for the other
team straight out of its own context. All either seat knows about the opposition is what
is on the board — which is the point of the engine being the authority. Nobody has to be
watching; the log holds every roll afterwards, and each seat's chat is its own transcript.

**The agent is paced** (`bloodbowl.agent_pace_s`, default 2s between its actions).
A model can take eight activations in under a second, which is a diff rather than a
game — the pace is what makes a turn something you can watch happen. Your own
clicks are never paced.

**Playing** — `bb_game_new` · `bb_game_state` · `bb_game_legal` · `bb_game_odds` ·
`bb_game_act` (`path=[[8,15],[8,16]]` walks a run; `drop_ball=True` for a Fumblerooski) ·
`bb_game_end_turn` · `bb_game_kickoff` · `bb_game_log` · `bb_game_abandon` · `bb_pass_ranges`

**A run is one call, not one per square.** A Move is still one square at a time and
every square is adjudicated as a lone Move would be — `path` collapses the round trip,
not the rules, and the route stays the coach's decision. It matters because an agent's
budget is measured in tool calls: a turn played one call per square runs out of turn
long before the team runs out of Move Allowance, which is exactly how a real game
stalled mid-activation. The run stops where the plan stops applying — a refusal, a
Turnover, the player going down or off the pitch, or a push landing them somewhere
other than the square asked for — and reports `steps_taken` / `steps_requested` plus
`halted`. `ok` is true only if every square was walked. The board still animates: the
match is saved and paced between squares, not just at the end.

**And the reply says so if you forget.** A second single-square Move for the same player
in one turn comes back with a `hint` pointing at `path`. This exists because documenting
the parameter was not enough on its own: `path` shipped fully described, and an agent
went on spending one call per square until it ran out of budget mid-turn — then quoted
that description back verbatim when asked about it. It was copying its own recent calls,
and only something arriving in the loop competes with that. Once per player per turn,
only when there is Move Allowance left worth batching, never when `path` was used.

**Choosing** — `bb_game_choose` answers a Kick-off Event that asks the Coach
something (High Kick, Quick Snap!, Solid Defence, Charge!). While one is pending
the engine refuses everything else and says what it is waiting for — the ball is
still in the air and the first turn has not started. Declining is always legal.
Charge! is the odd one out: answering it starts a free turn the selected players
play through `bb_game_act`, and `bb_game_choose` again ends it. The Apothecary's
Casualty branch asks the same way — two Casualty Rolls, and the Coach picks.

**Finishing** — `bb_game_here` · `bb_game_nudge` · `bb_game_setup` · `bb_game_apothecary` · `bb_game_extra_time` · `bb_game_penalties`

`bb_game_legal` and `bb_game_odds` are free and side-effect-free — ask them as
often as you like. They exist so the coach never has to work out a dodge modifier
or a block's dice count itself.

## The data

`data/skills.json` — all 108 Skills and Traits with their real text, category,
Active/Passive, and the four Elite markers. Scraped by `tools_scrape_skills.py`
from the `<h4>`/`<p>` structure, because three things on that page are *markup*
rather than words: a Trait is a trailing `*`, an Elite Skill is an `<img>`, and
the category is the enclosing `<h3>`. The flattened text has none of them.

`data/rosters.json` — 30 teams, 159 positionals, 63 star players, with the site's
errata applied. Scraped from the published tables with `tools_scrape_rosters.py`;
`tools_kb_docs.py` turns the same data into knowledge-base documents with every
stat labelled.

The source of record is <https://bloodbowlbase.ru/bb2025/>. It is a community
transcription, not Games Workshop's own text — the printed rulebook settles
disputes.

## Development

```
python3.12 -m venv .venv && ./.venv/bin/pip install -r requirements-dev.txt
./.venv/bin/python -m pytest -q          # 235 tests, host-free — no protoAgent needed
./.venv/bin/ruff check . && ./.venv/bin/ruff format --check .
```

Use the venv's Python. The system `python3` is 3.9 and produces bogus failures
(`zip() takes no keyword arguments`).

### Driving the view

`pytest` proves the endpoints answer and the tools don't throw. It says nothing
about whether the board is *legible* — which is where the first version failed.
`harness.py` serves the plugin's own routers on a throwaway port and drives the
page in a real browser:

```
./.venv/bin/pip install playwright uvicorn && ./.venv/bin/playwright install chromium
./.venv/bin/python harness.py            # screenshot the board to shots/
./.venv/bin/python harness.py --check    # 44 assertions about what broke before
```

Playwright is deliberately **not** in `requirements-dev.txt`: CI stays fast and
host-free, and the harness is a local tool for looking at the thing.

Drop a `harness_theme.css` next to it (`:root { --pl-color-*: … }`) and the harness
injects it, so the screenshots show the agent's real theme rather than unthemed
white. Without the design-system kit the view falls through to its no-kit shim —
which is worth exercising, since that is what an older host serves.

## Installing

Copy the directory into an instance's `plugins/` (copy, never symlink) and enable
it:

```
curl -X POST -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
     -d '{"enabled":true}' http://127.0.0.1:PORT/api/plugins/bloodbowl/enabled
```

A **new route** needs a process restart — FastAPI cannot swap a mounted router in
place, so the first mount wins and the reload's `restart_recommended: false` is
misleading here. See [docs/HANDOFF.md](docs/HANDOFF.md) §4.
