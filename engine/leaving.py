"""The Skills that fire when an opponent tries to leave your Tackle Zone.

Four of them, and they are one mechanism seen from four angles — which is why
they live together rather than in four places that would each re-derive who is
Marking whom and each get the ordering slightly different.

    TENTACLES      "When an opposition player attempts to Dodge, Jump or Leap away
                   from a square in this player's Tackle Zone … roll a D6 and add
                   their Strength … subtract the Strength Characteristic of the
                   opposition player … If the result is 6 or higher, OR THE ROLL IS
                   A NATURAL 6, then the opposition player DOES NOT LEAVE the
                   square they attempted to leave and their activation comes to an
                   end. If the result is 5 or lower, OR THE ROLL IS A NATURAL 1,
                   this Skill has no effect."
    DIVING TACKLE  "…and an Agility test HAS BEEN ROLLED and any modifiers and
                   re-rolls HAVE BEEN APPLIED, this player may use this Skill.
                   Immediately apply a -2 modifier to the opposition player's
                   Agility Test and PLACE THIS PLAYER PRONE in the square the
                   opposition player vacated."
    ARM BAR        "If an opposing player FALLS OVER as a result of attempting to
                   Dodge, Leap or Jump away … they may apply a +1 modifier to
                   EITHER the Armour Roll or Injury Roll."
    SHADOWING      "Each time an opposing player attempts to Dodge out of a square
                   within this player's Tackle Zone … roll a D6. On a 1-3, nothing
                   happens. On a 4+, this player is IMMEDIATELY PLACED INTO THE
                   SQUARE THAT THE OPPOSITION PLAYER VACATED. This player may only
                   use this Skill a number of times per Turn EQUAL TO THEIR MA."

THE ORDER IS THE RULES' OWN, and getting it wrong changes outcomes:

  1. TENTACLES, before the roll — it stops them leaving at all, so a Dodge that
     never happens cannot be failed, re-rolled or Diving-Tackled.
  2. the Agility Test, its modifiers and its re-rolls.
  3. DIVING TACKLE, after all of that — its -2 is applied to a roll that has
     already been made, which is exactly what makes it worth a Skill: the coach
     spends it knowing whether it will matter.
  4. then either they left (SHADOWING follows them) or they Fell Over (ARM BAR).

"IF A PLAYER TRIES TO LEAVE THE TACKLE ZONE OF MULTIPLE PLAYERS WITH THIS SKILL AT
THE SAME TIME, ONLY ONE OF THOSE PLAYERS MAY USE THIS SKILL." Three of the four
say it in those words. It is per Skill, not across them — a player can be Tentacled
and Diving-Tackled by two different opponents in the same step — so `_one_of` picks
a single user per Skill and the engine says which.

Note what is NOT here: Tackle and Prehensile Tail also key off leaving a Tackle
Zone, but they are a re-roll denial and a flat modifier, and both already ride
hooks that fit them. A Skill that needs its own roll, or that moves a player, does
not fit a value hook, which is the line this module is on the far side of.
"""

from __future__ import annotations

from .dice import Roll
from .events import Event
from .rules import strength_of
from .skills import can_use


def _shadowed_this_turn(match, shadow) -> int:
    """How many times this player has already Shadowed since the turn began.

    Derived from the log for the same reason everything else here is: a count kept
    on the object is a count a folded match plays without.
    """
    start = 0
    for i, e in enumerate(match.events):
        if e.kind == "turn_started":
            start = i
    return sum(
        1
        for e in match.events[start:]
        if e.kind == "player_pushed" and e.actor == shadow.id and (e.detail or {}).get("shadowing")
    )


def _one_of(markers, skill: str):
    """The single opponent who gets to use ``skill`` — "only one of those players
    may use this Skill". Lowest id, so a replay picks the same one."""
    able = sorted((m for m in markers if can_use(m, skill)), key=lambda m: m.id)
    return able[0] if able else None


def tentacles(match, p, markers, dice, rec) -> bool:
    """Rolled BEFORE the Agility Test. Returns True if they are held.

    The comparison is unusual and worth reading twice: a natural 6 always holds
    and a natural 1 never does, so a strong player is never quite safe from a weak
    one and never quite certain against them either.
    """
    holder = _one_of(markers, "Tentacles")
    if holder is None:
        return False
    d = dice.d6()
    total = d + strength_of(match, holder) - strength_of(match, p)
    held = d == 6 or (total >= 6 and d != 1)
    roll = Roll(
        kind="Tentacles",
        dice=[d],
        total=total,
        target=6,
        passed=held,
        note=f"D6 +{strength_of(match, holder)} ST -{strength_of(match, p)} ST",
    )
    dice.rolls.append(roll)
    rec.emit(
        Event(
            kind="note",
            actor=holder.id,
            rolls=[roll],
            detail={"skill": "Tentacles", "held": held, "target": p.id},
            text=f"{holder.name()} grabs {p.name()} with Tentacles. {roll.describe()}"
            + (f" {p.name()} cannot leave, and their activation ends." if held else f" {p.name()} slips free."),
        )
    )
    return held


def diving_tackle(match, p, markers, dice, rec, modifier: int) -> tuple[int, object]:
    """Applied AFTER the roll and its re-rolls. Returns (new modifier, tackler).

    "Immediately apply a -2 modifier … and place this player Prone in the square
    the opposition player vacated" — so it costs the tackler their feet, every
    time, whether or not the -2 changes the outcome. The engine spends it only
    when it turns a success into a failure: any other use is pure loss, and there
    is nobody at the table to want one.
    """
    tackler = _one_of(markers, "Diving Tackle")
    if tackler is None:
        return modifier, None
    return modifier - 2, tackler


def arm_bar(match, p, markers, rec) -> int:
    """+1 to the Armour or Injury Roll of a player who Fell Over leaving. Returns
    the bonus, which `injury.risk_injury` spends the same way Mighty Blow's is —
    on the Armour Roll only when that is what breaks it."""
    who = _one_of(markers, "Arm Bar")
    if who is None:
        return 0
    rec.emit(
        Event(
            kind="note",
            actor=who.id,
            detail={"skill": "Arm Bar", "target": p.id},
            text=f"{who.name()} gets an Arm Bar in as {p.name()} goes down — +1 to the Armour or Injury Roll.",
        )
    )
    return 1


def shadowing(match, p, markers, dice, rec, vacated: tuple[int, int]) -> None:
    """Follow an opponent who got away, on a 4+.

    "This player may only use this Skill A NUMBER OF TIMES PER TURN EQUAL TO THEIR
    MA." Counted from the log rather than tracked on the player: every use emits a
    `player_pushed` carrying `shadowing`, so the count is derivable and survives a
    fold — which a counter on the object would not.
    """
    shadow = _one_of(markers, "Shadowing")
    if shadow is None or match.at(*vacated) is not None:
        return
    if _shadowed_this_turn(match, shadow) >= shadow.movement():
        rec.emit(
            Event(
                kind="note",
                actor=shadow.id,
                detail={"skill": "Shadowing", "spent": True},
                text=f"{shadow.name()} has shadowed {shadow.movement()} times this Turn and is out of puff.",
            )
        )
        return
    d = dice.d6()
    roll = Roll(kind="Shadowing", dice=[d], total=d, target=4, passed=d >= 4)
    dice.rolls.append(roll)
    if not roll.passed:
        rec.emit(
            Event(
                kind="note",
                actor=shadow.id,
                rolls=[roll],
                text=f"{shadow.name()} tries to shadow {p.name()} and is left standing. {roll.describe()}",
            )
        )
        return
    rec.emit(
        Event(
            kind="player_pushed",
            actor=shadow.id,
            rolls=[roll],
            detail={"x": vacated[0], "y": vacated[1], "shadowing": True},
            text=f"{shadow.name()} shadows {p.name()} into ({vacated[0]},{vacated[1]}). {roll.describe()}",
        )
    )
