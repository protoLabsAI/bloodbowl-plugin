"""Blood Bowl pitch geometry, roster data and scenario state.

Pure logic, no host imports — so the whole module is unit-testable with nothing but
the standard library.

Geometry is the S3 pitch: **26 squares long by 15 wide**. Squares are addressed
``(x, y)`` 1-indexed, ``x`` across the width, ``y`` down the length. Row 1 and row 26
are the End Zones (one square deep each). The Line of Scrimmage runs between rows 13
and 14, so row 13 is the home half's front line and row 14 the away half's. The two
Wide Zones are four squares wide (x 1-4 and x 12-15), leaving a seven-square Centre
Field (x 5-11) — 4 + 7 + 4 = 15, which is the arithmetic check that the zones tile
the width exactly.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data" / "rosters.json"

LENGTH = 26  # squares, End Zone to End Zone
WIDTH = 15  # squares, sideline to sideline
END_ZONE_DEPTH = 1
WIDE_ZONE_WIDTH = 4
CENTRE_FIELD_WIDTH = WIDTH - 2 * WIDE_ZONE_WIDTH  # 7
LOS_ROWS = (13, 14)  # home front line, away front line

# Setup limits (S3). The plugin does not enforce these — it reports them, so the
# operator can see at a glance whether a formation would be legal.
MAX_PLAYERS_ON_PITCH = 11
MIN_ON_LINE_OF_SCRIMMAGE = 3
MAX_PER_WIDE_ZONE = 2

SIDES = ("home", "away")


def in_bounds(x: int, y: int) -> bool:
    return 1 <= x <= WIDTH and 1 <= y <= LENGTH


def zone_of(x: int) -> str:
    """Which lateral zone a column falls in."""
    if x <= WIDE_ZONE_WIDTH:
        return "wide_left"
    if x > WIDTH - WIDE_ZONE_WIDTH:
        return "wide_right"
    return "centre"


def is_end_zone(y: int) -> bool:
    return y <= END_ZONE_DEPTH or y > LENGTH - END_ZONE_DEPTH


def half_of(y: int) -> str:
    """`home` owns rows 1-13, `away` owns 14-26."""
    return "home" if y <= LOS_ROWS[0] else "away"


def on_line_of_scrimmage(side: str, y: int) -> bool:
    return y == (LOS_ROWS[0] if side == "home" else LOS_ROWS[1])


@dataclass
class Player:
    side: str  # home | away
    x: int
    y: int
    position: str = ""  # e.g. "Jaguar Warrior"
    team: str = ""  # e.g. "Amazon"
    label: str = ""  # short badge text; defaults to initials of `position`
    MA: str = ""
    ST: str = ""
    AG: str = ""
    PA: str = ""
    AV: str = ""
    skills: list[str] = field(default_factory=list)
    cost: str = ""
    role: str | None = None

    def badge(self) -> str:
        if self.label:
            return self.label
        parts = [p for p in self.position.split() if p]
        return ("".join(p[0] for p in parts[:2]) or "?").upper()


@dataclass
class Scenario:
    """One board state. Deliberately a SINGLE current pitch rather than a library —
    the operator asked for a practice board, not a saved-formation manager."""

    name: str = "Untitled"
    home_team: str = ""
    away_team: str = ""
    note: str = ""
    players: list[Player] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        for p, src in zip(d["players"], self.players, strict=True):
            p["badge"] = src.badge()
            p["zone"] = zone_of(src.x)
            p["on_los"] = on_line_of_scrimmage(src.side, src.y)
        return d

    @classmethod
    def from_dict(cls, data: dict) -> Scenario:
        known = set(Player.__dataclass_fields__)  # type: ignore[attr-defined]
        players = [Player(**{k: v for k, v in p.items() if k in known}) for p in (data.get("players") or [])]
        return cls(
            name=data.get("name") or "Untitled",
            home_team=data.get("home_team") or "",
            away_team=data.get("away_team") or "",
            note=data.get("note") or "",
            players=players,
        )

    # --- mutation ---------------------------------------------------------

    def at(self, x: int, y: int) -> Player | None:
        return next((p for p in self.players if p.x == x and p.y == y), None)

    def place(self, player: Player) -> tuple[bool, str]:
        if not in_bounds(player.x, player.y):
            return False, f"({player.x},{player.y}) is off the pitch — it is {WIDTH} wide by {LENGTH} long."
        if player.side not in SIDES:
            return False, f"unknown side {player.side!r}; use 'home' or 'away'."
        occupied = self.at(player.x, player.y)
        if occupied is not None:
            self.players.remove(occupied)
        self.players.append(player)
        return True, f"{player.position or 'player'} placed at ({player.x},{player.y})."

    def remove_at(self, x: int, y: int) -> bool:
        p = self.at(x, y)
        if p is None:
            return False
        self.players.remove(p)
        return True

    def clear(self, side: str | None = None) -> int:
        """Clear one side, or the whole pitch when ``side`` is None."""
        before = len(self.players)
        self.players = [] if side is None else [p for p in self.players if p.side != side]
        return before - len(self.players)

    # --- reporting (NOT enforcement) --------------------------------------

    def review(self, side: str) -> dict:
        """Describe a side's setup against the S3 limits. Reports; never blocks —
        an illegal board is a legitimate thing to want while working something out."""
        mine = [p for p in self.players if p.side == side]
        on_los = [p for p in mine if on_line_of_scrimmage(side, p.y)]
        wide_l = [p for p in mine if zone_of(p.x) == "wide_left"]
        wide_r = [p for p in mine if zone_of(p.x) == "wide_right"]
        wrong_half = [p for p in mine if half_of(p.y) != side]
        problems = []
        if len(mine) > MAX_PLAYERS_ON_PITCH:
            problems.append(f"{len(mine)} players on the pitch — the limit is {MAX_PLAYERS_ON_PITCH}.")
        if mine and len(on_los) < MIN_ON_LINE_OF_SCRIMMAGE:
            problems.append(
                f"{len(on_los)} on the Line of Scrimmage — at least {MIN_ON_LINE_OF_SCRIMMAGE} "
                "must be in the Centre Field adjacent to it."
            )
        for label, zone in (("left", wide_l), ("right", wide_r)):
            if len(zone) > MAX_PER_WIDE_ZONE:
                problems.append(f"{len(zone)} in the {label} Wide Zone — no more than {MAX_PER_WIDE_ZONE} are allowed.")
        if wrong_half:
            problems.append(f"{len(wrong_half)} deployed beyond the Line of Scrimmage into the opponent's half.")
        # INSIGNIFICANT: "When creating a Team Draft List, you may NOT include MORE
        # players with this Trait THAN players WITHOUT this Trait." A drafting rule,
        # and the nearest thing this engine has to a Draft List is the board — so
        # it is checked against the board and REPORTED, which is what the practice
        # board does with every other limit.
        small = [p for p in mine if any(s.split("(")[0].strip().casefold() == "insignificant" for s in p.skills)]
        if len(small) > len(mine) - len(small):
            problems.append(
                f"{len(small)} of {len(mine)} have Insignificant — a team may not include more "
                "players with that Trait than without it."
            )
        return {
            "side": side,
            "insignificant": len(small),
            "count": len(mine),
            "on_line_of_scrimmage": len(on_los),
            "wide_left": len(wide_l),
            "wide_right": len(wide_r),
            "legal": not problems,
            "problems": problems,
        }


# --- roster data ----------------------------------------------------------

_ROSTERS: dict | None = None


def rosters() -> dict:
    """The shipped S3 roster data, loaded once. Structured on purpose: a hover card
    reading a parsed table cannot drift the way a paraphrase of a prose chunk can."""
    global _ROSTERS
    if _ROSTERS is None:
        _ROSTERS = json.loads(DATA.read_text(encoding="utf-8"))
    return _ROSTERS


def team_names() -> list[str]:
    return [t["name"] for t in rosters()["teams"]]


def find_team(name: str) -> dict | None:
    if not name:
        return None
    want = name.strip().casefold()
    teams = rosters()["teams"]
    for t in teams:
        if t["name"].casefold() == want:
            return t
    # Forgiving match so "amazons" / "orcs" / "wood elves" resolve.
    for t in teams:
        n = t["name"].casefold()
        if want.startswith(n) or n.startswith(want) or want.rstrip("s") == n.rstrip("s"):
            return t
    return None


def stars() -> list[dict]:
    return rosters().get("stars", [])


def find_star(name: str) -> dict | None:
    """Look a Star Player up by name, forgivingly.

    Names carry punctuation a coach will not type back exactly — "Morg 'n' Thorg",
    "Ivan 'the Animal' Deathshroud" — so an exact match is tried first and then a
    punctuation-stripped substring match.
    """
    if not name:
        return None
    want = name.strip().casefold()
    all_stars = stars()
    for s in all_stars:
        if s["name"].casefold() == want:
            return s

    def bare(s: str) -> str:
        return re.sub(r"[^a-z0-9 ]+", "", s.casefold()).strip()

    wb = bare(want)
    if not wb:
        return None
    for s in all_stars:
        if bare(s["name"]) == wb:
            return s
    for s in all_stars:
        if wb in bare(s["name"]) or any(wb == bare(m["name"]) for m in s.get("members", [])):
            return s
    return None


def stars_for_team(team_name: str) -> list[dict]:
    """The Stars a team may hire, cheapest first.

    Read off the TEAM page's own star list, which is the side that prices them —
    a star's page names the teams but the price lives with the team.
    """
    team = find_team(team_name)
    if team is None:
        return []
    out = []
    for entry in team.get("star_players", []):
        s = find_star(entry["name"])
        out.append(
            {
                "name": entry["name"],
                "cost": entry.get("cost") or (s or {}).get("cost"),
                "known": s is not None,
            }
        )
    out.sort(key=lambda e: (int(re.sub(r"\D", "", e["cost"] or "0") or 0), e["name"]))
    return out


def find_position(team: dict, position: str) -> dict | None:
    want = (position or "").strip().casefold()
    if not want:
        return None
    for p in team["positionals"]:
        if p["position"].casefold() == want:
            return p
    for p in team["positionals"]:
        if want in p["position"].casefold() or (p.get("role") or "").casefold().startswith(want):
            return p
    return None


def player_from_roster(side: str, x: int, y: int, team_name: str, position: str) -> tuple[Player | None, str]:
    """Build a fully-statted Player from the shipped roster, or explain why not."""
    team = find_team(team_name)
    if team is None:
        return None, f"unknown team {team_name!r}. Known teams: {', '.join(team_names())}."
    pos = find_position(team, position)
    if pos is None:
        opts = ", ".join(p["position"] for p in team["positionals"])
        return None, f"{team['name']} has no positional matching {position!r}. It has: {opts}."
    return (
        Player(
            side=side,
            x=x,
            y=y,
            position=pos["position"],
            team=team["name"],
            MA=pos.get("MA", ""),
            ST=pos.get("ST", ""),
            AG=pos.get("AG", ""),
            PA=pos.get("PA", ""),
            AV=pos.get("AV", ""),
            skills=list(pos.get("skills") or []),
            cost=pos.get("cost", ""),
            role=pos.get("role"),
        ),
        "",
    )


def geometry() -> dict:
    """Everything the view needs to draw the board, from one place."""
    return {
        "length": LENGTH,
        "width": WIDTH,
        "end_zone_depth": END_ZONE_DEPTH,
        "wide_zone_width": WIDE_ZONE_WIDTH,
        "centre_field_width": CENTRE_FIELD_WIDTH,
        "los_rows": list(LOS_ROWS),
        "limits": {
            "max_players": MAX_PLAYERS_ON_PITCH,
            "min_on_los": MIN_ON_LINE_OF_SCRIMMAGE,
            "max_per_wide_zone": MAX_PER_WIDE_ZONE,
        },
    }
