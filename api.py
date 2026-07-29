"""Two routers, two prefixes (plugin-views rule 1).

``build_view_router``  -> mounted PUBLIC at /plugins/bloodbowl      (serves the page)
``build_data_router``  -> mounted GATED  at /api/plugins/bloodbowl  (serves the board)

An iframe navigation cannot carry a bearer, so the page must be reachable without
one; everything it then reads or writes goes through the authed prefix.
"""

from __future__ import annotations

from pathlib import Path

from .pitch import Player, Scenario, find_team, geometry, player_from_roster, team_names

# `store` is deliberately NOT imported here — the routers resolve it per request.
# See build_game_router for why.

WEB = Path(__file__).resolve().parent / "web"

# Only these are servable. An allowlist of suffixes rather than a traversal check
# alone: the static route is PUBLIC, so it should be incapable of serving a .py or
# a .json out of the plugin directory even if a path check is ever weakened.
_MEDIA = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
}


def build_view_router(cfg: dict | None = None):
    from fastapi import APIRouter, HTTPException
    from fastapi.responses import FileResponse, HTMLResponse

    r = APIRouter()

    @r.get("/view", response_class=HTMLResponse)
    async def _view() -> HTMLResponse:
        return HTMLResponse((WEB / "index.html").read_text(encoding="utf-8"))

    @r.get("/static/{path:path}")
    async def _static(path: str):
        """Serve the page's own modules and stylesheet.

        Public, like the page — an iframe navigation carries no bearer, and a
        stylesheet the browser cannot fetch leaves an unreadable board. Everything
        that reads or writes the BOARD still goes through the gated prefix.
        """
        target = (WEB / path).resolve()
        if not target.is_file() or WEB not in target.parents:
            raise HTTPException(status_code=404, detail="not found")
        media = _MEDIA.get(target.suffix.lower())
        if media is None:
            raise HTTPException(status_code=404, detail="not found")
        return FileResponse(target, media_type=media)

    return r


def build_data_router(cfg: dict | None = None):
    from fastapi import APIRouter, HTTPException

    r = APIRouter()

    # See build_game_router: resolved per request so a reload is not half-applied.
    def _store():
        from . import store

        return store

    def _ok(sc: Scenario) -> dict:
        _store().save(sc)
        return sc.to_dict()

    @r.get("/meta")
    async def _meta() -> dict:
        return {"geometry": geometry(), "teams": team_names(), "scenario": _store().load().to_dict()}

    @r.get("/state")
    async def _state() -> dict:
        return _store().load().to_dict()

    @r.get("/roster")
    async def _roster(team: str) -> dict:
        t = find_team(team)
        if t is None:
            raise HTTPException(status_code=404, detail=f"unknown team {team!r}")
        return t

    @r.post("/teams")
    async def _teams(body: dict) -> dict:
        sc = _store().load()
        for key in ("home_team", "away_team"):
            if key in body and body[key]:
                if find_team(body[key]) is None:
                    raise HTTPException(status_code=400, detail=f"unknown team {body[key]!r}")
                setattr(sc, key, find_team(body[key])["name"])
        return _ok(sc)

    @r.post("/place")
    async def _place(body: dict) -> dict:
        sc = _store().load()
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
        sc = _store().load()
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
        sc = _store().load()
        try:
            x, y = int(body["x"]), int(body["y"])
        except (KeyError, TypeError, ValueError):
            raise HTTPException(status_code=400, detail="x and y are required integers") from None
        sc.remove_at(x, y)
        return _ok(sc)

    @r.post("/clear")
    async def _clear(body: dict | None = None) -> dict:
        sc = _store().load()
        side = (body or {}).get("side")
        sc.clear(side if side in ("home", "away") else None)
        return _ok(sc)

    @r.post("/replace")
    async def _replace(body: dict) -> dict:
        """Set the whole board at once. Backs undo: the view keeps a stack of prior
        boards and posts one back wholesale rather than replaying moves, which would
        be wrong the moment the agent edited the board in between.

        Re-hydrates any player missing a statline from the shipped roster, so a board
        restored from a hand-written or trimmed payload still gets working hover
        cards instead of a row of blanks."""
        sc = Scenario.from_dict(body or {})
        for p in sc.players:
            if not (1 <= p.x <= 15 and 1 <= p.y <= 26):
                raise HTTPException(status_code=400, detail=f"({p.x},{p.y}) is off the pitch")
            if p.position:
                # The shipped roster is the source of truth for a statline, so
                # re-hydrate unconditionally rather than only when one is missing.
                # A board placed before a roster-data fix would otherwise keep the
                # stale blanks forever — which is exactly what happened when the
                # "Skills & Traits" header bug was corrected under a live board.
                team = p.team or (sc.home_team if p.side == "home" else sc.away_team)
                full, _err = player_from_roster(p.side, p.x, p.y, team, p.position)
                if full is not None:
                    p.team, p.MA, p.ST, p.AG = full.team, full.MA, full.ST, full.AG
                    p.PA, p.AV, p.skills, p.cost, p.role = full.PA, full.AV, full.skills, full.cost, full.role
        return _ok(sc)

    @r.get("/presets")
    async def _presets() -> dict:
        from .presets import all_presets

        return {"presets": [p.to_dict() for p in all_presets()]}

    @r.post("/presets/load")
    async def _preset_load(body: dict) -> dict:
        from .presets import apply_to, find

        preset = find(str((body or {}).get("name") or ""))
        if preset is None:
            raise HTTPException(status_code=404, detail=f"no preset named {(body or {}).get('name')!r}")
        side = str((body or {}).get("side") or "")
        sc = apply_to(
            preset,
            side=side if side in ("home", "away") else "",
            mirror=bool((body or {}).get("mirror")),
            current=_store().load(),
        )
        return _ok(sc)

    @r.post("/presets/save")
    async def _preset_save(body: dict) -> dict:
        from .presets import save as save_preset

        preset, err = save_preset(
            str((body or {}).get("name") or ""), _store().load(), note=str((body or {}).get("note") or "")
        )
        if preset is None:
            raise HTTPException(status_code=400, detail=err)
        return {"ok": True, "saved": preset.name}

    @r.post("/presets/delete")
    async def _preset_delete(body: dict) -> dict:
        from .presets import delete

        done, err = delete(str((body or {}).get("name") or ""))
        if not done:
            raise HTTPException(status_code=400, detail=err)
        return {"ok": True}

    @r.get("/previous")
    async def _previous() -> dict:
        """The board as it was before the last write — the restart-proof safety net
        behind the view's in-session undo."""
        prev = _store().load_previous()
        return {"scenario": prev.to_dict() if prev else None}

    return r


def build_game_router(cfg: dict | None = None):
    """The match, over HTTP.

    Every route here goes through ``engine.game``, which is the same code the
    agent's tools call. The view and the coach therefore cannot end up playing by
    different rules — the alternative is two implementations of a turnover that
    agree right up until they don't.
    """
    from fastapi import APIRouter, HTTPException

    r = APIRouter()

    # Imported per REQUEST, not per router-build, and that is not fussiness.
    # The host cannot swap a mounted router, so a router that binds `act` at
    # build time keeps calling the ORIGINAL function object for the life of the
    # process — while a lazy import elsewhere (store.py resolves `Match` inside a
    # function) picks the reloaded one up. The plugin then runs NEW state with OLD
    # rules, which is worse than not reloading at all: the symptoms are a match
    # payload carrying fields the engine does not honour. Looking the module up on
    # each call costs a dict lookup and makes `POST /enabled` mean what it says.
    def engine():
        from .engine import game

        return game

    def _store():
        from . import store

        return store

    def _need_match():
        m = _store().load_match()
        if m is None:
            raise HTTPException(status_code=404, detail="no match in progress")
        return m

    @r.get("/game")
    async def _game() -> dict:
        m = _store().load_match()
        if m is None:
            return {"ok": False, "match": None}
        return {"ok": True, **engine().state_report(m)}

    @r.post("/game/new")
    async def _new(body: dict | None = None) -> dict:
        body = body or {}
        sc = _store().load()
        if not sc.players:
            raise HTTPException(status_code=400, detail="the board is empty — set a scenario up first")
        m = engine().new_match(
            sc,
            seed=int(body.get("seed") or 0),
            kicking_to=("away" if body.get("kicking_to") == "away" else "home"),
        )
        _store().save_match(m)
        return {"ok": True, "match": m.to_dict(include_log=False)}

    @r.get("/game/legal")
    async def _legal(player: str) -> dict:
        return engine().legal_moves(_need_match(), player)

    @r.post("/game/act")
    async def _act(body: dict) -> dict:
        m = _need_match()
        body = body or {}
        # Forward the WHOLE command rather than naming the fields. Listing them
        # meant a Block's `target` was silently dropped the moment Blocking was
        # added — the request answered 200 with ok:false and the board simply did
        # nothing, which reads as a dead button rather than a bug.
        cmd = {k: v for k, v in body.items() if k != "action"}
        cmd["player"] = str(cmd.get("player") or "")
        before = len(m.events)
        report = engine().act(m, str(body.get("action") or "move"), cmd)
        _store().save_match(m)
        report["match"] = m.to_dict(include_log=False)
        report["log"] = [e.text for e in m.events[before:] if e.text]
        return report

    @r.post("/game/choose")
    async def _choose(body: dict | None = None) -> dict:
        """Answer the question a Kick-off Event stopped to ask. The board is where
        a coach will see it asked, so the board is where they must be able to
        answer — the pitch view refuses every other click until they do."""
        m = _need_match()
        before = len(m.events)
        out = engine().resolve_choice(m, dict(body or {}), engine().dice_for(m))
        if out.get("ok"):
            _store().save_match(m)
        out["log"] = [e.text for e in m.events[before:] if e.text]
        out["match"] = m.to_dict(include_log=False)
        return out

    @r.post("/game/end-turn")
    async def _end_turn() -> dict:
        m = _need_match()
        out = engine().end_turn(m)
        _store().save_match(m)
        out["match"] = m.to_dict(include_log=False)
        return out

    @r.post("/game/kickoff")
    async def _kickoff(body: dict | None = None) -> dict:
        m = _need_match()
        want = str((body or {}).get("receiving") or "")
        side = want if want in ("home", "away") else m.opponent(m.clock.active)
        before = len(m.events)
        engine().start_drive(m, receiving=side)
        _store().save_match(m)
        return {
            "ok": True,
            "drive": m.drive,
            "receiving": side,
            "log": [e.text for e in m.events[before:] if e.text],
            "match": m.to_dict(include_log=False),
        }

    @r.post("/game/abandon")
    async def _abandon() -> dict:
        return {"ok": True, "discarded": _store().clear_match()}

    @r.get("/game/log")
    async def _log(last: int = 40) -> dict:
        from .engine.events import describe

        m = _need_match()
        n = max(1, min(int(last), 200))
        return {
            "ok": True,
            "log": [
                {"kind": e.kind, "actor": e.actor, "text": describe(e), "rolls": [x.describe() for x in e.rolls]}
                for e in m.events[-n:]
            ],
        }

    return r
