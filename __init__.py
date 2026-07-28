"""bloodbowl — a Blood Bowl pitch, scenario board and roster reference.

``register()`` is the only place plugin code runs. Host-only imports stay lazy so
the test suite imports every module with no protoAgent host present.

The tools here deliberately return STRUCTURED roster data parsed from tables rather
than prose. A coach reading a stat off a parsed cell cannot drift the way a
paraphrase of a retrieved passage can — which is the failure this plugin is partly
built to route around.
"""

from __future__ import annotations

import json
import logging

from langchain_core.tools import tool

log = logging.getLogger("protoagent.plugins.bloodbowl")


def register(registry) -> None:
    cfg = registry.config or {}

    try:
        from .api import build_data_router, build_view_router

        registry.register_router(build_view_router(cfg), prefix="/plugins/bloodbowl")
        registry.register_router(build_data_router(cfg), prefix="/api/plugins/bloodbowl")
    except Exception:  # noqa: BLE001 — a router failure must not sink the tools
        log.exception("[bloodbowl] mounting routers failed")

    try:
        for t in _tools(cfg):
            registry.register_tool(t)
    except Exception:  # noqa: BLE001
        log.exception("[bloodbowl] registering tools failed")

    log.info("[bloodbowl] registered")


def _tools(cfg: dict):
    from .pitch import (
        Player,
        find_star,
        find_team,
        geometry,
        player_from_roster,
        stars,
        stars_for_team,
        team_names,
    )
    from .store import load, save

    @tool
    def bb_list_teams() -> str:
        """List every Blood Bowl team the shipped S3 roster data covers.

        Use this before placing players so the team name you pass is one that exists.
        """
        teams = []
        for t in _rosters_teams():
            teams.append({"name": t["name"], "tier": t.get("tier"), "positionals": len(t["positionals"])})
        return json.dumps({"count": len(teams), "teams": teams})

    @tool
    def bb_get_roster(team: str) -> str:
        """Exact positionals for one team: quantity limits, MA/ST/AG/PA/AV, skills,
        primary/secondary skill access and cost.

        This is parsed table data, not prose — quote it directly rather than
        recalling stats from memory.
        """
        t = find_team(team)
        if t is None:
            return json.dumps({"ok": False, "error": f"unknown team {team!r}", "known": team_names()})
        return json.dumps({"ok": True, "team": t})

    @tool
    def bb_team_costs(team: str) -> str:
        """What a team pays for staff and, crucially, a Team Re-roll — plus its
        league and special rules.

        Re-roll price varies by team and drives most drafting decisions, so read it
        here rather than recalling it.
        """
        t = find_team(team)
        if t is None:
            return json.dumps({"ok": False, "error": f"unknown team {team!r}", "known": team_names()})
        return json.dumps(
            {
                "ok": True,
                "team": t["name"],
                "tier": t.get("tier"),
                "reroll_cost": t.get("reroll_cost"),
                "staff": t.get("staff", {}),
                "league": t.get("league", []),
                "special_rules": t.get("special_rules", []),
            }
        )

    @tool
    def bb_list_stars(team: str = "") -> str:
        """Star Players. With a team name, only the Stars that team may hire, priced
        for that team and cheapest first; with no team, every Star in the data.
        """
        if team:
            t = find_team(team)
            if t is None:
                return json.dumps({"ok": False, "error": f"unknown team {team!r}", "known": team_names()})
            hire = stars_for_team(t["name"])
            return json.dumps({"ok": True, "team": t["name"], "count": len(hire), "stars": hire})
        every = [{"name": s["name"], "cost": s["cost"], "teams": len(s.get("teams", []))} for s in stars()]
        return json.dumps({"ok": True, "count": len(every), "stars": every})

    @tool
    def bb_get_star(name: str) -> str:
        """One Star Player in full: cost, statline, skills, their own special rule
        and its exact text, and which teams may hire them.

        A pair (Grak and Crumbleberry, the Swift Twins) comes back with one entry
        per member under ``members`` and a single price for the pair.
        """
        s = find_star(name)
        if s is None:
            return json.dumps({"ok": False, "error": f"unknown star {name!r}", "known": [x["name"] for x in stars()]})
        return json.dumps({"ok": True, "star": s})

    @tool
    def bb_pitch_show() -> str:
        """The current state of the practice pitch: geometry, both teams, and every
        player placed with their square, zone and whether they are on the Line of
        Scrimmage.
        """
        sc = load()
        return json.dumps({"geometry": geometry(), "scenario": sc.to_dict()})

    @tool
    def bb_pitch_setup(
        home_team: str = "",
        away_team: str = "",
        players: str = "",
        name: str = "",
        clear_first: bool = True,
    ) -> str:
        """Set up a scenario on the pitch in one call.

        ``players`` is a JSON list of objects, each ``{"side","position","x","y"}`` —
        side is "home" or "away", x is 1-15 across the width, y is 1-26 along the
        length. Row 1 and row 26 are the End Zones; the Line of Scrimmage sits
        between rows 13 (home) and 14 (away). Positions are looked up in the named
        team's roster, so the placed player carries real stats.

        Example: [{"side":"home","position":"Jaguar Warrior","x":7,"y":13}]
        """
        sc = load()
        if clear_first:
            sc.clear(None)
        if name:
            sc.name = name
        if home_team:
            t = find_team(home_team)
            if t is None:
                return json.dumps({"ok": False, "error": f"unknown team {home_team!r}", "known": team_names()})
            sc.home_team = t["name"]
        if away_team:
            t = find_team(away_team)
            if t is None:
                return json.dumps({"ok": False, "error": f"unknown team {away_team!r}", "known": team_names()})
            sc.away_team = t["name"]

        try:
            spec = json.loads(players) if players else []
        except json.JSONDecodeError as exc:
            return json.dumps({"ok": False, "error": f"players must be a JSON list: {exc}"})

        placed, errors = 0, []
        for row in spec:
            side = str(row.get("side") or "home")
            team = str(row.get("team") or (sc.home_team if side == "home" else sc.away_team) or "")
            try:
                x, y = int(row["x"]), int(row["y"])
            except (KeyError, TypeError, ValueError):
                errors.append(f"{row!r}: x and y are required integers")
                continue
            player, err = player_from_roster(side, x, y, team, str(row.get("position") or ""))
            if player is None:
                player = Player(side=side, x=x, y=y, label=str(row.get("label") or ""))
                if err:
                    errors.append(err)
            good, msg = sc.place(player)
            if good:
                placed += 1
            else:
                errors.append(msg)

        save(sc)
        return json.dumps(
            {
                "ok": not errors,
                "placed": placed,
                "errors": errors,
                "home": sc.review("home"),
                "away": sc.review("away"),
            }
        )

    @tool
    def bb_pitch_place(side: str, position: str, x: int, y: int, team: str = "") -> str:
        """Place or move a single player onto a square of the practice pitch.

        ``x`` is 1-15 across the width, ``y`` is 1-26 along the length. Placing onto
        an occupied square replaces whoever was there.
        """
        sc = load()
        team = team or (sc.home_team if side == "home" else sc.away_team) or ""
        player, err = player_from_roster(side, int(x), int(y), team, position)
        if player is None:
            return json.dumps({"ok": False, "error": err})
        good, msg = sc.place(player)
        if not good:
            return json.dumps({"ok": False, "error": msg})
        save(sc)
        return json.dumps({"ok": True, "message": msg, "review": sc.review(side)})

    @tool
    def bb_pitch_clear(side: str = "") -> str:
        """Clear the practice pitch. Pass "home" or "away" to clear one side only."""
        sc = load()
        n = sc.clear(side if side in ("home", "away") else None)
        save(sc)
        return json.dumps({"ok": True, "removed": n})

    @tool
    def bb_pitch_review(side: str = "home") -> str:
        """Check a side's current setup against the S3 deployment limits — 11 players
        max, at least 3 in the Centre Field on the Line of Scrimmage, no more than 2
        in each Wide Zone, nobody past the Line of Scrimmage.

        Reports; it never blocks. An illegal board is a legitimate thing to want
        while working a shape out.
        """
        return json.dumps(load().review(side if side in ("home", "away") else "home"))

    return [
        bb_list_teams,
        bb_get_roster,
        bb_team_costs,
        bb_list_stars,
        bb_get_star,
        bb_pitch_show,
        bb_pitch_setup,
        bb_pitch_place,
        bb_pitch_clear,
        bb_pitch_review,
    ]


def _rosters_teams() -> list[dict]:
    from .pitch import rosters

    return rosters()["teams"]
