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
        # The Range Ruler is the one measured (not quoted) thing in the engine, so
        # it is configurable — see engine/ruler.py.
        from .engine.ruler import configure as _configure_ruler

        _configure_ruler(cfg)
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
        data.include_router(build_game_router(cfg))
        registry.register_router(data, prefix="/api/plugins/bloodbowl")
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
    def bb_game_new(
        seed: int = 0,
        kicking_to: str = "",
        rerolls: int = -1,
        assistant_coaches: int = 0,
        cheerleaders: int = 0,
        fan_factor: int = 0,
        weather: str = "",
        apothecary: bool = False,
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
        for a free Offensive Assist, and Pitch Invasion adds ``fan_factor``. Both
        sides get whatever you pass.

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
                    "fan_factor": int(fan_factor),
                }
                for side in ("home", "away")
            },
            weather=weather or None,
            apothecary=bool(apothecary),
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
        choice: int = 0,
        follow_up: bool = True,
        team_reroll: bool = False,
        drop_ball: bool = False,
        second_target: str = "",
    ) -> str:
        """Take an action.

        ``action`` is one of:
          "move"    — with ``x``/``y``; picks the ball up automatically if it is
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

        For a Block, ``choice`` picks which of the rolled dice to apply — but only
        when YOU are the one entitled to choose, which is when your player is the
        stronger. Ask bb_game_odds first: it tells you how many dice you get and
        who picks them. ``follow_up`` moves into the vacated square after a push.

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
        from .engine.game import act
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
            cmd.update({"target": target, "choice": int(choice), "follow_up": bool(follow_up)})
            if second_target:
                cmd["second_target"] = second_target
        elif action in ("handoff", "blitz", "foul", "throwteam"):
            cmd["target"] = target
        before = len(m.events)
        report = act(m, action, cmd)
        save_match(m)
        report["rolls"] = [r.describe() for e in m.events[before:] for r in e.rolls]
        report["log"] = [e.text for e in m.events[before:] if e.text]
        return json.dumps(report)

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
    def bb_game_end_turn() -> str:
        """End the active team's turn and hand over."""
        from .engine.game import end_turn
        from .store import load_match, save_match

        m = load_match()
        if m is None:
            return json.dumps({"ok": False, "error": "no match in progress"})
        out = end_turn(m)
        save_match(m)
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
        from .engine.game import dice_for, resolve_choice
        from .store import load_match, save_match

        m = load_match()
        if m is None:
            return json.dumps({"ok": False, "error": "no match in progress"})
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
        )
        if out.get("ok"):
            save_match(m)
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
        """Discard the match in progress. The practice board is unaffected."""
        from .store import clear_match

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
        bb_game_odds,
        bb_game_end_turn,
        bb_game_kickoff,
        bb_pass_ranges,
        bb_get_skill,
        bb_list_skills,
        bb_game_choose,
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
