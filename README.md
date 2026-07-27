# bloodbowl-plugin

A Blood Bowl pitch, scenario board and roster reference for [protoAgent](https://github.com/protoLabsAI/protoAgent).

Adds one console view and seven tools. No network access, no secrets, no runtime pip deps.

## What it does

- **A real 26×15 pitch** in the console — End Zones one square deep, Wide Zones four
  squares wide, a seven-square Centre Field, and the Line of Scrimmage between rows 13
  and 14. Drawn as a CSS grid with an SVG overlay in pitch units, so the zone lines
  can't drift out of step with the squares.
- **Drag to place.** Pull a positional off the roster palette onto a square, drag a
  player to move them, drag one off the board to remove them. Hover any player for a
  stat card: MA/ST/AG/PA/AV, skills, cost.
- **Or ask the agent.** `bb_pitch_setup` places a whole formation in one call, so
  "set up an Orc defensive line against Skaven" works from chat. The board and the
  view are two writers onto the same state.
- **Ships parsed S3 roster data** for 30 teams — 159 positionals with full statlines,
  scraped from the published tables rather than recalled. A hover card reading a parsed
  cell cannot drift the way a paraphrase of a prose passage can.

## Tools

| Tool | What it does |
|---|---|
| `bb_list_teams` | Every team the roster data covers |
| `bb_get_roster` | One team's positionals, exact — quantities, stats, skills, cost |
| `bb_pitch_show` | Current board: geometry plus every player, square, zone, LoS flag |
| `bb_pitch_setup` | Place a whole formation in one call |
| `bb_pitch_place` | Place or move a single player |
| `bb_pitch_clear` | Clear the pitch, or one side |
| `bb_pitch_review` | Check a side against the S3 deployment limits |

`bb_pitch_review` **reports, it never blocks** — an illegal board is a legitimate thing
to want while working a shape out.

## Install

```
plugin install https://github.com/protoLabsAI/bloodbowl-plugin
```

Ships `enabled: false`. Enabling is the operator's decision.

## Data

Roster data is transcribed from [Blood Bowl Base](https://bloodbowlbase.ru/bb2025/),
a community reference for Third Season Edition. It is not Games Workshop's own text —
where a ruling is disputed, the printed rulebook settles it. Blood Bowl is a trademark
of Games Workshop; this is an unofficial fan tool.

## Development

```
python3.12 -m venv .venv && ./.venv/bin/pip install -r requirements-dev.txt
./.venv/bin/python -m pytest -q      # host-free suite, no protoAgent needed
./.venv/bin/ruff check . && ./.venv/bin/ruff format --check .
```

### Driving the view

`pytest` proves the endpoints answer and the tools don't throw. It says nothing
about whether the board is *legible* — which is where the first version failed.
`harness.py` serves the plugin's own routers on a throwaway port and drives the
page in a real browser:

```
./.venv/bin/pip install playwright uvicorn && ./.venv/bin/playwright install chromium
./.venv/bin/python harness.py            # screenshot the board to shots/
./.venv/bin/python harness.py --check    # assert the things that broke before
```

Playwright is deliberately **not** in `requirements-dev.txt`: CI stays fast and
host-free, and the harness is a local tool for looking at the thing.

Drop a `harness_theme.css` next to it (`:root { --pl-color-*: … }`) and the harness
injects it, so the screenshots show the agent's real theme rather than unthemed
white. Without the design-system kit the view falls through to its no-kit shim —
which is worth exercising, since that is what an older host serves.
