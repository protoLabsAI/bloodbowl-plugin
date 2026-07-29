"""Skills as hooks, so adding one is a registration rather than an edit.

Blood Bowl has around a hundred Skills and Traits, and every one of them is an
exception to a rule somewhere. Written as branches inside the actions, the
movement code would be a wall of `if player.has_skill(...)` within a release. So
each Skill registers against a NAMED HOOK, the actions ask the registry, and a
new Skill touches exactly one file.

The other half of the design matters as much: **a Skill nobody has implemented is
reported, not ignored.** Every roster player carries their real Skill list, so the
engine always knows when a Skill it does not model is sitting on the pitch. It
says so, in the action's result and in the log, rather than quietly playing a
Troll as though Always Hungry did not exist. Silence there would be the same
failure this whole plugin exists to avoid — a confident answer with something
missing from underneath it.

Only Skills whose text has been read off the S3 source are registered. Guessing at
one from memory is exactly the confabulation the engine is meant to rule out.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from .events import Event

CATALOGUE = Path(__file__).resolve().parent.parent / "data" / "skills.json"

# --- the registry ---------------------------------------------------------

# hook name -> [(skill name, fn)]. A list, not a dict, so two Skills can answer
# the same hook and both are applied in registration order.
_HOOKS: dict[str, list[tuple[str, Callable]]] = {}
_MODELLED: set[str] = set()
# Skills the engine applies only PARTLY, and what it leaves out. "Modelled" and
# "not modelled" is a binary that flatters: a Skill with two clauses of which one
# is applied would otherwise report as modelled and quietly do half its job, which
# is the same failure as saying nothing — worse, because it sounds settled.
_PARTIAL: dict[str, str] = {}


def skill_hook(skill: str, hook: str, partial: str = ""):
    """Register ``fn`` as ``skill``'s behaviour at ``hook``.

    ``partial`` names what the engine does NOT apply, for a Skill that is modelled
    in part. It is reported beside the Skill wherever the Skill is.
    """

    def deco(fn: Callable) -> Callable:
        _HOOKS.setdefault(hook, []).append((skill, fn))
        _MODELLED.add(skill.casefold())
        if partial:
            _PARTIAL[skill.casefold()] = partial
        return fn

    return deco


def partial_skills() -> dict[str, str]:
    """Skill (casefolded) -> what the engine leaves out."""
    return dict(_PARTIAL)


def modelled() -> set[str]:
    return set(_MODELLED)


def hooks_for(hook: str) -> list[tuple[str, Callable]]:
    return list(_HOOKS.get(hook, ()))


def from_skills(player, hook: str) -> bool:
    """Does this player carry any Skill registered under ``hook``, and may they use
    it? For marker hooks — the ones whose whole content is "this player has it" —
    where the behaviour lives at the roll site rather than in the hook body."""
    return any(can_use(player, skill) for skill, _fn in hooks_for(hook))


@dataclass
class SkillContext:
    """What a hook is allowed to look at. Deliberately narrow — a hook that can
    reach the whole match can also change it behind the action's back."""

    match: object
    player: object
    value: int = 0
    flags: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def can_use(player, skill: str) -> bool:
    """May this player use this Skill right now?

    S3, on Distracted: "Whilst a player is Distracted, they cannot use ACTIVE
    Skills or Traits, cannot attempt to Intercept a Pass Action, and cannot
    attempt to Catch the ball."

    Active versus Passive is in the shipped catalogue, so this is the one rule the
    engine can enforce for all 108 at once — including the 81 it does not model.
    Unknown Skills are allowed rather than blocked: a catalogue that failed to load
    must not silently switch every Skill off.
    """
    if not player.has_skill(skill):
        return False
    if not getattr(player, "distracted", False):
        return True
    entry = catalogue().get(skill.casefold())
    return not (entry and entry.get("when") == "Active")


def apply_value_hook(hook: str, ctx: SkillContext, player) -> int:
    """Run every registered hook the PLAYER actually has, folding ``ctx.value``."""
    for skill, fn in hooks_for(hook):
        if player.has_skill(skill):
            fn(ctx)
    return ctx.value


# --- the two hooks every roll goes through --------------------------------
#
# A Skill that changes a roll changes one of two things: the number, or whether
# you get a second go. Rather than a hook per test — dodge_modifier,
# catch_modifier, pickup_modifier — there is ONE of each, and the test's NAME is
# in the context. Skills that apply to several tests then read as they are
# written: Nerves of Steel says "when making an Agility Test to Catch the ball, or
# when making a Passing Ability Test to Pass", and that is one function with one
# `in ("catch", "pass")`.
#
# The tests, by the name the rules use: dodge · catch · pick_up · intercept · pass


def roll_modifier(match, player, test: str, base: int = 0, **flags) -> SkillContext:
    """Every Skill modifier that applies to ``player``'s own ``test`` roll.

    Returns the whole context: ``value`` is the modifier, ``notes`` go in the log
    (a coach reading "needed 3+, rolled 2 — passed" with no explanation has been
    handed a mystery rather than an adjudication), and ``flags`` carries anything
    a once-per-turn Skill needs recorded.
    """
    ctx = SkillContext(match=match, player=player, value=base, flags={"test": test, **flags})
    for skill, fn in hooks_for("roll_modifier"):
        if can_use(player, skill):
            fn(ctx)
    # …and the sky. Three of the five Weather conditions are modifiers on named
    # tests, which is what this hook already carries, so they ride the same rail
    # rather than being sprinkled through every roll site.
    from .weather import modifier as weather_modifier
    from .weather import name_of

    sky = weather_modifier(getattr(match, "weather", "perfect"), test)
    if sky:
        ctx.value += sky
        ctx.notes.append(f"{name_of(match.weather)}: {sky} to the {test.replace('_', ' ')}")

    # …and the opposition standing nearby. Disturbing Presence belongs to OTHER
    # players, so it cannot be a hook on this one's Skills — same reason as the
    # weather, and the same rail.
    near = disturbing_presence(match, player, test)
    if near:
        ctx.value += near
        ctx.notes.append(f"Disturbing Presence: {near} from {-near} opponent(s) within {DISTURBING_RANGE} squares")
    return ctx


# PRO cannot re-roll these: "an Armour Roll, Injury Roll, Casualty roll, a roll
# made OUTSIDE OF THE PLAYER'S ACTIVATION, or any dice roll NOT MADE ON THE
# PLAYER'S BEHALF (such as Argue the Call or if the Crowd Takes Action)".
PRO_CANNOT_REROLL = ("armour", "injury", "casualty", "argue", "crowd", "intercept")


def may_reroll(match, player, test: str, **flags) -> tuple[bool, str]:
    """May ``player`` re-roll this failed ``test``, and under what name?

    Only Skills — Team Re-rolls are a separate thing the engine does not model.
    """
    ctx = SkillContext(match=match, player=player, flags={"test": test, **flags})
    for skill, fn in hooks_for("reroll"):
        if can_use(player, skill):
            fn(ctx)
            if ctx.flags.get("may_reroll"):
                return True, skill
    return False, ""


def pro_reroll(match, player, test: str, dice, rec) -> bool:
    """PRO: "During this player's activation, they may attempt to re-roll a single
    dice … To use this Skill, THE PLAYER MUST ROLL A D6: on a 3+ the dice may be
    re-rolled, on a 1-2 the dice may not be re-rolled … Once a player has ATTEMPTED
    to use this Skill, they CANNOT USE A RE-ROLL FROM ANY OTHER SOURCE to re-roll
    the dice."

    A re-roll that can fail, which is what makes it a General Skill rather than a
    good one — and attempting it burns the Team Re-roll option for that die, so it
    is asked LAST, after the free Skill re-rolls and only in place of a Team one.

    Returns True if the die may be rolled again.
    """
    if not can_use(player, "Pro") or player.pro_used:
        return False
    if any(test.casefold().startswith(x) for x in PRO_CANNOT_REROLL):
        return False
    from .dice import Roll

    d = dice.d6()
    roll = Roll(kind="Pro", dice=[d], total=d, target=3, passed=d >= 3)
    dice.rolls.append(roll)
    rec.emit(
        Event(
            kind="skill_spent",
            actor=player.id,
            rolls=[roll],
            detail={"flag": "pro_used", "skill": "Pro"},
            text=f"{player.name()} is a Pro and tries to re-roll the {test}. {roll.describe()}"
            + ("" if roll.passed else " No re-roll — and no other re-roll may be used on it either."),
        )
    )
    return bool(roll.passed)


# --- activation gates ------------------------------------------------------
#
# Five Traits share one shape: "Whenever this player is activated, AFTER DECLARING
# THEIR ACTION they must roll a D6", and on a failure something goes wrong. They
# differ only in the target, the modifier and the consequence — so they are one
# mechanism with three numbers rather than five copies of a paragraph.
#
# The consequences, all defined in the rules rather than invented here:
#   distracted      no Tackle Zone, no Active Skills, no Catch, no Intercept, and
#                   "their activation immediately ends"
#   rooted          cannot Move, cannot Follow-up, cannot be Pushed Back
#   end_activation  nothing happens; the activation is simply over
#   lash_out        an adjacent Standing team-mate is Knocked Down


def activation_gates(match, player, action: str, target=None) -> list[dict]:
    """Every gate this player must pass before performing ``action``."""
    out = []
    for skill, fn in hooks_for("activation_gate"):
        if player.has_skill(skill):  # NOT can_use: a Trait you cannot avoid
            ctx = SkillContext(match=match, player=player, flags={"action": action, "target_player": target})
            fn(ctx)
            out.append({"skill": skill, "notes": ctx.notes, **ctx.flags})
    return out


def unmodelled_skills(player) -> list[str]:
    """The Skills this player has that the engine does not implement.

    Reported so a coach is never told a clean result that quietly ignored half a
    Troll's profile.
    """
    known = modelled()
    out = []
    for s in player.player.skills or []:
        base = s.split("(")[0].strip()
        if base.casefold() not in known:
            out.append(base)
    return sorted(set(out))


# --- reporting the gap, once ----------------------------------------------
#
# "Unmodelled is reported, never ignored" is a rule about HONESTY, not about
# volume, and the two pull apart at scale. Naming an Orc Blitzer's Break Tackle on
# every step of every activation, for twenty-two players over sixteen turns, is
# not more honest than saying it once — it is the same fact several hundred times,
# and a warning that always fires is one a coach stops reading.
#
# So the gap is reported TWICE, in two different registers:
#
#   * ``unmodelled_on_pitch`` is the standing summary, recomputed from the board
#     whenever anyone asks. It cannot go stale and needs no bookkeeping, so a
#     coach can always find out what this engine is not applying.
#   * ``first_mentions`` is the running one: the first time a Skill is actually
#     relevant to something that happened, the log says so, and then never again.
#
# The ledger for the second is the LOG — the same invariant as everything else
# here. Remembering "already said that" on the object would not survive the match
# being reloaded from disk between tool calls, and would re-announce every Skill
# on every call while looking like it worked.

NOTED = "unmodelled_noted"


def unmodelled_on_pitch(match) -> list[dict]:
    """Every unmodelled Skill currently on the pitch, and who is carrying it.

    The standing answer to "what is this engine not applying?" — derived from the
    board on demand rather than recorded, so it is right after a Casualty leaves
    and right again after a new drive brings the Knocked-out back.
    """
    holders: dict[str, list[str]] = {}
    for p in match.on_pitch():
        for s in unmodelled_skills(p):
            holders.setdefault(s, []).append(p.id)
    out = []
    for skill, ids in sorted(holders.items()):
        row = {"skill": skill, "players": sorted(ids), "count": len(ids)}
        # What the rulebook calls it, so the summary itself cannot be glossed.
        # The full text is a `bb_get_skill` away rather than inline: 12 skills at
        # 400 characters each would bury the board this rides with.
        entry = catalogue().get(skill.casefold())
        if entry:
            row.update({"kind": entry["kind"], "category": entry["category"], "when": entry["when"]})
        out.append(row)
    return out


def partly_modelled_on_pitch(match) -> list[dict]:
    """Skills on the pitch that the engine applies only in part, and what it
    leaves out. The companion to ``unmodelled_on_pitch`` — a coach needs both, or
    a half-applied Skill reads as a fully applied one."""
    holders: dict[str, list[str]] = {}
    for p in match.on_pitch():
        for raw in p.player.skills or []:
            base = raw.split("(")[0].strip()
            if base.casefold() in _PARTIAL:
                holders.setdefault(base, []).append(p.id)
    return [
        {"skill": skill, "players": sorted(ids), "not_applied": _PARTIAL[skill.casefold()]}
        for skill, ids in sorted(holders.items())
    ]


def already_noted(match) -> set[str]:
    """Skills this match has already announced as unmodelled."""
    return {s for e in match.events if e.kind == NOTED for s in (e.detail.get("skills") or [])}


def first_mentions(match, skills) -> list[str]:
    """Of ``skills``, the ones this match has not mentioned yet."""
    return sorted(set(skills) - already_noted(match))


# --- the catalogue --------------------------------------------------------
#
# All 108 Skills and Traits with their real text, so a coach can QUOTE one.
#
# This is the cheap half of the fix for the failure in docs/HANDOFF.md §1. Asked
# to play a Foul, the agent drove the engine perfectly and quoted every roll — and
# then explained an unmodelled Skill unprompted, saying Break Tackle was "an
# ST-based alternative" to the dodge. It is a +1/+2/+3 MODIFIER to the Agility
# Test. The correct text existed and went unread.
#
# Modelling a Skill changes the game and has to be done one careful hook at a
# time. Shipping its TEXT changes what can honestly be said about it, costs one
# JSON file, and works for all 108 at once — including every one the engine will
# never model.


@lru_cache(maxsize=1)
def catalogue() -> dict:
    """Name (casefolded) -> entry. Empty if the catalogue is missing, because a
    plugin that cannot describe a Skill should still be able to play one."""
    try:
        raw = json.loads(CATALOGUE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k.casefold(): v for k, v in (raw.get("skills") or {}).items()}


def describe_skill(name: str) -> dict | None:
    """One Skill, as the rulebook has it, plus whether this engine applies it.

    The second half is the point. "Break Tackle: <text> — NOT applied by this
    engine" is a true sentence a coach can say; either half alone is a way to
    mislead them.
    """
    base = str(name or "").split("(")[0].strip()
    entry = catalogue().get(base.casefold())
    if entry is None:
        return None
    key = base.casefold()
    out = {**entry, "modelled": key in modelled()}
    if key in _PARTIAL:
        out["partial"] = _PARTIAL[key]
    return out


def find_skills(query: str = "", *, category: str = "", kind: str = "", only_unmodelled: bool = False) -> list[dict]:
    """Browse the catalogue. Every filter is optional; no filters lists all 108."""
    q, cat, knd = query.casefold(), category.casefold(), kind.casefold()
    out = []
    for key, entry in catalogue().items():
        if q and q not in key and q not in entry.get("text", "").casefold():
            continue
        if cat and entry.get("category", "").casefold() != cat:
            continue
        if knd and entry.get("kind", "").casefold() != knd:
            continue
        is_modelled = key in modelled()
        if only_unmodelled and is_modelled:
            continue
        row = {**entry, "modelled": is_modelled}
        if key in _PARTIAL:
            row["partial"] = _PARTIAL[key]
        out.append(row)
    return sorted(out, key=lambda e: e["name"])


# --- the Skills that are actually modelled --------------------------------
#
# Each one quotes the S3 text it implements. If a Skill is not here, the engine
# does not apply it and says so.


@skill_hook("Jump Up", "stand_up_cost")
def _jump_up(ctx: SkillContext) -> None:
    """S3: "A Prone player with this Skill can stand up for free without having
    to spend 3 squares of movement to do so." """
    ctx.value = 0
    ctx.notes.append("Jump Up: stood up for free")


@skill_hook("Dodge", "dodge_reroll")
def _dodge(ctx: SkillContext) -> None:
    """S3: "Once per Turn, this player may re-roll a single Agility Test when
    attempting to Dodge." Once per TURN, not per activation — the engine tracks
    it on the player for the turn."""
    if not ctx.flags.get("dodge_reroll_used"):
        ctx.flags["may_reroll"] = True
        ctx.notes.append("Dodge: re-rolling the failed Agility Test")


@skill_hook("Sure Feet", "rush_reroll")
def _sure_feet(ctx: SkillContext) -> None:
    """S3: "Once per Turn, this player may re-roll a single D6 when attempting to
    Rush." The Dodge Skill's twin, on the other roll a Move Action can fail — once
    per TURN, not per activation, so the flag lives on the player."""
    if not ctx.flags.get("rush_reroll_used"):
        ctx.flags["may_reroll"] = True
        ctx.notes.append("Sure Feet: re-rolling the failed Rush")


@skill_hook("Sprint", "extra_rush")
def _sprint(ctx: SkillContext) -> None:
    """S3: "When this player performs a Move Action they may attempt to Rush ONE
    ADDITIONAL TIME than they would normally be allowed to."

    Three Rushes rather than two — and it says "attempt", so the extra one is a
    third chance to trip, not a free square. Applied in move.validate, which is
    the only place the cap is enforced.
    """
    ctx.value += 1


@skill_hook("Prehensile Tail", "opponent_dodge_modifier")
def _prehensile_tail(ctx: SkillContext) -> None:
    """S3: "When an opposition player attempts to Dodge, Jump or Leap away from a
    square in this player's Tackle Zone, they apply an additional -1 modifier."

    Note this is the OPPONENT's Skill modifying our roll, which is why it hangs
    off a separate hook — the player rolling does not have it.
    """
    ctx.value -= 1
    ctx.notes.append("Prehensile Tail: -1 to the Dodge")


@skill_hook("Block", "both_down_optout")
def _block(ctx: SkillContext) -> None:
    """S3: "A player with this Skill may choose not to be Knocked Down when a Both
    Down result is applied during a Block Action that they are part of."

    Either party may have it — that is what makes Both Down a good result for a
    blocker who has Block against a target who does not.
    """
    ctx.flags["stays_up"] = True
    ctx.notes.append("Block: stays on their feet")


@skill_hook("Mighty Blow", "knockdown_bonus")
def _mighty_blow(ctx: SkillContext) -> None:
    """S3: "Whenever this player Knocks Down an opposition player during a Block
    Action, even if this player is also Knocked Down, they may apply a +1 modifier
    to either the Armour Roll or Injury Roll. This modifier may be applied after
    the roll has been made."
    """
    ctx.value += 1


# --- the Stunty Injury Table, and Thick Skull on top of it ----------------
#
# ORDER IS LOAD-BEARING HERE. Hooks run in registration order, so Stunty must
# REPLACE the table before Thick Skull ADJUSTS the result — reversed, a 7 on the
# Stunty table would come out Knocked-out when the rules say Stunned. The test
# `test_stunty_and_thick_skull_together` fails if these two are ever swapped.


@skill_hook("Stunty", "injury_outcome")
def _stunty_injury(ctx: SkillContext) -> None:
    """S3: "If an Injury Roll is made for a player with the Stunty Trait, then use
    the Stunty Injury Table below instead of the standard one."

        STUNTY INJURY TABLE — 2-6 Stunned · 7-8 Knocked-out · 9 Badly Hurt ·
        10-12 Casualty

    Badly Hurt is a Casualty for the purposes of a single match ("Remove them from
    the pitch and place them in the Casualty box"); the League consequence — an
    automatic Badly Hurt on the Casualty Table rather than a roll — belongs to the
    Casualty Roll, which this engine does not make either way.
    """
    total = ctx.value
    ctx.flags["outcome"] = "stunned" if total <= 6 else "knocked_out" if total <= 8 else "casualty"
    ctx.notes.append(f"Stunty: the Stunty Injury Table reads {total} as {ctx.flags['outcome'].replace('_', ' ')}")


@skill_hook("Thick Skull", "injury_outcome")
def _thick_skull(ctx: SkillContext) -> None:
    """S3: "When an Injury Roll is made for this player, they will only be
    Knocked-out on the roll of a 9; a roll of an 8 will be treated as a Stunned
    result. If this player also has the Stunty Trait, then they will only be
    Knocked-out on the roll of an 8; a roll of a 7 will be treated as a Stunned
    result."

    Written as "the lowest roll that still knocks them out", which is the same
    sentence for both tables and cannot drift apart the way two branches would.
    """
    ko_from = 8 if ctx.player.has_skill("Stunty") else 9
    if ctx.flags.get("outcome") == "knocked_out" and ctx.value < ko_from:
        ctx.flags["outcome"] = "stunned"
        ctx.notes.append(f"Thick Skull turns the {ctx.value} into a Stunned (Knocked-out only from {ko_from})")


# --- Skills that modify a roll --------------------------------------------


@skill_hook("Break Tackle", "roll_modifier")
def _break_tackle(ctx: SkillContext) -> None:
    """S3: "Once per Turn, when this player attempts to Dodge, they may apply a +1
    modifier to the Agility Test if they have a Strength characteristic of 3 or
    lower, a +2 modifier … if they have a Strength Characteristic of 4, or a +3
    modifier … if they have a Strength Characteristic of 5 or higher."

    A MODIFIER to the Agility Test — not, as is tempting to assume, a
    Strength-based alternative to rolling one. That exact mistake is the
    confabulation written up in docs/HANDOFF.md §1.
    """
    if ctx.flags.get("test") != "dodge" or ctx.flags.get("break_tackle_used"):
        return
    from .rules import strength_of

    st = strength_of(ctx.match, ctx.player)
    bonus = 1 if st <= 3 else 2 if st == 4 else 3
    ctx.value += bonus
    ctx.flags["break_tackle_spent"] = True
    ctx.notes.append(f"Break Tackle: +{bonus} for ST {st}")


@skill_hook("Stunty", "roll_modifier")
def _stunty_dodge(ctx: SkillContext) -> None:
    """S3: "When this player attempts to Dodge, they do not suffer any negative
    modifiers to their Agility Test for being Marked by opposition players.
    Additionally, this player applies a -1 modifier to the Agility Test when
    attempting to Intercept the ball."

    Only the MARKING penalty is cancelled, and only on a Dodge — a Stunty player
    still suffers everything else, which is why this adds back exactly the marking
    component rather than flooring the modifier at zero.
    """
    if ctx.flags.get("test") == "dodge":
        marking = int(ctx.flags.get("marking", 0))
        if marking:
            # `marking` arrives as the PENALTY, i.e. negative, and is already in
            # `value`. Cancelling it means subtracting it back out — adding it
            # doubles the very thing the Trait is meant to remove.
            ctx.value -= marking
            ctx.notes.append(f"Stunty: ignores {marking} for being Marked")
    elif ctx.flags.get("test") == "intercept":
        ctx.value -= 1
        ctx.notes.append("Stunty: -1 to Intercept")


@skill_hook("Titchy", "roll_modifier")
def _titchy(ctx: SkillContext) -> None:
    """S3: "A player with this Trait may apply a +1 modifier to the Agility Test
    when attempting to Dodge."

    (Its other half — that a Titchy player does NOT apply their own -1 for Marking
    an opponent dodging into their Tackle Zone — lives on the marker, not the
    dodger, and is applied in rules.dodge_modifier.)
    """
    if ctx.flags.get("test") == "dodge":
        ctx.value += 1
        ctx.notes.append("Titchy: +1 to the Dodge")


@skill_hook("Big Hand", "roll_modifier")
def _big_hand(ctx: SkillContext) -> None:
    """S3: "This player ignores all negative modifiers when attempting to pick up
    the ball." ALL of them, not just Marking — so this floors it at zero."""
    if ctx.flags.get("test") == "pick_up" and ctx.value < 0:
        ctx.notes.append(f"Big Hand: ignores {ctx.value} when picking up")
        ctx.value = 0


@skill_hook("Nerves of Steel", "roll_modifier")
def _nerves_of_steel(ctx: SkillContext) -> None:
    """S3: "This player may ignore any modifiers for being Marked when making an
    Agility Test to Catch the ball, or when making a Passing Ability Test to Pass
    the ball." Marked only — a Long Bomb is still a Long Bomb."""
    if ctx.flags.get("test") in ("catch", "pass"):
        marking = int(ctx.flags.get("marking", 0))
        if marking:
            ctx.value -= marking  # the penalty is already in `value`; take it back out
            ctx.notes.append(f"Nerves of Steel: ignores {marking} for being Marked")


@skill_hook("Accurate", "roll_modifier")
def _accurate(ctx: SkillContext) -> None:
    """S3: "When this player performs a Pass Action which is a Quick Pass or a
    Short Pass, this player may apply a +1 modifier to the Passing Ability Test."
    """
    if ctx.flags.get("test") == "pass" and ctx.flags.get("range") in ("Quick Pass", "Short Pass"):
        ctx.value += 1
        ctx.notes.append(f"Accurate: +1 on a {ctx.flags.get('range')}")


@skill_hook("Two Heads", "roll_modifier")
def _two_heads(ctx: SkillContext) -> None:
    """S3: "This player may apply a +1 modifier to the Agility Test whenever they
    attempt to Dodge." """
    if ctx.flags.get("test") == "dodge":
        ctx.value += 1
        ctx.notes.append("Two Heads: +1 to the Dodge")


@skill_hook("Cannoneer", "roll_modifier")
def _cannoneer(ctx: SkillContext) -> None:
    """S3: "When this player performs a Pass Action which is a LONG PASS or a LONG
    BOMB, this player may apply a +1 modifier to the Passing Ability Test."

    Accurate's mirror image: the same +1 at the other end of the ruler.
    """
    if ctx.flags.get("test") == "pass" and ctx.flags.get("range") in ("Long Pass", "Long Bomb"):
        ctx.value += 1
        ctx.notes.append(f"Cannoneer: +1 on a {ctx.flags.get('range')}")


@skill_hook("Strong Arm", "roll_modifier")
def _strong_arm(ctx: SkillContext) -> None:
    """S3: "When this player performs a THROW TEAM-MATE Action, this player may
    apply a +1 modifier to the Passing Ability Test."

    Not a Pass Action — the two have separate tests and separate distance bands,
    and a player with Strong Arm and no Throw Team-mate Trait cannot exist.
    """
    if ctx.flags.get("test") == "throwteam":
        ctx.value += 1
        ctx.notes.append("Strong Arm: +1 to the throw")


@skill_hook("Very Long Legs", "roll_modifier")
def _very_long_legs(ctx: SkillContext) -> None:
    """S3: "This player may apply a +1 modifier to the Agility Test whenever they
    attempt to LEAP OR JUMP, and may apply a +2 modifier to the Agility Test
    whenever they attempt to INTERCEPT the ball. Additionally, this player ignores
    the Cloud Burster Skill."

    Note the +2 — the Intercept clause is worth twice the other one, which is the
    difference between a roll that almost never lands and one that sometimes does.
    """
    test = ctx.flags.get("test")
    if test == "jump":
        ctx.value += 1
        ctx.notes.append("Very Long Legs: +1 to the Jump")
    elif test == "intercept":
        ctx.value += 2
        ctx.notes.append("Very Long Legs: +2 to the Intercept")


@skill_hook("Iron Hard Skin", "armour")
def _iron_hard_skin(ctx: SkillContext) -> None:
    """S3: "OPPOSITION PLAYERS CANNOT APPLY ANY MODIFIERS when making an Armour
    Roll against this player. Additionally, THE CLAWS SKILL CANNOT BE USED against
    this player."

    Registered on `armour` rather than `roll_modifier` because it belongs to the
    player being rolled AGAINST, and `roll_modifier` walks the roller's Skills.
    Applied in injury.risk_injury, which is the only place an Armour Roll is made.
    """


# Skills whose modifier belongs to somebody OTHER than the player rolling. They
# cannot be `roll_modifier` hooks — that walks the roller's own Skills — so they
# are applied in `roll_modifier` itself, on the same rail as the weather, and
# registered here so the catalogue reports them as modelled.


@skill_hook("Disturbing Presence", "nearby")
def _disturbing_presence(ctx: SkillContext) -> None:
    """S3: "Any opposition player that performs a Pass Action, Throw Team-mate
    Action or a Throw Bomb Special Action, or attempts to Intercept or Catch the
    ball, applies a -1 modifier to their Passing Ability Test or Agility Test FOR
    EACH PLAYER ON YOUR TEAM WITH THIS SKILL WITHIN 3 SQUARES of them."

    Each — they stack — and three squares is a long way: a single Beastman in the
    right place reaches most of a cage. Applied in `roll_modifier`.
    """


DISTURBING_TESTS = ("pass", "throwteam", "throw_bomb", "intercept", "catch")
DISTURBING_RANGE = 3


def disturbing_presence(match, player, test: str) -> int:
    """How much nearby Disturbing Presence costs this player's roll — 0 if none."""
    if test not in DISTURBING_TESTS or match is None:
        return 0
    n = 0
    for q in getattr(match, "players", []):
        if q.side == player.side or q.place != "pitch" or q.id == player.id:
            continue
        if not can_use(q, "Disturbing Presence"):
            continue
        if max(abs(q.x - player.x), abs(q.y - player.y)) <= DISTURBING_RANGE:
            n += 1
    return -n


@skill_hook("Leap", "roll_modifier")
def _leap(ctx: SkillContext) -> None:
    """S3: "can attempt to Leap over a single adjacent square REGARDLESS OF WHAT IS
    IN THE SQUARE. Leaping works the same way as Jumping … with the exception that
    the Leaping player may REDUCE THE NEGATIVE MODIFIERS they would receive by
    Leaping BY 1, TO A MINIMUM OF -1."

    A minimum of -1, not of 0 — a Leap is never free, however open the pitch. That
    floor is the difference between Leap and Pogo, which ignores them entirely.
    """
    if ctx.flags.get("test") == "jump" and ctx.value < 0:
        was = ctx.value
        ctx.value = min(-1, ctx.value + 1)
        if ctx.value != was:
            ctx.notes.append(f"Leap: {was} becomes {ctx.value}")


@skill_hook("Pogo", "roll_modifier")
def _pogo(ctx: SkillContext) -> None:
    """S3: "Pogoing works the same way as Jumping … with the exception that the
    Pogoing player may IGNORE ALL NEGATIVE MODIFIERS they would receive by
    Jumping." All of them, with no floor — which is what makes it a Trait and Leap
    a Skill."""
    if ctx.flags.get("test") == "jump" and ctx.value < 0:
        ctx.notes.append(f"Pogo: ignores {ctx.value}")
        ctx.value = 0


@skill_hook("Extra Arms", "roll_modifier")
def _extra_arms(ctx: SkillContext) -> None:
    """S3: "This player applies a +1 modifier to the Agility Test whenever they
    attempt to CATCH, PICK UP or INTERCEPT the ball." Three tests, one number —
    the plainest Skill in the catalogue and the most generally useful."""
    if ctx.flags.get("test") in ("catch", "pick_up", "intercept"):
        ctx.value += 1
        ctx.notes.append(f"Extra Arms: +1 to the {str(ctx.flags.get('test')).replace('_', ' ')}")


@skill_hook("Diving Catch", "roll_modifier")
def _diving_catch(ctx: SkillContext) -> None:
    """S3: "…this player may apply a +1 modifier to their Agility Test when
    attempting to Catch the ball AS PART OF A PASS ACTION IF THEY ARE IN THE TARGET
    SQUARE."

    The other half is in `ball.diving_catch`: "This player may attempt to Catch the
    ball IF IT LANDS IN A SQUARE IN THEIR TACKLE ZONE as a result of a PASS,
    THROW-IN or KICK-OFF. They may NOT use this Skill … as a result of a BOUNCE."
    Three sources, and the fourth excluded by name — which is the clause a
    paraphrase drops.
    """
    if ctx.flags.get("test") == "catch" and ctx.flags.get("target_square"):
        ctx.value += 1
        ctx.notes.append("Diving Catch: +1 in the target square")


@skill_hook("Defensive", "may_assist_while_marked")
def _defensive(ctx: SkillContext) -> None:
    """S3: "DURING YOUR OPPONENT'S TURNS, opposition players Marked by this player
    CANNOT USE THE GUARD OR PUT THE BOOT IN SKILLS."

    The counter to Guard, and it reads backwards from every other Skill: it is a
    Skill on the MARKER that switches off a Skill on the player being asked about.
    Applied in rules.assist_count beside the two it cancels.
    """


@skill_hook("Timmm-ber!", "roll_modifier")
def _timber(ctx: SkillContext) -> None:
    """S3: "If this player has AN MA OF 2 OR LESS and attempts to stand up, apply a
    +1 modifier to the roll for standing up FOR EACH OPEN STANDING TEAM-MATE
    ADJACENT to this player. A roll of a natural 1 will still fail as normal."

    Only a player with MA 2 or less ever rolls to stand at all, so the condition
    is really a reminder rather than a filter — but it is in the text, and a Skill
    on an MA 3 player doing nothing is the sort of thing a coach asks about.

    OPEN, so a team-mate pinned by a Tackle Zone is no help: they are busy.
    """
    if ctx.flags.get("test") != "stand_up" or ctx.player.movement() > 2:
        return
    from .rules import adjacent, is_open

    match = ctx.match
    helpers = [
        q
        for q in match.on_pitch(ctx.player.side)
        if q.id != ctx.player.id and adjacent(q.x, q.y, ctx.player.x, ctx.player.y) and is_open(match, q)
    ]
    if helpers:
        ctx.value += len(helpers)
        ctx.notes.append(f"Timmm-ber!: +{len(helpers)} from team-mates hauling them up")


@skill_hook("Decay", "casualty")
def _decay(ctx: SkillContext) -> None:
    """S3: "Apply a +1 modifier to any Casualty Roll made against this player."
    Against — so it belongs to the player being rolled for, not to whoever put
    them there, and a higher D16 is a worse result."""


@skill_hook("Regeneration", "casualty")
def _regeneration(ctx: SkillContext) -> None:
    """S3: "Whenever this player suffers a Casualty, BEFORE MAKING THE CASUALTY
    ROLL for them, roll a D6. On a 1-3, this player suffers the Casualty … On a
    4+, this player REGENERATES and ignores the Casualty … and is instead placed
    in their team's RESERVES BOX." """


@skill_hook("Hail Mary Pass", "pass")
def _hail_mary(ctx: SkillContext) -> None:
    """S3: "they may declare ANY SQUARE ON THE PITCH as the target square RATHER
    THAN USING THE RANGE RULER. Make a Passing Ability Test as normal TREATING THE
    THROW AS A LONG BOMB, and treating ANY RESULT OF AN ACCURATE PASS AS AN
    INACCURATE PASS. A Hail Mary Pass CANNOT BE INTERCEPTED."

    Three separate things, and the third is what makes it worth the -3: nobody
    gets to try to take it out of the air. The second is what stops it being a
    better Long Bomb — it ALWAYS scatters, so it is a way of moving the ball a
    long way rather than of putting it in somebody's hands.

    Note the first applies even to a square the ruler could reach: "treating the
    throw as a Long Bomb", with no exception for a short one.
    """


@skill_hook("Cloud Burster", "pass")
def _cloud_burster(ctx: SkillContext) -> None:
    """S3: "When this player performs a Pass Action, opposition players MAY NOT
    ATTEMPT TO INTERCEPT the ball." The thrower's Skill shutting off the
    defender's roll — Very Long Legs is the counter, and ignores it."""


@skill_hook("My Ball", "possession")
def _my_ball(ctx: SkillContext) -> None:
    """S3: "…may not willingly give up the ball … may not declare Pass Actions,
    Hand-off Actions, or use any other Skill or Trait that would allow them to
    relinquish possession." Refused at declaration, with the reason."""


@skill_hook("No Ball", "possession")
def _no_ball(ctx: SkillContext) -> None:
    """S3: "A player with this Trait MAY NEVER HAVE POSSESSION of the ball. If this
    player would be required to attempt to Catch or Pick-up the Ball, they will
    AUTOMATICALLY FAIL to do so AS IF THEY HAD ROLLED A NATURAL 1. A player with
    this Trait may not attempt to Intercept a Pass."

    "As if they had rolled a natural 1" matters: it is a failure that no re-roll
    can rescue, not a hard target.
    """


@skill_hook("Unsteady", "possession")
def _unsteady(ctx: SkillContext) -> None:
    """S3: "This player may not declare Secure the Ball Actions." """


@skill_hook("Steady Footing", "footing")
def _steady_footing(ctx: SkillContext) -> None:
    """S3: "Whenever this player would be Knocked Down or Fall Over, roll a D6. On
    a 6, this player does NOT get Knocked Down or Fall Over. If this happens
    during their activation, they may continue their activation as normal AND NO
    TURNOVER WILL BE CAUSED."

    Not an Armour Roll saved — the knock-down never happens, so there is no
    Injury Roll, no dropped ball and no Turnover.
    """


# --- Skills that grant a re-roll ------------------------------------------


@skill_hook("Pro", "pro")
def _pro(ctx: SkillContext) -> None:
    """S3: "During this player's activation, they may attempt to re-roll a SINGLE
    DICE … the player must roll a D6: ON A 3+ the dice may be re-rolled … The Skill
    CANNOT be used to re-roll a dice made as part of an ARMOUR ROLL, INJURY ROLL,
    CASUALTY roll, a roll made OUTSIDE OF THE PLAYER'S ACTIVATION, or any dice roll
    NOT MADE ON THE PLAYER'S BEHALF … Once a player has ATTEMPTED to use this
    Skill, they cannot use a re-roll from ANY OTHER SOURCE to re-roll the dice."

    A re-roll that can fail, and attempting it burns the Team Re-roll option for
    that die — so it is asked LAST, after the free Skill re-rolls and in place of
    a Team Re-roll rather than before one. See `pro_reroll`.
    """


@skill_hook("Sure Hands", "reroll")
def _sure_hands(ctx: SkillContext) -> None:
    """S3: "This player may re-roll the D6 when attempting to pick up the ball,
    though not when making a Secure the Ball Action."

    The exclusion is the whole point of Secure the Ball — it is already a flat 2+
    bought by giving up the rest of the activation.
    """
    if ctx.flags.get("test") == "pick_up" and not ctx.flags.get("securing"):
        ctx.flags["may_reroll"] = True


@skill_hook("Catch", "reroll")
def _catch(ctx: SkillContext) -> None:
    """S3: "This player may re-roll any failed Agility Test when attempting to
    Catch the ball." """
    if ctx.flags.get("test") == "catch":
        ctx.flags["may_reroll"] = True


@skill_hook("Pass", "reroll")
def _pass(ctx: SkillContext) -> None:
    """S3: "This player may re-roll any failed Passing Ability Test when performing
    a Pass Action." """
    if ctx.flags.get("test") == "pass":
        ctx.flags["may_reroll"] = True


# --- Skills that change where a push goes, or whether it happens ----------
#
# These four are applied in engine/actions/block.py rather than through a value
# hook, because a push is a POSITION rather than a number and the arc they widen
# is board geometry. They register here so the catalogue reports them as modelled
# and so this file stays the one place that lists what the engine applies.


@skill_hook("Stand Firm", "push")
def _stand_firm(ctx: SkillContext) -> None:
    """S3: "When this player would be Pushed Back during a Block Action, including
    during a Chain Push, they can choose to not be Pushed Back and instead remain
    in their current square."

    A CHOICE, and there is only one coach at the table — so the engine takes it in
    the one case where the alternative is unambiguously worse (the Crowd, which is
    an Injury Roll with no armour behind it) and says so in the log otherwise
    rather than guessing on the defence's behalf. See block._do_push.
    """


@skill_hook("Sidestep", "push")
def _sidestep(ctx: SkillContext) -> None:
    """S3: "Whenever this player is Pushed Back for any reason, then instead of the
    opposing Coach choosing where this player is Pushed Back to, this player's
    Coach may choose any adjacent unoccupied square … If there are no adjacent
    unoccupied squares, then this Skill cannot be used."

    Played for the pushed player as "the square furthest from the blocker", which
    is the one thing a coach being shoved always wants. Suppressed by Grab.
    """


@skill_hook("Grab", "push")
def _grab(ctx: SkillContext) -> None:
    """S3: "When this player declares a Block Action, if the opposition player is
    Pushed Back, then this player's Coach may choose any unoccupied square adjacent
    to the target … Additionally, when this player performs a Block Action,
    opposition players cannot use the Sidestep Skill."

    Belongs to the acting coach, so it simply widens what ``push_to`` may name.
    """


@skill_hook("Fend", "push")
def _fend(ctx: SkillContext) -> None:
    """S3: "When a player with this Skill is Pushed Back as a result of a Block
    Action performed against them, then the opposition player may not Follow-up."

    Not optional and not overridable by the acting coach's ``follow_up`` — the
    only thing that beats it is a Juggernaut mid-Blitz.
    """


@skill_hook("Juggernaut", "push")
def _juggernaut(ctx: SkillContext) -> None:
    """S3: "when this player performs a Block Action as part of a Blitz Action,
    opposition players cannot use the Fend, Stand Firm or Wrestle Skills."

    Only the suppression half is modelled. Its other clause — "they may treat any
    result of Both Down as Pushed Back during any Block Actions they perform
    during the Blitz Action" — is a choice on a result the engine does not offer a
    choice on yet, and Wrestle is not modelled at all, so both are still reported.
    """


# --- the activation gates -------------------------------------------------


def _blitzy(ctx: SkillContext) -> bool:
    """Did they declare a Block or a Blitz? Three gates give +2 for it."""
    return ctx.flags.get("action") in ("block", "blitz")


@skill_hook("Bloodlust", "activation_gate")
def _bloodlust(ctx: SkillContext) -> None:
    """S3: "…they must roll a D6, ADDING 1 TO THE ROLL if they declared a Block
    Action or a Blitz Action. If they roll EQUAL TO OR HIGHER THAN the number shown
    in brackets, they may activate as normal … If this player DOES NOT BITE a
    Thrall Lineman for any reason, then A TURNOVER IS CAUSED, this player becomes
    DISTRACTED, and will IMMEDIATELY DROP THE BALL if they were holding it. If this
    player was in the opposing End Zone, NO TOUCHDOWN IS SCORED."

    The one gate with a MODIFIER that depends on the declared Action, which is why
    the gate mechanism carries `action` in its flags at all.

    The BITE is real: "at the end of their activation, this player MAY BITE AN
    ADJACENT THRALL LINEMAN team-mate REGARDLESS OF THE STATUS of the Thrall
    Lineman … immediately make an Injury Roll for [them], treating any Casualty
    result as BADLY HURT; this will not cause a Turnover UNLESS the Thrall Lineman
    was holding the ball." Thrall Lineman is a KEYWORD, and the Vampire roster
    prints it — so the engine bites when there is one adjacent and applies the
    failure when there is not, which is what the rule asks for either way.
    """
    import re as _re

    target = 4
    for raw in ctx.player.player.skills or []:
        if raw.split("(")[0].strip().casefold() == "bloodlust":
            m = _re.search(r"\d+", raw)
            target = int(m.group(0)) if m else 4
    ctx.flags.update(
        target=target,
        modifier=1 if ctx.flags.get("action") in ("block", "blitz") else 0,
        on_fail="distracted",
    )
    ctx.notes.append("Bloodlust — and no Thrall Lineman to bite")


@skill_hook("Animosity", "activation_gate")
def _animosity(ctx: SkillContext) -> None:
    """S3: "Whenever this player attempts to perform A PASS ACTION OR A HAND-OFF
    ACTION to a team-mate WITH THE SAME KEYWORD as the one shown in brackets, roll
    a D6. ON A 1, the player REFUSES to perform the action and their activation
    immediately ends. Some players may have the ANIMOSITY (ALL) Trait, in which
    case they will apply this rule to ALL of their team-mates, REGARDLESS of the
    Keywords they have."

    The keyword IS checked: it was in `data/rosters.json` all along under `role`,
    which is the parenthesised list the scraper captured after each position name.
    So a Goblin's Animosity (Human) refuses to pass to the Human on the team and
    not to the other Goblins, which is the whole flavour of it.
    """
    from .rules import shares_keyword, trait_parameter

    if ctx.flags.get("action") not in ("pass", "handoff"):
        ctx.flags.update(skip=True)
        return
    mate = ctx.flags.get("target_player")
    wanted = trait_parameter(ctx.player, "Animosity")
    if mate is not None and not shares_keyword(ctx.player, mate, wanted):
        ctx.flags.update(skip=True)
        return
    ctx.flags.update(target=2, modifier=0, on_fail="end_activation")
    ctx.notes.append(f"Animosity ({wanted or 'all'}) — they may refuse")


@skill_hook("Bone Head", "activation_gate")
def _bone_head(ctx: SkillContext) -> None:
    """S3: "Whenever this player is activated, after declaring their Action they
    must roll a D6. On a 2+, the player may perform the declared Action as normal.
    On a 1, the player becomes Distracted." """
    ctx.flags.update(target=2, modifier=0, on_fail="distracted")


@skill_hook("Really Stupid", "activation_gate")
def _really_stupid(ctx: SkillContext) -> None:
    """S3: "…they must roll a D6. They may apply a +2 modifier to the roll if they
    have any Standing team-mates who are not Distracted, AND DO NOT HAVE THE
    REALLY STUPID TRAIT, adjacent to them. On a 4+, the player may perform the
    declared Action as normal. On a 1-3, this player becomes Distracted."

    Two Really Stupid players propping each other up is exactly what the rule
    excludes, and it is the clause a paraphrase drops.
    """
    from .rules import adjacent, has_tackle_zone

    p = ctx.player
    helpers = [
        q
        for q in ctx.match.on_pitch(p.side)
        if q.id != p.id and has_tackle_zone(q) and not q.has_skill("Really Stupid") and adjacent(q.x, q.y, p.x, p.y)
    ]
    ctx.flags.update(target=4, modifier=2 if helpers else 0, on_fail="distracted")
    if helpers:
        ctx.notes.append(f"+2 — {helpers[0].name()} is next to them")


@skill_hook("Take Root", "activation_gate")
def _take_root(ctx: SkillContext) -> None:
    """S3: "…IF THEY ARE STANDING they must roll a D6. On a 2+, the player may
    perform the declared Action as normal. On a 1, the player becomes Rooted." """
    if ctx.player.down != "standing":
        ctx.flags.update(skip=True)
        return
    ctx.flags.update(target=2, modifier=0, on_fail="rooted")


@skill_hook("Unchannelled Fury", "activation_gate")
def _unchannelled_fury(ctx: SkillContext) -> None:
    """S3: "…They may apply a +2 modifier to the roll if they have declared a Block
    Action or a Blitz Action. On a 4+, the player may perform the declared Action
    as normal. On a 1-3, this player rages incoherently but nothing really happens.
    Their activation immediately ends." """
    ctx.flags.update(target=4, modifier=2 if _blitzy(ctx) else 0, on_fail="end_activation")


@skill_hook("Animal Savagery", "activation_gate")
def _animal_savagery(ctx: SkillContext) -> None:
    """S3: "…+2 … if they have declared a Block Action or a Blitz Action. On a 4+,
    the player may perform the declared action as normal. On a 1-3, this player
    lashes out at one of their team-mates. Choose one Standing team-mate adjacent
    to this player; the chosen player is immediately Knocked Down. This will not
    cause a Turnover unless the player was holding the ball. … If this player rolls
    a 1-3 and there are no Standing team-mates adjacent to them, then they are
    Distracted."
    """
    ctx.flags.update(target=4, modifier=2 if _blitzy(ctx) else 0, on_fail="lash_out")


@skill_hook("Stab", "special_action")
def _stab(ctx: SkillContext) -> None:
    """S3: "select a Standing opposition player adjacent to this player and make an
    Armour Roll for the selected player. THIS ARMOUR ROLL CANNOT BE MODIFIED IN
    ANY WAY. If the player's Armour is broken, make an Injury Roll for them."

    Unmodifiable is why it passes no responsible player — Mighty Blow and Claws
    both hang off one, and letting either through would modify the unmodifiable.
    """


@skill_hook("Punt", "special_action")
def _punt(ctx: SkillContext) -> None:
    """S3: "…they can Punt it downfield. Position the Throw-in Template … Roll a D6
    to determine the DIRECTION … and then a SECOND D6 to determine HOW MANY SQUARES
    … NO TURNOVER is caused if the ball comes to rest ON THE GROUND; however, if
    after the Punt Special Action is resolved the ball is in possession of AN
    OPPOSITION PLAYER, or IN THE CROWD, a Turnover IS caused."

    Which makes it a way of clearing your own half rather than a way of scoring:
    the ball landing loose downfield costs nothing, and only handing it over does.
    """


@skill_hook("Hypnotic Gaze", "special_action")
def _hypnotic_gaze(ctx: SkillContext) -> None:
    """S3: "they select a Standing opposition player ADJACENT to them and roll a
    D6. On a 1-2, nothing happens and this player's activation immediately ends.
    On a 3+, the selected opposition player becomes DISTRACTED and this player's
    activation immediately ends."

    Either way the activation ends, which is what makes it a real cost: not a free
    debuff, a whole player's turn spent on one. There is no once-per-turn limit —
    "there is no limit to the number of players that can declare this Special
    Action each Turn" — which is unusual enough to be worth saying.
    """


@skill_hook("Projectile Vomit", "special_action")
def _projectile_vomit(ctx: SkillContext) -> None:
    """S3: "roll a D6. On a 2+ … Make an Armour Roll for the targeted player … On a
    1, this player covers THEMSELVES in acidic bile; make an Armour Roll for THIS
    player." The roll is made for whoever it landed on, which can be the thrower."""


@skill_hook("Breathe Fire", "special_action")
def _breathe_fire(ctx: SkillContext) -> None:
    """S3: "roll a D6, applying a -1 modifier if the target has an ST of 5 or
    higher. On a 1, this player is immediately Knocked Down. On a 2-3, nothing
    happens. On a 4+, the opposition player is immediately Placed Prone. If the
    roll is a NATURAL 6, the opposition player is Knocked Down instead."

    Natural, so the -1 for a large target cannot take the knock-down away — and
    Placed Prone risks no harm while Knocked Down does, so the two outcomes on 4+
    and a natural 6 are genuinely different."""


@skill_hook("Chainsaw", "special_action")
def _chainsaw(ctx: SkillContext) -> None:
    """S3: "roll a D6. On a 2+, this player immediately makes an Armour Roll
    against one opposition player they are Marking, applying a +3 modifier … On a
    1, the Chainsaw will Kick-back and this player is Knocked Down instead. If
    this player is Knocked Down or Falls Over FOR ANY REASON … a +3 modifier is
    applied when the opposition Coach makes an Armour Roll for this player. THIS
    +3 MODIFIER MUST ALWAYS BE APPLIED. … this player may also use their chainsaw
    when performing a Foul Action, in which case they may apply a +3 modifier."

    Three clauses, and two of them are passive — carrying a chainsaw is dangerous
    to its owner, which is the trade the Trait exists to make."""


@skill_hook("Monstrous Mouth", "special_action")
def _monstrous_mouth(ctx: SkillContext) -> None:
    """S3: "roll a D6. On a 1-2 nothing happens. On a 3+, the opposition player is
    considered to be Chomped. Whilst Chomped, the opposition player cannot leave
    the square they are in whilst this player remains Marking them. THIS CONDITION
    ENDS IMMEDIATELY if this player is no longer Marking the opposition player for
    any reason."

    "For any reason" is why it is asked of the live board rather than remembered
    as a flag — being knocked down, pushed away or sent off would none of them
    think to clear one."""


@skill_hook("Ball & Chain", "special_action")
def _ball_and_chain(ctx: SkillContext) -> None:
    """S3: "the ONLY action they can declare is a Ball & Chain Special Action …
    position the Throw-in Template over this player so it faces one of the two End
    Zones or either Sideline. Then roll a D6 and move this player into the square
    as indicated … A player that moves in this manner does not have to make an
    Agility Test to Dodge away from another player's Tackle Zone; THEY WILL
    AUTOMATICALLY PASS … can move a number of squares up to their MA."

    Movement without agency: the coach picks the facing, the die picks the square.
    Free Dodges are what make the Trait survivable at all."""


@skill_hook("Bombardier", "special_action")
def _bombardier(ctx: SkillContext) -> None:
    """S3: "they throw a bomb IN THE SAME MANNER as when a player performs a Pass
    Action … Though this is NOT a Pass Action itself … may not perform a Move
    Action before throwing the bomb … When a bomb explodes, any player in the
    square it exploded in is hit. Additionally, roll a D6 for each player adjacent
    … On a 4+, they are hit. Any Standing player that is hit is immediately
    Knocked Down. Additionally, make an Armour Roll for any Prone or Stunned
    players hit."

    The catch-and-throw-it-back chain is reported rather than followed: it needs a
    coach at the other end, which is the one thing this engine cannot supply."""


@skill_hook("Kick", "kick")
def _kick(ctx: SkillContext) -> None:
    """S3: "If this player is nominated as the kicking player, then when kicking
    Deviates this player's Coach may choose for it to ONLY DEVIATE D3 SQUARES
    rather than the usual D6."

    Halving the scatter is the whole Skill — a kick that lands where you meant it
    to is worth more than any modifier on the ball.
    """


@skill_hook("Secret Weapon", "drive_end")
def _secret_weapon(ctx: SkillContext) -> None:
    """S3: "At the end of a Drive in which this player took part, EVEN IF THEY ARE
    NOT ON THE PITCH at the end of the Drive, they are Sent-off FOR COMMITTING A
    FOUL."

    "As if they had committed a Foul" is doing real work: it means they may still
    Argue the Call, and it goes through the Foul's own sending-off rather than a
    second implementation. See game._deal_with_secret_weapons.
    """


@skill_hook("Insignificant", "draft")
def _insignificant(ctx: SkillContext) -> None:
    """S3: "When creating a Team Draft List, you may not include more players with
    this Trait than players without this Trait."

    The nearest thing this engine has to a Draft List is the BOARD, so the
    constraint is checked against the board and REPORTED by `Scenario.review` —
    which is exactly what the practice board does with the four Set-up limits. A
    drafting rule enforced on the only list the engine keeps.
    """


@skill_hook("Kick Team-mate", "action")
def _kick_team_mate(ctx: SkillContext) -> None:
    """S3: "works exactly the same as a Throw Team-mate Action, with the following
    exceptions: Performing a Kick Team-mate Special Action DOES NOT COUNT as a
    team's Throw Team-mate Action for the Turn … However, if a Kick Team-mate
    Special Action results in a Fumbled Throw, immediately make an Injury Roll for
    the team-mate being kicked, TREATING ANY RESULT OF STUNNED AS KNOCKED OUT."

    Kicking somebody is worse for them than throwing them, which is the whole
    point of the distinction."""


@skill_hook("Throw Team-mate", "action")
def _throw_team_mate(ctx: SkillContext) -> None:
    """S3: "This player may declare the Throw Team-mate Action." The permission
    IS the Trait — see actions/throwteam.py."""


@skill_hook("Right Stuff", "action")
def _right_stuff(ctx: SkillContext) -> None:
    """S3: "This player can be thrown by a team-mate with the Throw Team-mate
    Trait, EVEN IF THIS PLAYER IS PRONE." Being on the floor is explicitly no
    obstacle to being thrown — though it does mean an automatic fail on landing."""


@skill_hook("Always Hungry", "action")
def _always_hungry(ctx: SkillContext) -> None:
    """S3: "Whenever this player performs a Throw Team-mate Action, BEFORE making
    the Passing Ability Test, they must roll a D6. On a 2+ … as normal. On a 1,
    the player will attempt to eat their team-mate … On a 2+, the team-mate will
    squirm free and the Throw Team-mate Action will automatically result in a
    Fumbled Throw. On a 1, the player will eat their team-mate — immediately
    remove them from your Team Draft List. No Apothecary can be used to save them,
    and no Regeneration rolls can be attempted." """


@skill_hook("Lethal Flight", "action")
def _lethal_flight(ctx: SkillContext) -> None:
    """S3: "When this player is thrown as part of a Throw Team-mate Action, if they
    land in a square that contains an opposition player … AND THE OPPOSITION PLAYER
    IS KNOCKED DOWN, then they may apply a +1 modifier to EITHER the Armour Roll or
    Injury Roll."

    The thrown player's Skill, spent on the player they landed on — the same shape
    as Mighty Blow and Arm Bar, so it rides the same `bonus`.
    """


@skill_hook("Swoop", "action")
def _swoop(ctx: SkillContext) -> None:
    """S3: "…they may CHOOSE NOT TO SCATTER before landing as normal. If they do,
    position the Throw-in Template over this player … Roll a D6 to determine the
    direction this player will travel, and then a second die … Additionally, if
    they choose not to Scatter as normal, this player MAY RE-ROLL THE AGILITY TEST
    when attempting to land."

    One roll of each rather than three D8 steps, so a Swooping player travels
    further in one direction rather than staggering — and the landing re-roll is
    the half that makes it worth taking.
    """


@skill_hook("Bullseye", "action")
def _bullseye(ctx: SkillContext) -> None:
    """S3: "if the result of the throw is a Superb Throw then the thrown player
    will not Scatter before landing and will instead land in the target square." """


@skill_hook("Leader", "team_reroll")
def _leader(ctx: SkillContext) -> None:
    """S3: "A team that has one or more players with this Skill ON THE PITCH at the
    start of a half may gain A SINGLE EXTRA Team Re-roll … A team can only use a
    Leader Re-roll IF THEY HAVE A PLAYER WITH THE LEADER SKILL ON THE PITCH, and
    if ALL players with this Skill are removed from play … THEN IT IS LOST."

    A single extra, however many Leaders, and it evaporates the moment the last one
    leaves — so it is computed from the board rather than banked as a number, and
    the bought Re-rolls are spent first because those are the ones that survive.
    """


@skill_hook("Loner", "team_reroll")
def _loner(ctx: SkillContext) -> None:
    """S3: "Whenever this player wishes to use a Team Re-roll, they must roll a D6.
    If they roll equal to or higher than the number shown in brackets, then they
    may use the Team Re-roll as normal. If they roll lower than the number shown
    in brackets, then they may not re-roll the dice and THE TEAM RE-ROLL IS LOST
    just as if it had been used."

    Losing it either way is the whole cost of the Trait, and the half that would
    be easy to skip — a Loner who fails has spent the re-roll and got nothing.
    Applied in engine/rerolls.py, where the number in the brackets is read off the
    player's own skill string (`Loner (4+)`) rather than assumed.
    """


@skill_hook("Drunkard", "roll_modifier")
def _drunkard(ctx: SkillContext) -> None:
    """S3: "This player applies a -1 modifier to test whenever they attempt to
    Rush." """
    if ctx.flags.get("test") == "rush":
        ctx.value -= 1
        ctx.notes.append("Drunkard: -1 to the Rush")


@skill_hook("Wrestle", "block_result")
def _wrestle(ctx: SkillContext) -> None:
    """S3: "When this player performs a Block Action, or is the target of a Block
    Action, if the Both Down result is applied, this player may choose to use this
    Skill. If they do, both players in the Block Action are Placed Prone,
    regardless of any other Skills they may possess."

    PLACED PRONE, not Knocked Down — "they aren't at risk of being caused harm",
    so neither player rolls armour. Applied by whichever participant would
    otherwise be Knocked Down; see block._both_down_choice.
    """


@skill_hook("Brawler", "block_result")
def _brawler(ctx: SkillContext) -> None:
    """S3: "When this player declares a Block Action, they may re-roll a single
    Both Down result."

    A single one, and it re-rolls the DICE rather than reinterpreting the result.
    "Declares", so a Blitz switches it off — see block.declared_a_block.
    """


@skill_hook("Dauntless", "block_result")
def _dauntless(ctx: SkillContext) -> None:
    """S3: "When a player with this Skill performs a Block Action against an
    opposition player with a higher Strength Characteristic (before any modifiers
    are applied to either player), this player may roll a D6 and add their own
    Strength Characteristic. If the result is higher than the opposition player's
    unmodified Strength Characteristic, then this player increases their unmodified
    Strength Characteristic to MATCH the opposition player for the duration of the
    Block Action. Modifiers are then applied as normal."

    Matches, never exceeds — and the assists are then re-counted against the new
    number. Because it is a ROLL, `validate` reports that it is coming rather than
    making it: validate is asked freely and must never touch the dice.
    """


@skill_hook("Horns", "block_result")
def _horns(ctx: SkillContext) -> None:
    """S3: "Whenever this player declares a Blitz Action, then they apply a +1
    modifier to their Strength Characteristic for any Block Actions performed
    during that Blitz Action." Only on a Blitz, and deterministic — so it is in
    `validate` too, and the odds a coach is shown are the odds they get.
    """


@skill_hook("Claws", "block_result")
def _claws(ctx: SkillContext) -> None:
    """S3: "Whenever an Armour Roll is made for an opposition player that has been
    Knocked Down by this player during a Block Action, even if this player is also
    Knocked Down, then any roll of a NATURAL 8+ on the Armour Roll will break the
    opposition player's armour regardless of their actual Armour Value."

    It lowers the bar to 8 rather than adding to the roll — which is everything
    against AV 10+ and nothing against AV 8+. Natural, so Mighty Blow's +1 cannot
    manufacture one.
    """


@skill_hook("Tackle", "deny_dodge_skill")
def _tackle(ctx: SkillContext) -> None:
    """S3: "When an opposition player attempts to Dodge away from a square in this
    player's Tackle Zone, they cannot use the Dodge Skill. Additionally, when this
    player performs a Block Action against an opposition player, the opposition
    player does not count as having the Dodge Skill if a Stumble result is
    selected."

    Both halves are the same sentence — "you do not have Dodge against me" — so
    both call this hook, and the caller says which case it is asking about. Note
    it is the square being LEFT that matters for the first half, not the one being
    entered.
    """
    ctx.flags["denied"] = True
    ctx.notes.append("Tackle: the Dodge Skill does not apply")


@skill_hook("Guard", "may_assist_while_marked")
def _guard(ctx: SkillContext) -> None:
    """S3: "This player can provide Offensive and Defensive Assists when a player
    performs a Block Action regardless of how many opposition players are Marking
    this player." """
    ctx.flags["assists_anyway"] = True


# --- Skills about where the ball ends up ---------------------------------


@skill_hook("Safe Pass", "ball")
def _safe_pass(ctx: SkillContext) -> None:
    """S3: "If this player rolls A NATURAL 1 when making a Passing Ability Test,
    then it will not result in a Fumbled Pass. Instead, the player RETAINS
    POSSESSION of the ball and their activation immediately ends. NO TURNOVER is
    caused."

    Natural 1 only. A fumble that came from modifiers — "if the Passing Ability
    Test is a 1 AFTER MODIFIERS" — is still a fumble, and the rule is careful
    about the difference.
    """


@skill_hook("Give and Go", "ball")
def _give_and_go(ctx: SkillContext) -> None:
    """S3: "If this player performs a Pass Action that is A QUICK PASS, or performs
    a HAND-OFF Action, then, SO LONG AS A TURNOVER ISN'T CAUSED, their activation
    does not end … they may continue with their Move Action using any movement
    they have remaining." Quick Foul's cousin on the other half of the pitch."""


@skill_hook("Safe Pair of Hands", "ball")
def _safe_pair_of_hands(ctx: SkillContext) -> None:
    """S3: "If this player would be Knocked Down, Fall Over or be Placed Prone
    WHILST IN POSSESSION OF THE BALL then, BEFORE THEY BECOME PRONE, they may
    place the ball in any adjacent unoccupied square … INSTEAD OF BOUNCING the
    ball as normal."

    Placed, not bounced: not a scatter with better odds, a choice of square.
    """


@skill_hook("Strip Ball", "ball")
def _strip_ball(ctx: SkillContext) -> None:
    """S3: "…if an opposition player is Pushed Back then they will DROP THE BALL IN
    THE SQUARE THEY ARE PUSHED BACK INTO, at which point it will Bounce from that
    square. This Bounce will happen BEFORE the opposition player becomes Prone (if
    applicable) but AFTER this player chooses to Follow-up."

    One of the few orderings the rules spell out, so the bounce is deferred to
    after the Follow-up rather than happening where it reads most naturally.
    """


@skill_hook("Fumblerooski", "ball")
def _fumblerooski(ctx: SkillContext) -> None:
    """S3: "When this player performs a Move Action whilst they are in possession
    of the ball, they may choose to PLACE the ball on the ground in any square
    they MOVE OUT OF during their Move Action. THIS WILL NOT CAUSE A TURNOVER."

    A choice, so `bb_game_act(action="move", drop_ball=True)` asks for it rather
    than the engine deciding to put the ball down on somebody's behalf.
    """


# --- The Foul Action's own Skills ----------------------------------------
#
# Five Devious Skills, all applied in engine/actions/foul.py. They register here
# so the catalogue reports them as modelled and so this file stays the one list of
# what the engine applies.


@skill_hook("Dirty Player", "foul")
def _dirty_player(ctx: SkillContext) -> None:
    """S3: "When this player performs a Foul Action, they may apply a +1 modifier
    to EITHER the Armour Roll or Injury Roll. This modifier MAY BE APPLIED AFTER
    THE ROLL HAS BEEN MADE."

    The same shape as Mighty Blow, so it is spent the same way: on the Armour Roll
    only when that is what breaks it, and on the Injury Roll otherwise.
    """


@skill_hook("Lone Fouler", "foul")
def _lone_fouler(ctx: SkillContext) -> None:
    """S3: "When this player performs a Foul Action, IF THERE ARE NO PLAYERS
    PROVIDING AN OFFENSIVE OR DEFENSIVE ASSIST, then this player may re-roll a
    failed Armour Roll." Nobody at all, on either side."""


@skill_hook("Sneaky Git", "foul")
def _sneaky_git(ctx: SkillContext) -> None:
    """S3: "This player is not Sent-off when performing a Foul Action if a natural
    double is rolled for the ARMOUR Roll, SO LONG AS THE TARGET PLAYER'S ARMOUR IS
    NOT BROKEN. If the target player's Armour is broken, this player will still be
    sent off as normal."

    Both halves matter. The second is what stops it being a free Foul: it protects
    exactly the Fouls that achieved nothing.
    """


@skill_hook("Quick Foul", "foul")
def _quick_foul(ctx: SkillContext) -> None:
    """S3: "This player's activation DOES NOT END after performing a Foul Action,
    and they may continue with their Move Action with any movement they have
    remaining." A Foul normally ends the activation outright."""


@skill_hook("Put the Boot In", "may_assist_a_foul")
def _put_the_boot_in(ctx: SkillContext) -> None:
    """S3: "This player can provide OFFENSIVE Assists when a team-mate performs a
    Foul Action REGARDLESS OF HOW MANY OPPOSITION PLAYERS ARE MARKING THIS PLAYER."

    Guard's cousin, narrowed to one Action and one direction — it does nothing for
    the side being fouled, and nothing on a Block. Applied in rules.assist_count.
    """


# --- The Skills that fire when an opponent leaves your Tackle Zone --------
#
# One mechanism seen from four angles, implemented in engine/leaving.py and
# applied by the Move Action in the rules' own order. They register here so the
# catalogue reports them as modelled and so this file stays the one list of what
# the engine applies.


# --- The Skills that make a Block into more than one thing ----------------


@skill_hook("Violent Innovator", "scoring")
def _violent_innovator(ctx: SkillContext) -> None:
    """S3: "If an opposition player suffers a Casualty as a result of a Special
    Action this player performed, this player WILL EARN STAR PLAYER POINTS for
    causing a Casualty as appropriate."

    It matters because SPP are EARNED during a game, whatever they are spent on
    after one: "it's important to keep track of every time a player does something
    that generates SPP DURING A GAME." The default is that a Casualty only counts
    when it came from a Block Action — "other methods, such as SPECIAL ACTIONS or
    by Injury by the Crowd, do not generate SPP" — and this Skill is the single
    exception to that sentence. See `engine/spp.py`.
    """


@skill_hook(
    "Plague Ridden",
    "scoring",
    partial="the POST-GAME half — 'during the Post-game Sequence, this player may be hired in "
    "the same manner as any Journeyman', which needs a league and a Treasury",
)
def _plague_ridden(ctx: SkillContext) -> None:
    """S3: "Once per game, when a player with this Trait causes a Casualty … and
    that player suffers a DEAD result … you may immediately add one new Lineman
    player from your team's Team Roster to your RESERVES BOX … During the Post-game
    Sequence, this player may be hired…"

    The in-match half is real and is applied: "you may IMMEDIATELY add one new
    Lineman player from your team's Team Roster to your RESERVES BOX", and the
    Lineman is in `data/rosters.json` — the positional with the 0-16 limit, the
    same one `flesh_out` picks for a preset token. Four exclusions come with it
    ("cannot be used against BIG GUY players, or any player with the DECAY,
    REGENERATION or STUNTY Traits"), and the first of those is a Keyword rather
    than a Trait.

    What is left is the sentence after: hiring them permanently is Post-game.
    """


@skill_hook("Hatred", "block_reroll")
def _hatred(ctx: SkillContext) -> None:
    """S3: "Whenever this player performs a Block Action against A PLAYER WITH THE
    SAME KEYWORD AS THAT SHOWN IN BRACKETS, this player may re-roll a single Player
    Down result."

    The keyword IS checked. It was in `data/rosters.json` all along under `role` —
    the parenthesised list the scraper captured after each position name ("Eagle
    Warrior (Lineman, Human)") — and nothing had ever read it.
    """


@skill_hook("Frenzy", "second_action")
def _frenzy(ctx: SkillContext) -> None:
    """S3: "Every time this player performs a Block Action, if the target is Pushed
    Back, then this player MUST FOLLOW-UP IF ABLE. Additionally, if after the
    target is Pushed Back they are STILL STANDING, then this player MUST PERFORM A
    SECOND BLOCK ACTION targeting THE SAME opposition player."

    MUST, twice. This is the one Skill that makes the engine take an Action nobody
    asked for, and the reason it is a liability as often as an advantage — a
    Frenzied player can be walked into the crowd by a defender who plans for it.

    Exactly ONE extra: "a second Block Action", so the recursion is one deep.
    """


@skill_hook("Multiple Block", "second_action")
def _multiple_block(ctx: SkillContext) -> None:
    """S3: "they may perform TWO Block Actions each targeting A DIFFERENT
    opposition player THEY ARE MARKING. If they do, then this player will REDUCE
    THEIR STRENGTH CHARACTERISTIC BY 2 for the duration … BOTH Block Actions are
    resolved in full, EVEN IF ONE OF THEM RESULTS IN A TURNOVER. This player CANNOT
    FOLLOW-UP during either."

    The turnover clause is what shapes the implementation: the second Block runs
    whatever the first did, so the turnover is collected at the end rather than
    returned from the middle. Rolling them one after the other is not an
    approximation — it is the procedure the rules themselves offer, in the same
    breath: "though YOU MAY WISH TO ROLL THEM SEPARATELY FOR CLARITY".
    """


@skill_hook("Pile Driver", "second_action")
def _pile_driver(ctx: SkillContext) -> None:
    """S3: "When an opposition player is KNOCKED DOWN by this player during a Block
    Action, this player MAY perform a FREE FOUL ACTION against the opposition
    player so long as they are STILL STANDING and are STILL MARKING them. This
    player is then PLACED PRONE and their activation immediately ends."

    Placed Prone rather than Knocked Down, so it costs them their feet but not an
    Armour Roll — and the activation ends whatever the Foul achieved, which is the
    price of it.
    """


@skill_hook("Hit and Run", "second_action")
def _hit_and_run(ctx: SkillContext) -> None:
    """S3: "…after FULLY RESOLVING the Action, they may immediately move ONE FREE
    SQUARE IGNORING TACKLE ZONES, so long as they are STILL STANDING. The player
    must ensure that AFTER THIS FREE MOVE THEY ARE NOT MARKED BY OR MARKING ANY
    OPPOSITION PLAYERS."

    That last sentence makes it a retreat rather than a reposition: there has to BE
    a square with nobody adjacent, or the Skill cannot be used at all.
    """


@skill_hook("Saboteur", "block_reaction")
def _saboteur_skill(ctx: SkillContext) -> None:
    """S3: "When THIS PLAYER IS KNOCKED DOWN as a result of AN OPPOSITION PLAYER'S
    Block Action, BEFORE THE ARMOUR ROLL IS MADE, they may roll a D6 … On a 4+ …
    the opposition player is ALSO Knocked Down … If this player's sabotaged weapon
    goes off, then they are AUTOMATICALLY KNOCKED OUT and THE ARMOUR ROLL IS NOT
    MADE for them."

    A trade, not a save, and the only way in the game to spend a player
    deliberately. Whose Skill it is matters: it belongs to the player going to the
    floor, not to the one who put them there.
    """


@skill_hook("Trickster", "block_reaction")
def _trickster(ctx: SkillContext) -> None:
    """S3: "Whenever an opposition player attempts to perform a Block Action against
    this player … BEFORE DETERMINING HOW MANY DICE ARE ROLLED, this player may be
    removed from the pitch and placed in ANY OTHER UNOCCUPIED SQUARE ADJACENT TO
    THE PLAYER PERFORMING THE ACTION. The Action then takes place as normal."

    Before the dice are counted, so it is a way of changing the ASSISTS rather than
    of escaping — they are still adjacent, still Blocked, just somewhere better.
    """


@skill_hook("Dump-off", "block_reaction")
def _dump_off(ctx: SkillContext) -> None:
    """S3: "Whenever an opposition player attempts to perform a Block Action against
    this player … this player may immediately perform a QUICK PASS before the
    Action targeting them is resolved. This Quick Pass CANNOT CAUSE A TURNOVER, but
    otherwise follows all the normal rules … Once the Quick Pass has been resolved,
    this Action targeting this player CONTINUES."

    The ball leaving before the hit lands is the whole point: it is how a carrier
    survives being caught, and the no-turnover clause is what makes trying it free.
    """


@skill_hook("Pick-me-up", "end_of_turn")
def _pick_me_up(ctx: SkillContext) -> None:
    """S3: "At the end of each of the OPPOSITION'S Turns, roll a D6 for each PRONE
    TEAM-MATE WITHIN 3 SQUARES of one or more STANDING players with this Trait. On
    a 5+, the Prone player may immediately STAND UP. Should a player with this
    Trait stand up as a result of a team-mate using this Trait, they may not also
    use this Trait during the same Turn."

    Standing up for free and out of turn, which is why it is worth a Trait at all —
    a Prone player normally spends three squares of their own activation on it.
    """


@skill_hook("On the Ball", "reaction")
def _on_the_ball(ctx: SkillContext) -> None:
    """S3, two halves. The one the engine applies: "during the Start of Drive
    Sequence, AFTER THE KICK DEVIATES but BEFORE THE KICK-OFF EVENT IS ROLLED, a
    single OPEN player on the RECEIVING team with this Skill may move UP TO 3
    SQUARES … they cannot Rush … may not move into the opposition half."

    The other half interrupts an opponent's Pass Action: "AFTER THE TARGET SQUARE
    HAS BEEN DECLARED but BEFORE THE PASSING ABILITY TEST IS ROLLED". That window
    is one step wide and it is inside `throw.resolve` — validate has settled the
    square and no die has been thrown. See `throw._on_the_ball`.
    """


@skill_hook("Foul Appearance", "block_reaction")
def _foul_appearance(ctx: SkillContext) -> None:
    """S3: "Whenever an opposition player attempts to perform a Block Action against
    this player … they must roll a D6 BEFORE ANY OTHER DICE ARE ROLLED. On a 2+,
    the Block Action continues as normal. On a 1, the Block Action is IMMEDIATELY
    CANCELLED and the opposition player's activation immediately ends."

    Not a Turnover — the Block simply does not happen. For a Blitz that means the
    team's one Blitz is gone for nothing, which is the real cost of it.
    """


@skill_hook("Taunt", "block_reaction")
def _taunt(ctx: SkillContext) -> None:
    """S3: "When a player with this Skill is Pushed Back as a result of a Block
    Action performed against them, this player's Coach MAY CHOOSE TO MAKE the
    opposition player FOLLOW-UP."

    The defender forcing the attacker forward — the opposite of Fend, which is why
    the two cannot both apply. The engine takes it whenever it is available: a
    defender who taunts wants the attacker off their line, and declining is never
    the reason they took the Skill.
    """


@skill_hook("Eye Gouge", "block_reaction")
def _eye_gouge(ctx: SkillContext) -> None:
    """S3: "When an opposition player is Pushed Back by this player, the opposition
    player CANNOT PROVIDE OFFENSIVE OR DEFENSIVE ASSISTS UNTIL AFTER THEY ARE NEXT
    ACTIVATED."

    Distracted is exactly that duration — "they will remain Distracted until they
    are next activated" — and it already removes a Tackle Zone, which is what an
    assist needs. So this is the existing condition rather than a second one that
    would have to agree with it.
    """


@skill_hook("Tentacles", "leaving")
def _tentacles(ctx: SkillContext) -> None:
    """S3: "…roll a D6 and add their Strength … subtract the Strength
    Characteristic of the opposition player … If the result is 6 or higher, OR THE
    ROLL IS A NATURAL 6, then the opposition player does not leave the square they
    attempted to leave and their activation comes to an end."

    Rolled BEFORE the Agility Test, because it stops them leaving at all.
    """


@skill_hook("Diving Tackle", "leaving")
def _diving_tackle(ctx: SkillContext) -> None:
    """S3: "…and an Agility test HAS BEEN ROLLED and any modifiers and re-rolls
    HAVE BEEN APPLIED, this player may use this Skill. Immediately apply a -2
    modifier … and place this player Prone in the square the opposition player
    vacated."

    After everything, which is what makes it worth a Skill: the coach spends it
    knowing whether it will matter.
    """


@skill_hook("Arm Bar", "leaving")
def _arm_bar(ctx: SkillContext) -> None:
    """S3: "If an opposing player FALLS OVER as a result of attempting to Dodge,
    Leap or Jump away from a square in this player's Tackle Zone … they may apply
    a +1 modifier to either the Armour Roll or Injury Roll." """


@skill_hook("Shadowing", "leaving")
def _shadowing(ctx: SkillContext) -> None:
    """S3: "…roll a D6. On a 1-3, nothing happens. On a 4+, this player is
    immediately placed into the square that the opposition player vacated." """
