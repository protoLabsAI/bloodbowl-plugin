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

The Casualty Roll's D16 table decides what happens BETWEEN matches — "In all
instances the player will miss the rest of the current game", so nothing on it
changes the match in progress. It is rolled and reported anyway, because a coach
asking what happened to their Blitzer deserves the answer, and because an
Apothecary or a Special Rule keys off specific results.
"""

from __future__ import annotations

from .dice import Roll, roll_2d6
from .events import Event
from .rules import armour_target
from .skills import SkillContext, from_skills, hooks_for

STUNNED_MAX = 7
KO_MAX = 9
CLAWS_BREAKS_FROM = 8


def _emit(match, sink: list, event: Event) -> Event:
    """Apply and record. Every helper in here applies as it goes, so a caller
    appends the returned list rather than replaying it."""
    match.apply(event)
    sink.append(event)
    return event


def place_prone(match, player, dice, reason: str = "") -> list[Event]:
    """The third way onto the floor, and the harmless one.

    S3 names all three — "Placed Prone, Falls Over or Knocked Down" — and only
    this one costs nothing: "When a player is Placed Prone THEY AREN'T AT RISK OF
    BEING CAUSED HARM." No Armour Roll, no Injury Roll. That is the entire value
    of Wrestle, and treating it as a knock-down would quietly hand out two armour
    rolls the rules do not allow.

    The ball still goes: "If a player with the ball is Placed Prone then the ball
    will Bounce from the player's square."
    """
    events = [
        Event(
            kind="player_placed_prone",
            actor=player.id,
            detail={"down": "prone", "reason": reason},
            text=f"{player.name()} is Placed Prone" + (f" ({reason})." if reason else "."),
        )
    ]
    match.apply(events[0])
    from .ball import drop

    events.extend(drop(match, player, dice, reason="is Placed Prone"))
    return events


def knock_down(match, player, dice, by=None, cause: str = "Knocked Down", bonus: int = 0) -> list[Event]:
    """Put a player on the ground and resolve what it cost them.

    ``by`` is the player responsible, when there is one — Mighty Blow belongs to
    the player who knocked them down, not to the one who fell.

    ``bonus`` is a +1 for the Armour OR Injury Roll that came from somewhere other
    than the knocker-down: Arm Bar, whose owner is a bystander to a failed Dodge
    rather than the cause of it. It is spent exactly as Mighty Blow's is.
    """
    events = [
        Event(
            kind="player_fell",
            actor=player.id,
            detail={"down": "prone"},
            text=f"{player.name()} is {cause} and lies Prone.",
        )
    ]
    match.apply(events[0])
    # Losing the ball is part of going down, not a separate thing a caller has to
    # remember — every route here (Block, failed Dodge, failed Rush) drops it.
    from .ball import drop

    events.extend(drop(match, player, dice, reason=f"is {cause}"))
    events.extend(risk_injury(match, player, dice, by=by, bonus=bonus))
    return events


def risk_injury(match, player, dice, by=None, armour_modifier: int = 0, bonus: int = 0) -> list[Event]:
    """Armour, and the injury behind it if the armour breaks.

    ``armour_modifier`` exists for the Foul Action, which is the one route here
    that modifies the Armour Roll directly: "the player making the Foul Action may
    apply a +1 modifier for each Offensive Assist, and apply a -1 modifier for each
    Defensive Assist." A Block spends its assists on Strength instead, before any
    of this, which is why nothing else passes it.
    """
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
    # …plus any +1 from a bystander (Arm Bar). Same rule, same spend: it goes on
    # the Armour Roll only when that is what breaks it, and on the Injury Roll
    # otherwise.
    mighty += bonus

    # CLAWS: "any roll of a natural 8+ on the Armour Roll will break the
    # opposition player's armour REGARDLESS OF THEIR ACTUAL ARMOUR VALUE" — so it
    # lowers the bar to 8 rather than adding to the roll, which matters for AV 10+
    # and matters not at all for AV 8+. Natural, so the Mighty Blow +1 below
    # cannot manufacture one.
    av = armour_target(player)
    clawed = by is not None and by.has_skill("Claws") and av > CLAWS_BREAKS_FROM

    # IRON HARD SKIN: "Opposition players CANNOT APPLY ANY MODIFIERS when making an
    # Armour Roll against this player. Additionally, THE CLAWS SKILL CANNOT BE USED
    # against this player." It belongs to the player being rolled against, which is
    # why it is not a `roll_modifier` hook — that walks the ROLLER's Skills.
    #
    # "Any modifiers" is read as any modifier an OPPOSITION player applies: Foul
    # assists, Mighty Blow and Claws all come from them. Chainsaw's +3 does not —
    # it is the carrier's own liability, "regardless of how it occurred", applied
    # even when nobody is responsible at all.
    iron = from_skills(player, "armour")
    if iron:
        if armour_modifier:
            events.append(
                Event(
                    kind="note",
                    actor=player.id,
                    text=f"Iron Hard Skin: the {armour_modifier:+d} to the Armour Roll does not apply.",
                )
            )
            match.apply(events[-1])
        armour_modifier = 0
        mighty = 0
        clawed = False
    # CHAINSAW: "If this player is Knocked Down or Falls Over for any reason,
    # regardless of how it occurred, then a +3 modifier is applied when the
    # opposition Coach makes an Armour Roll for this player. THIS +3 MODIFIER MUST
    # ALWAYS BE APPLIED." Always, and against them — carrying one is dangerous.
    if player.has_skill("Chainsaw"):
        armour_modifier += 3
    armour = roll_2d6(
        dice,
        "Armour",
        av,
        modifier=armour_modifier,
        note=f"AV {player.player.AV}" + (" · Foul assists" if armour_modifier else ""),
    )
    spent_on_armour = 0
    if mighty and not armour.passed and armour.total + mighty >= av:
        spent_on_armour = mighty
        armour.modifier += mighty
        armour.total += mighty
        armour.passed = True
        armour.note = (armour.note + " · Mighty Blow +1").strip(" ·")

    if clawed and not armour.passed and armour.dice[0] + armour.dice[1] >= CLAWS_BREAKS_FROM:
        armour.passed = True
        armour.note = (armour.note + " · Claws break on a natural 8+").strip(" ·")

    _emit(
        match,
        events,
        Event(
            kind="armour_roll",
            actor=player.id,
            rolls=[armour],
            text=f"Armour on {player.name()}: {armour.describe()}"
            + (" — armour is broken." if armour.passed else " — the armour holds."),
        ),
    )
    if not armour.passed:
        return events

    bonus = mighty - spent_on_armour
    events.extend(injury_roll(match, player, dice, bonus=bonus))
    return events


def injury_roll(match, player, dice, bonus: int = 0) -> list[Event]:
    """The Injury Roll itself, and the condition it leaves the player in.

    Split out because the Crowd goes straight here: "INJURY BY THE CROWD: Make an
    Injury Roll for a player Pushed into the Crowd" — no Armour Roll at all, which
    is the whole reason being shoved off the pitch is worse than being hit.
    """
    events: list[Event] = []
    injury = roll_2d6(dice, "Injury", KO_MAX + 1, modifier=bonus, note="Mighty Blow +1" if bonus else "")
    total = injury.total

    # Thick Skull and Stunty both answer here — Stunty REPLACES the table and Thick
    # Skull then adjusts the result, in that order (see skills.py).
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
            text=f"Injury on {player.name()}: {total} — {_INJURY_TEXT[outcome]}."
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
            text=f"{player.name()} is {_INJURY_TEXT[outcome]}.",
        ),
    )
    if outcome == "casualty":
        events.extend(casualty_roll(match, player, dice))
    return events


def injure_by_crowd(match, player, dice) -> list[Event]:
    """S3 INJURY BY THE CROWD: "Make an Injury Roll for a player Pushed into the
    Crowd. If the player would be Stunned, place them in their team's Reserve Box.
    Otherwise, follow the result on the relevant Injury Table."

    Two things the ordinary knock-down path gets wrong here, and both were wrong:

    * NO ARMOUR ROLL. The crowd does not care what you are wearing.
    * A Stunned result means the RESERVES BOX, not lying Stunned on the pitch —
      they are not on the pitch any more, they are in the stands.
    """
    events = [
        Event(
            kind="player_left_pitch",
            actor=player.id,
            detail={"reason": "crowd"},
            text=f"{player.name()} disappears into the Crowd.",
        )
    ]
    match.apply(events[0])
    # The ball does not go with them.
    from .ball import drop

    events.extend(drop(match, player, dice, reason="is thrown into the Crowd"))
    events.extend(injury_roll(match, player, dice))
    return events


# "D16 RESULT — 1-8 BADLY HURT: The player suffers no long term effects. 9-10
# SERIOUSLY HURT: must miss their next game. 11-12 SERIOUS INJURY: a Niggling
# Injury and must miss their next game. 13-14 LASTING INJURY: a Characteristic
# reduction and must miss their next game. 15-16 DEAD: The player is dead!"
CASUALTY_TABLE = (
    (8, "Badly Hurt", "no long term effects"),
    (10, "Seriously Hurt", "misses their next game"),
    (12, "Serious Injury", "a Niggling Injury, and misses their next game"),
    (14, "Lasting Injury", "a Characteristic reduction, and misses their next game"),
    (16, "Dead", "the player is dead"),
)


def casualty_roll(match, player, dice) -> list[Event]:
    """The D16 Casualty Table. Reported, not applied — everything on it is a
    League consequence, and "in all instances the player will miss the rest of the
    current game" either way."""
    d = dice.dn(16)
    roll = Roll(kind="Casualty", dice=[d], total=d, note="D16")
    dice.rolls.append(roll)
    name, effect = next((n, e) for cap, n, e in CASUALTY_TABLE if d <= cap)
    ev = Event(
        kind="casualty_roll",
        actor=player.id,
        rolls=[roll],
        detail={"result": name, "roll": d, "league_only": True},
        text=f"Casualty Roll for {player.name()}: {d} — {name.upper()} ({effect}). "
        "Beyond this match, so nothing here changes.",
    )
    match.apply(ev)
    return [ev]


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
