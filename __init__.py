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
import uuid

from langchain_core.tools import tool

# WHICH CHAT A TOOL WAS CALLED FROM. `current_session_id()` reads EMPTY inside a
# tool body — the tracing contextvar does not survive the hop — so the graph state
# is the only reliable carrier, and it arrives through this annotation.
#
# Guarded because `langgraph` is a HOST dependency: this plugin's own suite and its
# browser harness import and register it with no host at all, and must keep doing
# so. Without langgraph the parameter is a plain optional that nothing fills in,
# every tool still works, and only the session binding is lost — which is exactly
# what "no host" should cost.
try:  # pragma: no cover — one branch per environment, and CI only has one
    from typing import Annotated, Any

    from langgraph.prebuilt import InjectedState

    _Injected = Annotated[Any, InjectedState]
except Exception:  # noqa: BLE001
    _Injected = "Any"


def _session_of(state) -> str:
    """The chat id out of injected graph state, or "" when there is none."""
    if not state:
        return ""
    if isinstance(state, dict):
        return str(state.get("session_id") or "")
    return str(getattr(state, "session_id", "") or "")


log = logging.getLogger("protoagent.plugins.bloodbowl")

# How the plugin publishes on the bus. Captured at registration because the routers
# and tools are built once and the registry is not reachable from them — and left
# as a no-op so every host-free test and the harness behave exactly as before.
_emit = None

# How many separate single-square Move calls each player has cost in the CURRENT turn,
# and who has already been told about it.
#
# NOT game state, and deliberately not an event: how many tool calls a coach spent is a
# fact about the conversation, not about the match, and the log is for facts a replay must
# reproduce. Same line `engine/pace.py` sits on. Process-local, so a restart forgets it —
# which is the right cost for a hint.
_STEP_CALLS: dict = {"turn": None, "counts": {}, "told": set()}

#: Say something on the SECOND single-square call for a player in a turn. The first is
#: ordinary — a step into a tackle zone, a shuffle for position, and a coach who wanted one
#: square should not be lectured for asking for one. By the second it is a pattern, and the
#: whole point is to interrupt it while the run is still ahead of them rather than after.
_HINT_AFTER = 2

#: Below this there is nothing left to batch and the advice would just be noise.
_HINT_MIN_LEFT = 2


def _seat_sessions() -> tuple:
    """The session ids of a full-AI match's two seats, or () when there is no such match.

    Read from the board rather than recomputed, so it stays true to whatever the match
    actually recorded — including matches started before per-match tokens existed.
    """
    try:
        from .store import load_match

        m = load_match()
    except Exception:  # noqa: BLE001 — a nudge must never die on a bad read
        return ()
    return tuple((getattr(m, "session_ids", None) or {}).values()) if m is not None else ()


def _seat_of(match, state) -> str:
    """Which SIDE this call is coming from, or "agent" when it cannot be told.

    A full-AI match seats one agent per side in its own conversation, and the engine has
    to be able to tell them apart — both are "agent", so the controller kind cannot. The
    session the tool is running in is the only thing that distinguishes them, and
    `match.session_ids` already records it.

    Falls back to "agent", which is right for a head-to-head: there is only one agent seat
    and the controller kind identifies it perfectly well.
    """
    session = _session_of(state)
    if not session:
        return "agent"
    for side, sid in (getattr(match, "session_ids", None) or {}).items():
        if sid == session:
            return side
    return "agent"


def _ai_sessions(base: str) -> dict:
    """A chat per seat for a full-AI match — a FRESH pair for every match.

    Derived from the conversation the game was started in, so both are findable
    afterwards (the whole match is readable as two transcripts) and stable across a
    reload; the ids are recorded in `match_started` and folded like everything else. A
    match started from the BOARD has no conversation behind it, so the seats hang off a
    fixed prefix rather than the Activity thread, which one seat could otherwise flood.

    **THE TOKEN IS THE POINT.** These used to be fixed strings, so every full-AI match
    ever played reused `bloodbowl:home` and `bloodbowl:away`. A seat therefore inherited
    every earlier match's transcript — including, after two games were abandoned, three
    consecutive turns concluding "No match in progress — game already concluded". It then
    repeated that conclusion for a live match WITHOUT CALLING bb_game_state at all, and
    the game sat frozen for nine minutes while the nudges fired into it.

    A model trusts its own recent output over its instructions; the fix is not to argue
    with it in the prompt but to stop handing it somebody else's conversation. A fresh
    pair per match cannot inherit a previous game's conclusions.
    """
    root = (base or "bloodbowl").strip() or "bloodbowl"
    token = uuid.uuid4().hex[:6]
    return {"home": f"{root}:{token}:home", "away": f"{root}:{token}:away"}


def _step_hint(match, player: str, used_path: bool) -> str:
    """A nudge toward ``path``, delivered in the REPLY rather than the docstring.

    The docstring is read once, at the top of a long context; the reply lands at the
    moment of the decision, every time. That difference is not theoretical — the `path`
    parameter shipped fully documented and an agent went on spending one call per square
    for a whole turn until it ran out of budget mid-activation, having quoted the
    documentation back verbatim when asked. It was copying its own recent calls, and only
    something arriving IN the loop can compete with that.

    Fires at most once per player per turn (`first_mentions` precedent — honest, not
    loud), only when there is enough Move Allowance left for the advice to be worth
    taking, and never when they already used ``path``.
    """
    clock = match.clock
    turn = (clock.half, clock.turn, clock.active)
    if _STEP_CALLS["turn"] != turn:
        _STEP_CALLS.update({"turn": turn, "counts": {}, "told": set()})
    counts = _STEP_CALLS["counts"]

    if used_path:
        # They took the advice — start them clean rather than holding the earlier
        # single-square calls against them for the rest of the turn.
        counts[player] = 0
        return ""

    counts[player] = counts.get(player, 0) + 1
    if counts[player] < _HINT_AFTER or player in _STEP_CALLS["told"]:
        return ""
    who = match.by_id(player)
    if who is None or who.done or who.down != "standing":
        return ""
    left = who.movement() - who.ma_used
    if left < _HINT_MIN_LEFT:
        return ""
    _STEP_CALLS["told"].add(player)
    return (
        f"That is {counts[player]} separate calls to move {who.name()} this turn, and they have "
        f"{left} squares of Move Allowance left. Send the rest of the run as one `path` — "
        "the squares in order, each adjacent to the last. Every square is still adjudicated "
        "on its own; you are only saving the round trip, and a turn spent one call per square "
        "runs out of turn before the team runs out of Move Allowance."
    )


def announce(before: dict, after: dict) -> dict:
    """Publish `bloodbowl.turn_ready` when the match starts waiting on somebody NEW.

    This is the seam that makes a head-to-head playable: the human can see the board
    and knows it is their move, but an agent only acts when something asks it to.
    `sdk.react_on` turns this event into an agent turn — see `register`.
    """
    from .engine import handover

    fresh = handover.changed(before, after)
    if fresh and _emit is not None:
        try:
            _emit("turn_ready", dict(fresh))
        except Exception:  # noqa: BLE001 — a bus failure must never break a move
            log.exception("[bloodbowl] publishing turn_ready failed")
    return fresh


def register(registry) -> None:
    global _emit
    cfg = registry.config or {}
    _emit = getattr(registry, "emit", None)

    try:
        # The Range Ruler is the one measured (not quoted) thing in the engine, so
        # it is configurable — see engine/ruler.py.
        from .engine.pace import configure as _configure_pace
        from .engine.ruler import configure as _configure_ruler

        _configure_ruler(cfg)
        # How fast the agent may play. See engine/pace.py — a turn taken at model
        # speed arrives as a diff rather than as something you can watch.
        _configure_pace(cfg)
        # Where the player icons come from. An operator's own pack wins over the
        # shipped one FILE BY FILE, so replacing a single Troll does not mean
        # redrawing the other 152.
        from .sprites import use_pack as _use_pack

        _use_pack(cfg.get("sprite_dir"))
    except Exception:  # noqa: BLE001
        log.exception("[bloodbowl] range-ruler config failed")

    try:
        # THE BOARD RIDES THE PROMPT (ADR 0032). A seat was reading the position
        # nine to sixteen times a turn, and between two reads the board moves —
        # so a coach working from an earlier answer is working from a board that
        # no longer exists. Attached per model call, it is always current, costs
        # no round trip, and replaces its own previous copy instead of stacking.
        from .middleware import factory as _board_middleware

        if hasattr(registry, "register_middleware"):
            registry.register_middleware(_board_middleware)
    except Exception:  # noqa: BLE001
        log.exception("[bloodbowl] range-ruler config failed")

    try:
        from .api import build_data_router, build_game_router, build_view_router

        registry.register_router(build_view_router(cfg), prefix="/plugins/bloodbowl")
        # ONE router per prefix. The host mounts plugin routers keyed on
        # (plugin_id, prefix) and SKIPS any it has already mounted, so handing it a
        # second router for /api/plugins/bloodbowl silently discarded every game
        # route — the whole match API 404'd on a real host while the board's routes
        # worked, because the data router happened to be registered first.
        data = build_data_router(cfg)
        data.include_router(build_game_router(cfg, announce=announce))
        registry.register_router(data, prefix="/api/plugins/bloodbowl")
    except Exception:  # noqa: BLE001 — a router failure must not sink the tools
        log.exception("[bloodbowl] mounting routers failed")

    try:
        for t in _tools(cfg):
            registry.register_tool(t)
    except Exception:  # noqa: BLE001
        log.exception("[bloodbowl] registering tools failed")

    # THE NUDGE. When the match starts waiting on the side the AGENT plays, run a
    # turn in the Activity thread telling it to play. Without this a head-to-head
    # stalls the moment the human ends their turn: the board is correct, it is the
    # agent's move, and nothing has told the agent that.
    #
    # Guarded because the SDK is a host module — every test and the browser harness
    # import this plugin with no host at all, and must keep working.
    # SUBSCRIBING AT REGISTRATION IS TOO EARLY AND FAILS SILENTLY. `register()` runs
    # during the GRAPH BUILD; the host's event bus is not populated until the
    # server's STARTUP hook, so `registry.on(...)` here logs "dropped — no bus" at
    # debug level and the nudge never arrives. Nothing is broken, nothing is
    # reported, and the agent simply never takes its turn.
    #
    # `register_surface` is the seam for exactly this: `start` runs in the startup
    # hook, by which time `registry.host.on` exists.
    def _start_nudge():
        from graph import sdk  # type: ignore[import-not-found]

        # NOT `sdk.react_on`: that binds ONE session at registration, and the
        # session varies per match — the whole point is that your opponent's turn
        # arrives in the chat you are playing in. So this subscribes directly and
        # calls `run_in_session` with the session the match itself recorded.
        def _turn_ready(payload: dict) -> None:
            d = (payload or {}).get("data") or {}
            if d.get("controller") != "agent":
                return
            if d.get("why") == "answer":
                prompt = (
                    f"Your Blood Bowl opponent has moved, and the engine is waiting on an answer "
                    f"from {d.get('side')} — your side: {d.get('question')}\n\n"
                    "Answer it with bb_game_choose, then carry on with your turn."
                )
            else:
                # Who is on the other side changes what the closing sentence is FOR.
                # Against a person it is a message to them. In a full-AI match the
                # other seat is a separate conversation that will never read it, and
                # telling an agent to address an opponent who cannot hear it invites
                # exactly the narrating-to-yourself the human version warns against —
                # so there it becomes the note the SPECTATOR reads, which is the only
                # audience a self-playing game has.
                closing = (
                    "Then say in a sentence or two what you did and what it means for the "
                    "position — somebody is watching the board, and that note is the only "
                    "commentary they get. The other coach is a separate conversation and "
                    "will never see it; all they get is the board, so do not address them."
                    if d.get("opponent") == "agent"
                    else "Then tell your opponent in a sentence or two what you did and what it "
                    "means for the position — you are playing a person, not narrating to yourself."
                )
                prompt = (
                    f"Your turn in the Blood Bowl match: you play {d.get('side')}, and it was "
                    f"half {d.get('half')}, turn {d.get('turn')} when this was sent.\n\n"
                    "Read the board with bb_game_state, then PLAY THE WHOLE TURN — activate the "
                    "players you want and finish with bb_game_end_turn, which hands the board back. "
                    "bb_game_legal and bb_game_odds are free, so check before you commit.\n\n"
                    "THE BOARD IS THE TRUTH, not this message. Nudges queue, so the clock may have "
                    "moved on since — if the board says it is the other coach's turn, or that a "
                    "Kick-off question is pending for THEM, that is the game working and not a "
                    "fault: say so in one line and stop. A pending question blocks everything, "
                    "including the ball landing, until the coach it belongs to answers it. Never "
                    "conclude a match is broken or orphaned from this message alone. " + closing
                )
            # A match started from the BOARD has no chat behind it, so fall back to
            # the durable Activity thread rather than dropping the turn on the
            # floor. `bb_game_here` is how a person moves it into their own chat.
            session = str(d.get("session_id") or "") or "system:activity"
            # A FULL-AI SEAT GETS A FRESH CONVERSATION PER TURN.
            #
            # A seat's context grows by roughly 40k tokens a turn — one `bb_game_state` is
            # ~2.6k, `bb_game_legal` is asked per player, and every `bb_game_act` returns
            # its events. Measured on a live match: an away seat rebuilt 165,000 tokens in
            # FOUR turns after being purged. It then degraded exactly as you would expect —
            # burning a whole 600s fire timeout on three model calls without moving anyone,
            # and eventually not responding to a nudge at all. Purging bought one turn.
            #
            # A turn is self-contained: the BOARD is the truth and the engine is
            # authoritative, which is the invariant this whole plugin is built on. A coach
            # who needs to know what happened has `bb_game_log`. So each turn starts clean
            # and the context is bounded by construction rather than by hoping a match ends
            # before the window does.
            #
            # HEAD-TO-HEAD IS UNTOUCHED: there the session is the PERSON's chat, and
            # fragmenting it per turn would scatter their game across sixteen threads.
            # Only the seats minted by `_ai_sessions` are split, and they are already
            # machine-side conversations nobody reads live.
            if session in (_seat_sessions() or ()):
                session = f"{session}:h{d.get('half')}t{d.get('turn')}"
            # A job id PER HANDOVER, not one shared id. `run_in_session` is
            # idempotent-REPLACE: a second call with the same id CANCELS the
            # pending one. That is right for a chatty rule that only needs its
            # latest firing — and wrong here, where every handover is a distinct
            # turn that must actually run. With a constant id, a nudge for turn 3
            # silently cancelled turn 2 and the game stopped dead with nobody to
            # act. Observed live: five nudges, three turns.
            # A UNIQUE id PER ATTEMPT, not per handover.
            #
            # `run_in_session` is idempotent-REPLACE, so re-using an id CANCELS whatever
            # that id already has pending — including a turn that is CURRENTLY RUNNING.
            # A constant id was the first version of this bug (a nudge for turn 3
            # cancelled turn 2); per-handover fixed that and introduced a subtler one:
            # re-nudging the SAME handover — which is exactly what `bb_game_nudge` and
            # `POST /game/nudge` are for, and what any watchdog does when a board looks
            # stuck — killed the turn it was trying to rescue. Observed: a seat mid-blitz
            # reporting "my previous turn was interrupted mid-action", three times over,
            # and an httpx.ReadTimeout on the abandoned A2A stream that the scheduler then
            # logged as a failed fire and retried.
            #
            # With a per-attempt id a re-nudge QUEUES instead. A duplicate turn is safe
            # now: the seat check refuses anything that is not that side's move, so a
            # late arrival finds the handover gone and stops.
            job = (
                f"bloodbowl-turn-h{d.get('half')}t{d.get('turn')}-{d.get('side')}-{d.get('why')}-{uuid.uuid4().hex[:6]}"
            )
            out = sdk.run_in_session(session, prompt, job_id=job)
            log.info("[bloodbowl] nudged %s (%s): %s", session, job, out.get("message"))

        registry.on("bloodbowl.turn_ready", _turn_ready)
        log.info("[bloodbowl] turn nudge subscribed")

    try:
        # ⚠️ `reload=` IS NOT OPTIONAL HERE, AND LEAVING IT OUT COSTS THE WHOLE
        # FEATURE SILENTLY.
        #
        # `start` runs in the STARTUP hook. A plugin reload re-registers the
        # surface and does NOT re-run start — while the reload does drop the
        # handler the previous start had subscribed. So after any `POST /enabled`
        # the bus has nobody listening for `bloodbowl.turn_ready`, and a full-AI
        # match sits at the kick-off forever: no error, no warning, an idle board
        # that looks exactly like a slow model.
        #
        # Measured on the live agent: 313 nudges over the plugin's life, then a
        # deploy, then a match that never took a single turn. The log tells you
        # which state you are in — "turn nudge subscribed" appears once per START,
        # so a `loaded bloodbowl:` line with no subscribe line after it means the
        # nudge is dead until a restart.
        #
        # Re-subscribing is safe rather than doubling: the reload drops the old
        # handler, which is exactly why nothing fired at all.
        registry.register_surface(
            _start_nudge,
            name="bloodbowl-turns",
            reload=lambda _cfg=None: _start_nudge(),
        )
    except Exception:  # noqa: BLE001 — no host, no nudge; the plugin still works
        log.debug("[bloodbowl] turn nudge unavailable (no host)", exc_info=True)

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

    # --- preset setups ----------------------------------------------------

    @tool
    def bb_presets() -> str:
        """Named setups you can recall by name — shipped reference shapes plus
        anything saved on this instance.

        Use these instead of describing a formation square by square. "Put the
        standard defence up" is both faster and less likely to go wrong than
        eleven separate placements.
        """
        from .presets import all_presets

        items = [{"name": p.name, "note": p.note, "builtin": p.builtin, **p.counts()} for p in all_presets()]
        return json.dumps({"ok": True, "count": len(items), "presets": items})

    @tool
    def bb_preset_load(name: str, side: str = "", mirror: bool = False) -> str:
        """Put a preset onto the practice board, replacing what is there.

        ``side`` loads only one side's players. ``mirror`` flips a home shape into
        the away half, so one stored defence serves both as a defence and as the
        thing you practise attacking into.

        Shipped presets store ROLES rather than positionals, so they arrive as
        labelled tokens on the right squares — swap in real players with
        bb_pitch_place.
        """
        from .presets import apply_to, find
        from .store import save as save_board

        preset = find(name)
        if preset is None:
            from .presets import all_presets

            return json.dumps(
                {"ok": False, "error": f"no preset named {name!r}", "known": [p.name for p in all_presets()]}
            )
        sc = apply_to(preset, side=side if side in ("home", "away") else "", mirror=bool(mirror), current=load())
        save_board(sc)
        return json.dumps({"ok": True, "loaded": preset.name, "note": preset.note, "board": sc.to_dict()})

    @tool
    def bb_preset_save(name: str, note: str = "") -> str:
        """Save the current board as a named preset so it can be recalled later."""
        from .presets import save as save_preset
        from .store import load

        preset, err = save_preset(name, load(), note=note)
        if preset is None:
            return json.dumps({"ok": False, "error": err})
        return json.dumps({"ok": True, "saved": preset.name, **preset.counts()})

    @tool
    def bb_preset_delete(name: str) -> str:
        """Delete a saved preset. Shipped presets cannot be deleted."""
        from .presets import delete

        done, err = delete(name)
        return json.dumps({"ok": done, "error": err} if not done else {"ok": True, "deleted": name})

    # --- playing a match --------------------------------------------------
    #
    # The division of labour is the point. The coach decides WHAT to do and says
    # why; the engine decides whether that was legal and what the dice said. So
    # there is a free, side-effect-less way to ask what is possible
    # (`bb_game_legal`) and a separate one to commit (`bb_game_act`) — and the
    # narration comes from `bb_game_log`, which holds the rolls as they happened.

    @tool
    def bb_roster_options(team: str) -> str:
        """What a team may hire, with the limits and costs that apply to drafting it.

        Every positional with its Hiring Fee and how many the roster allows, the cost of a
        Team Re-roll, whether an Apothecary is available, and the rulebook's own caps —
        11-16 players, 8 Team Re-rolls, 6 Assistant Coaches, 6 Cheerleaders, Dedicated
        Fans improvable to 3 at 5,000 each. Ask this before drafting rather than working
        from memory: the costs and quantities differ per team and are printed, not derived.
        """
        from .draft import team_options

        o = team_options(team)
        return json.dumps(o if o else {"ok": False, "error": f"unknown team {team!r}"})

    @tool
    def bb_roster_save(
        name: str,
        team: str,
        players: dict,
        rerolls: int = 0,
        coaches: int = 0,
        cheerleaders: int = 0,
        apothecary: bool = False,
        fans: int = 1,
        budget: int = 0,
    ) -> str:
        """Save a Team Draft List, and say what (if anything) is wrong with it.

        ``players`` maps a positional name to how many you are hiring —
        ``{"Gnoblar Lineman": 10, "Ogre Blocker": 5}``. Everything is costed and checked
        server-side against the shipped roster: over budget, over a positional's limit,
        fewer than 11 or more than 16, an Apothecary a team may not hire.

        An ILLEGAL list still saves, with its problems reported. A team is over budget and
        short of players for most of the time it is being drafted, and refusing to record
        work in progress would make the tool useless. Placing one on the board is the step
        that refuses.
        """
        from .draft import DEFAULT_BUDGET, price, problems, save

        roster = {
            "team": team,
            "players": {k: int(v) for k, v in (players or {}).items()},
            "rerolls": int(rerolls),
            "coaches": int(coaches),
            "cheerleaders": int(cheerleaders),
            "apothecary": bool(apothecary),
            "fans": int(fans),
            "budget": int(budget) or DEFAULT_BUDGET,
        }
        try:
            saved_roster = save(name, roster)
        except ValueError as exc:
            return json.dumps({"ok": False, "error": str(exc)})
        return json.dumps(
            {"ok": True, "roster": saved_roster, "price": price(saved_roster), "problems": problems(saved_roster)}
        )

    @tool
    def bb_roster_list() -> str:
        """Every saved Team Draft List: team, squad size, what it cost, and whether it is legal."""
        from .draft import saved

        return json.dumps({"ok": True, "rosters": saved()})

    @tool
    def bb_roster_import_fumbbl(team_json: str, name: str = "", save: bool = False) -> str:
        """Turn a FUMBBL team into a Team Draft List. Paste the JSON from
        `https://fumbbl.com/api/team/get/<team id>`.

        This plugin makes no network calls, so the coach fetches that URL and hands
        the JSON over; nothing here contacts FUMBBL.

        The two catalogues disagree about names in both directions at once — FUMBBL's
        `Underworld Troll` is our `Troll*`, its bare `Blitzer` is our `Dwarf Blitzer`,
        and `Dwarf Blocker Lineman` is BB2020's name for `Dwarf Lineman`. Matching is
        scoped to the identified team and reports `how` for each position. ANYTHING IT
        CANNOT MATCH IS NAMED IN `unmatched` RATHER THAN GUESSED, so read that before
        playing the list: it is short by exactly those players.

        `save=True` stores it as a roster; the import is otherwise read-only.
        """
        from . import draft as _d
        from . import fumbbl as _f

        try:
            payload = json.loads(team_json) if isinstance(team_json, str) else team_json
        except (TypeError, ValueError) as exc:
            return json.dumps({"ok": False, "error": f"that is not JSON: {exc}"})
        if not isinstance(payload, dict):
            return json.dumps({"ok": False, "error": "expected the team object from /api/team/get/<id>"})

        report = _f.import_team(payload, name=name)
        if report["ok"] and save:
            report["roster"] = _d.save(report["roster"]["name"], report["roster"])
            report["saved"] = True
        return json.dumps(report)

    @tool
    def bb_roster_get(name: str) -> str:
        """One saved Team Draft List, itemised, with its remaining Treasury and any problems."""
        from .draft import load, price, problems

        d = load(name)
        if d is None:
            return json.dumps({"ok": False, "error": f"no roster named {name!r}"})
        return json.dumps({"ok": True, "roster": d, "price": price(d), "problems": problems(d)})

    @tool
    def bb_game_new(
        seed: int = 0,
        kicking_to: str = "",
        rerolls: int = -1,
        assistant_coaches: int = 0,
        cheerleaders: int = 0,
        dedicated_fans: int = 1,
        weather: str = "",
        apothecary: bool = False,
        you: str = "",
        state: _Injected = None,
    ) -> str:
        """Start a match from the current practice board.

        Every player set up on the board takes the field. Pass a ``seed`` to make
        the match reproducible — the same seed and the same moves replay to the
        same game. The practice board is left untouched.

        ``kicking_to`` names the RECEIVING side. Leave it out and the engine rolls
        off for it, which is what the rules do — "The Coach who rolls highest
        decides which team is kicking and which team is receiving."

        ``rerolls`` is how many Team Re-rolls EACH side gets; leave it out for the
        default. How many a team really has is a drafting decision and a practice
        board was never drafted, so the engine takes it as an input and tells you
        what it used rather than inventing one.

        ``assistant_coaches`` and ``cheerleaders`` are the same kind of number, and
        the Kick-off Event Table asks for both: Brilliant Coaching adds Assistant
        Coaches to a D6 for a free Team Re-roll, Cheering Fans adds Cheerleaders
        for a free Offensive Assist. Both sides get whatever you pass.

        ``you`` starts a HEAD-TO-HEAD: name the side the person is playing and you
        take the other one. The board refuses to move your players and your tools
        refuse to move theirs, and when the turn comes to you the engine says so.
        Leave it out for a practice match where one coach moves both teams.

        ``you="neither"`` starts a FULL-AI match — you play BOTH sides and the game
        plays itself to full time, one turn handing over to the next. Each side gets
        its OWN conversation, so neither seat can read the other's plan; all either
        knows about the opposition is what is on the board. Nobody has to be
        watching for it to finish, and every roll is in the log afterwards.

        ``dedicated_fans`` feeds the Pre-game Sequence's first step: Fan Factor is
        ROLLED as "a D3 [for Fair-weather Fans] plus your Dedicated Fans
        Characteristic", and a drafted team "automatically" has 1 of those. Pitch
        Invasion adds the total to a D6, so this is not decoration.

        ``weather`` forces a condition for a drill — one of ``sweltering_heat``,
        ``very_sunny``, ``perfect``, ``pouring_rain``, ``blizzard``. Left out, it
        is rolled on the Weather Table like a real game, and the reply says which
        came up. Rain penalises every ball roll and a Blizzard forbids a Long
        Pass outright, so it is worth knowing before planning a drive.
        """
        from .engine.game import new_match
        from .store import load, save_match

        sc = load()
        if not sc.players:
            return json.dumps({"ok": False, "error": "the board is empty — set a scenario up first"})
        m = new_match(
            sc,
            seed=int(seed or 0),
            kicking_to=kicking_to if kicking_to in ("home", "away") else "",
            rerolls=None if int(rerolls) < 0 else int(rerolls),
            staff={
                side: {
                    "assistant_coaches": int(assistant_coaches),
                    "cheerleaders": int(cheerleaders),
                    "dedicated_fans": int(dedicated_fans),
                }
                for side in ("home", "away")
            },
            weather=weather or None,
            apothecary=bool(apothecary),
            # HEAD-TO-HEAD: `you` is the side the PERSON plays; the agent takes the
            # other. Left out, nobody owns a side and either may move anyone, which
            # is the practice board and is still the default.
            controllers=(
                {"home": "agent", "away": "agent"}
                if str(you).strip().lower() == "neither"
                else ({you: "human", ("away" if you == "home" else "home"): "agent"} if you in ("home", "away") else {})
            ),
            # The chat you start the game in is the chat it gets played in — your
            # opponent's turns arrive where you are looking rather than in the
            # Activity thread. See Match.session_id.
            session_id=_session_of(state),
            # FULL AI: a chat per seat, derived from this one so they are findable
            # and stable across a reload. Two agent seats sharing a conversation
            # would be one coach with both hands — see Match.session_ids.
            session_ids=_ai_sessions(_session_of(state)) if str(you).strip().lower() == "neither" else None,
        )
        save_match(m)
        out = {"ok": True, "match": m.to_dict(include_log=False), "message": m.events[0].text}
        if m.pending:
            # The kick-off can end with a question, and the ball is still in the
            # air until it is answered. Say so HERE rather than letting the coach
            # discover it by having their first action refused.
            out["pending"] = dict(m.pending)
            out["message"] = str(m.pending.get("text") or "") + " Answer with bb_game_choose, or decline."
        return json.dumps(out)

    @tool
    def bb_game_nudge() -> str:
        """Re-send the "it is your move" signal for whoever the match is waiting on.

        The handover is automatic and this should never be needed. It exists
        because a nudge can be LOST — an agent restart mid-turn, a cancelled job —
        and when that happens the board is correct, it is genuinely somebody's
        move, and nothing happens. That looks exactly like thinking.
        """
        from .engine import handover
        from .store import load_match

        m = load_match()
        if m is None:
            return json.dumps({"ok": False, "error": "no match in progress"})
        owed = handover.owed(m)
        if not owed:
            return json.dumps({"ok": False, "error": "nobody is waiting on a move"})
        announce({}, owed)
        return json.dumps({"ok": True, "nudged": owed})

    @tool
    def bb_game_here(state: _Injected = None) -> str:
        """Play the current match HERE — in this conversation.

        Your turns are enqueued into a chat, so a head-to-head has to know which
        one. A match you started with ``bb_game_new`` is already bound to the chat
        you started it in; use this for one that was started **from the board**,
        which has no conversation behind it, or to move a game into a different
        chat. Until it is bound, your turns arrive in the Activity thread.
        """
        from .engine.events import Event
        from .store import load_match, save_match

        sid = _session_of(state)
        if not sid:
            return json.dumps(
                {
                    "ok": False,
                    "error": "no session to bind — this looks like a host-free call, "
                    "so there is no conversation to play in",
                }
            )
        m = load_match()
        if m is None:
            return json.dumps({"ok": False, "error": "no match in progress; start one with bb_game_new"})
        was = m.session_id
        m.apply(
            Event(
                kind="session_bound",
                detail={"session_id": sid, "was": was},
                text="This match will be played out here.",
            )
        )
        save_match(m)
        return json.dumps({"ok": True, "session_id": sid, "was": was, "message": "your turns will arrive here"})

    @tool
    def bb_game_state() -> str:
        """The match as it stands: clock, score, ball, and every player with their
        square, status and movement used.

        Read this rather than recalling where anyone was — the board changes every
        action.

        ``unmodelled_skills`` lists the Skills currently on the pitch that this
        engine does NOT apply, and who is carrying them. Worth a glance before
        promising a coach what a player will do.

        ``partly_modelled_skills`` is the one that catches people out: a Skill the
        engine applies in PART, with the clause it leaves out spelled in
        ``not_applied``. Read it before saying a skill "works" — half of a skill
        working looks exactly like all of it working.
        """
        from .engine.game import state_report
        from .store import load_match

        m = load_match()
        if m is None:
            return json.dumps({"ok": False, "error": "no match in progress; start one with bb_game_new"})
        return json.dumps({"ok": True, **state_report(m)})

    @tool
    def bb_game_legal(player: str) -> str:
        """What a player may do right now — every square they could step to, which
        need a Dodge and at what modifier, and which need a Rush.

        Ask this BEFORE moving. It is the engine's own arithmetic; working the
        odds out from a board description instead is how a confident wrong answer
        gets made. Costs nothing and changes nothing, so ask freely.
        """
        from .engine.game import legal_moves
        from .store import load_match

        m = load_match()
        if m is None:
            return json.dumps({"ok": False, "error": "no match in progress"})
        return json.dumps(legal_moves(m, player))

    @tool
    def bb_game_act(
        action: str,
        player: str,
        x: int = 0,
        y: int = 0,
        target: str = "",
        path: list | None = None,
        prefer: str = "",
        follow_up: bool = True,
        team_reroll: bool = False,
        drop_ball: bool = False,
        second_target: str = "",
        sidestep_to: list | None = None,
        stand_firm: bool | None = None,
        trickster_to: list | None = None,
        juggernaut: bool | None = None,
        state: _Injected = None,
    ) -> str:
        """Take an action.

        ``action`` is one of:
          "move"    — with ``x``/``y`` for ONE square, or ``path`` for a whole run
                      (see below); picks the ball up automatically if it is
                      lying on the square you step onto
          "block"   — with ``target``
          "blitz"   — with ``target``: DECLARES a Blitz against them. Rolls
                      nothing. Then "move" as normal, and "block" that same
                      target once adjacent — see below
          "foul"    — with ``target``, an ADJACENT opponent who is already Prone
                      or Stunned. The mirror of a Block, which needs a Standing
                      target. A natural double on either the Armour or Injury
                      Roll gets your player SENT OFF for the rest of the match
                      and causes a turnover — the engine then rolls Argue the
                      Call for you
          "handoff" — with ``target``, an ADJACENT team-mate who must Catch it
          "throwteam" — with ``target`` (an ADJACENT team-mate with Right Stuff)
                      and ``x``/``y``. Only a Quick or Short Throw — a team-mate
                      does not go as far as a ball. Dropping them is NOT a
                      turnover unless they were holding the ball
          "pass"    — with ``x``/``y``, the target SQUARE. Check bb_game_legal
                      first: it gives the range band and the modifier
          "kickteam" — like "throwteam", but it does NOT use up the team's Throw
                      Team-mate for the turn. A fumble hurts the kicked player
          "ball_chain" — with ``facing`` ("north"/"south"/"east"/"west"): the
                      player is dragged D6-wise up to their MA, Dodging for free
          "throw_bomb" — with ``x``/``y``: thrown like a Pass, explodes where it
                      stops, and catches everyone adjacent on a 4+
          "stab" · "vomit" · "breathe_fire" · "chainsaw" · "chomp" — SPECIAL
                      ACTIONS, each granted by a Skill of the same name and each
                      taking an adjacent Standing ``target``. Any number of
                      players may use one per turn, and one may REPLACE the Block
                      of a Blitz — but a Special Action is not a Block, so nothing
                      that affects a Block affects it
          "forego"  — this player will not be activated at all, and cannot be
                      later this turn. Use it to END an activation too: a player
                      who could have walked the ball in and did not is Stalling,
                      and the crowd rolls for it when the activation finishes
          "secure"  — S3's Secure the Ball: a flat 2+ pick-up that ends the
                      activation, legal only when no Standing opponent is within
                      2 squares OF THE BALL

        ``path`` WALKS A WHOLE RUN IN ONE CALL: [[8,15],[8,16],[8,17]] — the
        squares in order, each adjacent to the last. A Move is still one square at
        a time and every square is adjudicated exactly as a lone Move would be,
        with each Dodge and Rush rolled and logged in turn; what this saves is the
        round trip, not the rules. Prefer it for any run of more than a square or
        two — a turn spent one call per square is how a coach runs out of turn
        before the team runs out of Move Allowance.

        THE ROUTE IS STILL YOURS. The engine will not choose a way across the
        pitch for you: which squares, and whose tackle zones they pass, is the
        tactical decision. Check bb_game_legal for the squares around the player
        before committing to one.

        The run STOPS where the plan stops applying, and says where: a refusal, a
        Turnover, your player going down or off the pitch, or a push landing them
        somewhere other than the square you asked for — after which the rest of
        the route was drawn against a board that no longer exists. The reply
        carries ``steps_taken`` / ``steps_requested`` and, when it stopped early,
        ``halted`` with the reason. ``ok`` is true only if EVERY square was walked,
        so read ``steps_taken`` rather than assuming the run finished.

        A BLITZ IS THREE CALLS, not one: declare it, walk, hit them — and you may
        keep walking afterwards, which is the whole point of a Blitz. Your team
        gets ONE per turn and declaring spends it even if you never throw the
        Block, so check bb_game_legal for the distance first. The Block itself
        costs a square of Move Allowance on top of the walk; if none is left the
        engine will Rush for it, and a failed Rush floors your player.

        ``team_reroll=True`` pre-commits a Team Re-roll: if a roll in this action
        fails and one could save it, the engine spends one. It is a PRE-commitment
        because the engine cannot stop mid-action to ask — check bb_game_legal for
        how many are left first. A free Skill re-roll is always tried before the
        team's, and a Loner must pass their own D6 or the re-roll is lost anyway.

        For a Block, ``prefer`` says WHAT YOU ARE TRYING TO DO — and it only means
        anything when you are the one entitled to choose the dice, which is when
        your player is the stronger. Ask bb_game_odds first: it tells you how many
        dice you get and who picks them. ``follow_up`` moves into the vacated
        square after a push.

            ""           leave it out. The engine applies the best face for
                         whoever is choosing, which is the right answer nearly
                         always — including preferring Both Down over a push when
                         your blocker will not fall on it.
            "push"       MOVE them rather than flatten them: into the crowd, off
                         the ball, out of a lane. This is the one intent the
                         engine cannot infer, because it is about the SQUARE
                         rather than the player.
            "knockdown"  put them down even at the cost of going down yourself.

        THERE IS NO WAY TO NAME A DIE, and that is deliberate. The parameter here
        used to be an INDEX into the rolled faces — which had to be sent before the
        roll, so it named a die nobody had seen. Every value was a guess, and one
        whole live game was played taking the first die every single time because
        an integer parameter invites a 0. A preference is answerable at the moment
        it is asked, which an index never was.

        Like ``team_reroll``, it is a PRE-commitment: the engine cannot stop
        mid-action to ask, and a Block can roll dice four times over (the roll
        itself, plus the Brawler, Hatred and Team Re-roll re-rolls). Your stated
        intent is applied to each of them.

        FOUR OF THESE BELONG TO THE DEFENDER, and all four say "may" in the rules,
        so the engine's policy is a default rather than the rule:

          ``sidestep_to``  [x, y] — SIDESTEP: where the pushed player goes
          ``stand_firm``   true to refuse the push outright, false to take one the
                           engine would otherwise have refused
          ``trickster_to`` [x, y] — TRICKSTER: where they slip to before the dice
                           are counted
          ``juggernaut``   false to keep a Both Down the Skill would have converted

        ``second_target`` on a Block is a MULTIPLE BLOCK — "two Block Actions each
        targeting a different opposition player they are Marking", at ST -2 and
        with no Follow-up from either. It needs the Skill.

        ``drop_ball=True`` on a Move is a FUMBLEROOSKI — "they may choose to place
        the ball on the ground in any square they move out of … this will not cause
        a Turnover". It needs the Skill, and it is asked for rather than assumed:
        the engine will not put the ball down on anybody's behalf.

        The engine adjudicates: an illegal action is refused with a reason rather
        than performed. The reply carries every roll that was made — quote those
        rather than describing what probably happened.
        """
        from .store import match_write

        # ⚠️ THE WHOLE ROUND TRIP IS SERIALISED, not just the save.
        #
        # This loads the match, applies an action and saves it back. A model
        # batches independent tool calls IN PARALLEL — normal, and usually what
        # you want — and two of them racing here both read the same state, each
        # apply their action, and the second save silently discards the first.
        # The action does not fail. It VANISHES, and the coach then spends its
        # turn arguing with a board that disagrees with what it just did.
        #
        # Observed on a live agent, which worked it out itself and still lost the
        # turn to it: "I intended to build a cage but the cage moves (h03, h06)
        # did not persist due to the parallel-call race."
        with match_write():
            from .engine import handover, pace
            from .engine.game import act, walk
            from .store import load_match, save_match

            m = load_match()
            if m is None:
                return json.dumps({"ok": False, "error": "no match in progress"})
            cmd = {
                "player": player,
                "x": int(x),
                "y": int(y),
                "team_reroll": bool(team_reroll),
                "drop_ball": bool(drop_ball),
            }
            if action == "block":
                cmd.update({"target": target, "follow_up": bool(follow_up)})
                # Only when something was actually said. `prefer` is a word rather than
                # an index precisely so that "I did not answer" and "I want the first
                # die" can no longer arrive looking identical — the empty string is
                # silence and the engine plays the side entitled to choose.
                if str(prefer).strip():
                    cmd["prefer"] = str(prefer).strip().lower()
                if second_target:
                    cmd["second_target"] = second_target
                # The DEFENDER's choices. Each belongs to the coach being Blocked
                # rather than the one Blocking, and each says "may" in the rules — so
                # the engine's policy is a default, not the rule.
                for field, value in (
                    ("sidestep_to", tuple(sidestep_to) if sidestep_to else None),
                    ("stand_firm", stand_firm),
                    ("trickster_to", tuple(trickster_to) if trickster_to else None),
                    ("juggernaut", juggernaut),
                ):
                    if value is not None:
                        cmd[field] = value
            elif action in ("handoff", "blitz", "foul", "throwteam"):
                cmd["target"] = target
            # The agent plays at a human pace. This is a real wait, and it is the whole
            # point: the board polls every couple of seconds, and a turn played faster
            # than that is not something a person can watch happen.
            waited = pace.wait()
            before = len(m.events)
            was = handover.owed(m)
            if action == "move" and path:
                # Save and pace BETWEEN squares, not just at the end. Both belong out here
                # rather than in the engine, and both are the reason a run is watchable: the
                # board polls every couple of seconds and reads the saved match, so a run
                # persisted only once arrives as a jump cut — you cannot see which step cost
                # the Dodge, which is the one thing worth watching a move for.
                def after_step(_report):
                    save_match(m)
                    pace.wait()

                report = walk(m, player, path, cmd=cmd, by=_seat_of(m, state), after_step=after_step)
            else:
                report = act(m, action, cmd, by=_seat_of(m, state))
            save_match(m)
            # The agent's own move can hand the game back — a Turnover does exactly
            # that — so this side announces too. `changed` is what stops it firing on
            # every action of its own turn.
            announce(was, handover.owed(m))
            if waited:
                report["paced_s"] = round(waited, 2)
            report["rolls"] = [r.describe() for e in m.events[before:] for r in e.rolls]
            report["log"] = [e.text for e in m.events[before:] if e.text]
            # Its own field, not a log line: `log` carries what HAPPENED on the pitch, and a
            # note about how the coach is spending calls is not that.
            if action == "move" and report.get("ok"):
                hint = _step_hint(m, player, bool(path))
                if hint:
                    report["hint"] = hint
            return json.dumps(report)

    @tool
    def bb_game_routes(player: str, top: int = 12) -> str:
        """Where this player can get to, and the chance of ARRIVING ON THEIR FEET.

        Ask this instead of walking `bb_game_legal` around the board a square at a
        time. `bb_game_legal` answers for the eight squares next to a player; a run
        is three or four steps, and its risk is the PRODUCT of them. Three separate
        2+ rolls is not three safe rolls, it is 58% — and that multiplication is
        the single easiest thing to get wrong by eye, so the engine does it.

        `chance` counts every Dodge and every Rush the route needs, with the
        engine's own modifiers. `to_end_zone` is the safest route to the line you
        are attacking, and `to_ball` is the safest route to a loose ball WITH the
        pick-up rolled in — walking to a ball you then fumble is how a drive ends.
        `unknowns` names what the number deliberately leaves out.

        `top` caps the square list; the named destinations are always included.
        """
        from .engine.game import routes
        from .store import load_match

        m = load_match()
        if m is None:
            return json.dumps({"ok": False, "error": "no match in progress"})
        return json.dumps(routes(m, player, limit=max(0, int(top))))

    @tool
    def bb_game_odds(player: str, target: str) -> str:
        """What a Block would be before you throw it: how many dice, WHO chooses
        them, and the assists on each side.

        Ask this first. Blocking a player stronger than yours hands the choice of
        dice to them, which turns a Block into a way of knocking your own player
        over — and the arithmetic that decides it (assists, who is Marked by whom)
        is exactly the sort a description of the board gets subtly wrong.
        """
        from .engine import actions
        from .store import load_match

        m = load_match()
        if m is None:
            return json.dumps({"ok": False, "error": "no match in progress"})
        actions.load_all()
        legal = actions.get("block")["validate"](m, {"player": player, "target": target})
        return json.dumps({"ok": legal.ok, "reason": legal.reason, **legal.detail})

    @tool
    def bb_game_end_turn(state: _Injected = None) -> str:
        """End the active team's turn and hand over.

        In a head-to-head this is how you give the board back — end your turn when
        you are done, and it becomes your opponent's move.
        """
        from .engine import handover
        from .engine.game import end_turn
        from .store import load_match, save_match

        m = load_match()
        if m is None:
            return json.dumps({"ok": False, "error": "no match in progress"})
        was = handover.owed(m)
        out = end_turn(m, by=_seat_of(m, state))
        if out.get("ok"):
            save_match(m)
        announce(was, handover.owed(m))
        return json.dumps(out)

    @tool
    def bb_pass_ranges() -> str:
        """How far each pass band reaches, and the caveat that comes with it.

        The Range Ruler is a physical template: the rules define passing by laying
        it on the table and give no table of squares. These limits are MEASURED
        and cross-checked, not quoted from the rulebook — say so if a coach asks
        why a pass was a Long Bomb rather than a Long Pass.
        """
        from .engine.ruler import describe

        return json.dumps({"ok": True, **describe()})

    @tool
    def bb_game_choose(
        decline: bool = False,
        player: str = "",
        moves: list | None = None,
        players: list | None = None,
        result: int = 0,
        state: _Injected = None,
    ) -> str:
        """Answer whatever the engine stopped to ask.

        Four Kick-off Events say "the Coach selects…", and the engine will not
        choose for you — it pauses the Drive, says what it is waiting for in
        ``bb_game_state``'s ``waiting_on``, and refuses other actions until you
        answer. Nothing else can happen in between: the ball is still in the air
        and the first turn has not started.

          High Kick      ``player`` — one Open player, placed where the ball lands
          Quick Snap!    ``moves`` — [{"id","x","y"}], each Open player one square
          Solid Defence  ``moves`` — up to D3+3 Open players set up again
          Charge!        ``players`` — up to D3+3 Open players to send in
          Apothecary     ``result`` — 1 for the original Casualty Roll, 2 for the
                         Apothecary's second one

        ``decline=True`` is always a legal answer; every one of these says "may".
        For the Apothecary it means keeping the roll you already had — by then the
        Apothecary is spent and there is no doing-nothing left.

        A Charge is different after that: the selected players take their free
        Actions through ``bb_game_act`` like any other activation (a Move each,
        plus at most one Blitz, one Throw Team-mate and one Kick Team-mate for the
        whole Charge). Call this again with ``decline=True`` to end it early.
        """
        from .engine import handover
        from .engine.game import dice_for, resolve_choice
        from .store import load_match, save_match

        m = load_match()
        if m is None:
            return json.dumps({"ok": False, "error": "no match in progress"})
        was = handover.owed(m)
        out = resolve_choice(
            m,
            {
                "decline": decline,
                "player": player,
                "moves": moves or [],
                "players": players or [],
                "result": int(result or 0),
            },
            dice_for(m),
            by=_seat_of(m, state),
        )
        if out.get("ok"):
            save_match(m)
        announce(was, handover.owed(m))
        return json.dumps(out)

    @tool
    def bb_game_setup(side: str, players: list) -> str:
        """Set a team up for the coming Drive, and have it CHECKED.

        ``players`` is a list of ``{"id": "h03", "x": 7, "y": 13}``. Unlike the
        practice board, this is strict — a Match refuses an illegal formation with
        every reason at once, so you can fix them all in one go:

          · in your own half, not beyond the Line of Scrimmage
          · at least three in the Centre Field on the Line
          · no more than two in either Wide Zone
          · as many players as you can field, up to eleven

        The kicking team sets up first. Anyone left out goes to the Reserves Box.
        Skip it and the engine reuses the opening set-up, which is the documented
        simplification rather than the rule.
        """
        from .engine.game import declare_setup
        from .store import load_match, save_match

        m = load_match()
        if m is None:
            return json.dumps({"ok": False, "error": "no match in progress"})
        out = declare_setup(m, side, list(players or []))
        if out.get("ok"):
            save_match(m)
        return json.dumps(out)

    @tool
    def bb_game_apothecary(player: str) -> str:
        """Patch a Knocked-out or Casualtied player up. ONCE PER GAME per side.

        A KNOCKED-OUT player comes back Stunned in the square they were in — which
        is a real swing, because they are back on the pitch. One taken out by the
        crowd goes to the Reserves Box instead.

        A CASUALTY works differently and this tool does not finish the job: "the
        opposing Coach makes a second Casualty Roll … and the player's controlling
        Coach may select either of the two results to apply." So it rolls, then
        stops and asks — answer with ``bb_game_choose(result=1)`` for the original
        or ``result=2`` for the Apothecary's. Only a Badly Hurt result brings them
        back (to the Reserves Box); anything else and the Casualty stands.

        The Apothecary is spent the moment it is declared, win or lose. Only
        available if the match was started with ``apothecary=True``.
        """
        from .engine.game import dice_for, use_apothecary
        from .store import load_match, save_match

        m = load_match()
        if m is None:
            return json.dumps({"ok": False, "error": "no match in progress"})
        out = use_apothecary(m, player, dice_for(m))
        if out.get("ok"):
            save_match(m)
        return json.dumps(out)

    @tool
    def bb_game_extra_time(receiving: str = "home") -> str:
        """Play Extra Time after a drawn game — an extra eight turns each.

        Team Re-rolls are NOT replenished and whatever is left carries over, which
        is the difference between this and half-time. Only legal on a draw at full
        time; if Extra Time is also drawn, use bb_game_penalties.
        """
        from .engine.game import start_extra_time
        from .store import load_match, save_match

        m = load_match()
        if m is None:
            return json.dumps({"ok": False, "error": "no match in progress"})
        out = start_extra_time(m, receiving="away" if receiving == "away" else "home")
        if out.get("ok"):
            save_match(m)
        return json.dumps(out)

    @tool
    def bb_game_penalties() -> str:
        """Settle a still-drawn game with a Penalty Shoot-out: five roll-offs,
        ties re-rolled, most wins takes it. No other re-roll may be used."""
        from .engine.game import dice_for, penalty_shootout
        from .store import load_match, save_match

        m = load_match()
        if m is None:
            return json.dumps({"ok": False, "error": "no match in progress"})
        out = penalty_shootout(m, dice_for(m))
        save_match(m)
        return json.dumps(out)

    @tool
    def bb_get_skill(name: str) -> str:
        """What a Skill or Trait actually does — the rulebook's own words.

        USE THIS BEFORE SAYING WHAT ANY SKILL DOES. Not because the rules are
        hard, but because they are the sort of thing that feels obvious and
        isn't: Break Tackle sounds like a Strength-based alternative to dodging
        and is in fact a +1/+2/+3 modifier to the same Agility Test. Quote the
        ``text`` rather than paraphrasing it.

        The reply also says whether this ENGINE applies the skill. Both halves
        matter — "Break Tackle: <text> — but this engine does not apply it" is
        the true and useful sentence; either half alone misleads.
        """
        from .engine.skills import describe_skill

        found = describe_skill(name)
        if found is None:
            from .engine.skills import find_skills

            near = [s["name"] for s in find_skills(str(name or "")[:12])][:5]
            return json.dumps({"ok": False, "error": f"no Skill or Trait named {name!r}", "did_you_mean": near})
        return json.dumps({"ok": True, "skill": found})

    @tool
    def bb_list_skills(query: str = "", category: str = "", kind: str = "", only_unmodelled: bool = False) -> str:
        """Browse the 108 Skills and Traits.

        ``category`` is one of Agility, Devious, General, Mutation, Passing,
        Strength (or Trait); ``kind`` is Skill or Trait — a Trait is marked with
        an asterisk in the rulebook and is not normally learnable. ``query``
        matches the name or the text, so "re-roll" finds every skill that grants
        one. ``only_unmodelled`` narrows to what this engine does not apply.

        Returns names and summaries; call bb_get_skill for the full text.
        """
        from .engine.skills import find_skills

        rows = find_skills(query, category=category, kind=kind, only_unmodelled=only_unmodelled)
        return json.dumps(
            {
                "ok": True,
                "count": len(rows),
                "skills": [
                    {
                        "name": r["name"],
                        "kind": r["kind"],
                        "category": r["category"],
                        "when": r["when"],
                        "elite": r["elite"],
                        "modelled": r["modelled"],
                        "text": r["text"][:160] + ("…" if len(r["text"]) > 160 else ""),
                    }
                    for r in rows
                ],
            }
        )

    @tool
    def bb_game_kickoff(receiving: str = "") -> str:
        """Start the next drive: everyone back to their setup, a kick, the
        Kick-off Event, and the ball landing.

        Normally automatic — a Touchdown ends the drive and the conceding team
        receives — so this is for starting one by hand. The reply names the
        Kick-off Event and says whether the engine applied it or only reported it.
        """
        from .engine.game import start_drive
        from .store import load_match, save_match

        m = load_match()
        if m is None:
            return json.dumps({"ok": False, "error": "no match in progress"})
        side = receiving if receiving in ("home", "away") else m.opponent(m.clock.active)
        before = len(m.events)
        start_drive(m, receiving=side)
        save_match(m)
        return json.dumps(
            {
                "ok": True,
                "drive": m.drive,
                "receiving": side,
                "log": [e.text for e in m.events[before:] if e.text],
                "match": m.to_dict(include_log=False),
            }
        )

    @tool
    def bb_game_log(last: int = 20) -> str:
        """What has happened, most recent last, with the dice that decided it.

        This is the narration source. A line here already says "Dodge needed 3+,
        rolled 2 — FAILED"; report that, do not reconstruct it from the board.
        """
        from .store import load_match

        m = load_match()
        if m is None:
            return json.dumps({"ok": False, "error": "no match in progress"})
        from .engine.events import describe

        n = max(1, min(int(last or 20), 200))
        return json.dumps(
            {
                "ok": True,
                "clock": m.clock.to_dict(),
                "log": [
                    {"kind": e.kind, "text": describe(e), "rolls": [r.describe() for r in e.rolls]}
                    for e in m.events[-n:]
                ],
            }
        )

    @tool
    def bb_game_abandon() -> str:
        """Discard the match in progress. The practice board is unaffected.

        **A MATCH SOMEBODY IS PLAYING CANNOT BE DISCARDED FROM HERE — there is no
        override.** Abandoning a live game is the operator's call, from the board.

        This had an escape hatch (`confirm="discard"`) "for when an operator asks". It
        was documented in this very docstring, so the agent read it as an available
        option and used it the first time it wanted to — destroying a match in progress
        forty seconds later. An escape hatch written into the tool the agent holds is not
        a guard; the refusal message even named the magic word. The hatch is gone.

        **A question pending for the OTHER coach is not a broken game.** It is the
        handover working: the engine has stopped and is waiting on them, exactly as it
        stops and waits on you. Both abandonments so far were a seat deciding a normal
        position was broken. Say what you think is wrong and end your turn.
        """
        from .store import clear_match, load_match

        m = load_match()
        if m is not None and m.controllers:
            owed = ", ".join(f"{side}={who}" for side, who in sorted(m.controllers.items()))
            return json.dumps(
                {
                    "ok": False,
                    "error": (
                        f"this match is being played ({owed}) — it is not yours to discard, and "
                        "there is no override. If the position looks wrong, say so and end your "
                        "turn; an operator abandons it from the board. A Kick-off question "
                        "pending for the other coach is normal — the engine is waiting on them."
                    ),
                    "clock": m.clock.to_dict(),
                    "pending": dict(m.pending),
                }
            )
        return json.dumps({"ok": True, "discarded": clear_match()})

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
        bb_roster_options,
        bb_roster_save,
        bb_roster_list,
        bb_roster_get,
        bb_roster_import_fumbbl,
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
        bb_presets,
        bb_preset_load,
        bb_preset_save,
        bb_preset_delete,
        bb_game_new,
        bb_game_state,
        bb_game_legal,
        bb_game_act,
        bb_game_routes,
        bb_game_odds,
        bb_game_end_turn,
        bb_game_kickoff,
        bb_pass_ranges,
        bb_get_skill,
        bb_list_skills,
        bb_game_choose,
        bb_game_here,
        bb_game_nudge,
        bb_game_setup,
        bb_game_apothecary,
        bb_game_extra_time,
        bb_game_penalties,
        bb_game_log,
        bb_game_abandon,
    ]


def _rosters_teams() -> list[dict]:
    from .pitch import rosters

    return rosters()["teams"]
