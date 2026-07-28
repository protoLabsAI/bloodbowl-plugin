"""Knocking a player down: Armour, then Injury, then where they end up.

One place, because every route to it must behave identically — a Block, a failed
Dodge, a failed Rush, a Chain Push into the crowd. Two implementations of "what
happens when a player hits the ground" is how a game starts quietly disagreeing
with itself.

S3, read from source:
  ARMOUR   2D6 compared to the Armour Value ("9+"). Failed → nothing further
           happens. Successful → armour is 'broken' and they risk injury.
  INJURY   2D6: 2-7 Stunned · 8-9 Knocked-out · 10-12 Casualty.
  CASUALTY removed to the Casualty box, then a D16 Casualty Roll.

The Casualty Roll's D16 table (Badly Hurt through Dead) is a LEAGUE concern —
it decides what happens between matches, not during one. The player is removed
to the Casualty box, which is all a single match needs, and the roll itself is
reported as not modelled rather than silently skipped.
"""

from __future__ import annotations

from .dice import roll_2d6
from .events import Event
from .rules import armour_target
from .skills import SkillContext, hooks_for

STUNNED_MAX = 7
KO_MAX = 9


def _emit(match, sink: list, event: Event) -> Event:
    """Apply and record. Every helper in here applies as it goes, so a caller
    appends the returned list rather than replaying it."""
    match.apply(event)
    sink.append(event)
    return event


def knock_down(match, player, dice, by=None, cause: str = "Knocked Down") -> list[Event]:
    """Put a player on the ground and resolve what it cost them.

    ``by`` is the player responsible, when there is one — Mighty Blow belongs to
    the player who knocked them down, not to the one who fell.
    """
    events = [
        Event(
            kind="player_fell",
            actor=player.id,
            detail={"down": "prone"},
            text=f"{player.player.position} is {cause} and lies Prone.",
        )
    ]
    match.apply(events[0])
    # Losing the ball is part of going down, not a separate thing a caller has to
    # remember — every route here (Block, failed Dodge, failed Rush) drops it.
    from .ball import drop

    events.extend(drop(match, player, dice, reason=f"is {cause}"))
    events.extend(risk_injury(match, player, dice, by=by))
    return events


def risk_injury(match, player, dice, by=None) -> list[Event]:
    """Armour, and the injury behind it if the armour breaks."""
    events: list[Event] = []

    # Mighty Blow: +1 to EITHER the Armour or the Injury roll, "after the roll has
    # been made". Modelled as: spend it on Armour only when that is what turns a
    # failure into a break, otherwise keep it for the Injury roll — which is the
    # choice the rule exists to allow.
    mighty = 0
    if by is not None:
        ctx = SkillContext(match=match, player=by, value=0)
        for skill, fn in hooks_for("knockdown_bonus"):
            if by.has_skill(skill):
                fn(ctx)
        mighty = ctx.value

    av = armour_target(player)
    armour = roll_2d6(dice, "Armour", av, note=f"AV {player.player.AV}")
    spent_on_armour = 0
    if mighty and not armour.passed and armour.total + mighty >= av:
        spent_on_armour = mighty
        armour.modifier += mighty
        armour.total += mighty
        armour.passed = True
        armour.note = (armour.note + " · Mighty Blow +1").strip(" ·")

    _emit(
        match,
        events,
        Event(
            kind="armour_roll",
            actor=player.id,
            rolls=[armour],
            text=f"Armour on {player.player.position}: {armour.describe()}"
            + (" — armour is broken." if armour.passed else " — the armour holds."),
        ),
    )
    if not armour.passed:
        return events

    bonus = mighty - spent_on_armour
    injury = roll_2d6(dice, "Injury", KO_MAX + 1, modifier=bonus, note="Mighty Blow +1" if bonus else "")
    total = injury.total

    # Thick Skull: "they will only be Knocked-out on the roll of a 9; a roll of an
    # 8 will be treated as a Stunned result." Applied to the TOTAL, and asked as a
    # hook so the Stunty interaction can join it later without editing this.
    tctx = SkillContext(match=match, player=player, value=total, flags={"outcome": _outcome(total)})
    for skill, fn in hooks_for("injury_outcome"):
        if player.has_skill(skill):
            fn(tctx)
    outcome = tctx.flags["outcome"]

    injury.passed = outcome != "stunned"
    injury.note = (injury.note + f" · {outcome}").strip(" ·")
    _emit(
        match,
        events,
        Event(
            kind="injury_roll",
            actor=player.id,
            rolls=[injury],
            detail={"outcome": outcome},
            text=f"Injury on {player.player.position}: {total} — {_INJURY_TEXT[outcome]}."
            + ("".join(f" {n}." for n in tctx.notes)),
        ),
    )
    _emit(
        match,
        events,
        Event(
            kind="player_condition",
            actor=player.id,
            detail={"outcome": outcome},
            text=f"{player.player.position} is {_INJURY_TEXT[outcome]}.",
        ),
    )
    return events


_INJURY_TEXT = {
    "stunned": "Stunned",
    "knocked_out": "Knocked-out and leaves the pitch",
    "casualty": "a Casualty and leaves the pitch",
}


def _outcome(total: int) -> str:
    if total <= STUNNED_MAX:
        return "stunned"
    if total <= KO_MAX:
        return "knocked_out"
    return "casualty"
