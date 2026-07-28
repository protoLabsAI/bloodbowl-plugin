"""Board maths every action shares: adjacency, Tackle Zones, Marking, targets.

Kept in one place because these are the questions each action asks in the same
way, and two subtly different answers to "who is Marking this square" is the kind
of divergence nobody notices until a game plays wrong.

Every rule here was read off the S3 text rather than recalled. The ones that
differ from what an older edition (or a language model) would tell you are called
out where they sit, because those are the ones that get "corrected" back to the
wrong thing later.
"""

from __future__ import annotations

import re

from .state import Match, PlayerState

STAND_UP_COST = 3  # squares of Move Allowance
STAND_UP_ROLL = 4  # MA 2 or less must roll this instead
MAX_RUSHES = 2


def adjacent(ax: int, ay: int, bx: int, by: int) -> bool:
    """The eight squares around a player — a Tackle Zone's shape."""
    if (ax, ay) == (bx, by):
        return False
    return abs(ax - bx) <= 1 and abs(ay - by) <= 1


def has_tackle_zone(p: PlayerState) -> bool:
    """Only STANDING players project a Tackle Zone.

    Prone and Stunned players do not, so walking away from a player you just
    knocked down needs no Dodge. (S3 adds a third case, Distracted: a Standing
    player that has lost its Tackle Zone. Modelled as a flag so the rest of the
    engine asks this question and not `down == "standing"`.)
    """
    return p.place == "pitch" and p.down == "standing" and not getattr(p, "distracted", False)


def markers_of_square(match: Match, side: str, x: int, y: int) -> list[PlayerState]:
    """Opposition players Marking square (x, y), from ``side``'s point of view."""
    foe = match.opponent(side)
    return [p for p in match.on_pitch(foe) if has_tackle_zone(p) and adjacent(p.x, p.y, x, y)]


def is_marked(match: Match, player: PlayerState) -> bool:
    """Is this player Marked where they stand? Marked is what forces a Dodge."""
    return bool(markers_of_square(match, player.side, player.x, player.y))


def dodge_modifier(match: Match, player: PlayerState, to_x: int, to_y: int) -> int:
    """-1 for each opposition player Marking the square being moved INTO.

    The square being LEFT does not modify the roll — only whether a Dodge is
    needed at all. Getting this backwards is the single most common way to
    mis-model Blood Bowl movement, and it produces plausible numbers either way.
    """
    return -len(markers_of_square(match, player.side, to_x, to_y))


def agility_target(player: PlayerState) -> int:
    """AG as a number. Roster values read "3+"."""
    m = re.search(r"\d+", str(player.player.AG or ""))
    return int(m.group(0)) if m else 4


def armour_target(player: PlayerState) -> int:
    m = re.search(r"\d+", str(player.player.AV or ""))
    return int(m.group(0)) if m else 8


def occupied(match: Match, x: int, y: int) -> bool:
    return match.at(x, y) is not None
