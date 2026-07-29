"""The Set-up step of the Start of Drive Sequence, enforced.

    "When setting up their players, each team must select 11 players to take part
     in the Drive. If a team has less than 11 players … then they must set up as
     many as they can. Any players not chosen … are placed in the Reserves Box.
     THE KICKING TEAM MUST SET UP FIRST followed by the receiving team. Players
     must be set up as follows:
       · Players must be deployed in their own half, and not beyond the Line of
         Scrimmage into their opponent's half.
       · At least THREE players from each team must be deployed in the Centre
         Field, directly adjacent to the Line of Scrimmage.
       · No more than TWO players from each team may be deployed within each Wide
         Zone.
       · Should a team only be able to field three or fewer players, then they
         must be deployed in the Centre Field directly adjacent to the Line of
         Scrimmage."

    TOO MANY PLAYERS: "if the mistake is not noticed until the first Turn of the
     Drive has begun, then any extra players on the pitch will be immediately
     removed … The players that are removed CANNOT HAVE THE BALL, CANNOT BE A STAR
     PLAYER and are chosen by the opposition Coach."

THE PRACTICE BOARD STAYS PERMISSIVE. This is the strict half, and the split is
deliberate and long-standing here: an illegal position is a legitimate thing to
want while working a shape out, and a Match refuses with a reason. So
``pitch.Scenario.review`` still reports and never blocks, and this module —
reached only once a match is under way — returns violations that stop a Drive.
"""

from __future__ import annotations

from ..pitch import (
    LOS_ROWS,
    MAX_PLAYERS_ON_PITCH,
    MIN_ON_LINE_OF_SCRIMMAGE,
    WIDE_ZONE_WIDTH,
    WIDTH,
    in_bounds,
)

MAX_PER_WIDE_ZONE = 2


def own_half(side: str, y: int) -> bool:
    """ "deployed in their own half, and not beyond the Line of Scrimmage"."""
    return y <= LOS_ROWS[0] if side == "home" else y >= LOS_ROWS[1]


def on_the_line(side: str, x: int, y: int) -> bool:
    """ "in the Centre Field, DIRECTLY ADJACENT to the Line of Scrimmage" — so the
    row itself, and only the seven central columns."""
    return y == (LOS_ROWS[0] if side == "home" else LOS_ROWS[1]) and WIDE_ZONE_WIDTH < x <= WIDTH - WIDE_ZONE_WIDTH


def wide_zone(x: int) -> str:
    if x <= WIDE_ZONE_WIDTH:
        return "left"
    if x > WIDTH - WIDE_ZONE_WIDTH:
        return "right"
    return ""


def violations(side: str, squares: list[tuple[int, int]], available: int) -> list[str]:
    """Every rule this set-up breaks, in the rulebook's own words."""
    out = []
    n = len(squares)
    if n > MAX_PLAYERS_ON_PITCH:
        out.append(f"{n} players set up — a team may field at most {MAX_PLAYERS_ON_PITCH}")
    if n < min(available, MAX_PLAYERS_ON_PITCH):
        out.append(f"only {n} of {available} available players set up — 'they must set up as many as they can'")
    if any(not in_bounds(x, y) for x, y in squares):
        out.append("a player is off the pitch")
    if len(set(squares)) != n:
        out.append("two players share a square")
    if any(not own_half(side, y) for _x, y in squares):
        out.append("a player is deployed beyond the Line of Scrimmage, into their opponent's half")

    on_line = sum(1 for x, y in squares if on_the_line(side, x, y))
    if n <= MIN_ON_LINE_OF_SCRIMMAGE:
        # "Should a team only be able to field three or fewer players, then they
        # MUST be deployed in the Centre Field directly adjacent to the Line."
        if on_line != n:
            out.append(f"with {n} players, all of them must be on the Line of Scrimmage")
    elif on_line < MIN_ON_LINE_OF_SCRIMMAGE:
        out.append(f"only {on_line} on the Line of Scrimmage — at least {MIN_ON_LINE_OF_SCRIMMAGE} are required")

    for zone in ("left", "right"):
        count = sum(1 for x, _y in squares if wide_zone(x) == zone)
        if count > MAX_PER_WIDE_ZONE:
            out.append(f"{count} players in the {zone} Wide Zone — no more than {MAX_PER_WIDE_ZONE} are allowed")
    return out


def too_many(match, side: str) -> list:
    """TOO MANY PLAYERS, once the Turn has begun: who the opposition would remove.

    "The players that are removed CANNOT HAVE THE BALL, CANNOT BE A STAR PLAYER
    and are chosen by the opposition Coach." The engine plays that choice for
    them, and picks from the back — a Coach removing your players will not take
    the ones nearest their own End Zone.
    """
    on = [p for p in match.on_pitch(side) if match.ball.carrier != p.id and p.player.role != "star"]
    surplus = len(match.on_pitch(side)) - MAX_PLAYERS_ON_PITCH
    if surplus <= 0:
        return []
    back = sorted(on, key=lambda p: p.y, reverse=side == "away")
    return back[:surplus]
