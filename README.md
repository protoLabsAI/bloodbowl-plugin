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
- **Injury** — armour, the injury table, Stunned / Knocked-out / Casualty
- **The ball** — pick up, Secure the Ball, hand-off, passing with ranges and
  interceptions, bounces, catches, touchdowns
- **The game** — kick-off with the full event table, drives, half-time, full time

Every roll lands in an event log with its arithmetic shown, and that log is what
the agent narrates from.

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

Anything not modelled is **reported, not ignored** — but reported once. The first
time an unmodelled Skill is relevant, the log says so and then stops; `bb_game_state`
carries the standing list of every unmodelled Skill on the pitch and who has it.
Ten of the eleven kick-off events say plainly that they were rolled but not applied.

## Tools

**Rosters** — `bb_list_teams` · `bb_get_roster` · `bb_team_costs` ·
`bb_list_stars` · `bb_get_star`

**The board** — `bb_pitch_show` · `bb_pitch_setup` · `bb_pitch_place` ·
`bb_pitch_clear` · `bb_pitch_review`

**Presets** — `bb_presets` · `bb_preset_load` · `bb_preset_save` ·
`bb_preset_delete`

**Playing** — `bb_game_new` · `bb_game_state` · `bb_game_legal` · `bb_game_odds` ·
`bb_game_act` · `bb_game_end_turn` · `bb_game_kickoff` · `bb_game_log` ·
`bb_game_abandon` · `bb_pass_ranges`

`bb_game_legal` and `bb_game_odds` are free and side-effect-free — ask them as
often as you like. They exist so the coach never has to work out a dodge modifier
or a block's dice count itself.

## The data

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
