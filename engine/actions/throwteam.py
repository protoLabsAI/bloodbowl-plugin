"""The Throw Team-mate Action — launching a small friend downfield.

Every rule quoted from the S3 source.

    "During each Turn, a single player on the active team may declare a Throw
     Team-mate Action … A player may only declare this Action if they have the
     Throw Team-mate Trait. When a player declares a Throw Team-mate Action they
     are first allowed to make a Move Action, though they cannot continue to move
     after … they may pick up an adjacent team-mate with the RIGHT STUFF Trait."

    RANGE: "The declared square must be wholly underneath the FIRST TWO SECTIONS
     of the Range Ruler … I: Quick Throw, II: Short Throw." So a team-mate does not
     go as far as a ball does — there is no Long Throw at all.

    ACCURACY: "a Passing Ability Test … Quick Throw, there is no modifier … Short
     Throw, apply a -1 modifier. Apply a -1 modifier for each opposition player
     Marking the player performing the Throw Team-mate Action."

      SUPERB  "If the Passing Ability Test is successful, or the roll is a natural
              6 … The thrown player will Scatter (3) from the target square."
      SUBPAR  "If the Passing Ability Test is failed … Scatter (3) from the target
              square, though they will find it harder to do so."   (-1 to land)
      FUMBLED "a 1 after modifiers, or the roll is a natural 1 … The thrown player
              is dropped and will Bounce from the THROWING player's square."

    LANDING: an Agility Test, -1 if Subpar, -1 if Fumbled, -1 per opposition
     player Marking the landing square. "Players that were Prone, Stunned or
     Distracted when they were thrown will AUTOMATICALLY FAIL."
     Pass → on their feet, "if they have not yet been activated that Turn then
     they can still be activated". Fail → they Fall Over, "however, this will only
     cause a Turnover IF THE THROWN PLAYER WAS HOLDING THE BALL".

    CRASH LANDING: "the player in the occupied square is automatically Knocked
     Down EVEN IF THEY ARE ALREADY PRONE OR STUNNED. The thrown player will then
     Bounce from the occupied square and will Fall Over … If after the Bounce the
     thrown player lands in another occupied square, repeat the above process."

The turnover rule is the one to read twice. Nearly everything that goes wrong in
Blood Bowl during your own turn ends it; a botched Throw Team-mate does NOT,
unless the ball was in the thrown player's hands. Launching a Goblin into the
crowd is free — which is what makes the second, angrier use of this Action
tactically sane.
"""

from __future__ import annotations

from ...pitch import in_bounds
from ..ball import DIRECTIONS, bounce, check_touchdown
from ..dice import Roll, roll_target
from ..events import Event
from ..injury import injure_by_crowd, knock_down
from ..ruler import band
from ..rules import adjacent, agility_target, markers_of_square
from ..skills import roll_modifier, unmodelled_skills
from ..state import Match
from . import Legality, Outcome, Recorder, ended, refuse_if_spent, register

# "I: Quick Throw, II: Short Throw" — the first two sections only.
THROW_BANDS = {"Quick Pass": ("Quick Throw", 0), "Short Pass": ("Short Throw", -1)}


def _passing_target(p) -> int:
    import re

    m = re.search(r"\d+", str(p.player.PA or ""))
    return int(m.group(0)) if m else 0


def validate(match: Match, cmd: dict) -> Legality:
    if match.over:
        return Legality(False, "the match is over")
    p = match.by_id(str(cmd.get("player") or ""))
    if p is None:
        return Legality(False, f"no player with id {cmd.get('player')!r}")
    if p.side != match.clock.active:
        return Legality(False, f"it is {match.clock.active}'s turn, and that player is {p.side}")
    spent = refuse_if_spent(match, p, "kickteam" if cmd.get("_kick") else "throwteam")
    if spent:
        return Legality(False, spent)
    if p.place != "pitch" or p.down != "standing":
        return Legality(False, "only a Standing player on the pitch can throw a team-mate")
    # A Kick Team-mate is granted by its OWN Trait — "works exactly the same as a
    # Throw Team-mate Action" describes the procedure, not the permission, and
    # demanding both would make the Trait unusable on its own.
    needed = "Kick Team-mate" if cmd.get("_kick") else "Throw Team-mate"
    if not p.has_skill(needed):
        return Legality(False, f"{p.name()} does not have the {needed} Trait")
    if not _passing_target(p):
        return Legality(False, f"{p.name()} has no Passing Ability and cannot throw")

    t = match.by_id(str(cmd.get("target") or ""))
    if t is None:
        return Legality(False, f"no team-mate with id {cmd.get('target')!r}")
    if t.side != p.side or t.place != "pitch":
        return Legality(False, "you throw a TEAM-MATE, and they must be on the pitch")
    if not adjacent(p.x, p.y, t.x, t.y):
        return Legality(False, f"{t.name()} is not adjacent — you have to pick them up first")
    if not t.has_skill("Right Stuff"):
        # "even if this player is Prone" — Right Stuff is the whole permission, and
        # being on the floor is explicitly no obstacle to being thrown.
        return Legality(False, f"{t.name()} does not have the Right Stuff Trait and cannot be thrown")

    try:
        x, y = int(cmd["x"]), int(cmd["y"])
    except (KeyError, TypeError, ValueError):
        return Legality(False, "a target square (x, y) is required")
    if not in_bounds(x, y):
        return Legality(False, f"({x},{y}) is off the pitch")

    reach = band(p.x, p.y, x, y)
    throw = THROW_BANDS.get(reach[0]) if reach else None
    if throw is None:
        return Legality(False, f"({x},{y}) is beyond a Short Throw — a team-mate does not go as far as a ball")
    label, band_mod = throw
    marking = len(markers_of_square(match, p.side, p.x, p.y))
    return Legality(
        True,
        "",
        {
            "target": t.id,
            "range": label,
            "range_modifier": band_mod,
            "marking_thrower": -marking,
            "modifier": band_mod - marking,
            "passing_target": _passing_target(p),
            "measured_ruler": True,
            "turnover_if_dropped": match.ball.carrier == t.id,
        },
    )


def _scatter_player(match: Match, who, dice, rec: Recorder, times: int = 3) -> None:
    """ "Scatter (3) from the target square" — three D8 steps, applied to a PLAYER.

    The ball's scatter moves the ball; this moves a body, and a body can end up
    off the pitch or on top of somebody, which is why it is its own walk.
    """
    for _ in range(times):
        d = dice.d8()
        roll = Roll(kind="Scatter", dice=[d], total=d, note="D8")
        dice.rolls.append(roll)
        dx, dy = DIRECTIONS[d]
        nx, ny = who.x + dx, who.y + dy
        rec.emit(
            Event(
                kind="player_pushed",
                actor=who.id,
                detail={"x": nx, "y": ny, "scatter": True},
                rolls=[roll],
                text=f"{who.name()} tumbles to ({nx},{ny}).",
            )
        )
        if not in_bounds(nx, ny):
            return


def _land(match: Match, who, dice, rec: Recorder, penalty: int, had_ball: bool) -> bool:
    """Put the thrown player down. Returns True if a Turnover was caused.

    "this will only cause a Turnover if the thrown player WAS HOLDING THE BALL,
    otherwise no Turnover is caused" — which is what makes throwing a Goblin at an
    opponent a sane thing to do.
    """
    if not in_bounds(who.x, who.y):
        # "Should a thrown player leave the pitch as a result of a throw, they will
        # risk Injury by the Crowd and a Turnover will be caused." Always, here.
        rec.absorb(injure_by_crowd(match, who, dice))
        return True

    occupant = next((q for q in match.on_pitch() if q.id != who.id and (q.x, q.y) == (who.x, who.y)), None)
    if occupant is not None:
        # CRASH LANDING: "the player in the occupied square is automatically
        # Knocked Down EVEN IF THEY ARE ALREADY PRONE OR STUNNED."
        rec.emit(
            Event(
                kind="note",
                actor=who.id,
                text=f"{who.name()} crash-lands on {occupant.name()}.",
            )
        )
        rec.absorb(knock_down(match, occupant, dice, by=who, cause="landed on"))
        rec.absorb(bounce(match, dice))  # the thrown player bounces on from here
        rec.absorb(knock_down(match, who, dice, cause="crash-lands"))
        return had_ball

    # "Players that were Prone, Stunned or Distracted when they were thrown will
    # automatically fail the Agility Test to land."
    helpless = who.down != "standing" or getattr(who, "distracted", False)
    marking = -len(markers_of_square(match, who.side, who.x, who.y))
    mod = penalty + marking
    if helpless:
        rec.emit(
            Event(
                kind="note",
                actor=who.id,
                text=f"{who.name()} was thrown while {who.down} and cannot land on their feet.",
            )
        )
        rec.absorb(knock_down(match, who, dice, cause="lands badly"))
        return had_ball

    r = roll_target(dice, "Landing", agility_target(who), mod)
    if r.passed:
        rec.emit(Event(kind="note", actor=who.id, rolls=[r], text=f"{who.name()} lands on their feet. {r.describe()}"))
        rec.absorb(check_touchdown(match, who))
        return False
    rec.emit(Event(kind="note", actor=who.id, rolls=[r], text=f"{who.name()} lands badly. {r.describe()}"))
    rec.absorb(knock_down(match, who, dice, cause="lands badly"))
    return had_ball


def resolve(match: Match, cmd: dict, dice) -> Outcome:
    legal = validate(match, cmd)
    if not legal.ok:
        return Outcome(ok=False, text=legal.reason)

    p = match.by_id(str(cmd["player"]))
    t = match.by_id(str(cmd["target"]))
    x, y = int(cmd["x"]), int(cmd["y"])
    rec = Recorder(match)
    d = legal.detail
    had_ball = bool(d["turnover_if_dropped"])
    unmodelled = sorted(set(unmodelled_skills(p)) | set(unmodelled_skills(t)))

    rec.emit(
        Event(
            kind="note",
            actor=p.id,
            detail={"carrying": t.id, "x": x, "y": y, "range": d["range"]},
            text=f"{p.name()} picks up {t.name()} for a {d['range']} to ({x},{y}).",
        )
    )

    # ALWAYS HUNGRY, before the Passing Ability Test, because that is where the
    # rule puts it: "before making the Passing Ability Test, they must roll a D6".
    fumbled = False
    if p.has_skill("Always Hungry"):
        hunger = roll_target(dice, "Always Hungry", 2)
        rec.emit(
            Event(kind="note", actor=p.id, rolls=[hunger], text=f"{p.name()} eyes their team-mate. {hunger.describe()}")
        )
        if not hunger.passed:
            escape = roll_target(dice, "Squirm free", 2)
            rec.emit(
                Event(
                    kind="note",
                    actor=t.id,
                    rolls=[escape],
                    text=f"{t.name()} tries to squirm free. {escape.describe()}",
                )
            )
            if escape.passed:
                fumbled = True
            else:
                # "immediately remove them from your Team Draft List. No Apothecary
                # … no Regeneration." Eaten is worse than a Casualty.
                rec.emit(
                    Event(
                        kind="player_condition",
                        actor=t.id,
                        detail={"outcome": "casualty", "eaten": True},
                        text=f"{p.name()} EATS {t.name()}. No Apothecary, no Regeneration.",
                    )
                )
                rec.emit(ended(p.id, "throwteam"))
                return Outcome(
                    ok=False,
                    events=rec.events,
                    turnover=True,
                    text=f"{p.name()} ate {t.name()} — turnover.",
                    unmodelled=unmodelled,
                )

    if not fumbled:
        # Through the hook: Strong Arm's +1 and any Disturbing Presence nearby are
        # both modifiers on THIS test, and the rule names it specifically.
        tctx = roll_modifier(match, p, "throwteam", base=d["modifier"], range=d["range"])
        r = roll_target(
            dice,
            "Throw Team-mate",
            d["passing_target"],
            tctx.value,
            note=d["range"] + "".join(f" · {n}" for n in tctx.notes),
        )
        fumbled = r.dice[0] == 1 or (r.total is not None and r.total <= 1)
        superb = r.passed and not fumbled
        rec.emit(
            Event(
                kind="note",
                actor=p.id,
                rolls=[r],
                detail={"outcome": "superb" if superb else "fumbled" if fumbled else "subpar"},
                text=f"{p.name()} makes a "
                + ("Superb Throw" if superb else "Fumbled Throw" if fumbled else "Subpar Throw")
                + f". {r.describe()}",
            )
        )
    else:
        superb = False

    # Where the thrown player starts tumbling from.
    if fumbled:
        # "The thrown player is dropped and will Bounce from the THROWING player's
        # square" — not the target square, which is the whole cost of a fumble.
        start = (p.x, p.y)
        penalty = -1
        if cmd.get("_kick"):
            # "if a Kick Team-mate Special Action results in a Fumbled Throw,
            # immediately make an Injury Roll for the team-mate being kicked,
            # TREATING ANY RESULT OF STUNNED AS KNOCKED OUT."
            from ..injury import injury_roll

            hurt = injury_roll(match, t, dice)
            rec.absorb(hurt)
            outcome = next((e.detail.get("outcome") for e in hurt if e.kind == "injury_roll"), "")
            if outcome == "stunned":
                rec.emit(
                    Event(
                        kind="player_condition",
                        actor=t.id,
                        detail={"outcome": "knocked_out"},
                        text=f"A kick is worse than a throw — {t.name()} is Knocked-out rather than Stunned.",
                    )
                )
    else:
        start = (x, y)
        penalty = 0 if superb else -1
    rec.emit(
        Event(
            kind="player_pushed",
            actor=t.id,
            detail={"x": start[0], "y": start[1], "thrown": True},
            text=f"{t.name()} is launched from ({start[0]},{start[1]}).",
        )
    )
    # BULLSEYE: "if the result of the throw is a Superb Throw then the thrown
    # player will not Scatter before landing and will instead land in the target
    # square."
    if not (superb and p.has_skill("Bullseye")):
        _scatter_player(match, t, dice, rec, times=1 if fumbled else 3)

    turned_over = _land(match, t, dice, rec, penalty, had_ball)
    rec.emit(ended(p.id, "kickteam" if cmd.get("_kick") else "throwteam"))
    return Outcome(
        ok=not turned_over,
        events=rec.events,
        turnover=turned_over,
        text=f"{p.name()} threw {t.name()}" + (" — turnover." if turned_over else f", who ends up at ({t.x},{t.y})."),
        unmodelled=unmodelled,
    )


def _kick_validate(match: Match, cmd: dict) -> Legality:
    """KICK TEAM-MATE: "works exactly the same as a Throw Team-mate Action, with
    the following exceptions: Performing a Kick Team-mate Special Action DOES NOT
    COUNT as a team's Throw Team-mate Action for the Turn, and so a team can
    perform both … in the same Turn if they wish."

    So it borrows every check but the once-per-turn one, which it swaps for its
    own — "only one player can declare this Special Action each Turn".
    """
    return validate(match, {**cmd, "_kick": True})


def _kick_resolve(match: Match, cmd: dict, dice) -> Outcome:
    """…and its one real difference: "if a Kick Team-mate Special Action results in
    a FUMBLED THROW, immediately make an Injury Roll for the team-mate being
    kicked, TREATING ANY RESULT OF STUNNED AS KNOCKED OUT."

    Kicking somebody is worse for them than throwing them, which is the entire
    point of the distinction.
    """
    return resolve(match, {**cmd, "_kick": True}, dice)


register("throwteam", validate, resolve)
register("kickteam", _kick_validate, _kick_resolve)
