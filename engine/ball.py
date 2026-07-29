"""The ball: bouncing, catching, picking up, and scoring with it.

One module because these call each other in loops — a failed catch bounces, a
bounce can land on a player who must then catch, and that catch can fail and
bounce again. Splitting them would mean the recursion crossed module boundaries
for no gain.

Read from the S3 source, with the passages quoted where they decide something:

* BOUNCE is "Scatter (1)" — one D8, one square.
* CATCH is an Agility Test at -1 per opposition player Marking the catcher.
  A natural 6 always catches and a natural 1 always fails. Prone, Stunned and
  Distracted players AUTOMATICALLY fail and the ball bounces on.
* PICKING UP is an Agility Test at -1 per opposition player Marking the picker,
  and failing it is a TURNOVER. It happens only on a VOLUNTARY move during your
  own activation: "If a player is ever involuntarily moved into a square
  containing the ball, such as being pushed... they may not attempt to pick it up
  and it will Bounce; however, no Turnover will be caused."
* TOUCHDOWN needs possession AND Standing in the opposing End Zone — and it can
  happen on a PUSH, or during the opponent's turn. "should a player with the ball
  be Placed Prone, Fall Over, or be Knocked Down as they move into the opposition
  End Zone, then no Touchdown will be scored."
"""

from __future__ import annotations

from ..pitch import in_bounds
from . import rerolls as team_rerolls
from .dice import roll_target
from .events import Event
from .rules import agility_target, markers_of_square
from .skills import may_reroll, roll_modifier
from .state import touchdown_row


class _sink:
    """A Recorder-shaped shim for the ball helpers, which apply as they go and
    hand their events back rather than holding a Recorder."""

    def __init__(self, match, events):
        self.match, self.events = match, events

    def emit(self, event):
        self.match.apply(event)
        self.events.append(event)
        return event


# The Random Direction Template: D8 -> one of the eight directions.
#
# The template is a DIAGRAM in the source, so the specific rotation below is the
# conventional one rather than something read off the page. That is safe: the only
# property the rules depend on is that a D8 maps one-to-one onto the eight
# directions, so every direction is equally likely. Which numeral points which way
# is cosmetic, and the pitch's own orientation is a drawing choice anyway.
DIRECTIONS = {
    1: (-1, -1),
    2: (0, -1),
    3: (1, -1),
    4: (-1, 0),
    5: (1, 0),
    6: (-1, 1),
    7: (0, 1),
    8: (1, 1),
}


def scatter(match, dice, times: int = 1) -> list[Event]:
    """Move the ball ``times`` squares, one D8 roll at a time.

    Rolled one at a time and applied between rolls, as the rules specify — the
    path matters, not just the endpoint, because a ball that leaves the pitch part
    way through has already gone into the crowd.
    """
    events: list[Event] = []
    for _ in range(max(1, times)):
        d = dice.d8()
        from .dice import Roll

        roll = Roll(kind="Direction", dice=[d], note="D8")
        dice.rolls.append(roll)
        dx, dy = DIRECTIONS[d]
        nx, ny = match.ball.x + dx, match.ball.y + dy
        events.append(
            Event(
                kind="ball_moved",
                detail={"x": nx, "y": ny, "carrier": ""},
                rolls=[roll],
                text=f"The ball bounces to ({nx},{ny}).",
            )
        )
        match.apply(events[-1])
        if not in_bounds(nx, ny):
            break
    return events


def bounce(match, dice, depth: int = 0) -> list[Event]:
    """Bounce the ball one square, and let whoever is there try to catch it.

    A failed catch bounces again, which is why this recurses. The depth guard is a
    backstop against a pathological board, not a rule.
    """
    events = scatter(match, dice, 1)
    if not in_bounds(match.ball.x, match.ball.y):
        events.append(
            Event(
                kind="ball_out_of_bounds",
                detail={"x": match.ball.x, "y": match.ball.y},
                text="The ball leaves the pitch and is thrown back by the crowd.",
            )
        )
        match.apply(events[-1])
        return events

    landed = match.at(match.ball.x, match.ball.y)
    if landed is not None and depth < 8:
        events.extend(catch(match, landed, dice, depth=depth + 1))
    return events


def catch(match, player, dice, depth: int = 0, modifier: int = 0, team_reroll: bool = False) -> list[Event]:
    """A player tries to catch the ball in their square.

    ``team_reroll`` is threaded from the ACTION that caused this rather than read
    off the match: a catch after a bounce or a kick-off is nobody's declared
    action, and a Team Re-roll is only ever spent on a choice the coach made.
    """
    events: list[Event] = []
    if player.down != "standing" or getattr(player, "distracted", False):
        events.append(
            Event(
                kind="note",
                actor=player.id,
                text=f"{player.name()} is {player.down} and cannot Catch — the ball bounces.",
            )
        )
        match.apply(events[-1])
        events.extend(bounce(match, dice, depth=depth))
        return events

    marking = -len(markers_of_square(match, player.side, player.x, player.y))
    ctx = roll_modifier(match, player, "catch", base=modifier + marking, marking=marking)
    mod = ctx.value
    r = roll_target(dice, "Catch", agility_target(player), mod, note=" ".join(ctx.notes))
    rolls = [r]
    if not r.passed:
        # "This player may re-roll any failed Agility Test when attempting to
        # Catch the ball." Kept in the log beside the failure it re-rolls, because
        # a log that shows only the successful second attempt hides the first.
        allowed, skill = may_reroll(match, player, "catch")
        if allowed:
            r = roll_target(dice, "Catch (re-roll)", agility_target(player), mod, note=f"{skill} skill")
            rolls.append(r)
        elif team_reroll and team_rerolls.spend(match, player, "Catch", dice, _sink(match, events)):
            r = roll_target(dice, "Catch (Team Re-roll)", agility_target(player), mod)
            rolls.append(r)
    if r.passed:
        events.append(
            Event(
                kind="ball_picked_up",
                actor=player.id,
                rolls=rolls,
                text=f"{player.name()} catches the ball. {r.describe()}",
            )
        )
        match.apply(events[-1])
        events.extend(check_touchdown(match, player))
        return events

    events.append(
        Event(
            kind="note",
            actor=player.id,
            rolls=rolls,
            text=f"{player.name()} fails to catch it. {r.describe()}",
        )
    )
    match.apply(events[-1])
    events.extend(bounce(match, dice, depth=depth))
    return events


def pick_up(match, player, dice, team_reroll: bool = False) -> tuple[list[Event], bool]:
    """A voluntary move onto the ball. Returns (events, turnover).

    Failing is a Turnover — which is what makes an unprotected pickup the most
    expensive routine decision in the game.
    """
    events: list[Event] = []
    marking = -len(markers_of_square(match, player.side, player.x, player.y))
    ctx = roll_modifier(match, player, "pick_up", base=marking, marking=marking)
    mod = ctx.value
    r = roll_target(dice, "Pick up", agility_target(player), mod, note=" ".join(ctx.notes))
    rolls = [r]
    if not r.passed:
        allowed, skill = may_reroll(match, player, "pick_up")
        if allowed:
            r = roll_target(dice, "Pick up (re-roll)", agility_target(player), mod, note=f"{skill} skill")
            rolls.append(r)
        elif team_reroll and team_rerolls.spend(match, player, "Pick up", dice, _sink(match, events)):
            r = roll_target(dice, "Pick up (Team Re-roll)", agility_target(player), mod)
            rolls.append(r)
    if r.passed:
        ev = Event(
            kind="ball_picked_up",
            actor=player.id,
            rolls=rolls,
            text=f"{player.name()} picks the ball up. {r.describe()}",
        )
        match.apply(ev)
        return [*events, ev, *check_touchdown(match, player)], False

    ev = Event(
        kind="note",
        actor=player.id,
        rolls=rolls,
        text=f"{player.name()} fumbles the pick-up — turnover. {r.describe()}",
    )
    match.apply(ev)
    return [*events, ev, *bounce(match, dice)], True


# The Throw-in Template is a DIAGRAM with three arrows, so — like the Range Ruler
# and the push arc — the numbers here are read off the picture rather than quoted.
# The template is laid on the last square the ball occupied, facing IN from the
# edge it crossed, and its three arrows are the inward normal and its two
# diagonal neighbours. A D6 selects one, two numbers per arrow.
#
# "roll a D6 to determine the direction the crowd will throw the ball as
# determined by the Throw-in Template. The ball will then travel 2D6 squares in
# the direction of the Throw-in before landing, counting the square underneath
# the Blood Bowl logo … as the first square."
THROW_IN_ARROWS = {1: -1, 2: -1, 3: 0, 4: 0, 5: 1, 6: 1}


def _inward(x: int, y: int) -> tuple[int, int]:
    """Which way is "in" from the square the ball left by."""
    from ..pitch import LENGTH, WIDTH

    return (1 if x < 1 else -1 if x > WIDTH else 0, 1 if y < 1 else -1 if y > LENGTH else 0)


def throw_in(match, dice, lx: int, ly: int) -> list[Event]:
    """The crowd throws it back.

    A CORNER is its own rule — "Should the ball leave the pitch from a corner
    square, position the Random Direction Template … and roll a D3" — because from
    a corner there is no single inward normal to spread three arrows around, so
    the three candidates are the two along the sidelines and the diagonal.
    """
    from ..pitch import LENGTH, WIDTH
    from .dice import Roll

    events: list[Event] = []
    sx, sy = min(max(lx, 1), WIDTH), min(max(ly, 1), LENGTH)
    ix, iy = _inward(lx, ly)

    if ix and iy:  # a corner: both axes are out
        d = dice.dn(3)
        roll = Roll(kind="Corner Throw-in", dice=[d], total=d, note="D3")
        options = [(ix, 0), (ix, iy), (0, iy)]
        dx, dy = options[d - 1]
    else:
        d = dice.d6()
        roll = Roll(kind="Throw-in", dice=[d], total=d, note="D6, three arrows")
        # Rotate the inward normal by one of the three arrows.
        ring = ((1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1))
        base = ring.index((ix, iy))
        dx, dy = ring[(base + THROW_IN_ARROWS[d]) % 8]
    dice.rolls.append(roll)

    a, b = dice.d6(), dice.d6()
    dist = Roll(kind="Throw-in distance", dice=[a, b], total=a + b, note="2D6 squares")
    dice.rolls.append(dist)
    steps = a + b
    nx, ny = sx + dx * steps, sy + dy * steps
    nx, ny = min(max(nx, 1), WIDTH), min(max(ny, 1), LENGTH)

    ev = Event(
        kind="ball_moved",
        detail={"x": nx, "y": ny, "carrier": ""},
        rolls=[roll, dist],
        text=f"The crowd hurls the ball back in {steps} squares, to ({nx},{ny}).",
    )
    match.apply(ev)
    events.append(ev)
    landed = match.at(nx, ny)
    events.extend(catch(match, landed, dice) if landed is not None else bounce(match, dice))
    return events


def drop(match, player, dice, reason: str = "goes down") -> list[Event]:
    """The carrier loses the ball where they stand, and it bounces."""
    if match.ball.carrier != player.id:
        return []
    ev = Event(
        kind="ball_dropped",
        actor=player.id,
        detail={"x": player.x, "y": player.y},
        text=f"{player.name()} {reason} and drops the ball.",
    )
    match.apply(ev)
    return [ev, *bounce(match, dice)]


def check_touchdown(match, player) -> list[Event]:
    """Score if this player is Standing, holding the ball, in the opposing End Zone.

    Checked wherever possession or position can change rather than only after a
    Move, because S3 allows a Touchdown from a push, from a catch, from a pick-up,
    and even during the opposing team's turn.
    """
    if match.ball.carrier != player.id or player.place != "pitch":
        return []
    if player.down != "standing":
        return []
    if player.y != touchdown_row(player.side):
        return []
    ev = Event(
        kind="touchdown",
        actor=player.id,
        detail={"side": player.side},
        text=f"TOUCHDOWN! {player.name()} scores for {player.side}.",
    )
    match.apply(ev)
    return [ev]


def standing_opponents_within(match, side: str, x: int, y: int, distance: int) -> list:
    """Opposition players Standing within ``distance`` squares — the Secure the
    Ball test, which asks about the BALL's surroundings, not the player's."""
    foes = match.opponent(side)
    return [p for p in match.on_pitch(foes) if p.down == "standing" and max(abs(p.x - x), abs(p.y - y)) <= distance]
