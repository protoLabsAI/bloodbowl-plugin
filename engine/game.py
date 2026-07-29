"""One place where an action is turned into a change to a match.

Tools, HTTP routes and tests all come through here, so the sequence — validate,
resolve, apply, handle the turnover — exists once. When Block and Pass arrive they
plug into the same path rather than each re-deriving what a turnover does.
"""

from __future__ import annotations

from . import actions, charge
from .dice import SeededDice
from .events import Event
from .rerolls import DEFAULT_REROLLS
from .skills import NOTED, activation_gates, first_mentions, partly_modelled_on_pitch, unmodelled_on_pitch
from .state import Match, flesh_out, starting_positions
from .weather import from_roll as _weather_from_roll
from .weather import name_of as weather_name

TURNOVER_TEXT = {
    True: "Turnover — the team's turn ends.",
    False: "",
}


def _roll_off(dice) -> str:
    """A simple roll-off, ties re-rolled. Returns the RECEIVING side, because that
    is what the rest of the engine asks for."""
    while True:
        h, a = dice.d6(), dice.d6()
        if h != a:
            return "away" if h > a else "home"


def roll_weather(dice) -> str:
    """S3: "each Coach rolls a D6 and adds the two rolls together"."""
    return _weather_from_roll(dice.d6() + dice.d6())[0]


def new_match(
    scenario,
    seed: int = 0,
    kicking_to: str = "",
    rerolls: int | None = None,
    staff: dict | None = None,
    weather: str | None = None,
    apothecary: bool = False,
) -> Match:
    """Start a match from a set-up board.

    The seed is stored so the match can be regenerated; the log is what lets it be
    re-watched. Both, for the reasons in dice.py.

    ``kicking_to`` is the RECEIVING side — the team the ball is kicked to, which
    takes the first turn.
    """
    # A board drawn from a preset holds labelled TOKENS with no stats. Give them
    # a real player's numbers before anything reads a Move Allowance off them —
    # `movement()` returns 0 for an empty MA, so the alternative is eleven players
    # who cannot move and no explanation.
    tokens = flesh_out(scenario)
    m = starting_positions(scenario, seed=seed)
    # How many Team Re-rolls a team has is a DRAFTING decision, and a practice
    # board was never drafted — so it is an input with a stated default rather
    # than a number invented from the roster. See engine/rerolls.py.
    n = DEFAULT_REROLLS if rerolls is None else max(0, int(rerolls))
    # "each Coach rolls a D6 and adds the two rolls together" — rolled from the
    # match seed so it is reproducible, or forced outright for a drill.
    sky = SeededDice(seed=seed)
    condition = weather if weather else roll_weather(sky)
    # "this is done with a simple coin toss … The Coach who rolls highest decides
    # which team is kicking and which team is receiving." Rolled when nobody said.
    receiving = kicking_to if kicking_to in ("home", "away") else _roll_off(sky)
    # THE FANS, the first step of the Pre-game Sequence: "roll a D3 … add … your
    # Dedicated Fans Characteristic". It used to default to zero, which made Pitch
    # Invasion quietly milder than the rules intend — Fan Factor is added to a D6
    # there, and a zero is not a neutral default, it is a wrong one.
    from .pregame import DEFAULT_DEDICATED_FANS, fan_factor
    from .pregame import steps as pregame_steps

    fans = {}
    for side in ("home", "away"):
        given = (staff or {}).get(side) or {}
        if given.get("fan_factor"):
            fans[side] = int(given["fan_factor"])  # an operator may state it outright
            continue
        dedicated = int(given.get("dedicated_fans", DEFAULT_DEDICATED_FANS))
        fans[side], roll = fan_factor(sky, dedicated)
    m.apply(
        Event(
            kind="match_started",
            detail={
                "kicking_to": receiving,
                "seed": seed,
                "rerolls": {"home": n, "away": n},
                # Assistant Coaches, Cheerleaders, Fan Factor — the Kick-off Event
                # Table asks for all three, and a practice board hired none of
                # them. Zero unless told otherwise, and reported either way.
                "staff": {
                    side: {**dict((staff or {}).get(side) or {}), "fan_factor": fans[side]} for side in ("home", "away")
                },
                "pregame": pregame_steps(league=False),
                "weather": condition,
                "apothecary": {"home": bool(apothecary), "away": bool(apothecary)},
            },
            text=f"Match begins. {m.home_team or 'Home'} vs {m.away_team or 'Away'}. "
            f"{n} Team Re-roll(s) each. Weather: {weather_name(condition)}. "
            f"Fan Factor {fans['home']}-{fans['away']}."
            + (
                f" {len(tokens)} preset token(s) took the field as linemen: {'; '.join(tokens[:4])}"
                + ("…" if len(tokens) > 4 else "")
                + "."
                if tokens
                else ""
            ),
        )
    )
    start_drive(m, receiving=receiving, dice=dice_for(m))
    return m


class _gone:
    """Stands in for a player id that is no longer in the match, so a stale setup
    row cannot crash a kick-off."""

    place = "casualty"  # any of the never-come-back places will do


def start_drive(match: Match, receiving: str, dice=None, aim=None) -> list[Event]:
    """Set up, kick off, and hand the first turn to the receiving team.

    Everyone goes back to where they stood at the last kick-off rather than the
    operator being asked to rebuild the board after each score. Casualties stay
    out for the match; a Knocked-out player misses this drive and returns to
    Reserves for the next one, which is the shape of the real End of Drive
    sequence without pretending to model the parts (Apothecary, recovery rolls)
    that are not here.
    """
    from .kickoff import kick

    dice = dice or dice_for(match)
    # The setup is captured ONCE, at the first kick-off, and reused for every
    # later drive. Re-capturing from the current board would bake in wherever the
    # last drive happened to leave everyone, so a team that ended the drive strung
    # out across the pitch would line up that way for the next one.
    #
    # (S3 lets each team set up afresh every drive. Reusing the opening setup is
    # the honest simplification while there is no setup PHASE to do it in — the
    # operator can always rearrange the board and start a fresh match.)
    _deal_with_secret_weapons(match, dice)
    # A set-up DECLARED for this Drive wins over the captured opening one. The
    # captured one is the fallback documented below, not the rule.
    declared = [row for side in ("home", "away") for row in match.setups.get(side, ())]
    setup = (
        declared
        or match.setup
        or [{"id": p.id, "x": p.x, "y": p.y} for p in match.players if p.place in ("pitch", "reserves")]
    )
    gone = ("casualty", "sent_off")
    setup = [row for row in setup if (match.by_id(str(row["id"])) or _gone()).place not in gone]
    events = [
        Event(
            kind="drive_started",
            detail={"drive": match.drive + 1, "setup": setup, "receiving": receiving},
            text=f"Drive {match.drive + 1}: {receiving} receive.",
        )
    ]
    match.apply(events[0])
    events.extend(kick(match, dice, receiving=receiving))
    # A Kick-off Event that asked the Coach something is not finished, and the
    # ball is still in the air. The turn cannot start over an undecided kick-off —
    # `_finish_kickoff` runs this tail once the question is answered.
    if not match.pending:
        events.extend(_open_the_turn(match, receiving))
    return events


def _open_the_turn(match: Match, receiving: str) -> list[Event]:
    """Hand the first turn of the Drive to the receiving team."""
    ev = Event(
        kind="turn_started",
        detail={"side": receiving, "half": match.clock.half, "turn": match.clock.turn},
        text=f"Half {match.clock.half}, turn {match.clock.turn} — {receiving} to act.",
    )
    match.apply(ev)
    return [ev]


def _deal_with_secret_weapons(match: Match, dice) -> None:
    """S3, at the end of a Drive: "If a Coach fielded any players with the Secret
    Weapon Trait during the current Drive, then they will immediately be Sent-off
    AS IF THEY HAD COMMITTED A FOUL ACTION, EVEN IF THEY WERE NOT ON THE PITCH at
    the end of the Drive. Players Sent-off in this way may still Argue the Call."

    Reusing the Foul's own sending-off is the point of that phrasing — the
    Argue-the-Call roll, the ejected-Coach ban and all — so it calls the same
    helper rather than a second implementation that agrees until it doesn't.
    """
    from .actions.foul import _argue_the_call

    weapons = [p for p in match.players if p.place in ("pitch", "reserves") and p.has_skill("Secret Weapon")]
    if not weapons:
        return
    sink = _Sink(match)
    for p in weapons:
        sink.emit(
            Event(
                kind="player_sent_off",
                actor=p.id,
                detail={"reason": "secret weapon"},
                text=f"The referee confiscates {p.name()}'s Secret Weapon and sends them off.",
            )
        )
        _argue_the_call(match, p, dice, sink)


def resolve_choice(match: Match, answer: dict, dice) -> dict:
    """Answer whatever the engine stopped to ask.

    ``answer`` is ``{"decline": True}`` or the choice itself. Declining is always
    legal — every one of these events says the Coach "MAY" — and it is a real
    answer rather than a no-op, because the Drive cannot go on until one is given.
    """
    from ..pitch import in_bounds
    from .rules import is_open
    from .setup import violations

    if charge.active(match):
        # The Charge's own answer: stop early. "MAY then be activated" — a Coach
        # who has seen enough is not obliged to send the rest in.
        if answer.get("decline") or answer.get("end"):
            return {"ok": True, "log": [e.text for e in end_charge(match, "the Coach ends it", dice) if e.text]}
        return {
            "ok": False,
            "error": "the Charge is under way — activate the selected players with bb_game_act, or end it",
            "charge": dict(match.charge),
        }

    pending = match.pending
    if not pending:
        return {"ok": False, "error": "the engine is not waiting on anything"}
    kind, side = pending.get("choice"), pending.get("side")

    # Declining is "take no action" — which is a real answer to a Kick-off Event,
    # because every one of them says the Coach "may". It is NOT an option for the
    # Apothecary: by then the Apothecary is already spent and a second roll has
    # been made, so there is no doing-nothing left. Its branch reads a decline as
    # "keep the result you already had", which is the honest equivalent.
    if answer.get("decline") and kind != "apothecary":
        done = Event(
            kind="choice_made",
            detail={"choice": kind, "declined": True},
            text=f"{side} decline the {kind.replace('_', ' ')}.",
        )
        match.apply(done)
        after = _finish_kickoff(match, pending, dice)
        return {"ok": True, "declined": True, "log": [e.text for e in [done, *after] if e.text]}

    events: list = []
    if kind == "high_kick":
        # "ONE Open player … may immediately be placed in the square the ball is
        # going to land in."
        p = match.by_id(str(answer.get("player") or ""))
        if p is None or p.side != side or not is_open(match, p):
            return {"ok": False, "error": "name one Open player on the receiving team"}
        x, y = pending["square"]
        if match.at(x, y) is not None:
            return {"ok": False, "error": f"({x},{y}) is occupied"}
        events.append(
            Event(
                kind="player_pushed",
                actor=p.id,
                detail={"x": x, "y": y, "high_kick": True},
                text=f"{p.name()} sprints under the High Kick to ({x},{y}).",
            )
        )

    elif kind in ("quick_snap", "solid_defence"):
        rows = list(answer.get("moves") or [])
        limit = int(pending.get("limit", 0))
        if len(rows) > limit:
            return {"ok": False, "error": f"the roll allows up to {limit} players, and {len(rows)} were named"}

        # Both re-place the named players and leave everyone else where they
        # stand, so the destination checks run against a board with the movers
        # LIFTED OFF — otherwise a pair swapping squares refuses itself, and for
        # Solid Defence a player would collide with the square they just left.
        named = {str(r.get("id") or "") for r in rows}
        if len(named) != len(rows):
            return {"ok": False, "error": "the same player was named twice"}
        taken = {(q.x, q.y) for q in match.on_pitch() if q.id not in named}
        placed: list[tuple[int, int]] = []

        for row in rows:
            p = match.by_id(str(row.get("id") or ""))
            x, y = int(row.get("x", 0)), int(row.get("y", 0))
            if p is None or p.side != side or not is_open(match, p):
                return {"ok": False, "error": f"{row.get('id')!r} is not an Open player on {side}"}
            if (x, y) in taken or not in_bounds(x, y):
                return {"ok": False, "error": f"({x},{y}) is not a free square on the pitch"}
            # "may immediately move ONE SQUARE IN ANY DIRECTION, even if this takes
            # them into the opposition's half." No Dodge, no Rush, no Move
            # Allowance — the halfway line is the only thing the rule bothers to
            # permit, so nothing else restricts the square.
            if kind == "quick_snap" and max(abs(p.x - x), abs(p.y - y)) != 1:
                return {"ok": False, "error": f"{p.name()} may move exactly one square, not to ({x},{y})"}
            taken.add((x, y))
            placed.append((x, y))
            events.append(
                Event(
                    kind="player_pushed",
                    actor=p.id,
                    detail={"x": x, "y": y, kind: True},
                    text=(
                        f"{p.name()} steals a square to ({x},{y})."
                        if kind == "quick_snap"
                        else f"{p.name()} re-forms at ({x},{y})."
                    ),
                )
            )

        if kind == "solid_defence":
            # "can be set up again FOLLOWING ALL THE USUAL RESTRICTIONS FOR SETTING
            # UP THE TEAM" — so it is the whole resulting formation that must be
            # legal, not just the squares that moved. Three on the Line and two per
            # Wide Zone are properties of the team, and a re-placement can break
            # them from either end.
            squares = [(q.x, q.y) for q in match.on_pitch(side) if q.id not in named] + placed
            broken = violations(side, squares, len(squares))
            if broken:
                return {"ok": False, "error": "; ".join(broken), "violations": broken}
    elif kind == "apothecary":
        # "the player's controlling Coach MAY SELECT EITHER OF THE TWO RESULTS to
        # apply." Two rolls, one choice, and no third option — declining means
        # keeping the one they already had.
        offered = list(pending.get("results") or [])
        want = answer.get("result")
        pick = 0 if answer.get("decline") or want in (1, "1", "first") else 1 if want in (2, "2", "second") else None
        if pick is None:
            return {"ok": False, "error": "say result=1 for the original roll or result=2 for the Apothecary's"}
        chosen = offered[pick] if pick < len(offered) else {}
        p = match.by_id(str(pending.get("player") or ""))
        kept = str(chosen.get("result") or "")
        saved = kept == "Badly Hurt"
        events.append(
            Event(
                kind="apothecary_result",
                actor=p.id if p is not None else "",
                detail={"result": kept, "roll": chosen.get("roll"), "which": pick + 1},
                text=(
                    f"{side} take the {'Apothecary' if pick else 'original'} roll — {kept.upper()}. "
                    + (
                        f"{p.name() if p else 'The player'} is Patched-up and goes to the Reserves Box."
                        if saved
                        else "The Casualty stands."
                    )
                ),
            )
        )

    elif kind == "charge":
        # "selects up to D3+3 Open players … may THEN be activated one at a time."
        # The selection is the answer; the activations are ordinary actions after
        # it, so this opens the mode rather than resolving anything.
        picked = [str(pid) for pid in (answer.get("players") or answer.get("moves") or [])]
        limit = int(pending.get("limit", 0))
        if len(picked) > limit:
            return {"ok": False, "error": f"the roll allows up to {limit} players, and {len(picked)} were named"}
        if len(set(picked)) != len(picked):
            return {"ok": False, "error": "the same player was named twice"}
        for pid in picked:
            p = match.by_id(pid)
            if p is None or p.side != side or not is_open(match, p):
                return {"ok": False, "error": f"{pid!r} is not an Open player on {side}"}
        if not picked:
            return {"ok": False, "error": "name at least one player, or decline the Charge"}
        started = charge.start(match, side, picked, land=str(pending.get("land") or ""))
        match.apply(
            Event(
                kind="choice_made",
                detail={"choice": kind, "moved": len(picked)},
                text=f"{side} send in {len(picked)} player(s).",
            )
        )
        return {"ok": True, "charge": dict(match.charge), "log": [started.text]}

    else:
        return {"ok": False, "error": f"nothing known about a {kind!r} choice"}

    for ev in events:
        match.apply(ev)
    done = Event(
        kind="choice_made",
        detail={"choice": kind, "moved": len(events)},
        text=(
            f"{side} answer the {kind.replace('_', ' ')}."
            if kind == "apothecary"
            else f"{side} answer the {kind.replace('_', ' ')}: {len(events)} player(s) moved."
        ),
    )
    match.apply(done)
    after = _finish_kickoff(match, pending, dice)
    return {"ok": True, "moved": len(events), "log": [e.text for e in [*events, done, *after] if e.text]}


def _finish_kickoff(match: Match, pending: dict, dice) -> list[Event]:
    """Bring the ball down, now that the Kick-off Event is actually resolved.

    "At this point the ball is still HIGH UP IN THE AIR and cannot be caught UNTIL
    AFTER THE KICK-OFF EVENT HAS BEEN RESOLVED." A question the Coach has not
    answered is not resolved, so `kickoff.kick` stops before landing it and this
    finishes the job. Landing it first made High Kick meaningless — the player
    would be placed under a ball that had already come down and bounced away.
    """
    receiving = str(pending.get("land") or "")
    if not receiving:
        return []
    from .kickoff import land

    return [*land(match, dice, receiving), *_open_the_turn(match, receiving)]


def declare_setup(match: Match, side: str, squares: list[dict]) -> dict:
    """One team's Set-up for the coming Drive, checked against all four rules.

    Strict, unlike the practice board — "an illegal position is a legitimate thing
    to want while working a shape out" applies to `pitch.Scenario`, and a Match
    refuses with a reason. Every violation is returned at once rather than the
    first, because a coach fixing a formation wants the whole list.
    """
    from .setup import violations

    if side not in ("home", "away"):
        return {"ok": False, "error": f"unknown side {side!r}"}
    rows, seen = [], set()
    for row in squares or []:
        p = match.by_id(str(row.get("id") or ""))
        if p is None:
            return {"ok": False, "error": f"no player with id {row.get('id')!r}"}
        if p.side != side:
            return {"ok": False, "error": f"{p.name()} is {p.side}, not {side}"}
        if p.place in ("casualty", "sent_off"):
            return {"ok": False, "error": f"{p.name()} is {p.place.replace('_', ' ')} and takes no further part"}
        if p.id in seen:
            return {"ok": False, "error": f"{p.name()} is set up twice"}
        seen.add(p.id)
        rows.append({"id": p.id, "x": int(row["x"]), "y": int(row["y"])})

    available = len([p for p in match.players if p.side == side and p.place not in ("casualty", "sent_off")])
    problems = violations(side, [(r["x"], r["y"]) for r in rows], available)
    if problems:
        return {"ok": False, "error": "; ".join(problems), "violations": problems}

    match.apply(
        Event(
            kind="drive_setup",
            detail={"side": side, "squares": rows},
            text=f"{side} set up {len(rows)} players for the next Drive.",
        )
    )
    other = "away" if side == "home" else "home"
    return {"ok": True, "side": side, "players": len(rows), "waiting_on": other if other not in match.setups else ""}


def enforce_squad_size(match: Match) -> list:
    """TOO MANY PLAYERS, once the Turn has begun."""
    from .setup import too_many

    out = []
    for side in ("home", "away"):
        for p in too_many(match, side):
            ev = Event(
                kind="player_status",
                actor=p.id,
                detail={"reserves": True},
                text=f"{p.name()} is one player too many and is sent to the Reserves Box.",
            )
            match.apply(ev)
            out.append(ev)
    return out


def dice_for(match: Match):
    """A dice source positioned past the rolls already made.

    Re-seeding per action would hand every action the same numbers, so the stream
    is advanced by however many rolls the log already holds. Deterministic, and it
    survives the match being reloaded from disk between actions.
    """
    d = SeededDice(seed=match.seed)
    already = sum(len(e.rolls) for e in match.events)
    for _ in range(already):
        d.d6()
    return d


def act(match: Match, action: str, cmd: dict, dice=None) -> dict:
    """Resolve one action and fold its facts in. Returns a report for the caller."""
    actions.load_all()
    entry = actions.get(action)
    if entry is None:
        return {"ok": False, "error": f"unknown action {action!r}", "actions": actions.names()}
    if match.over:
        return {"ok": False, "error": "the match is over"}

    if match.pending:
        # A Kick-off Event that gives a Coach a choice is resolved BEFORE the ball
        # lands, so nothing else can happen in between. Refusing here — with the
        # question attached — beats choosing on their behalf.
        why = (
            f"the Kick-off Event is waiting on {match.pending['side']} — "
            f"answer the {str(match.pending['choice']).replace('_', ' ')} with bb_game_choose first"
        )
        return {"ok": False, "error": why, "text": why, "pending": dict(match.pending)}

    # CHARGE!: "activated one at a time, exactly as if it was their team's Turn."
    # Everything below is that machinery unchanged; this is the fence around it.
    if charge.active(match):
        no = charge.refuse(match, action, str(cmd.get("player") or ""))
        if no:
            return {"ok": False, "error": no, "text": no, "charge": dict(match.charge)}

    dice = dice or dice_for(match)

    # "Whenever this player is activated, AFTER DECLARING THEIR ACTION they must
    # roll a D6." Five Traits gate an activation this way, and they fire here
    # rather than inside an action because they are about the ACTIVATION, not
    # about what was declared — and because a gate that lived in `move` would not
    # fire on a Block.
    gated = _run_activation_gates(match, action, cmd, dice)
    if gated is not None:
        return gated

    # resolve applies its own events (see actions.Outcome) — do not re-apply.
    outcome = entry["resolve"](match, cmd, dice)
    # Announce any Skill this engine does not apply, BEFORE the turnover and
    # drive bookkeeping below, so the notice sits beside the action it belongs to
    # rather than after the next kick-off.
    noted = _note_unmodelled(match, outcome.unmodelled)

    # "…yet finishes their activation without having scored a Touchdown." Any
    # action that ends the activation is such a finish, not just Forego — which
    # already runs its own check before ending its own.
    if action != "forego" and not outcome.turnover:
        who = match.by_id(str(cmd.get("player") or ""))
        if who is not None and who.done:
            from .actions.forego import stalling_check

            sink = _Sink(match)
            if stalling_check(match, who, dice, sink):
                outcome.events.extend(sink.events)
                outcome.turnover = True
                outcome.ok = False
            else:
                outcome.events.extend(sink.events)

    # A Touchdown ends the DRIVE, not just the turn: "As soon as a Touchdown is
    # scored, play stops as a Turnover occurs — however, this is very much a
    # Turnover you can be pleased by! Scoring a Touchdown also marks the end of a
    # Drive." The conceder receives the next kick-off.
    scored = _unresolved_touchdown(match)
    if scored is not None:
        scorer = str(scored.detail.get("side") or match.clock.active)
        end_turn(match, forced=True, start_next=False)
        if not match.over:
            start_drive(match, receiving=match.opponent(scorer), dice=dice)
        return _report(match, outcome, noted, touchdown=scorer)

    if charge.active(match):
        # A Charge is not a Turn, so its failures are not Turnovers. "If a selected
        # player Falls Over or is Knocked Down … no further selected players can be
        # activated and THE CHARGE ENDS" — a much smaller thing than a Turnover,
        # which would advance the Turn Marker and hand over a ball that has not
        # even landed yet.
        outcome.events.extend(charge.note_action(match, action))
        why = charge.should_end(match, charge.fell_over(match, outcome.events))
        if why:
            outcome.events.extend(end_charge(match, why, dice))
        outcome.turnover = False
        return _report(match, outcome, noted)

    if outcome.turnover:
        match.apply(Event(kind="turnover", detail={"side": match.clock.active}, text=TURNOVER_TEXT[True]))
        end_turn(match, forced=True)

    return _report(match, outcome, noted)


def end_charge(match: Match, why: str, dice) -> list[Event]:
    """Close a Charge and finish the kick-off it interrupted.

    The ball is still in the air throughout — the Kick-off Event is not resolved
    until the Charge is over — so ending it lands the ball and opens the receiving
    team's turn, exactly as answering any other kick-off question does.
    """
    land_to = str(match.charge.get("land") or "")
    events = charge.end(match, why)
    return [*events, *_finish_kickoff(match, {"land": land_to}, dice)]


def _end_of_game(match: Match, dice) -> None:
    """Full time — and, if the scores are level, what the rules do about it.

    EXTRA TIME: "should a game end in a draw, a period of Extra Time will be
    played … an extra eight-Turn period … however, TEAM RE-ROLLS WILL NOT BE
    REPLENISHED like they would be at half-time. Any Team Re-rolls not spent at
    the end of the game may carry over."

    PENALTIES: "both Coaches will roll off against each other five times … rolling
    a D6 (RE-ROLLING ANY TIES, though no other re-rolls from any source can be
    used), with the Coach that wins the most roll-offs winning."

    Extra Time is not started automatically — it is "in instances where it is
    vital to have a definitive winner", which is a decision about the fixture
    rather than about the match. The engine says the game is drawn and that Extra
    Time is available; `bb_game_extra_time` starts it.
    """
    drawn = match.score.get("home", 0) == match.score.get("away", 0)
    match.apply(
        Event(
            kind="match_over",
            detail={"score": dict(match.score), "drawn": drawn},
            text="Full time. "
            + ("The scores are level — Extra Time is available." if drawn else "")
            + f" {match.score.get('home', 0)}-{match.score.get('away', 0)}.",
        )
    )


def use_apothecary(match: Match, player_id: str, dice) -> dict:
    """S3: "If a team has an Apothecary, then they can use them ONCE PER GAME in
    order to attempt to Patch-up a player on their team that has either been
    Knocked-out or suffered a Casualty. If an Apothecary is used to Patch-up a
    Knocked-out player then the player is NOT removed from the pitch … Instead,
    the player will become STUNNED IN THE SQUARE THEY ARE IN. If the player was
    Knocked-out as a result of an INJURY BY THE CROWD, they are placed in the
    Reserves Box instead."

    The KNOCKED-OUT branch is applied outright — there is nothing to decide.

    The CASUALTY branch is a choice, and now a real one: "After a Casualty Roll is
    made … their Coach MAY DECLARE THEY ARE USING THEIR APOTHECARY. The OPPOSING
    COACH makes a SECOND Casualty Roll for the player, and the player's controlling
    Coach MAY SELECT EITHER OF THE TWO RESULTS to apply. If a Badly Hurt result is
    selected, then the player is successfully Patched-up and placed into their
    Reserves Box instead of the Casualty Box."

    So it rolls, then stops and asks — the same `Match.pending` the Kick-off Events
    use. Choosing for them would be choosing whether a player comes back.
    """
    from .dice import Roll
    from .injury import CASUALTY_TABLE

    p = match.by_id(player_id)
    if p is None:
        return {"ok": False, "error": f"no player with id {player_id!r}"}
    if match.pending:
        return {"ok": False, "error": f"the engine is already waiting on a {match.pending.get('choice')}"}
    if not match.apothecary.get(p.side):
        return {"ok": False, "error": f"{p.side} have no Apothecary left — they are once per game"}
    if p.place not in ("knocked_out", "casualty"):
        return {"ok": False, "error": f"{p.name()} is {p.place.replace('_', ' ')}, and needs no patching up"}

    crowd = any(e.kind == "player_left_pitch" and e.actor == p.id for e in match.events)
    was = p.place

    if was == "casualty":
        # The Apothecary is spent the moment it is declared — win or lose, "once
        # per game" — so this event fires before the second roll, not after the
        # Coach likes the look of it.
        first = next(
            (e for e in reversed(match.events) if e.kind == "casualty_roll" and e.actor == p.id),
            None,
        )
        d = dice.dn(16)
        roll = Roll(kind="Casualty (Apothecary)", dice=[d], total=d, note="D16, rolled by the opposing Coach")
        dice.rolls.append(roll)
        name, effect = next((n, e) for cap, n, e in CASUALTY_TABLE if d <= cap)
        original = {
            "roll": int((first.detail or {}).get("roll") or 0),
            "result": str((first.detail or {}).get("result") or "unknown"),
        }
        offered = [original, {"roll": d, "result": name}]
        text = (
            f"The {p.side} Apothecary goes to work on {p.name()}. The opposing Coach rolls again: "
            f"{d} — {name.upper()} ({effect}). Either result may be applied — "
            f"the first was {original['roll']} ({original['result'].upper()})."
        )
        match.apply(
            Event(
                kind="apothecary_declared",
                actor=p.id,
                rolls=[roll],
                detail={"side": p.side, "results": offered},
                text=text,
            )
        )
        match.apply(
            Event(
                kind="choice_pending",
                detail={
                    "choice": "apothecary",
                    "side": p.side,
                    "player": p.id,
                    "results": offered,
                    "text": text,
                },
                text=text + " Answer with bb_game_choose(result=1 or 2).",
            )
        )
        return {"ok": True, "pending": dict(match.pending), "results": offered, "log": [text]}

    match.apply(
        Event(
            kind="apothecary_used",
            actor=p.id,
            detail={"side": p.side, "was": was, "crowd": crowd},
            text=f"The {p.side} Apothecary patches {p.name()} up"
            + (" — back in the Reserves Box." if crowd else " — Stunned, but still on the pitch."),
        )
    )
    return {"ok": True, "player": p.to_dict()}


def penalty_shootout(match: Match, dice) -> dict:
    """ "both Coaches will roll off against each other five times … re-rolling any
    ties, though no other re-rolls from any source can be used"."""
    from .dice import Roll

    wins = {"home": 0, "away": 0}
    events = []
    for n in range(1, 6):
        while True:
            h, a = dice.d6(), dice.d6()
            if h != a:
                break
        winner = "home" if h > a else "away"
        wins[winner] += 1
        roll = Roll(kind="Penalty", dice=[h, a], note=f"kick {n}: home {h}, away {a}")
        dice.rolls.append(roll)
        ev = Event(
            kind="note",
            rolls=[roll],
            detail={"kick": n, "winner": winner},
            text=f"Penalty {n}: home {h}, away {a} — {winner} score.",
        )
        match.apply(ev)
        events.append(ev)
    champion = "home" if wins["home"] > wins["away"] else "away"
    ev = Event(
        kind="match_over",
        detail={"penalties": wins, "winner": champion},
        text=f"Penalty Shoot-out: {wins['home']}-{wins['away']} — {champion} win the game.",
    )
    match.apply(ev)
    events.append(ev)
    return {"ok": True, "winner": champion, "wins": wins, "log": [e.text for e in events]}


def start_extra_time(match: Match, receiving: str = "home") -> dict:
    """ "an extra eight-Turn period … Team Re-rolls will NOT be replenished."""
    if not match.over:
        return {"ok": False, "error": "the game is not over yet"}
    if match.score.get("home", 0) != match.score.get("away", 0):
        return {"ok": False, "error": "Extra Time is for a draw; this game has a winner"}
    match.apply(
        Event(
            kind="extra_time",
            detail={"receiving": receiving},
            text="Extra Time: an extra eight turns each. Team Re-rolls are NOT replenished.",
        )
    )
    start_drive(match, receiving=receiving)
    return {"ok": True, "clock": match.clock.to_dict()}


def _run_activation_gates(match: Match, action: str, cmd: dict, dice) -> dict | None:
    """Roll any activation gate the acting player carries. Returns a report if the
    activation is over before it began, or None to carry on.

    An activation BEGINS with the player's first action of the turn, so the gate is
    rolled when `acted` is still false. Clearing Distracted here is the rule, not
    tidiness: "they will remain Distracted UNTIL THEY ARE NEXT ACTIVATED" — a new
    turn does not clear it, and this is where being next activated happens.
    """
    from .events import Event
    from .injury import knock_down
    from .rules import adjacent, has_tackle_zone

    p = match.by_id(str(cmd.get("player") or ""))
    if p is None or p.acted or p.side != match.clock.active:
        return None
    gates = activation_gates(match, p, action, target=match.by_id(str(cmd.get("target") or "")))
    if p.distracted:
        match.apply(
            Event(
                kind="player_status",
                actor=p.id,
                detail={"distracted": False},
                text=f"{p.name()} shakes it off and is no longer Distracted.",
            )
        )
    if not gates:
        return None

    from .dice import roll_target

    events: list = []
    for gate in gates:
        if gate.get("skill_skipped") or gate.get("skip"):
            continue
        r = roll_target(dice, gate["skill"], gate["target"], gate.get("modifier", 0), note=" ".join(gate["notes"]))
        ev = Event(kind="note", actor=p.id, rolls=[r], text=f"{gate['skill']}: {r.describe()}")
        match.apply(ev)
        events.append(ev)
        if r.passed:
            continue

        # BLOODLUST's bite, before the failure lands: "at the end of their
        # activation, this player MAY BITE AN ADJACENT THRALL LINEMAN team-mate
        # REGARDLESS OF THE STATUS of the Thrall Lineman … treating any Casualty
        # result as BADLY HURT; this will not cause a Turnover UNLESS the Thrall
        # Lineman was holding the ball." A bitten Vampire carries on as normal.
        if gate["skill"] == "Bloodlust" and _bite_a_thrall(match, p, dice, events):
            continue

        fail = gate["on_fail"]
        if fail in ("distracted", "lash_out"):
            mate = None
            if fail == "lash_out":
                mate = next(
                    (
                        q
                        for q in match.on_pitch(p.side)
                        if q.id != p.id and has_tackle_zone(q) and adjacent(q.x, q.y, p.x, p.y)
                    ),
                    None,
                )
            if mate is not None:
                # "Choose one Standing team-mate adjacent to this player; the
                # chosen player is immediately Knocked Down. This will NOT cause a
                # Turnover unless the player was holding the ball."
                held = match.ball.carrier == mate.id
                ev = Event(
                    kind="note",
                    actor=p.id,
                    text=f"{p.name()} lashes out at {mate.name()}.",
                )
                match.apply(ev)
                events.append(ev)
                events.extend(knock_down(match, mate, dice, by=p, cause="lashed out at"))
                return _gate_report(match, p, events, turnover=held, text=f"{p.name()} lashed out at {mate.name()}.")
            ev = Event(
                kind="player_status",
                actor=p.id,
                detail={"distracted": True},
                text=f"{p.name()} is Distracted — no Tackle Zone, no Active Skills, and their activation ends.",
            )
        elif fail == "rooted":
            ev = Event(
                kind="player_status",
                actor=p.id,
                detail={"rooted": True},
                text=f"{p.name()} takes root — they cannot Move, Follow-up or be Pushed Back until the "
                "Drive ends or they hit the ground.",
            )
        else:  # end_activation
            ev = Event(
                kind="note",
                actor=p.id,
                text=f"{p.name()} rages incoherently and nothing really happens. Their activation ends.",
            )
        match.apply(ev)
        events.append(ev)
        return _gate_report(match, p, events, turnover=False, text=ev.text)
    return None


def _bite_a_thrall(match: Match, p, dice, events: list) -> bool:
    """Feed a failed Bloodlust roll. Returns True if the Vampire fed and may carry
    on. See the quote in `_run_activation_gates`.

    "REGARDLESS OF THE STATUS of the Thrall Lineman" — Prone, Stunned, it does not
    matter, which is the grisly point. Thrall Lineman is a KEYWORD, and the Vampire
    roster prints it on exactly one positional.
    """
    from .events import Event
    from .injury import injury_roll
    from .rules import adjacent, keywords

    thrall = next(
        (
            q
            for q in match.on_pitch(p.side)
            if q.id != p.id and "thrall" in keywords(q) and adjacent(q.x, q.y, p.x, p.y)
        ),
        None,
    )
    if thrall is None:
        return False
    events.append(
        Event(
            kind="note",
            actor=p.id,
            detail={"skill": "Bloodlust", "bit": thrall.id},
            text=f"{p.name()} sinks their teeth into {thrall.name()} and carries on.",
        )
    )
    match.apply(events[-1])
    # "treating any Casualty result as Badly Hurt" — which the Injury Roll's own
    # Casualty branch would not do, so the roll is made and the branch capped.
    events.extend(injury_roll(match, thrall, dice, cap_casualty=True))
    return True


def _pick_me_up(match: Match, dice) -> None:
    """The Trait that hauls team-mates up between turns. See the quote in end_turn.

    "Should a player with this Trait STAND UP AS A RESULT OF A TEAM-MATE using this
    Trait, they may not also use this Trait during the same Turn" — so the helpers
    are fixed before anybody is helped, rather than a player standing up and then
    immediately helping the next one.
    """
    from .dice import Roll

    side = match.opponent(match.clock.active)
    helpers = [p for p in match.on_pitch(side) if p.down == "standing" and p.has_skill("Pick-me-up")]
    if not helpers:
        return
    lifted = [h.id for h in helpers]
    for p in match.on_pitch(side):
        if p.down != "prone" or p.id in lifted:
            continue
        if not any(max(abs(h.x - p.x), abs(h.y - p.y)) <= 3 for h in helpers):
            continue
        d = dice.d6()
        roll = Roll(kind="Pick-me-up", dice=[d], total=d, target=5, passed=d >= 5)
        dice.rolls.append(roll)
        match.apply(
            Event(
                kind="player_stood_up" if roll.passed else "note",
                actor=p.id,
                rolls=[roll],
                detail={"skill": "Pick-me-up", "ma_used": 0} if roll.passed else {"skill": "Pick-me-up"},
                text=f"{p.name()} is hauled to their feet between turns. {roll.describe()}"
                if roll.passed
                else f"{p.name()} stays down. {roll.describe()}",
            )
        )


def _gate_report(match: Match, p, events: list, turnover: bool, text: str) -> dict:
    """A failed gate ends the activation before the declared Action happens."""
    from .actions import ended
    from .events import Event

    end = ended(p.id, "gate")
    match.apply(end)
    events.append(end)
    if turnover:
        match.apply(Event(kind="turnover", detail={"side": match.clock.active}, text=TURNOVER_TEXT[True]))
        end_turn(match, forced=True)
    return {
        "ok": False,
        "turnover": turnover,
        "text": text,
        "events": [e.to_dict() for e in events],
        "unmodelled_skills": [],
        "clock": match.clock.to_dict(),
        "over": match.over,
    }


class _Sink:
    """Recorder-shaped, for helpers called from here rather than from an action."""

    def __init__(self, match):
        self.match, self.events = match, []

    def emit(self, event):
        self.match.apply(event)
        self.events.append(event)
        return event

    def absorb(self, events):
        self.events.extend(events)


def _report(match: Match, outcome, noted: list[str], touchdown: str | None = None) -> dict:
    """One shape for every reply, so a field cannot exist on the ordinary path and
    quietly go missing on the one that scored."""
    report = outcome.to_dict()
    report["unmodelled_skills"] = noted
    report["clock"] = match.clock.to_dict()
    report["over"] = match.over
    if touchdown is not None:
        report["touchdown"] = touchdown
    return report


def _note_unmodelled(match: Match, skills) -> list[str]:
    """Record, once per match, each Skill the engine did not apply.

    The Outcome carries every unmodelled Skill the participants hold; this narrows
    that to the ones this match has not already mentioned and writes them into the
    log. Read ``skills.unmodelled_on_pitch`` for the standing summary — that is
    the one that is always complete.
    """
    fresh = first_mentions(match, skills)
    if fresh:
        match.apply(
            Event(
                kind=NOTED,
                detail={"skills": fresh},
                text="Not modelled by this engine, so not applied: " + ", ".join(fresh) + ".",
            )
        )
    return fresh


def _unresolved_touchdown(match: Match):
    """A Touchdown with no Drive started after it.

    Derived from the log rather than remembered in a side table keyed on the
    match object: a match is reloaded from disk between tool calls, so object
    identity does not survive, and the log is the only thing that does.
    """
    for e in reversed(match.events):
        if e.kind == "drive_started":
            return None
        if e.kind == "touchdown":
            return e
    return None


def end_turn(match: Match, forced: bool = False, start_next: bool = True, dice=None) -> dict:
    """End the active team's turn.

    Stunned players recover to Prone at the end of a turn — modelled here rather
    than in the clock so the recovery is a recorded fact like everything else.
    """
    for p in match.players:
        if p.down == "stunned" and p.side == match.clock.active:
            match.apply(
                Event(
                    kind="player_placed_prone",
                    actor=p.id,
                    detail={"down": "prone"},
                    text=f"{p.name()} recovers from Stunned to Prone.",
                )
            )
    # PICK-ME-UP: "At the end of each of the OPPOSITION'S Turns, roll a D6 for each
    # PRONE TEAM-MATE WITHIN 3 SQUARES of one or more STANDING players with this
    # Trait. On a 5+, the Prone player may immediately STAND UP."
    #
    # The opposition's turn, so it fires for the side that is NOT active — standing
    # up for free and out of turn, which is why it is worth a Trait at all.
    _pick_me_up(match, dice or dice_for(match))

    was = match.clock.active
    before = match.clock.half
    match.apply(
        Event(
            kind="turn_ended",
            detail={"side": was, "forced": forced},
            text=("Turnover ends " if forced else "") + f"{was}'s turn.",
        )
    )
    if match.clock.half != before and not match.over:
        # Say so in the log, and put the Team Re-rolls back: a coach who spent all
        # three in the first half needs to know they have them again.
        match.apply(
            Event(
                kind="half_time",
                detail={"half": match.clock.half, "rerolls": dict(match.rerolls_max)},
                text="Half time. Team Re-rolls are replenished — "
                + ", ".join(f"{side} {n}" for side, n in sorted(match.rerolls_max.items())),
            )
        )
    if match.over or not start_next:
        if match.over:
            _end_of_game(match, dice_for(match))
        return {"ok": True, "clock": match.clock.to_dict(), "over": match.over}
    if not match.over:
        match.apply(
            Event(
                kind="turn_started",
                detail={
                    "side": match.clock.active,
                    "half": match.clock.half,
                    "turn": match.clock.turn,
                },
                text=f"Half {match.clock.half}, turn {match.clock.turn} — {match.clock.active} to act.",
            )
        )
    return {"ok": True, "clock": match.clock.to_dict(), "over": match.over}


def state_report(match: Match) -> dict:
    """The match as a caller should see it: the board, plus what the engine is
    knowingly not applying to it.

    The summary rides with the state rather than being a separate tool because
    the honest version of "here is the position" includes the ways the position is
    a simplification. A coach reading only the board would have to know to ask.
    """
    out = {
        "match": match.to_dict(include_log=False),
        "unmodelled_skills": unmodelled_on_pitch(match),
        # Both lists, always. A Skill applied in part reads as fully applied
        # unless something says otherwise, and that is the more dangerous of the
        # two gaps because it sounds settled.
        "partly_modelled_skills": partly_modelled_on_pitch(match),
    }
    if match.pending:
        # Top level, not buried in `match`: while a Kick-off Event is unanswered
        # NOTHING else can happen, so it is the first thing about the position and
        # not a detail of it.
        out["waiting_on"] = dict(match.pending)
    if match.charge:
        # Same reasoning: during a Charge only the selected players may act, and
        # which of the three one-off Actions are left is not deducible from the
        # board.
        out["charge"] = dict(match.charge)
    return out


def legal_moves(match: Match, player_id: str) -> dict:
    """Every square this player could step to, and what each would cost.

    The anti-confabulation tool. A coach asking this gets the engine's own answer
    for all eight neighbours — which need a Dodge, at what modifier, which need a
    Rush — instead of working it out from a board description and being confidently
    wrong about one of them.
    """
    actions.load_all()
    p = match.by_id(player_id)
    if p is None:
        return {"ok": False, "error": f"no player with id {player_id!r}"}
    validate = actions.get("move")["validate"]
    squares = []
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            if dx == 0 and dy == 0:
                continue
            x, y = p.x + dx, p.y + dy
            legal = validate(match, {"player": player_id, "x": x, "y": y})
            entry = {"x": x, "y": y, "legal": legal.ok}
            if legal.ok:
                entry.update(legal.detail)
            else:
                entry["reason"] = legal.reason
            squares.append(entry)
    # Blocks the same player could throw, with the arithmetic already done. A
    # coach eyeballing "that looks like a good block" is exactly how you end up
    # handing two dice to a stronger opponent.
    blocks = []
    validate_block = actions.get("block")["validate"]
    for foe in match.on_pitch(match.opponent(p.side)):
        legal = validate_block(match, {"player": player_id, "target": foe.id})
        if not legal.ok:
            continue
        blocks.append(
            {
                "target": foe.id,
                "x": foe.x,
                "y": foe.y,
                "position": foe.player.position,
                # `name` rather than `position` alone: a board built from a preset
                # holds labelled TOKENS with no positional, and the panel was
                # printing raw ids like "h07" at them. PlayerState.name() is where
                # that fallback already lives — the view must not re-derive it.
                "name": foe.name(),
                **legal.detail,
            }
        )

    # Blitz targets, with the distance already walked. A coach eyeballing "I can
    # get there" is how a team's ONE Blitz per turn gets spent on an opponent two
    # squares out of reach — and unlike a bad Block, that one cannot be taken back.
    blitz = {"available": False, "targets": []}
    declare = actions.get("blitz")
    if declare is not None:
        if match.blitz:
            blitz["declared"] = dict(match.blitz)
        for foe in match.on_pitch(match.opponent(p.side)):
            legal = declare["validate"](match, {"player": player_id, "target": foe.id})
            if not legal.ok:
                continue
            blitz["available"] = True
            blitz["targets"].append(
                {
                    "target": foe.id,
                    "x": foe.x,
                    "y": foe.y,
                    "position": foe.player.position,
                    "name": foe.name(),
                    **legal.detail,
                }
            )

    # Fouls: adjacent opponents already on the floor. Offered separately from
    # blocks because they are the exact complement — a Block needs a Standing
    # target, a Foul needs one that is not — and because the interesting number is
    # not the odds of hurting them but the odds of being caught, which the detail
    # spells out rather than leaving to be recalled.
    fouls = []
    kick = actions.get("foul")
    if kick is not None:
        for foe in match.on_pitch(match.opponent(p.side)):
            legal = kick["validate"](match, {"player": player_id, "target": foe.id})
            if legal.ok:
                fouls.append(
                    {
                        "target": foe.id,
                        "x": foe.x,
                        "y": foe.y,
                        "position": foe.player.position,
                        "name": foe.name(),
                        **legal.detail,
                    }
                )

    blitz["targets"].sort(key=lambda t: (t["steps"], not t["can_block"]))

    # Ball actions this player could take right now, so the view and the coach
    # both learn about Secure the Ball from the engine rather than being expected
    # to remember that S3 added it.
    ball_actions = []
    # Pass targets, with the band and modifier already worked out. A coach
    # eyeballing "that looks catchable" is how a Long Bomb gets thrown by
    # accident — the ruler is measured, not intuited.
    throw = actions.get("pass")
    if throw is not None and match.ball.carrier == player_id:
        for tx in range(1, 16):
            for ty in range(1, 27):
                legal = throw["validate"](match, {"player": player_id, "x": tx, "y": ty})
                if legal.ok:
                    ball_actions.append({"action": "pass", "x": tx, "y": ty, **legal.detail})
    for name in ("secure", "handoff"):
        entry = actions.get(name)
        if entry is None:
            continue
        if name == "handoff":
            for mate in match.on_pitch(p.side):
                legal = entry["validate"](match, {"player": player_id, "target": mate.id})
                if legal.ok:
                    ball_actions.append({"action": name, "target": mate.id, "x": mate.x, "y": mate.y, **legal.detail})
        else:
            legal = entry["validate"](match, {"player": player_id})
            if legal.ok:
                ball_actions.append({"action": name, "x": match.ball.x, "y": match.ball.y, **legal.detail})

    from .rerolls import _loner_target, available

    return {
        "ok": True,
        "player": p.to_dict(),
        "movement_left": max(0, p.movement() - p.ma_used),
        # Free to ask, so the coach can see the cost before committing. `loner`
        # is the D6 they would first have to pass to spend one.
        "team_rerolls": {"left": available(match, p), "loner": _loner_target(p)},
        "squares": squares,
        "blocks": blocks,
        "blitz": blitz,
        "fouls": fouls,
        "ball": match.ball.to_dict(),
        "ball_actions": ball_actions,
    }
