"""Two routers, two prefixes (plugin-views rule 1).

``build_view_router``  -> mounted PUBLIC at /plugins/bloodbowl      (serves the page)
``build_data_router``  -> mounted GATED  at /api/plugins/bloodbowl  (serves the board)

An iframe navigation cannot carry a bearer, so the page must be reachable without
one; everything it then reads or writes goes through the authed prefix.
"""

from __future__ import annotations

from .pitch import Player, Scenario, find_team, geometry, player_from_roster, team_names
from .store import load, save
from .view import PAGE


def build_view_router(cfg: dict | None = None):
    from fastapi import APIRouter
    from fastapi.responses import HTMLResponse

    r = APIRouter()

    @r.get("/view", response_class=HTMLResponse)
    async def _view() -> HTMLResponse:
        return HTMLResponse(PAGE)

    return r


def build_data_router(cfg: dict | None = None):
    from fastapi import APIRouter, HTTPException

    r = APIRouter()

    def _ok(sc: Scenario) -> dict:
        save(sc)
        return sc.to_dict()

    @r.get("/meta")
    async def _meta() -> dict:
        return {"geometry": geometry(), "teams": team_names(), "scenario": load().to_dict()}

    @r.get("/state")
    async def _state() -> dict:
        return load().to_dict()

    @r.get("/roster")
    async def _roster(team: str) -> dict:
        t = find_team(team)
        if t is None:
            raise HTTPException(status_code=404, detail=f"unknown team {team!r}")
        return t

    @r.post("/teams")
    async def _teams(body: dict) -> dict:
        sc = load()
        for key in ("home_team", "away_team"):
            if key in body and body[key]:
                if find_team(body[key]) is None:
                    raise HTTPException(status_code=400, detail=f"unknown team {body[key]!r}")
                setattr(sc, key, find_team(body[key])["name"])
        return _ok(sc)

    @r.post("/place")
    async def _place(body: dict) -> dict:
        sc = load()
        try:
            x, y = int(body["x"]), int(body["y"])
        except (KeyError, TypeError, ValueError):
            raise HTTPException(status_code=400, detail="x and y are required integers") from None
        side = str(body.get("side") or "home")
        team = str(body.get("team") or (sc.home_team if side == "home" else sc.away_team) or "")
        position = str(body.get("position") or "")
        player, err = player_from_roster(side, x, y, team, position)
        if player is None:
            # Still allow a bare token — a blank square is a legitimate placeholder
            # while working out shapes, and refusing it would make the board unusable
            # for a team we failed to parse.
            if team or position:
                raise HTTPException(status_code=400, detail=err)
            player = Player(side=side, x=x, y=y, label=str(body.get("label") or ""))
        placed, msg = sc.place(player)
        if not placed:
            raise HTTPException(status_code=400, detail=msg)
        return _ok(sc)

    @r.post("/move")
    async def _move(body: dict) -> dict:
        sc = load()
        try:
            fx, fy = int(body["from"]["x"]), int(body["from"]["y"])
            tx, ty = int(body["to"]["x"]), int(body["to"]["y"])
        except (KeyError, TypeError, ValueError):
            raise HTTPException(status_code=400, detail="from{x,y} and to{x,y} are required") from None
        p = sc.at(fx, fy)
        if p is None:
            raise HTTPException(status_code=404, detail=f"no player at ({fx},{fy})")
        sc.players.remove(p)
        p.x, p.y = tx, ty
        placed, msg = sc.place(p)
        if not placed:
            p.x, p.y = fx, fy
            sc.players.append(p)
            raise HTTPException(status_code=400, detail=msg)
        return _ok(sc)

    @r.post("/remove")
    async def _remove(body: dict) -> dict:
        sc = load()
        try:
            x, y = int(body["x"]), int(body["y"])
        except (KeyError, TypeError, ValueError):
            raise HTTPException(status_code=400, detail="x and y are required integers") from None
        sc.remove_at(x, y)
        return _ok(sc)

    @r.post("/clear")
    async def _clear(body: dict | None = None) -> dict:
        sc = load()
        side = (body or {}).get("side")
        sc.clear(side if side in ("home", "away") else None)
        return _ok(sc)

    return r
