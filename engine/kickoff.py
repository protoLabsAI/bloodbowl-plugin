"""The kick-off, the Kick-off Event, and the drive it starts.

S3's Drive sequence, in order: SET-UP (kicking team first), THE KICK-OFF, then THE
KICK-OFF EVENT while the ball is in the air. "At this point the ball is still high
up in the air and cannot be caught until after the Kick-off Event has been
resolved" — so the event is rolled BEFORE the ball lands, and can change where it
lands.

The kick DEVIATES: "Immediately after the kick has Deviated, the Coach of the
kicking team must roll 2D6 and consult the Kick-off Event Table." Deviate is a D6
for distance and a D8 for direction.

TOUCHBACK: "The ball must land safely in the opposition half... If the ball ends up
exiting the pitch or crosses the Line of Scrimmage into the kicking team's half,
regardless if this was down to a Deviation, Bounce, or another effect, then it
will result in a Touchback." The receiving coach then gives it to any Standing
player, or places it on any unoccupied square in their half.

ON THE EVENT TABLE. All eleven results are listed with their real text, because a
coach asking "what happened" deserves the actual rule. Only the ones this engine
can honestly carry out are APPLIED — the rest depend on things it does not model
(Cheerleaders, Assistant Coaches, Fan Factor, Team Re-rolls, Weather, Inducements)
or on a choice no one is here to make. Those are reported as rolled-but-not-applied
rather than silently skipped, which is the same discipline as unmodelled Skills:
the alternative is a coach believing a Blitz happened when nothing moved.
"""

from __future__ import annotations

from ..pitch import LENGTH, LOS_ROWS, in_bounds
from .ball import DIRECTIONS, bounce, catch, scatter
from .dice import Roll, roll_2d6
from .events import Event
from .weather import from_roll as weather_from_roll

# 2D6 -> (name, rule text, applied-by-this-engine?)
KICKOFF_EVENTS: dict[int, tuple[str, str, bool]] = {
    2: (
        "Get the Ref",
        "Each team immediately receives one free Bribe Inducement. This Bribe must be used by "
        "the end of the game or it is lost.",
        False,  # Inducements are not modelled.
    ),
    3: (
        "Time-out!",
        "If the kicking team's Turn Marker is on turn 6, 7 or 8 for the half, move both teams' "
        "Turn Marker back one space. Otherwise, move both teams' Turn Marker forwards one space.",
        True,  # Pure clock — the engine owns this outright.
    ),
    4: (
        "Solid Defence",
        "The Coach of the kicking team selects up to D3+3 Open players on their team. The selected "
        "players are then removed from the pitch and can be set up again following all the usual "
        "restrictions for setting up the team.",
        True,  # Asked of the coach; see _ask.
    ),
    5: (
        "High Kick",
        "One Open player on the receiving team may immediately be placed in the square the ball is going to land in.",
        True,  # Asked of the coach; see _ask.
    ),
    6: (
        "Cheering Fans",
        "Both Coaches roll a D6 and add the number of Cheerleaders on their Team Roster. The first "
        "Block Action performed during the Coach with the highest roll's next Turn receives an "
        "additional Offensive Assist.",
        True,  # Cheerleaders are an input, like the Team Re-roll count.
    ),
    7: (
        "Brilliant Coaching",
        "Both Coaches roll a D6 and add the number of Assistant Coaches on their Team Roster. The "
        "Coach with the highest total gains a free Team Re-roll for the Drive ahead.",
        True,  # Team Re-rolls are modelled now; Assistant Coaches are an input.
    ),
    8: (
        "Changing Weather",
        "Immediately make a new roll on the Weather Table. If the new result is Perfect Conditions, "
        "the ball will Scatter (3) in the air before it lands.",
        True,  # Weather is modelled now.
    ),
    9: (
        "Quick Snap!",
        "The Coach of the receiving team selects up to D3+3 Open players on their team. The "
        "selected players may immediately move one square in any direction, even if this takes "
        "them into the opposition's half.",
        True,  # Asked of the coach; see _ask.
    ),
    10: (
        "Charge!",
        "The Coach of the kicking team selects up to D3+3 Open players on their team. The selected "
        "players may then be activated one at a time, exactly as if it was their team's Turn, and "
        "perform a free Move Action. One of the selected players may instead perform a free Blitz "
        "Action, one may perform a free Throw Team-mate Action, and one may perform a free Kick "
        "Team-mate Action. If a selected player Falls Over or is Knocked Down during their "
        "activation, no further selected players can be activated and the Charge ends.",
        True,  # A free turn, driven by the coach; see engine/charge.py.
    ),
    11: (
        "Dodgy Snack",
        "Both Coaches roll a D6. The Coach that rolled the lowest, or BOTH Coaches on a tie, "
        "randomly selects one of their players on the pitch and rolls a D6. On a 2+ the player "
        "reduces their MA and AV by 1 for the Drive. On a 1, place the player in the Reserves box.",
        True,  # Dice.dn gives the unbiased pick this needed.
    ),
    12: (
        "Pitch Invasion",
        "Both Coaches roll a D6 and add their Fan Factor. The Coach that rolled lowest, or both on "
        "a tie, randomly selects D3 of their players on the pitch. The selected players are "
        "immediately Placed Prone and become Stunned.",
        True,  # Fan Factor is an input, like the other roster numbers.
    ),
}


def receiving_half(side: str) -> tuple[int, int]:
    """The (min, max) rows of a side's own half, inclusive."""
    return (1, LOS_ROWS[0]) if side == "home" else (LOS_ROWS[1], LENGTH)


def in_own_half(side: str, y: int) -> bool:
    lo, hi = receiving_half(side)
    return lo <= y <= hi


def deviate(match, dice, x: int, y: int, kicker=None) -> tuple[tuple[int, int], list[Roll]]:
    """D6 squares in a D8 direction, as the kick does.

    KICK: "If this player is nominated as the kicking player, then when kicking
    Deviates this player's Coach may choose for it to only Deviate D3 SQUARES
    rather than the usual D6." Halving the scatter is the whole Skill — a kick that
    lands where you meant it to is worth more than any modifier on the ball. Always
    taken: a shorter deviation is never worse for the kicking team, because the
    aim point is already the square they chose.
    """
    from .skills import can_use

    booted = kicker is not None and can_use(kicker, "Kick")
    dist = dice.dn(3) if booted else dice.d6()
    direction = dice.d8()
    rolls = [
        Roll(kind="Deviate distance", dice=[dist], note="D3 (Kick)" if booted else "D6"),
        Roll(kind="Deviate direction", dice=[direction], note="D8"),
    ]
    dice.rolls.extend(rolls)
    dx, dy = DIRECTIONS[direction]
    return (x + dx * dist, y + dy * dist), rolls


def kick(match, dice, receiving: str, aim: tuple[int, int] | None = None) -> list[Event]:
    """Kick to the receiving team, roll the event, then land the ball.

    Ordered as S3 orders it: deviate, THEN the Kick-off Event, THEN the ball lands.
    Rolling the event after the ball has already been caught would make half the
    table meaningless.
    """
    events: list[Event] = []
    lo, hi = receiving_half(receiving)
    target = aim or ((match.ball.x or 8), (lo + hi) // 2)

    events.append(
        Event(
            kind="ball_moved",
            detail={"x": target[0], "y": target[1], "carrier": "", "air": True},
            text=f"The ball is kicked towards ({target[0]},{target[1]}) in {receiving}'s half.",
        )
    )
    match.apply(events[-1])

    # "If this player is NOMINATED as the kicking player" — the engine has no
    # nomination step, so it takes the kicking team's best available: any player
    # with Kick on the pitch. Naming somebody without the Skill would change
    # nothing, which is what makes the simplification safe.
    kicker = next(
        (q for q in match.on_pitch(match.opponent(receiving)) if q.has_skill("Kick")),
        None,
    )
    (nx, ny), rolls = deviate(match, dice, *target, kicker=kicker)
    events.append(
        Event(
            kind="ball_moved",
            detail={"x": nx, "y": ny, "carrier": "", "air": True},
            rolls=rolls,
            text=f"The kick deviates {rolls[0].dice[0]} squares to ({nx},{ny}).",
        )
    )
    match.apply(events[-1])

    # ON THE BALL, the half the engine can apply: "during the Start of Drive
    # Sequence, AFTER THE KICK DEVIATES but BEFORE THE KICK-OFF EVENT IS ROLLED, a
    # single OPEN player on the RECEIVING team with this Skill may move UP TO 3
    # SQUARES … they cannot Rush … may not move into the opposition half."
    #
    # The window is exactly one step wide and it is here. A single player, so the
    # engine takes the one with the furthest to go: it moves them toward where the
    # ball is about to land, which is the only thing anybody would use it for.
    events.extend(_on_the_ball(match, receiving, (nx, ny)))

    events.extend(kickoff_event(match, dice, receiving))

    # "At this point the ball is still HIGH UP IN THE AIR and cannot be caught
    # UNTIL AFTER THE KICK-OFF EVENT HAS BEEN RESOLVED." An event that stops to
    # ask the Coach something is not resolved yet, so the ball stays up: it lands
    # when the question is answered (see game.resolve_choice). Landing it here
    # made High Kick meaningless — the player would be placed under a ball that
    # had already come down and bounced away.
    if match.pending:
        return events

    # "Once the Kick-off Event has been fully resolved, the ball will land."
    events.extend(land(match, dice, receiving))
    return events


def _on_the_ball(match, receiving: str, where: tuple[int, int]) -> list[Event]:
    """The kick-off half of On the Ball. See the quote in `kick`."""
    from .rules import is_open

    if not in_bounds(*where) or not in_own_half(receiving, where[1]):
        # "A player may not use this Skill IF A TOUCHBACK IS CAUSED."
        return []
    runners = [p for p in match.on_pitch(receiving) if p.has_skill("On the Ball") and is_open(match, p)]
    if not runners:
        return []
    p = min(runners, key=lambda q: (max(abs(q.x - where[0]), abs(q.y - where[1])), q.id))

    # Up to three squares toward the ball, one step at a time, stopping at the
    # halfway line and at anybody in the way. No Rush, so three is the hard cap.
    x, y = p.x, p.y
    for _ in range(3):
        step = (x + _sign(where[0] - x), y + _sign(where[1] - y))
        if step == (x, y) or match.at(*step) is not None or not in_own_half(receiving, step[1]):
            break
        x, y = step
    if (x, y) == (p.x, p.y):
        return []
    ev = Event(
        kind="player_pushed",
        actor=p.id,
        detail={"x": x, "y": y, "skill": "On the Ball"},
        text=f"On the Ball: {p.name()} reads the kick and moves to ({x},{y}) before the event is rolled.",
    )
    match.apply(ev)
    return [ev]


def _sign(n: int) -> int:
    return (n > 0) - (n < 0)


def _open_ids(match, side: str) -> list[str]:
    from .rules import is_open

    return sorted(p.id for p in match.on_pitch(side) if is_open(match, p))


def _ask(match, kind: str, side: str, text: str, detail: dict, land: str, rolls=()) -> list[Event]:
    """Stop and record what the engine is waiting for.

    The alternative is choosing on the coach's behalf, which for these events
    would be inventing a formation — so the Drive pauses here and `bb_game_choose`
    resumes it. Declining is always a legal answer.
    """
    # The question travels IN the pending state, not only in the log line: the
    # coach answers in a separate call, by which time the Match has been rebuilt
    # from disk, and a board that has to go digging in the log to say what it is
    # waiting for will eventually say the wrong thing.
    ev = Event(
        kind="choice_pending",
        # `land` says what to do once this is answered. The ball is still in the
        # air while a Kick-off Event is unresolved, and whoever answers has to be
        # able to bring it down without knowing that is why they were asked.
        detail={"choice": kind, "side": side, "text": text, "land": land, **detail},
        rolls=list(rolls),
        text=text + " Answer with bb_game_choose, or decline.",
    )
    match.apply(ev)
    return [ev]


def _random_player(match, side: str, dice, exclude=()):
    """ "randomly selects one of their players on the pitch."

    A uniform pick among however many they have, which is what ``Dice.dn`` is for
    — and it is a recorded roll like any other, so a replay picks the same player.
    """
    pool = sorted((p for p in match.on_pitch(side) if p.id not in exclude), key=lambda p: p.id)
    if not pool:
        return None
    return pool[dice.dn(len(pool)) - 1]


def _losers(match, dice, label: str, staff_key: str = ""):
    """The side(s) a kick-off event lands on: "the Coach that rolled the LOWEST,
    or BOTH Coaches on a tie"."""
    totals = {}
    for side in ("home", "away"):
        bonus = int((match.staff.get(side) or {}).get(staff_key, 0)) if staff_key else 0
        d = dice.d6()
        roll = Roll(kind=label, dice=[d], total=d + bonus, modifier=bonus, note=side)
        dice.rolls.append(roll)
        totals[side] = d + bonus
    if totals["home"] == totals["away"]:
        return ("home", "away")
    return ("home",) if totals["home"] < totals["away"] else ("away",)


def _contested(match, dice, staff_key: str, label: str):
    """ "Both Coaches roll a D6 and add [some number on their Team Roster]."

    Returns (winning side or "", the events). A TIE gives nobody anything: the
    rule says "the Coach with the highest total", and on a tie there is not one.

    The staff number is an INPUT — a practice board never hired anybody — so it
    defaults to zero and the roll says what it added, rather than the engine
    quietly assuming a roster it cannot see.
    """
    out = []
    totals = {}
    for side in ("home", "away"):
        bonus = int((match.staff.get(side) or {}).get(staff_key, 0))
        d = dice.d6()
        roll = Roll(kind=label, dice=[d], total=d + bonus, modifier=bonus, note=f"{side} +{bonus} {staff_key}")
        dice.rolls.append(roll)
        totals[side] = d + bonus
        out.append(
            Event(
                kind="note",
                rolls=[roll],
                text=f"{label}: {side} roll {d}" + (f" +{bonus} = {d + bonus}." if bonus else "."),
            )
        )
        match.apply(out[-1])
    if totals["home"] == totals["away"]:
        out.append(Event(kind="note", text=f"{label}: a tie, so neither Coach gains anything."))
        match.apply(out[-1])
        return "", out
    return ("home" if totals["home"] > totals["away"] else "away"), out


def kickoff_event(match, dice, receiving: str) -> list[Event]:
    r = roll_2d6(dice, "Kick-off Event", 0)
    r.passed = None
    total = int(r.total or 0)
    name, text, applied = KICKOFF_EVENTS.get(total, ("Unknown", "", False))

    events = [
        Event(
            kind="kickoff_event",
            detail={"roll": total, "event": name, "applied": applied},
            rolls=[r],
            text=f"Kick-off Event {total}: {name.upper()} — {text}"
            + ("" if applied else " (rolled, but NOT applied by this engine)"),
        )
    ]
    match.apply(events[0])

    if name == "High Kick":
        # "ONE Open player on the receiving team MAY immediately be placed in the
        # square the ball is going to land in." One player, and "may" — declining
        # is a real answer, so this is offered rather than taken.
        events.extend(
            _ask(
                match,
                kind="high_kick",
                side=receiving,
                land=receiving,
                text=f"High Kick: one Open {receiving} player may be placed where the ball will land.",
                detail={"square": [match.ball.x, match.ball.y], "eligible": _open_ids(match, receiving)},
            )
        )

    elif name in ("Quick Snap!", "Solid Defence", "Charge!"):
        # All three open the same way — "selects UP TO D3+3 Open players on their
        # team" — so the number is rolled now and the choice asked after. What
        # follows differs: two are resolved by the answer, and Charge! is a free
        # turn that the answer only STARTS.
        quick = name.startswith("Quick")
        side = receiving if quick else match.opponent(receiving)
        n = dice.dn(3) + 3
        roll = Roll(kind=name, dice=[n - 3], total=n, note="D3+3 players")
        dice.rolls.append(roll)
        events.extend(
            _ask(
                match,
                kind={"Quick Snap!": "quick_snap", "Solid Defence": "solid_defence", "Charge!": "charge"}[name],
                side=side,
                land=receiving,
                rolls=[roll],
                text={
                    "Quick Snap!": f"Quick Snap: up to {n} Open {side} players may each move one square, "
                    "in any direction.",
                    "Solid Defence": f"Solid Defence: up to {n} Open {side} players may be set up again.",
                    "Charge!": f"Charge!: {side} may send in up to {n} Open players, one at a time, "
                    "for a free Move — one of them may Blitz, one may Throw Team-mate, one may Kick Team-mate.",
                }[name],
                detail={"limit": n, "eligible": _open_ids(match, side)},
            )
        )

    elif name == "Brilliant Coaching":
        # "Both Coaches roll a D6 and add the number of Assistant Coaches on their
        # Team Roster. The Coach with the highest total gains a free Team Re-roll
        # FOR THE DRIVE AHEAD." A tie gives nobody anything — "the Coach with the
        # highest total" and on a tie there is not one.
        winner, detail = _contested(match, dice, "assistant_coaches", "Brilliant Coaching")
        events.extend(detail)
        if winner:
            events.append(
                Event(
                    kind="kickoff_bonus",
                    detail={"side": winner, "reroll": True, "event": name},
                    text=f"{winner} gain a free Team Re-roll for this Drive.",
                )
            )
            match.apply(events[-1])

    elif name == "Cheering Fans":
        # "Both Coaches roll a D6 and add the number of Cheerleaders on their Team
        # Roster. The first Block Action performed during the Coach with the
        # highest roll's NEXT TURN receives an additional Offensive Assist."
        winner, detail = _contested(match, dice, "cheerleaders", "Cheering Fans")
        events.extend(detail)
        if winner:
            events.append(
                Event(
                    kind="kickoff_bonus",
                    detail={"side": winner, "cheer": True, "event": name},
                    text=f"The crowd is behind {winner} — their first Block next Turn gets an extra Offensive Assist.",
                )
            )
            match.apply(events[-1])

    elif name == "Changing Weather":
        # "Roll again on the Weather Table … If the new result is Perfect
        # Conditions, the ball will Scatter (3) in the air before it lands."
        total_w = dice.d6() + dice.d6()
        key, wname, wtext = weather_from_roll(total_w)
        roll = Roll(kind="Weather", dice=[total_w], total=total_w, note=wname)
        dice.rolls.append(roll)
        events.append(
            Event(
                kind="weather_changed",
                detail={"weather": key, "roll": total_w},
                rolls=[roll],
                text=f"The weather turns: {wname.upper()} — {wtext}",
            )
        )
        match.apply(events[-1])
        if key == "perfect":
            events.append(
                Event(
                    kind="note",
                    text="Perfect Conditions — the ball Scatters three times in the air before it lands.",
                )
            )
            match.apply(events[-1])
            events.extend(scatter(match, dice, 3))

    elif name == "Dodgy Snack":
        # "Both Coaches roll a D6. The Coach that rolled the LOWEST, or BOTH
        # Coaches on a tie, randomly selects one of their players on the pitch and
        # rolls a D6. On a 2+ the player reduces their MA and AV by 1 for the
        # Drive. On a 1, place the player in the Reserves box."
        for side in _losers(match, dice, "Dodgy Snack"):
            victim = _random_player(match, side, dice)
            if victim is None:
                continue
            d = dice.d6()
            r = Roll(kind="Dodgy Snack", dice=[d], total=d, target=2, passed=d >= 2)
            dice.rolls.append(r)
            if d >= 2:
                events.append(
                    Event(
                        kind="player_status",
                        actor=victim.id,
                        detail={"snacked": True},
                        rolls=[r],
                        text=f"{victim.name()} eats something dodgy — MA and AV down 1 for the Drive. {r.describe()}",
                    )
                )
            else:
                events.append(
                    Event(
                        kind="player_left_pitch",
                        actor=victim.id,
                        detail={"reason": "dodgy snack"},
                        rolls=[r],
                        text=f"{victim.name()} is violently unwell and leaves the pitch. {r.describe()}",
                    )
                )
            match.apply(events[-1])

    elif name == "Pitch Invasion":
        # "Both Coaches roll a D6 and add their Fan Factor. The Coach that rolled
        # lowest, or both on a tie, randomly selects D3 of their players on the
        # pitch. The selected players are immediately Placed Prone and become
        # Stunned."
        for side in _losers(match, dice, "Pitch Invasion", staff_key="fan_factor"):
            n = dice.dn(3)
            events.append(Event(kind="note", text=f"The crowd gets to {side}: D3 rolled {n}."))
            match.apply(events[-1])
            picked = []
            for _ in range(n):
                v = _random_player(match, side, dice, exclude={q.id for q in picked})
                if v is None:
                    break
                picked.append(v)
            for v in picked:
                events.append(
                    Event(
                        kind="player_condition",
                        actor=v.id,
                        detail={"outcome": "stunned"},
                        text=f"{v.name()} is dragged down by the crowd and Stunned.",
                    )
                )
                match.apply(events[-1])

    elif name == "Time-out!":
        kicking = match.opponent(receiving)
        # The rule reads off the KICKING team's turn marker.
        back = match.clock.turn >= 6
        events.append(
            Event(
                kind="clock_adjusted",
                detail={"delta": -1 if back else 1, "by": kicking},
                text=(
                    "Both Turn Markers move back one space." if back else "Both Turn Markers move forward one space."
                ),
            )
        )
        match.apply(events[-1])
    return events


def land(match, dice, receiving: str) -> list[Event]:
    """Where the kick ends up, and the Touchback if it ended up badly."""
    events: list[Event] = []
    x, y = match.ball.x, match.ball.y

    if not in_bounds(x, y) or not in_own_half(receiving, y):
        why = "off the pitch" if not in_bounds(x, y) else "back over the Line of Scrimmage"
        events.append(
            Event(
                kind="touchback",
                detail={"side": receiving, "why": why},
                text=f"The kick goes {why} — Touchback.",
            )
        )
        match.apply(events[-1])
        events.extend(award_touchback(match, receiving))
        return events

    standing = match.at(x, y)
    if standing is None:
        events.extend(bounce(match, dice))
    else:
        events.extend(catch(match, standing, dice))

    # A bounce can still carry it out of the half, which is also a Touchback.
    if not in_bounds(match.ball.x, match.ball.y) or (
        not match.ball.carrier and not in_own_half(receiving, match.ball.y)
    ):
        events.append(
            Event(
                kind="touchback",
                detail={"side": receiving, "why": "the bounce left the receiving half"},
                text="The ball bounces out of the receiving half — Touchback.",
            )
        )
        match.apply(events[-1])
        events.extend(award_touchback(match, receiving))
    return events


def award_touchback(match, receiving: str) -> list[Event]:
    """Give the ball to a Standing receiver, or place it in their half.

    The coach chooses; with nobody at the table the engine picks the player
    furthest from the Line of Scrimmage, which is the safe, conventional choice
    rather than a convenient one.
    """
    standing = [p for p in match.on_pitch(receiving) if p.down == "standing"]
    if standing:
        own_goal = 1 if receiving == "home" else LENGTH
        pick = min(standing, key=lambda p: (abs(p.y - own_goal), p.id))
        ev = Event(
            kind="ball_picked_up",
            actor=pick.id,
            detail={"touchback": True},
            text=f"Touchback: {pick.name()} is given the ball.",
        )
        match.apply(ev)
        return [ev]

    lo, hi = receiving_half(receiving)
    ev = Event(
        kind="ball_moved",
        detail={"x": 8, "y": (lo + hi) // 2, "carrier": ""},
        text="Touchback: no Standing receiver, so the ball is placed in their half.",
    )
    match.apply(ev)
    return [ev]
