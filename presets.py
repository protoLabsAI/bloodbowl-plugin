"""Named setups you can recall, compare and talk about.

The practice board was deliberately a SINGLE current pitch rather than a library.
That was the right call for "help me work a shape out"; it is the wrong one as
soon as the shapes are worth keeping — you cannot say "put the Orc defensive line
back up" to a board that only remembers where things are right now.

A preset is a saved arrangement, not a saved match: teams, players and squares,
with a name and a note. It is deliberately NOT a Match — a formation outlives the
game it was tried in, and freezing dice and a clock into it would make it useless
for the thing it is for.

Two kinds live side by side and are told apart by ``builtin``:

* SHIPPED presets, defined below, so a fresh install has something to look at and
  the names mean the same thing on every instance. They cannot be overwritten or
  deleted, because a reference layout that someone has edited is no longer a
  reference.
* SAVED presets, written by the operator or the agent into the instance's state
  directory.

Coordinates are stored exactly as the board uses them (x across 1-15, y along
1-26) so a preset can be diffed against a live board without a transform.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from .pitch import LOS_ROWS, Player, Scenario, in_bounds


@dataclass
class Preset:
    """``kind`` matters more than it looks.

    A SETUP is a kick-off formation and must obey the S3 deployment limits — 11
    players, 3+ in the Centre Field on the Line of Scrimmage, 2 per Wide Zone,
    nobody past the line. A FORMATION is a mid-game shape like a cage, which
    legitimately straddles the Line of Scrimmage and would be illegal as a setup.

    Without the distinction the legality sweep has to be either wrong about cages
    or useless about setups. With it, a test can hold every setup to the same
    rules the board itself reports on.
    """

    name: str
    kind: str = "setup"  # setup | formation
    note: str = ""
    home_team: str = ""
    away_team: str = ""
    players: list[dict] = field(default_factory=list)
    builtin: bool = False

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "kind": self.kind,
            "note": self.note,
            "home_team": self.home_team,
            "away_team": self.away_team,
            "players": [dict(p) for p in self.players],
            "builtin": self.builtin,
            "counts": self.counts(),
        }

    def counts(self) -> dict:
        return {
            "home": sum(1 for p in self.players if p.get("side") == "home"),
            "away": sum(1 for p in self.players if p.get("side") == "away"),
        }

    @classmethod
    def from_dict(cls, d: dict, builtin: bool = False) -> Preset:
        return cls(
            name=str(d.get("name") or "Untitled"),
            kind="formation" if str(d.get("kind") or "") == "formation" else "setup",
            note=str(d.get("note") or ""),
            home_team=str(d.get("home_team") or ""),
            away_team=str(d.get("away_team") or ""),
            players=[dict(p) for p in (d.get("players") or [])],
            builtin=builtin,
        )


def slug(name: str) -> str:
    """A filename-safe key. Two presets whose names differ only by punctuation or
    case are the same preset — otherwise "Orc Defence" and "orc defence" quietly
    become two entries that neither the operator nor the agent can tell apart."""
    s = re.sub(r"[^a-z0-9]+", "-", str(name or "").casefold()).strip("-")
    return s or "untitled"


# --- shipped presets ------------------------------------------------------
#
# Deliberately generic: a SHAPE with roles, not a specific team's roster. A
# preset that named Orc positionals would be unusable for anyone playing Skaven,
# and the point of a reference layout is that it transfers.
#
# Each is legal under the S3 setup limits the board already reports on: 11 players
# max, at least 3 in the Centre Field on the Line of Scrimmage, no more than 2 per
# Wide Zone. `tests/test_presets.py` asserts that rather than trusting the comment.

_LOS_HOME = LOS_ROWS[0]  # 13


def _row(side: str, y: int, xs: list[int], role: str) -> list[dict]:
    return [{"side": side, "x": x, "y": y, "label": role} for x in xs]


BUILTIN: list[Preset] = [
    Preset(
        name="Standard defence",
        note=(
            "Three on the Line of Scrimmage, a flat screen behind, one deep safety. "
            "The default shape: it concedes nothing cheaply and gives up the flanks last."
        ),
        builtin=True,
        players=(
            _row("home", _LOS_HOME, [7, 8, 9], "LOS")
            + _row("home", _LOS_HOME - 1, [4, 6, 10, 12], "screen")
            + _row("home", _LOS_HOME - 3, [3, 8, 13], "back")
            + _row("home", _LOS_HOME - 6, [8], "safety")
        ),
    ),
    Preset(
        name="Wide zone press",
        note=(
            "Two in each Wide Zone — the legal maximum — to contest a flank runner early. "
            "Strong against a fast team, thin up the middle if it fails."
        ),
        builtin=True,
        players=(
            _row("home", _LOS_HOME, [7, 8, 9], "LOS")
            + _row("home", _LOS_HOME - 1, [2, 3, 13, 14], "wide")
            + _row("home", _LOS_HOME - 2, [6, 10], "inside")
            + _row("home", _LOS_HOME - 4, [8], "cover")
            + _row("home", _LOS_HOME - 7, [8], "safety")
        ),
    ),
    Preset(
        name="Cage",
        kind="formation",
        note=(
            "The four corners around a carrier, the shape everything else in the game is "
            "built to break. A mid-game FORMATION, not a kick-off setup: it straddles the "
            "Line of Scrimmage on purpose, so it is deliberately not setup-legal."
        ),
        builtin=True,
        players=(
            [{"side": "home", "x": 8, "y": 13, "label": "BALL"}]
            + _row("home", 12, [7, 9], "corner")
            + _row("home", 14, [7, 9], "corner")
            + _row("home", 13, [5, 11], "escort")
            + _row("home", 11, [8], "lead")
        ),
    ),
    Preset(
        name="Kick-off receive",
        note=(
            "A deep receiving shape: a legal line, a spread middle, and two back to "
            "collect a kick. Pair it with a defence to rehearse the first drive."
        ),
        builtin=True,
        players=(
            _row("home", _LOS_HOME, [7, 8, 9], "LOS")
            + _row("home", _LOS_HOME - 2, [4, 12], "wide")
            + _row("home", _LOS_HOME - 4, [6, 10], "mid")
            + _row("home", _LOS_HOME - 7, [3, 13], "deep")
            + _row("home", _LOS_HOME - 9, [8], "catcher")
        ),
    ),
    Preset(
        name="Line of Scrimmage only",
        note="The bare legal minimum — three in the Centre Field on the line. A starting point.",
        builtin=True,
        players=_row("home", _LOS_HOME, [7, 8, 9], "LOS"),
    ),
]


# --- storage --------------------------------------------------------------


def presets_dir() -> Path:
    from .store import state_dir

    d = state_dir() / "presets"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _saved() -> dict[str, Preset]:
    out: dict[str, Preset] = {}
    for f in sorted(presets_dir().glob("*.json")):
        try:
            out[f.stem] = Preset.from_dict(json.loads(f.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            # One unreadable file must not hide the whole library.
            continue
    return out


def all_presets() -> list[Preset]:
    """Shipped first, then saved, each alphabetical.

    A saved preset may NOT shadow a shipped one — the names are how the operator
    and the agent refer to the same thing, and a reference layout that means
    something different on one instance is worse than no reference at all.
    """
    builtin = {slug(p.name): p for p in BUILTIN}
    saved = {k: v for k, v in _saved().items() if k not in builtin}
    return sorted(builtin.values(), key=lambda p: p.name) + sorted(saved.values(), key=lambda p: p.name)


def find(name: str) -> Preset | None:
    key = slug(name)
    return next((p for p in all_presets() if slug(p.name) == key), None)


def save(name: str, scenario: Scenario, note: str = "", kind: str = "setup") -> tuple[Preset | None, str]:
    """Store the current board under a name. Returns (preset, error)."""
    key = slug(name)
    if not str(name or "").strip():
        return None, "a preset needs a name"
    if any(slug(p.name) == key for p in BUILTIN):
        return None, f"{name!r} is a shipped preset and cannot be overwritten — pick another name"
    if not scenario.players:
        return None, "the board is empty — there is nothing to save"

    preset = Preset(
        name=str(name).strip(),
        kind="formation" if kind == "formation" else "setup",
        note=str(note or "").strip(),
        home_team=scenario.home_team,
        away_team=scenario.away_team,
        players=[
            {"side": p.side, "x": p.x, "y": p.y, "position": p.position, "team": p.team, "label": p.label}
            for p in scenario.players
        ],
    )
    path = presets_dir() / f"{key}.json"
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".preset-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(preset.to_dict(), fh, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        Path(tmp).unlink(missing_ok=True)
        raise
    return preset, ""


def delete(name: str) -> tuple[bool, str]:
    key = slug(name)
    if any(slug(p.name) == key for p in BUILTIN):
        return False, f"{name!r} is a shipped preset and cannot be deleted"
    path = presets_dir() / f"{key}.json"
    if not path.exists():
        return False, f"no preset named {name!r}"
    path.unlink()
    return True, ""


def apply_to(preset: Preset, side: str = "", mirror: bool = False, current: Scenario | None = None) -> Scenario:
    """Build a board from a preset.

    ``side`` restricts it to one side's players; ``mirror`` flips a home shape into
    the away half, which is what makes a single stored defence usable as both a
    defence and the thing you practise attacking into.

    ``current`` is the board being replaced, and exists so a SHIPPED preset does
    not clear your team selection. Those presets store roles rather than a roster
    and carry no teams at all, so overwriting Orc-vs-Skaven with two blanks loses
    work the preset never had an opinion about. A SAVED preset that did record
    teams still restores them.
    """
    from .pitch import LENGTH, player_from_roster

    sc = Scenario(name=preset.name, note=preset.note)
    sc.home_team = preset.home_team or (current.home_team if current else "")
    sc.away_team = preset.away_team or (current.away_team if current else "")
    for raw in preset.players:
        p_side = str(raw.get("side") or "home")
        if side and p_side != side:
            continue
        x, y = int(raw.get("x") or 0), int(raw.get("y") or 0)
        if mirror:
            # Reflect along the length. The Line of Scrimmage sits between the two
            # centre rows, so a shape that was legal for home is legal for away.
            y = LENGTH + 1 - y
            p_side = "away" if p_side == "home" else "home"
        if not in_bounds(x, y):
            continue
        team = str(raw.get("team") or (sc.home_team if p_side == "home" else sc.away_team) or "")
        position = str(raw.get("position") or "")
        player = None
        if team and position:
            player, _err = player_from_roster(p_side, x, y, team, position)
        if player is None:
            # A shipped preset stores a ROLE, not a positional, so most of these
            # land here — a labelled token, ready to be swapped for real players.
            player = Player(side=p_side, x=x, y=y, label=str(raw.get("label") or ""))
        sc.place(player)
    return sc
