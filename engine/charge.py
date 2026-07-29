"""CHARGE! — Kick-off Event 10, the one that is a free turn rather than a question.

    "The Coach of the kicking team selects up to D3+3 Open players on their team.
     The selected players may then be activated ONE AT A TIME, EXACTLY AS IF IT
     WAS THEIR TEAM'S TURN, and perform a free Move Action. ONE of the selected
     players MAY INSTEAD perform a free Blitz Action, ONE may perform a free Throw
     Team-mate Action, and ONE may perform a free Kick Team-mate Action. If a
     selected player Falls Over or is Knocked Down during their activation, no
     further selected players can be activated and the Charge ends."

"Exactly as if it was their team's Turn" is the whole implementation note: this
borrows the entire action machinery rather than re-deriving movement, dodges and
blocks in miniature. Four things make it not a turn, and they are the only four
things this module does:

* **Who may act** — the selected players, and nobody else. One at a time is what
  the existing ``done`` flag already means, so it comes for free.
* **What they may declare** — a Move Action each, plus AT MOST ONE Blitz, ONE
  Throw Team-mate and ONE Kick Team-mate across the whole Charge. Note "may
  INSTEAD": the special Action REPLACES that player's free Move, it is not an
  extra.
* **The stop condition** — a selected player hitting the floor ends it, and that
  is NOT a Turnover. A Turnover would advance the Turn Marker and hand the ball
  over; the rules say the Charge ends, which is a much smaller thing.
* **Where the Drive was up to** — the ball is still in the air (the Kick-off Event
  is not resolved until the Charge is over), and the receiving team's first turn
  has not started. Both resume when the Charge ends.

The clock's active side moves to the charging team for the duration, because every
action in the engine asks "is this player on the active side?" and the honest
answer during a Charge is yes. ``was`` remembers whose Drive it is.
"""

from __future__ import annotations

from .events import Event
from .state import Match

# "perform a free MOVE ACTION. One … may INSTEAD perform a free BLITZ Action, one
# … a free THROW TEAM-MATE Action, and one … a free KICK TEAM-MATE Action."
# Nothing else is on offer: no Pass, no Foul, no Hand-off, no Secure the Ball.
FREE = ("move",)
ONCE_EACH = ("blitz", "throwteam", "kickteam")
# Foregoing is not a fifth Action, it is the absence of one — "the player will not
# be activated" — and it has to be available or a Charge whose players have moved
# but not spent their whole Move Allowance can never finish.
ALLOWED = (*FREE, *ONCE_EACH, "forego")


def active(match: Match) -> bool:
    return bool(match.charge)


def start(match: Match, side: str, players: list[str], land: str) -> Event:
    ev = Event(
        kind="charge_started",
        detail={"side": side, "players": list(players), "used": [], "was": match.clock.active, "land": land},
        text=f"Charge! {side} may activate {len(players)} player(s), one at a time.",
    )
    match.apply(ev)
    return ev


def refuse(match: Match, action: str, player_id: str) -> str:
    """Why this action is not available during the Charge — "" if it is."""
    if not match.charge:
        return ""
    who = list(match.charge.get("players") or [])
    if player_id not in who:
        return f"Charge!: only the {len(who)} selected players may be activated" + (
            f" ({', '.join(who)})" if who else ""
        )
    if action not in ALLOWED:
        return f"Charge!: a selected player may perform a free {' or '.join(ALLOWED)} Action, not a {action}"
    if action in ONCE_EACH and action in (match.charge.get("used") or []):
        # "ONE of the selected players may instead perform a free Blitz Action" —
        # one, for the whole Charge, not one each.
        return f"Charge!: the free {action} has already been used"
    return ""


def note_action(match: Match, action: str) -> list[Event]:
    if action not in ONCE_EACH or action in (match.charge.get("used") or []):
        return []
    ev = Event(kind="charge_action", detail={"action": action}, text="")
    match.apply(ev)
    return [ev]


def should_end(match: Match, fell: bool) -> str:
    """Why the Charge is over — "" if it is not.

    Two ways: someone hit the floor, or there is nobody left to activate.
    """
    if not match.charge:
        return ""
    if fell:
        return "a selected player is off their feet"
    left = [
        pid
        for pid in (match.charge.get("players") or [])
        if (match.by_id(pid) is not None and not match.by_id(pid).done)
    ]
    if not left:
        return "every selected player has been activated"
    return ""


def end(match: Match, why: str) -> list[Event]:
    """Close the Charge and hand the Drive back. Does NOT land the ball — the
    caller does that, because landing it is the rest of the Kick-off Event and
    belongs with the other two events that defer it."""
    if not match.charge:
        return []
    ev = Event(kind="charge_ended", detail={"why": why}, text=f"The Charge ends — {why}.")
    match.apply(ev)
    return [ev]


def fell_over(match: Match, events: list[Event]) -> bool:
    """Did a SELECTED player go down in what just happened?

    "If a selected player FALLS OVER OR IS KNOCKED DOWN during their activation" —
    both, and either way it is about the charging team's own player. A defender
    flattened by the Charge's Blitz does not stop it.

    Both routes to the floor emit `player_fell` (`injury.knock_down`); the third
    way onto the floor, `player_placed_prone`, is deliberately NOT here — a player
    Placed Prone "aren't at risk of being caused harm", and the rule names the two
    that are.
    """
    who = set(match.charge.get("players") or [])
    return any(e.kind == "player_fell" and str(e.actor or "") in who for e in events)
